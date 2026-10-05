"""Large-file test data, resource measurement and the 1 MB – 10 GB scalability benchmark.

Nothing in this module invents numbers. Test files are generated locally and
really processed. Every stage runs as a separate ``vnx-dna`` subprocess (the
installed CLI) and is measured from outside:

* **wall time**: ``time.perf_counter`` around the subprocess;
* **CPU**: user and system time from ``os.wait4``, which includes the worker
  processes the stage reaped; utilisation = CPU / wall;
* **peak RAM**: the largest *sum* of ``VmRSS`` over the stage's whole process
  tree (main process and workers), sampled from ``/proc`` every 0.1 s. It is a
  sampled lower bound of the true peak, and shared pages are counted once per
  process (an overestimate);
* **swap**: the largest sum of ``VmSwap`` over the tree;
* **disk**: the largest total size of the working directory during the stage.

Generated data never goes into Git (``*.bin`` is ignored, and the default
directory is outside the repository).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..errors import ConfigurationError, InvalidInputError
from ..provenance import environment
from .container import publish
from .paths import atomic_write_text, check_output_file, private_temp

BLOCK = 4 << 20
PATTERNS = ("random", "compressible", "mixed", "structured")
_UNITS = {"": 1, "B": 1, "K": 1000, "KB": 1000, "M": 1000 ** 2, "MB": 1000 ** 2, "G": 1000 ** 3, "GB": 1000 ** 3,
          "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3}


def parse_size(text: str) -> int:
    """'10GB' → 10_000_000_000 (decimal units; KiB/MiB/GiB are binary)."""
    t = text.strip().upper().replace(" ", "")
    number = t.rstrip("KMGIB")
    unit = t[len(number):]
    if unit not in _UNITS or not number.replace(".", "", 1).isdigit():
        raise ConfigurationError(f"invalid size {text!r} (examples: 100MB, 1GB, 10GB, 64MiB)")
    return int(float(number) * _UNITS[unit])


# ======================================================================= generator
def _vocabulary(seed: int) -> list[bytes]:
    rng = np.random.default_rng([seed, 7])
    letters = np.frombuffer(b"etaoinshrdlcumwfgypbvkjxqz", dtype=np.uint8)
    weights = np.linspace(2.0, 0.2, letters.size)
    weights /= weights.sum()
    words = []
    for _ in range(2048):
        n = int(rng.integers(2, 10))
        words.append(letters[rng.choice(letters.size, n, p=weights)].tobytes())
    return words


def _block(pattern: str, seed: int, index: int, size: int, vocab: list[bytes]) -> bytes:
    rng = np.random.default_rng([seed, index])
    if pattern == "random":
        return rng.bytes(size)
    if pattern == "compressible":
        zipf = np.minimum(rng.zipf(1.3, size // 3 + 16), len(vocab)) - 1
        text = b" ".join(vocab[i] for i in zipf.tolist())
        while len(text) < size:
            text += b" " + text
        return text[:size]
    if pattern == "structured":
        n = -(-size // 64)
        rec = np.zeros(n, dtype=[("seq", "<u8"), ("time", "<u4"), ("sensor", "<u2"), ("flags", "<u2"),
                                 ("values", "<f4", (8,)), ("tag", "S16")])
        base = index * n
        rec["seq"] = np.arange(base, base + n)
        rec["time"] = (1_700_000_000 + (np.arange(base, base + n) // 10)).astype(np.uint32)
        rec["sensor"] = rng.integers(0, 64, n)
        rec["flags"] = (rng.random(n) < 0.01).astype(np.uint16)
        phase = np.arange(base, base + n)[:, None] / 500.0 + np.arange(8)[None, :]
        rec["values"] = np.round(np.sin(phase) * 100 + rng.normal(0, 0.5, (n, 8)), 1).astype(np.float32)
        rec["tag"] = b"VNXSENSOR-DATA01"
        return rec.tobytes()[:size]
    if pattern == "mixed":
        quarter = -(-size // 4)
        parts = [_block("random", seed, index * 4, quarter, vocab), _block("compressible", seed, index * 4 + 1, quarter, vocab),
                 _block("structured", seed, index * 4 + 2, quarter, vocab), _block("compressible", seed, index * 4 + 3, quarter, vocab)]
        return b"".join(parts)[:size]
    raise ConfigurationError(f"unknown pattern {pattern!r}; choose from {PATTERNS}")


def generate_file(output: str | os.PathLike, size: int, pattern: str = "mixed", seed: int = 42, *, overwrite: bool = False) -> dict[str, Any]:
    """Write a reproducible test file of exactly ``size`` bytes.

    The file is a pure function of (size, pattern, seed). Every full 4 MiB
    block ``i`` depends only on (pattern, seed, i), so files of different sizes
    share their full leading blocks; the final partial block also depends on its length.
    """
    if pattern not in PATTERNS:
        raise ConfigurationError(f"unknown pattern {pattern!r}; choose from {PATTERNS}")
    if not isinstance(size, int) or size < 0:
        raise ConfigurationError("size must be a non-negative integer")
    out = check_output_file(output, overwrite=overwrite)  # existing output is OUTPUT_ERROR (8), as documented
    started = time.perf_counter()
    vocab = _vocabulary(seed)
    h = hashlib.sha256()
    fd, tmp = private_temp(out)
    written = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            index = 0
            while written < size:
                data = _block(pattern, seed, index, min(BLOCK, size - written), vocab)
                handle.write(data)
                h.update(data)
                written += len(data)
                index += 1
        publish(tmp, out, overwrite=overwrite)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return {"status": "SUCCESS", "operation": "generate", "output": str(out), "pattern": pattern, "seed": seed, "size": written,
            "sha256": h.hexdigest(), "block_bytes": BLOCK, "elapsed_s": time.perf_counter() - started}


# ======================================================================= measurement
def _tree(pid: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            with open(f"/proc/{entry.name}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(entry.name))
        except (OSError, IndexError, ValueError):
            continue
    out, stack = [], [pid]
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(children.get(p, []))
    return out


def _mem(pid: int) -> tuple[int, int]:
    rss = swap = 0
    try:
        with open(f"/proc/{pid}/status", "rb") as handle:
            for line in handle:
                if line.startswith(b"VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                elif line.startswith(b"VmSwap:"):
                    swap = int(line.split()[1]) * 1024
    except OSError:
        pass
    return rss, swap


def dir_bytes(path: Path) -> int:
    total = 0
    stack = [path]
    while stack:
        p = stack.pop()
        try:
            for entry in os.scandir(p):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
        except OSError:
            continue
    return total


def run_measured(command: list[str], *, watch_dir: Path | None = None, env: dict[str, str] | None = None,
                 interval: float = 0.1) -> dict[str, Any]:
    """Run ``command`` and measure wall time, CPU, peak tree RSS, swap and disk use of ``watch_dir``."""
    started = time.perf_counter()
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    peak = {"rss": 0, "swap": 0, "disk": dir_bytes(watch_dir) if watch_dir else 0, "processes": 1}
    stop = threading.Event()

    def sample() -> None:
        disk_tick = 0
        while not stop.is_set():
            pids = _tree(proc.pid)
            rss = swap = 0
            for pid in pids:
                r, s = _mem(pid)
                rss += r
                swap += s
            peak["rss"] = max(peak["rss"], rss)
            peak["swap"] = max(peak["swap"], swap)
            peak["processes"] = max(peak["processes"], len(pids))
            disk_tick += 1
            if watch_dir is not None and disk_tick % 5 == 0:
                peak["disk"] = max(peak["disk"], dir_bytes(watch_dir))
            stop.wait(interval)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    out_chunks: list[bytes] = []
    out_pipe, err_pipe = proc.stdout, proc.stderr
    assert out_pipe is not None and err_pipe is not None  # both are pipes (Popen above)
    reader = threading.Thread(target=lambda: out_chunks.append(out_pipe.read()), daemon=True)
    reader.start()
    err = err_pipe.read()
    _, status, usage = os.wait4(proc.pid, 0)
    reader.join()
    stop.set()
    sampler.join()
    proc.returncode = os.waitstatus_to_exitcode(status)
    wall = time.perf_counter() - started
    if watch_dir is not None:
        peak["disk"] = max(peak["disk"], dir_bytes(watch_dir))
    stdout = b"".join(out_chunks).decode("utf-8", errors="replace")
    try:
        report = json.loads(stdout) if stdout.strip().startswith("{") else None
    except ValueError:
        report = None
    cpu = usage.ru_utime + usage.ru_stime
    return {"command": command[1:] if command[0] == sys.executable else command, "returncode": proc.returncode, "wall_s": wall,
            "cpu_user_s": usage.ru_utime, "cpu_system_s": usage.ru_stime, "cpu_utilisation": cpu / wall if wall else None,
            "peak_rss_tree_bytes": peak["rss"], "peak_swap_tree_bytes": peak["swap"], "max_processes": peak["processes"],
            "largest_single_process_maxrss_bytes": usage.ru_maxrss * 1024,
            "peak_workdir_bytes": peak["disk"], "report": report,
            "stderr_tail": err.decode("utf-8", errors="replace")[-2000:]}


def _cli() -> list[str]:
    return [sys.executable, "-m", "vnxdna"]


def _sha256_external(path: Path) -> str:
    """SHA-256 via the system ``sha256sum`` (independent of VNX-DNA's code); falls back to hashlib."""
    try:
        out = subprocess.run(["sha256sum", str(path)], capture_output=True, text=True, check=True)
        return out.stdout.split()[0]
    except (OSError, subprocess.CalledProcessError):
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(BLOCK), b""):
                h.update(block)
        return h.hexdigest()


def _cmp_external(a: Path, b: Path) -> bool:
    try:
        return subprocess.run(["cmp", "-s", str(a), str(b)]).returncode == 0
    except OSError:
        from .api import _compare_files
        return _compare_files(a, b)[0]


def _slice(path: Path, offset: int, length: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(offset)
        return handle.read(length)


# ======================================================================= scalability benchmark
def scale_benchmark(sizes: list[int], work_dir: str | os.PathLike, *, pattern: str = "mixed", seed: int = 42, profile: str = "balanced",
                    workers: int = 0, key_file: str | None = None, keep: bool = False, output: str | os.PathLike | None = None,
                    random_access_length: int = 1 << 20, log=None) -> dict[str, Any]:
    """For each size: generate → store → restore(container) → encode(VXS) → recover(DNA) → random access, all measured.

    The 10 GB class of run is a *storage pipeline* scalability test: the DNA
    representation is really produced and decoded, but no sequencing channel
    is applied (see docs/LARGE_FILES.md for why and where that boundary is).
    """
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"benchmark": "vnx-dna scale", "pattern": pattern, "seed": seed, "profile": profile,
                               "environment": environment(), "runs": []}
    common = ["--workers", str(workers)] if workers else []
    key_args = ["--key-file", key_file] if key_file else []

    def say(message: str) -> None:
        if log is not None:
            log(message)

    def save() -> None:
        if output is not None:
            atomic_write_text(output, json.dumps(results, indent=2, sort_keys=True, default=str) + "\n")

    for size in sizes:
        run_dir = work / f"size-{size}"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True)
        entry: dict[str, Any] = {"size": size, "stages": {}}
        results["runs"].append(entry)
        src = run_dir / "input.bin"
        say(f"[{size:,} B] generate")
        entry["generate"] = generate_file(src, size, pattern, seed)
        entry["input_sha256_sha256sum"] = _sha256_external(src)
        container, strands, restored, recovered = run_dir / "a.vxdna", run_dir / "strands.vxs", run_dir / "restored.bin", run_dir / "recovered.bin"
        stages = entry["stages"]

        def stage(name: str, args: list[str]) -> dict[str, Any]:
            say(f"[{size:,} B] {name}")
            result = run_measured(_cli() + args, watch_dir=run_dir)
            stages[name] = result
            save()
            if result["returncode"] != 0:
                raise RuntimeError(f"stage {name} failed (exit {result['returncode']}): {result['stderr_tail']}")
            return result

        try:
            stage("store", ["store", str(src), "-o", str(container), "--profile", profile, "--json"] + common + key_args)
            stage("restore_container", ["restore", str(container), "-o", str(restored), "--json"] + common + key_args)
            entry["restore_container_cmp"] = _cmp_external(src, restored)
            restored.unlink()
            offset = max(0, size // 2 - random_access_length // 2)
            length = min(random_access_length, size - offset)
            section = run_dir / "section-container.bin"
            stage("extract_container", ["extract", str(container), "--offset", str(offset), "--length", str(length),
                                             "-o", str(section), "--json"] + key_args)
            entry["random_access_container_match"] = section.read_bytes() == _slice(src, offset, length)
            section.unlink()
            stage("encode_vxs", ["encode", str(container), "-o", str(strands), "--json"] + common)
            container.unlink()  # the DNA file is now the only copy of the archive
            section = run_dir / "section-dna.bin"
            stage("extract_dna", ["extract", str(strands), "--offset", str(offset), "--length", str(length), "-o", str(section),
                                  "--temp-dir", str(run_dir), "--json"] + common + key_args)
            entry["random_access_dna_match"] = section.read_bytes() == _slice(src, offset, length)
            entry["random_access"] = {"offset": offset, "length": length}
            section.unlink()
            stage("recover_dna", ["recover", str(strands), "-o", str(recovered), "--temp-dir", str(run_dir), "--json"] + common + key_args)
            entry["recovered_sha256_sha256sum"] = _sha256_external(recovered)
            entry["recovered_cmp_identical"] = _cmp_external(src, recovered)
            entry["sha256_match"] = entry["recovered_sha256_sha256sum"] == entry["input_sha256_sha256sum"]
            entry["strands_file_bytes"] = strands.stat().st_size
            entry["status"] = "PASS" if entry["sha256_match"] and entry["recovered_cmp_identical"] and entry["random_access_dna_match"] \
                and entry["random_access_container_match"] and entry["restore_container_cmp"] else "FAIL"
        except RuntimeError as error:
            entry["status"] = "FAIL"
            entry["error"] = str(error)
        finally:
            if not keep:
                shutil.rmtree(run_dir, ignore_errors=True)
        save()
        say(f"[{size:,} B] {entry['status']}")
    return results


# ======================================================================= corruption acceptance
def damage_vxs(strands: Path, index_path: Path, output: Path, *, groups_within: int, groups_beyond: int, seed: int,
               substitutions_per_group: int = 0) -> dict[str, Any]:
    """Copy a VXS strand file, deleting strands of selected ECC groups.

    ``groups_within`` groups lose exactly M strands (inside the guarantee);
    ``groups_beyond`` groups lose M + 1 (outside it). Additionally
    ``substitutions_per_group`` random base substitutions are applied to
    surviving strands of the damaged groups (the inner code must fix or
    reject them). Returns the ground truth of what was damaged.
    """
    from .encoder import read_dna_index
    from .strandio import StrandWriter, read_vxs_range, vxs_info
    index = read_dna_index(index_path)
    info = vxs_info(strands)
    rows = index["chunks"]["rows"]  # first_strand, strands, byte_offset, byte_length, first_stripe, stripe_count
    rng = np.random.default_rng(seed)
    full = [c for c, r in enumerate(rows) if r[5] >= 2]
    if not full:
        raise InvalidInputError("archive too small for a corruption test (needs chunks with at least two ECC groups)")
    chunks = rng.choice(full, size=min(len(full), groups_within + groups_beyond), replace=False).tolist()
    drop: set[int] = set()
    substitute: dict[int, int] = {}
    truth = []
    group_strands = int(index["group_strands"])
    parity = int(index["parity_shards"])
    for i, c in enumerate(chunks):
        first_strand, _, _, _, first_stripe, _ = rows[c]
        lose = parity if i < groups_within else parity + 1
        start = first_strand  # the chunk's first ECC group: all K+M strands, in order (only the last group is shortened)
        victims = rng.choice(group_strands, size=lose, replace=False) + start
        drop.update(int(v) for v in victims)
        survivors = [start + s for s in range(group_strands) if start + s not in drop]
        for s in rng.choice(survivors, size=min(substitutions_per_group, len(survivors)), replace=False).tolist():
            substitute[int(s)] = int(rng.integers(0, info.strand_nt))
        truth.append({"chunk": c, "ecc_group": first_stripe, "strands_deleted": lose,
                      "within_guarantee": lose <= parity, "substituted_strands": min(substitutions_per_group, len(survivors))})
    batch = 1 << 16
    with StrandWriter(output, "vxs", strand_nt=info.strand_nt, overwrite=True) as writer:
        from .strandio import ReadBatch
        for first in range(0, info.count, batch):
            count = min(batch, info.count - first)
            codes = read_vxs_range(strands, info, first, count)
            ordinals = np.arange(first, first + count)
            keep = np.array([o not in drop for o in ordinals.tolist()]) if drop else np.ones(count, bool)
            for o, pos in substitute.items():
                if first <= o < first + count:
                    codes[o - first, pos] = (codes[o - first, pos] + 1) % 4
            writer.write_batch(ReadBatch.from_matrix(codes[keep]))
        writer.commit()
    return {"damaged_groups": truth, "strands_deleted": len(drop), "strands_substituted": len(substitute)}


def corruption_acceptance(size: int, work_dir: str | os.PathLike, *, pattern: str = "mixed", seed: int = 42, profile: str = "balanced",
                          groups_within: int = 50, substitutions_per_group: int = 5, workers: int = 0, keep: bool = False,
                          log=None) -> dict[str, Any]:
    """Large-file damage test: damage inside the guarantee must recover exactly; beyond it must fail clearly.

    1. generate ``size`` bytes, ``store``, ``encode`` to VXS (all with the CLI);
    2. delete exactly M strands in each of ``groups_within`` ECC groups (and
       apply substitutions to other strands of those groups), then ``recover``
       and compare with ``cmp`` and ``sha256sum``;
    3. delete M + 1 strands in one more group, then ``recover`` must exit
       with code 5 and write nothing, and ``verify`` must name that chunk.
    """
    work = Path(work_dir) / f"corruption-{size}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    common = ["--workers", str(workers)] if workers else []
    say = log or (lambda m: None)
    out: dict[str, Any] = {"benchmark": "vnx-dna corruption acceptance", "size": size, "pattern": pattern, "seed": seed,
                           "profile": profile, "environment": environment(), "stages": {}}
    try:
        src = work / "input.bin"
        say("generate")
        out["generate"] = generate_file(src, size, pattern, seed)
        for name, args in (("store", ["store", str(src), "-o", str(work / "a.vxdna"), "--profile", profile, "--json"] + common),
                           ("encode", ["encode", str(work / "a.vxdna"), "-o", str(work / "s.vxs"), "--json"] + common)):
            say(name)
            out["stages"][name] = run_measured(_cli() + args, watch_dir=work)
            if out["stages"][name]["returncode"] != 0:
                raise RuntimeError(out["stages"][name]["stderr_tail"])
        (work / "a.vxdna").unlink()
        say("damage within the guarantee")
        out["damage_within"] = damage_vxs(work / "s.vxs", work / "s.vxs.vxidx", work / "within.vxs", groups_within=groups_within,
                                          groups_beyond=0, seed=seed, substitutions_per_group=substitutions_per_group)
        say("recover (within)")
        rec = run_measured(_cli() + ["recover", str(work / "within.vxs"), "-o", str(work / "within.bin"), "--temp-dir", str(work),
                                     "--json"] + common, watch_dir=work)
        out["stages"]["recover_within"] = rec
        out["within_exit_code"] = rec["returncode"]
        out["within_cmp_identical"] = rec["returncode"] == 0 and _cmp_external(src, work / "within.bin")
        out["within_sha256_match"] = rec["returncode"] == 0 and _sha256_external(work / "within.bin") == out["generate"]["sha256"]
        (work / "within.bin").unlink(missing_ok=True)
        (work / "within.vxs").unlink(missing_ok=True)
        say("damage beyond the guarantee")
        out["damage_beyond"] = damage_vxs(work / "s.vxs", work / "s.vxs.vxidx", work / "beyond.vxs", groups_within=groups_within,
                                          groups_beyond=1, seed=seed + 1, substitutions_per_group=substitutions_per_group)
        say("recover (beyond)")
        rec = run_measured(_cli() + ["recover", str(work / "beyond.vxs"), "-o", str(work / "beyond.bin"), "--temp-dir", str(work),
                                     "--json"] + common, watch_dir=work)
        out["stages"]["recover_beyond"] = rec
        out["beyond_exit_code"] = rec["returncode"]
        out["beyond_output_written"] = (work / "beyond.bin").exists()
        out["beyond_error"] = rec["stderr_tail"].strip().splitlines()[-1] if rec["stderr_tail"].strip() else None
        say("verify (beyond)")
        ver = run_measured(_cli() + ["verify", str(work / "beyond.vxs"), "--temp-dir", str(work), "--json"] + common, watch_dir=work)
        out["stages"]["verify_beyond"] = ver
        reported = [d["chunk"] for d in (ver["report"] or {}).get("damaged_chunks", [])]
        expected = [g["chunk"] for g in out["damage_beyond"]["damaged_groups"] if not g["within_guarantee"]]
        out["verify_reported_damaged_chunks"] = reported
        out["verify_expected_damaged_chunks"] = expected
        out["status"] = "PASS" if (out["within_cmp_identical"] and out["within_sha256_match"] and out["beyond_exit_code"] == 5
                                   and not out["beyond_output_written"] and reported == expected) else "FAIL"
    except RuntimeError as error:
        out["status"] = "FAIL"
        out["error"] = str(error)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    return out
