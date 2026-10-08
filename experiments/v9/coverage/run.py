"""V9 adaptive computational coverage (SIMULATED; docs/V9_PREREGISTRATION.md §6).

A computational termination rule over an existing read pool: it does not control sequencing hardware.

Per case (channel, profile, seed): a 20,000-byte archive, a read pool at mean coverage 15 (negative binomial,
dispersion 4; ``experiments/v8/matrix`` model), its reads in a seeded random order, consumed in batches of 0.5x mean
coverage (``batch`` = half the strand count, in reads). After each batch the decoder runs on the reads consumed so far
and reports ``outer_ecc_margins`` (the smallest (verified symbols - k) over the outer rows, at the confidence levels
``pass1`` / ``consensus`` / ``all``). The rule stops at the first batch where ``margins[level] >= m``, or when the pool
is exhausted. Fixed coverage c is the prefix of 2c batches of the same pool (a thinning of the coverage-15 pool).

* ``--mode trajectory`` (DEV only): decodes every batch; ``--tune`` then picks (level, m) by the pre-registered rule:
  0 false terminations on DEV, then the fewest mean reads, ties to the larger m, then to the more conservative level
  (pass1, consensus, all). The choice is written to FREEZE.json.
* ``--mode eval`` (EVAL seeds, frozen parameters only): decodes batches until the rule stops, then the fixed coverages
  3/5/7/10/15 and, if the stop was not EXACT, further batches until EXACT ("recoveries after continuing").

False termination: the rule stopped, the decode at the stop is not EXACT, and the full pool of the same seed is EXACT.

    PYTHONPATH=src python experiments/v9/coverage/run.py --mode trajectory --seeds 90000-90009 --out .../dev.jsonl
    PYTHONPATH=src python experiments/v9/coverage/run.py --tune --out .../dev.jsonl
    PYTHONPATH=src python experiments/v9/coverage/run.py --mode eval --seeds 91000-91099 --out .../eval.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tests" / "nanopore"))
sys.path.insert(0, str(ROOT / "experiments" / "v8" / "matrix"))
sys.path.insert(0, str(ROOT / "experiments" / "v9" / "consensus"))
FREEZE = HERE / "FREEZE.json"
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"
POOL_COVERAGE = 15
FIXED = (3, 5, 7, 10, 15)
LEVELS = ("pass1", "consensus", "all")
MS = (0, 1, 2, 4, 8)


def _commit() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def fires(margins: dict | None, level: str, m: int) -> bool:
    return margins is not None and margins[level] >= m


def _records(path: Path) -> list[bytes]:
    lines = path.read_bytes().split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if len(lines) % 4:
        raise ValueError("FASTQ is not 4-line records")
    return [b"\n".join(lines[i:i + 4]) + b"\n" for i in range(0, len(lines), 4)]


def _decode(nf, w: Path, recs: list, order: np.ndarray, n: int, sha: str, opts) -> dict:
    from vnxdna.benchmark.outcome import classify_outcome, decode_claim
    from vnxdna.pipeline.decode import decode_reads
    fq, out = w / f"p{n}.fastq", w / f"p{n}.vnx"
    fq.write_bytes(b"".join(recs[i] for i in order[:n]))
    t = time.perf_counter()
    claim, res, err = decode_claim(lambda: decode_reads(fq, out, opts), out)
    secs = time.perf_counter() - t
    oc = classify_outcome(claim, {"container": sha})
    rep = res.report if res is not None else dict(getattr(err, "details", {}) or {})
    sc = rep.get("stage_counters") or {}
    for p in (fq, out):
        if p.exists():
            p.unlink()
    return {"outcome": oc["outcome"], "margins": sc.get("outer_ecc_margins"), "terminal_stage": sc.get("terminal_stage"),
            "seconds": round(secs, 3)}


def run_case(job: tuple) -> dict:
    import nanofunnel as nf
    import run as MX
    from candidates import cluster_config
    from vnxdna.simulation import engine
    mode, channel, profile, seed, candidate, params = job
    case = {"profile": profile, "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(candidate)}}
    opts = nf.decoder_options(case)
    with tempfile.TemporaryDirectory(prefix="vnx-v9cov-") as tmp:
        w = Path(tmp)
        arc = nf.build_archive(w / "archive", MX.SIZE, seed, profile)
        truth = engine.simulate_file_with_truth(arc["strands_path"], w / "pool.fastq", MX.case_model(channel, POOL_COVERAGE), seed)
        recs = _records(w / "pool.fastq")
        order = np.random.default_rng([seed, 0x5EED]).permutation(len(recs))
        n_strands = len(arc["keys"])
        batch = max(1, round(0.5 * n_strands))
        nb = math.ceil(len(recs) / batch)
        cache: dict = {}

        def at(b: int) -> dict:
            b = min(b, nb)
            if b not in cache:
                cache[b] = {"batch": b, "reads": min(b * batch, len(recs)), "coverage_used": round(min(b * batch, len(recs)) / n_strands, 3),
                            **_decode(nf, w, recs, order, min(b * batch, len(recs)), arc["container_sha256"], opts)}
            return cache[b]

        row = {"mode": mode, "channel": channel, "profile": profile, "seed": seed, "candidate": candidate,
               "cluster_config": case["decoder"]["cluster_config"], "strands": n_strands, "pool_reads": len(recs),
               "batch_reads": batch, "batches": nb, "container_sha256": arc["container_sha256"],
               "reads_sha256": truth["reads_sha256"], "label": LABEL}
        if mode == "trajectory":
            for b in range(1, nb + 1):
                at(b)
        else:
            level, m = params["level"], params["m"]
            stop = next((b for b in range(1, nb + 1) if fires(at(b)["margins"], level, m)), nb)
            for c in FIXED:
                at(2 * c)
            full = at(nb)
            row["params"] = params
            row["stop_batch"] = stop
            row["stop"] = at(stop)
            row["fixed"] = {str(c): at(2 * c)["outcome"] for c in FIXED}
            row["full_pool"] = full["outcome"]
            row["false_termination"] = at(stop)["outcome"] != "EXACT" and full["outcome"] == "EXACT" and stop < nb
            rec = None
            if at(stop)["outcome"] != "EXACT":
                rec = next((b for b in range(stop + 1, nb + 1) if at(b)["outcome"] == "EXACT"), None)
            row["recovered_after_continuing_at"] = rec
        row["trajectory"] = [cache[b] for b in sorted(cache)]
        row["false_success"] = any(x["outcome"] == "FALSE_SUCCESS" for x in cache.values())
        row["decode_seconds_total"] = round(sum(x["seconds"] for x in cache.values()), 3)
    row.update(load1=round(os.getloadavg()[0], 2), commit=_commit(), time=time.time())
    return row


def tune(rows: list) -> dict:
    """Pre-registered selection on DEV trajectories: 0 false terminations, then fewest mean reads, ties -> larger m, then the
    more conservative level (``min`` keeps the first of equal keys; LEVELS is ordered pass1, consensus, all)."""
    table = []
    for level in LEVELS:
        for m in MS:
            ft = reads = 0
            for r in rows:
                tr = r["trajectory"]
                stop = next((x for x in tr if fires(x["margins"], level, m)), tr[-1])
                ft += stop["outcome"] != "EXACT" and tr[-1]["outcome"] == "EXACT" and stop["batch"] < tr[-1]["batch"]
                reads += stop["reads"]
            table.append({"level": level, "m": m, "false_terminations": ft, "mean_reads": reads / max(1, len(rows))})
    ok = [t for t in table if t["false_terminations"] == 0]
    best = min(ok, key=lambda t: (t["mean_reads"], -t["m"])) if ok else None
    return {"table": table, "chosen": best, "dev_cases": len(rows)}


def _seeds(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("trajectory", "eval"), default="trajectory")
    ap.add_argument("--channel", default="d13-f1")
    ap.add_argument("--profiles", default="v4-balanced,v7-lowcov")
    ap.add_argument("--seeds", default="90000-90009")
    ap.add_argument("--candidate", default="v8")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--tune", action="store_true", help="choose (level, m) from DEV trajectories in --out and write FREEZE.json")
    a = ap.parse_args(argv)
    out = Path(a.out)
    rows = [json.loads(x) for x in out.read_text().splitlines()] if out.exists() else []
    if a.tune:
        dev = [r for r in rows if r["mode"] == "trajectory" and 90000 <= r["seed"] <= 90099]
        res = tune(dev)
        FREEZE.write_text(json.dumps({**res, "dev_file": str(out.resolve().relative_to(ROOT)), "commit": _commit()}, indent=1, sort_keys=True) + "\n")
        print(json.dumps(res["chosen"]))
        return 0 if res["chosen"] else 1
    params = None
    if a.mode == "eval":
        if not FREEZE.exists():
            raise SystemExit("eval needs FREEZE.json (run --tune on DEV trajectories first)")
        params = {k: json.loads(FREEZE.read_text())["chosen"][k] for k in ("level", "m")}
        if any(not 91000 <= s <= 91199 for s in _seeds(a.seeds)):
            raise SystemExit("eval runs on EVAL seeds 91000-91199 only")
    elif any(not 90000 <= s <= 90099 for s in _seeds(a.seeds)):
        raise SystemExit("trajectories (development) run on DEV seeds 90000-90099 only")
    done = {(r["mode"], r["channel"], r["profile"], r["seed"], r["candidate"]) for r in rows}
    todo = [(a.mode, a.channel, p, s, a.candidate, params) for p in a.profiles.split(",") for s in _seeds(a.seeds)
            if (a.mode, a.channel, p, s, a.candidate) not in done]
    out.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=a.jobs) as pool, out.open("a") as fh:
        for r in pool.map(run_case, todo):
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            fh.flush()
            print(f"{r['profile']} s{r['seed']}: batches={len(r['trajectory'])} full={r['trajectory'][-1]['outcome']}"
                  + (f" stop={r['stop_batch']}:{r['stop']['outcome']}" if r["mode"] == "eval" else ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
