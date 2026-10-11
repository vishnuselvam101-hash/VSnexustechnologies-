"""V11 WP3: peak RSS and input-equivalent copies vs input size, V10 (v10.0.0) vs V11 (SIMULATED reads).

Per (size, scenario) the input (random bytes, seed = SEED + size index), strands (``sdk.encode``, v4-balanced, this
tree) and reads (``sdk.simulate``, seed = SEED) are prepared once. Then each tree (``PYTHONPATH=<tree>/src``) runs
``encode`` of the same input and ``decode`` of the same reads in its own subprocess (decode output = the archive container, compared with the one
kept at preparation), the tree order alternating per
size (even index: V10 first). Scenarios: ``clean`` (shipped ``clean`` model, coverage 3, no errors) and ``indel``
(shipped ``illumina-like`` model with insertion = deletion = 1e-3 per base, coverage 10).

Recorded per stage: status, output SHA-256, wall seconds, the stage process's
``ru_maxrss`` (read before the output is hashed), the RSS right after imports (baseline), the summed RSS of the stage's whole process tree sampled from
/proc every 0.2 s, ``copies`` = (peak - baseline) / input bytes, the host load before the stage. Guards: every stage
runs with ``RLIMIT_AS`` = 12 GiB and is killed if its process tree's RSS passes 12 GiB (row status ``GUARD_KILLED``,
never a decoder outcome); the heaviest size (64 MiB) is refused while the 1-minute load is >= 3 (status ``NOT RUN: load``).

    V10_TREE=/root/vnx-dna-lab/v10-rel PYTHONPATH=src python experiments/v11/mem/rss.py run --sizes 1m,4m
    PYTHONPATH=src python experiments/v11/mem/rss.py summary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("VNX_MEM_OUT") or HERE / "results" / "rss.jsonl")
SEED = 111000
CAP = 12 * 2**30
SIZES = {"1m": 1 << 20, "4m": 4 << 20, "16m": 16 << 20, "64m": 64 << 20}
HEAVY = 64 << 20                                # the heaviest size: refused while load1 >= 3
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"
SCENARIOS = {"clean": {"model": "clean", "overrides": None},
             "indel": {"model": "illumina-like", "overrides": {"insertion_rate": 1e-3, "deletion_rate": 1e-3}}}
STAGE = r"""
import json, sys, time, resource, hashlib, inspect
def rss():
    with open("/proc/self/statm") as f: return int(f.read().split()[1]) * 4096
from vnxdna import sdk
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.recovery.options import DecodeOptions
from vnxdna.archive.operations import ArchiveOptions
from pathlib import Path
stage, a, b, workers = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
base = rss()
t = time.perf_counter()
if stage == "encode":
    r = sdk.encode([a], b, dna=DNAOptions(profile="v4-balanced", workers=workers), archive_options=ArchiveOptions(workers=workers),
                   allow_unencrypted=True)
else:
    r = sdk.decode(a, b, options=DecodeOptions(workers=workers), allow_unencrypted=True)
wall = time.perf_counter() - t
peak, peak_children = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
h = None
if b.exists():                       # streamed, and after the peak was read: the check must not add to the measurement
    h = hashlib.sha256()
    with b.open("rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    h = h.hexdigest()
print(json.dumps({"wall_s": wall, "baseline_rss": base, "maxrss": peak * 1024, "maxrss_children": peak_children * 1024,
                  "status": str(r.status), "out_sha256": h}))
"""


def _git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, check=False).stdout.strip()


def _tree_rss(root: int) -> int:
    """Summed RSS (bytes) of ``root`` and its descendants (all processes in its process group)."""
    total = 0
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            if int(fields[2]) != root:          # pgrp
                continue
            with open(f"/proc/{d}/statm") as f:
                total += int(f.read().split()[1]) * 4096
        except (OSError, IndexError, ValueError):
            continue
    return total


def _limit():
    os.setsid()
    resource.setrlimit(resource.RLIMIT_AS, (CAP, CAP))


def run_stage(tree: Path, stage: str, a: Path, b: Path, workers: int, timeout: float) -> dict:
    env = dict(os.environ, PYTHONPATH=str(tree / "src"))
    load = os.getloadavg()[0]
    p = subprocess.Popen([sys.executable, "-c", STAGE, stage, str(a), str(b), str(workers)], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env, preexec_fn=_limit)
    peak, killed, stop = [0], [False], threading.Event()

    def sample():
        while not stop.wait(0.2):
            v = _tree_rss(p.pid)
            peak[0] = max(peak[0], v)
            if v > CAP:
                killed[0] = True
                os.killpg(p.pid, signal.SIGKILL)
                return

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
        killed[0] = "timeout"
    stop.set()
    th.join()
    row = {"tree_peak_rss": peak[0], "load1_before": round(load, 2)}
    if killed[0]:
        return {**row, "status": "GUARD_KILLED" if killed[0] is True else "TIMEOUT"}
    if p.returncode:
        return {**row, "status": "ERROR", "rc": p.returncode, "stderr_tail": err[-600:]}
    return {**row, **json.loads(out.strip().splitlines()[-1])}


def prepare(work: Path, size: int, idx: int, scenario: str) -> tuple[Path, Path, dict]:
    from vnxdna import sdk
    from vnxdna.archive.operations import ArchiveOptions
    from vnxdna.pipeline.encode import DNAOptions
    import random
    src = work / f"in-{size}.bin"
    if not src.exists():
        src.write_bytes(random.Random(SEED + idx).randbytes(size))
    strands = work / f"strands-{size}.fasta"
    if not strands.exists():
        sdk.encode([src], strands, dna=DNAOptions(profile="v4-balanced", workers=4), archive_options=ArchiveOptions(workers=4),
                   allow_unencrypted=True, keep_archive=work / f"archive-{size}.vnx")
    sc = SCENARIOS[scenario]
    reads = work / f"reads-{size}-{scenario}.fastq"
    if not reads.exists():
        sdk.simulate(strands, reads, model=sc["model"], seed=SEED, overrides=sc["overrides"], workers=4)
    return src, reads, {"input_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                        "archive_sha256": hashlib.sha256((work / f"archive-{size}.vnx").read_bytes()).hexdigest(),
                        "strands_sha256": hashlib.sha256(strands.read_bytes()).hexdigest(),
                        "reads_bytes": reads.stat().st_size}


def run(sizes: list[str], scenarios: list[str], v10: Path, workers: int, timeout: float, work: Path) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    trees = {"v10": v10, "v11": ROOT}
    commits = {k: _git(t, "rev-parse", "HEAD") for k, t in trees.items()}
    for name in sizes:
        size, idx = SIZES[name], list(SIZES).index(name)
        for scenario in scenarios:
            base = {"label": LABEL, "size": size, "scenario": scenario, "workers": workers, "commits": commits,
                    "seed": SEED, "cpu_count": os.cpu_count(), "python": sys.version.split()[0]}
            if size >= HEAVY and os.getloadavg()[0] >= 3:
                for k in trees:
                    _append({**base, "tree": k, "stage": "decode", "status": f"NOT RUN: load {os.getloadavg()[0]:.1f} >= 3"})
                continue
            src, reads, prov = prepare(work, size, idx, scenario)
            order = list(trees) if idx % 2 == 0 else list(trees)[::-1]
            for k in order:
                for stage, a in (("encode", src), ("decode", reads)):
                    if stage == "encode" and scenario != "clean":
                        continue                                       # encode does not depend on the scenario
                    out = work / f"out-{k}-{stage}-{size}-{scenario}"
                    out.unlink(missing_ok=True)
                    r = run_stage(trees[k], stage, a, out, workers, timeout)
                    if "baseline_rss" in r:
                        peak = max(r["maxrss"], r.get("maxrss_children", 0))
                        r["copies"] = round((peak - r["baseline_rss"]) / size, 3)
                        r["exact"] = r["out_sha256"] == prov["archive_sha256"] if stage == "decode" else None
                    out.unlink(missing_ok=True)
                    _append({**base, **prov, "tree": k, "stage": stage, **r})


def _append(row: dict) -> None:
    with OUT.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    print(json.dumps({k: row.get(k) for k in ("size", "scenario", "tree", "stage", "status", "wall_s", "maxrss",
                                              "tree_peak_rss", "copies", "exact", "load1_before")}), flush=True)


def summarize(rows: list[dict]) -> dict:
    """Per tree/stage/scenario: peak RSS (MiB) per size and least-squares slopes, MiB of RSS per MiB of input, of the
    stage process's ``ru_maxrss`` and of the sampled process-tree peak."""
    mib = 2**20
    out: dict = {"label": LABEL, "series": {}}
    for r in rows:
        if "maxrss" not in r:
            continue
        key = f"{r['tree']}/{r['stage']}/{r['scenario']}"
        s = out["series"].setdefault(key, {"points": {}})
        s["points"][str(r["size"] // mib)] = {"maxrss_mib": round(max(r["maxrss"], r.get("maxrss_children", 0)) / mib, 1),
                                              "tree_peak_mib": round(r["tree_peak_rss"] / mib, 1), "copies": r["copies"],
                                              "wall_s": round(r["wall_s"], 1), "exact": r.get("exact")}
    for s in out["series"].values():
        for field, name in (("maxrss_mib", "slope_maxrss"), ("tree_peak_mib", "slope_tree_peak")):
            pts = [(int(k), v[field]) for k, v in s["points"].items()]
            if len(pts) >= 2:
                mx, my = sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
                den = sum((x - mx) ** 2 for x, _ in pts)
                s[name] = round(sum((x - mx) * (y - my) for x, y in pts) / den, 3) if den else None
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--sizes", default="1m,4m")
    ap.add_argument("--scenarios", default="clean,indel")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--work", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "summary":
        rows = [json.loads(line) for line in OUT.open()]
        s = summarize(rows)
        OUT.with_name("summary.json").write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: [v.get("slope_maxrss"), v.get("slope_tree_peak")] for k, v in s["series"].items()}))
        return 0
    v10 = Path(os.environ["V10_TREE"])
    work = a.work or Path(tempfile.mkdtemp(prefix="v11mem-", dir="/root/vnx-dna-lab/tmp"))
    work.mkdir(parents=True, exist_ok=True)
    run(a.sizes.split(","), a.scenarios.split(","), v10, a.workers, a.timeout, work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
