"""V10 opt acceptance: process-parallel clustering consensus vs the unchanged V10 tree (SIMULATED).

Per seed the archive and reads are prepared once (D13-F1 stress channel, coverage 10, v4-balanced, data and channel seed
= case seed, as in experiments/v9/scale/noisy.py), then decoded by each tree in its own subprocess, in an order that
alternates per seed (even seed: base first). Decoder E, 4 workers. Each subprocess imports vnxdna from its own tree
(``PYTHONPATH=<tree>/src``).

Recorded per decode: outcome, output SHA-256, report SHA-256 (report minus timings and threading provenance, as in
W1/W2, plus ``consensus_processes``), decode seconds, host load, the decoder's peak RSS, the largest single-process
RSS (``getrusage``) and the summed RSS of the decode's whole process tree (sampled from /proc every 0.5 s; includes
interpreter start-up).

Acceptance (fixed before the run): identical outcome, output and report on every case; 0 false success; the 1 MiB
median below 300 s is the plan's W2 target. Speed-up: paired per-seed ratio, geometric mean, percentile bootstrap.

    BASE_TREE=<V10 53d3b19 checkout> PYTHONPATH=src python experiments/v10/opt/bench.py run --size 1m --seeds 93000-93002
    BASE_TREE=... PYTHONPATH=src python experiments/v10/opt/bench.py run --size 20k --seeds 94000-94009
    PYTHONPATH=src python experiments/v10/opt/bench.py summary
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = Path(os.environ.get("VNX_OPT_OUT") or HERE / "results" / "opt.jsonl")   # override: smoke runs and the V10 final run
SUMMARY = OUT.with_name(OUT.stem + "-summary.json")
SIZES = {"20k": 20_000, "1m": 1 << 20}


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True).stdout.strip()


def _tree_rss(pid: int) -> int:
    """Summed RSS (bytes) of ``pid`` and all its descendants, from /proc (Linux)."""
    kids: dict = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    ppid = int(f.read().rsplit(")", 1)[1].split()[1])
                kids.setdefault(ppid, []).append(int(d))
            except (OSError, ValueError, IndexError):
                pass
    total, todo = 0, [pid]
    while todo:
        p = todo.pop()
        todo += kids.get(p, [])
        try:
            with open(f"/proc/{p}/statm") as f:
                total += int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            pass
    return total


class HarnessError(SystemExit):
    """The harness could not obtain a decode result (crash, timeout, wrong tree): no row is written, so it can never
    count as a decoder outcome or as a passing case."""


DECODE_TIMEOUT_S = float(os.environ.get("VNX_BENCH_DECODE_TIMEOUT", 4 * 3600))


def _run_sampled(cmd: list, env: dict, timeout: float = DECODE_TIMEOUT_S) -> tuple[subprocess.CompletedProcess, int]:
    """Run ``cmd`` in its own process group; sample the summed RSS of its process tree every 0.5 s; (result, peak
    bytes). On timeout or any exception here (including Ctrl-C) the whole group is killed before returning."""
    import signal
    import threading
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    peak = [0]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.5):
            peak[0] = max(peak[0], _tree_rss(proc.pid))
    t = threading.Thread(target=sample, daemon=True)
    t.start()
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HarnessError(f"decode exceeded {timeout:.0f} s: process group {proc.pid} killed, no row written")
    finally:
        stop.set()
        t.join()
        if proc.poll() is None or _group_alive(proc.pid):
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                time.sleep(2)
            proc.wait()
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err), peak[0]


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def _w2():
    for p in ("tests/nanopore", "experiments/v9/consensus"):
        sys.path.insert(0, str(ROOT / p))
    W2 = _load("v10_w2_bench", "experiments/v10/w2/bench.py")
    W2.W1.VOLATILE.add("consensus_processes")
    # V10 fix: candidate-budget configuration keys that the base tree's ClusterConfig does not have (configuration, not
    # results; outcome, output and every other report value must still match)
    W2.W1.VOLATILE.update({"max_candidate_pairs_per_read", "max_candidate_pairs_ceiling", "candidate_chunk_pairs"})
    return W2


def decode_one(work: Path, label: str, container_sha256: str) -> dict:
    """In the subprocess: decode work/r.fastq with this interpreter's vnxdna."""
    W2 = _w2()
    W1 = W2.W1
    import nanofunnel as nf
    from candidates import cluster_config
    os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
    case = {"profile": W1.PROFILE, "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(W1.CANDIDATE)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=W1.WORKERS, stage_counters=True)
    row, rep = W2._decode(work, label, opts, {"container_sha256": container_sha256})
    # the largest single process (self or one waited-for child), not a sum
    me, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    ru = max(me.ru_maxrss, kids.ru_maxrss)
    cl = rep.get("clustering") or {}
    return {**row, "peak_rss_bytes": rep.get("peak_rss_bytes"), "max_rss_tree_bytes": ru * 1024,
            "consensus_processes": cl.get("consensus_processes"), "vnxdna_file": __import__("vnxdna").__file__,
            # CPU seconds of this process and its waited-for worker processes (whole decode case incl. setup)
            "cpu_user_s": round(me.ru_utime + kids.ru_utime, 2), "cpu_sys_s": round(me.ru_stime + kids.ru_stime, 2)}


def run(size: int, seeds: list[int], base: Path) -> None:
    W2 = _w2()
    W1 = W2.W1
    trees = {"base": base, "opt": ROOT}
    done = {(r["size"], r["seed"], r["config"]) for r in _read(OUT)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for seed in seeds:
        if all((size, seed, c) in done for c in trees):
            continue
        with tempfile.TemporaryDirectory(prefix="vnx-v10opt-", dir=W1._TMP) as tmp:
            w = Path(tmp)
            _, arc, truth = W2._prepare(w, size, seed)
            order = ["base", "opt"] if seed % 2 == 0 else ["opt", "base"]
            for c in order:
                if (size, seed, c) in done:
                    continue
                env = {**os.environ, "PYTHONPATH": str(trees[c] / "src")}
                env.pop("VNXDNA_CLUSTER_THREADS", None)
                load = round(os.getloadavg()[0], 2)
                p, tree_peak = _run_sampled([sys.executable, str(Path(__file__)), "one", "--work", str(w), "--label", c,
                                             "--container", arc["container_sha256"]], env)
                if p.returncode:
                    raise HarnessError(f"{c} seed {seed}: decode process exited {p.returncode} (harness error, no "
                                       f"row written):\n{p.stderr[-3000:]}")
                row = json.loads(p.stdout.strip().splitlines()[-1])
                if not row["vnxdna_file"].startswith(str(trees[c])):
                    raise HarnessError(f"{c} imported {row['vnxdna_file']}, not its tree {trees[c]}")
                rec = {"seed": seed, "size": size, "config": c, "commit": _git(trees[c], "rev-parse", "HEAD"),
                       "reads_sha256": truth["reads_sha256"], "channel": W1.CHANNEL, "coverage": W1.COVERAGE,
                       "profile": W1.PROFILE, "candidate": W1.CANDIDATE, "workers": W1.WORKERS, "label": W1.LABEL,
                       "load1_start": load, "time": time.time(), "tree_rss_peak_bytes": tree_peak, **row}
                with OUT.open("a") as f:
                    f.write(json.dumps(rec, sort_keys=True) + "\n")
                print(seed, size, c, rec["outcome"], rec["decode_seconds"], f"load {load}->{rec['load1']}", flush=True)


def summarize(rows: list[dict]) -> dict:
    import numpy as np

    from vnxdna.benchmark import stats as S
    W1 = _w2().W1
    out = {"label": W1.LABEL, "false_success": sum(r["false_success"] for r in rows), "sizes": {}}
    for size in sorted({r["size"] for r in rows}):
        by: dict = {}
        for r in rows:
            if r["size"] == size:
                by.setdefault(r["seed"], {})[r["config"]] = r
        seeds = sorted(s for s, d in by.items() if set(d) == {"base", "opt"})
        same = [s for s in seeds if len({(d["outcome"], d["output_sha256"], d["report_sha256"], d["reads_sha256"])
                                         for d in by[s].values()}) == 1]
        cell = {"seeds": seeds, "identical": len(same), "of": len(seeds),
                "outcomes": {c: [by[s][c]["outcome"] for s in seeds] for c in ("base", "opt")},
                "decode_seconds": {c: [by[s][c]["decode_seconds"] for s in seeds] for c in ("base", "opt")},
                "median_decode_seconds": {c: float(np.median([by[s][c]["decode_seconds"] for s in seeds])) if seeds else None
                                          for c in ("base", "opt")},
                "peak_rss_mib": {c: [round((by[s][c]["peak_rss_bytes"] or 0) / 2 ** 20, 1) for s in seeds] for c in ("base", "opt")},
                "max_rss_single_process_mib": {c: [round(by[s][c]["max_rss_tree_bytes"] / 2 ** 20, 1) for s in seeds]
                                               for c in ("base", "opt")},
                "tree_rss_peak_mib": {c: [round(by[s][c]["tree_rss_peak_bytes"] / 2 ** 20, 1) for s in seeds]
                                      for c in ("base", "opt")},
                "consensus_processes": [by[s]["opt"]["consensus_processes"] for s in seeds],
                "load1": [r["load1"] for s in seeds for r in by[s].values()],
                "commits": sorted({r["commit"] for s in seeds for r in by[s].values()})}
        if len(seeds) >= 2:
            cell["speedup_base_over_opt"] = S.paired_ratio([by[s]["base"]["decode_seconds"] for s in seeds],
                                                           [by[s]["opt"]["decode_seconds"] for s in seeds], seed=10_005)
        cell["load_rule"] = S.load_ok(cell["load1"])
        out["sizes"][str(size)] = cell
    return out


def _read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _seeds(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "one", "summary"])
    ap.add_argument("--size", choices=sorted(SIZES), default="1m")
    ap.add_argument("--seeds", default="93000-93002")
    ap.add_argument("--work", type=Path)
    ap.add_argument("--label")
    ap.add_argument("--container")
    a = ap.parse_args(argv)
    if a.cmd == "one":
        print(json.dumps(decode_one(a.work, a.label, a.container), sort_keys=True))
        return 0
    if a.cmd == "run":
        base = Path(os.environ.get("BASE_TREE", "")).resolve()
        if not (base / "src/vnxdna").is_dir():
            raise SystemExit("set BASE_TREE to the unchanged V10 checkout")
        run(SIZES[a.size], _seeds(a.seeds), base)
    s = summarize(_read(OUT))
    SUMMARY.write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
