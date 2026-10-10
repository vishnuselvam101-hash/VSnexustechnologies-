"""V10 W1 acceptance: decode workers -> native cluster kernel threads (SIMULATED; docs/V10_ENGINEERING_PLAN.md §3).

Per benchmark seed (V9 manifest range 94000-94009): one 20 KB archive, D13-F1 stress channel at coverage 10, profile
v4-balanced, decoder E with 4 workers -- the V9 noisy setting. The same reads are decoded three times:

    v9    VNXDNA_CLUSTER_THREADS=1   the V9 behaviour (the kernels ignored ``workers``)
    t2    VNXDNA_CLUSTER_THREADS=2
    w1    variable unset             V10 W1: the kernels take the decoder's 4 workers

The run order rotates per seed. Acceptance: (a) the container bytes, outcome and report (minus timings and the thread
provenance) are identical for all three; (b) the paired speed-up v9/w1 with a 95 % percentile-bootstrap CI over seeds
(geometric mean of per-seed ratios, 10,000 resamples, fixed RNG seed).

    PYTHONPATH=src python experiments/v10/w1/bench.py run
    PYTHONPATH=src python experiments/v10/w1/bench.py summary
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for p in ("tests/nanopore", "experiments/v9/consensus"):
    sys.path.insert(0, str(ROOT / p))
_TMP = os.environ.get("VNX_TMPDIR") or ("/root/vnx-dna-lab/tmp" if os.path.isdir("/root/vnx-dna-lab/tmp") else None)
OUT = HERE / "results" / "w1.jsonl"
SUMMARY = HERE / "results" / "summary.json"
CHANNEL, COVERAGE, PROFILE, WORKERS, SIZE, CANDIDATE = "d13-f1", 10, "v4-balanced", 4, 20_000, "E"
SEEDS = range(94000, 94010)
CONFIGS = {"v9": "1", "t2": "2", "w1": None}
VOLATILE = {"seconds", "stage_seconds", "peak_rss_bytes", "elapsed", "elapsed_seconds", "wall_seconds",
            "native_backends", "native_threads"}
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _commit() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_seed(seed: int) -> list[dict]:
    import nanofunnel as nf
    from candidates import cluster_config
    from vnxdna.benchmark.outcome import classify_outcome, decode_claim
    from vnxdna.native import cluster as nc
    from vnxdna.pipeline.decode import decode_reads
    from vnxdna.simulation import engine
    MX = _load("v8_matrix_run", "experiments/v8/matrix/run.py")
    if nc.resolve_backend() != "native":
        raise SystemExit("native cluster kernels not active; build them first (python -m vnxdna.native.cluster build)")
    case = {"profile": PROFILE, "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(CANDIDATE)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=WORKERS, stage_counters=True)
    names = list(CONFIGS)
    k = seed % len(names)
    order = names[k:] + names[:k]
    rows = []
    with tempfile.TemporaryDirectory(prefix="vnx-v10w1-", dir=_TMP) as tmp:
        w = Path(tmp)
        arc = nf.build_archive(w / "a", SIZE, seed, PROFILE)
        truth = engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq", MX.case_model(CHANNEL, COVERAGE),
                                                seed)
        for name in order:
            env = CONFIGS[name]
            if env is None:
                os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
            else:
                os.environ["VNXDNA_CLUSTER_THREADS"] = env
            out = w / f"o-{name}.vnx"
            t = time.perf_counter()
            claim, res, err = decode_claim(lambda: decode_reads(w / "r.fastq", out, opts), out)
            secs = time.perf_counter() - t
            oc = classify_outcome(claim, {"container": arc["container_sha256"]})
            rep = res.report if res is not None else dict(getattr(err, "details", {}) or {})
            data = out.read_bytes() if out.exists() else b""
            nb = rep.get("native_backends") or {}
            backends = {k: (v.get("backend") if isinstance(v, dict) else v) for k, v in sorted(nb.items())}
            if not backends or any(v in (None, "reference") for v in backends.values()):
                raise SystemExit(f"a native kernel is not active ({backends}); run tools/vnx-lab native first")
            rows.append({"seed": seed, "config": name, "env_threads": env, "backends": backends,
                         "native_threads": (rep.get("clustering") or {}).get("native_threads"),
                         "outcome": oc["outcome"], "false_success": oc["outcome"] == "FALSE_SUCCESS",
                         "output_sha256": hashlib.sha256(data).hexdigest() if data else None,
                         "report_sha256": hashlib.sha256(json.dumps(_strip(rep), sort_keys=True, default=str)
                                                         .encode()).hexdigest(),
                         "decode_seconds": round(secs, 3), "load1": round(os.getloadavg()[0], 2),
                         "reads_sha256": truth["reads_sha256"], "size": SIZE, "channel": CHANNEL,
                         "coverage": COVERAGE, "profile": PROFILE, "candidate": CANDIDATE, "workers": WORKERS,
                         "label": LABEL, "commit": _commit(), "time": time.time()})
    os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
    return rows


def summarize(rows: list[dict]) -> dict:
    import numpy as np
    by = {}
    for r in rows:
        by.setdefault(r["seed"], {})[r["config"]] = r
    seeds = sorted(s for s, d in by.items() if set(d) == set(CONFIGS))
    identical = [s for s in seeds if len({(d["outcome"], d["output_sha256"], d["report_sha256"])
                                          for d in by[s].values()}) == 1]
    logr = np.array([math.log(by[s]["v9"]["decode_seconds"] / by[s]["w1"]["decode_seconds"]) for s in seeds])
    rng = np.random.default_rng(10_001)
    boot = np.exp(rng.choice(logr, size=(10_000, len(logr)), replace=True).mean(axis=1)) if len(logr) else np.zeros(0)
    med = lambda c: float(np.median([by[s][c]["decode_seconds"] for s in seeds])) if seeds else None  # noqa: E731
    return {"label": LABEL, "seeds": seeds, "identical_all_configs": len(identical), "of": len(seeds),
            "outcomes": {c: sorted({by[s][c]["outcome"] for s in seeds}) for c in CONFIGS},
            "false_success": sum(r["false_success"] for r in rows),
            "native_threads": {c: sorted({by[s][c]["native_threads"] for s in seeds}) for c in CONFIGS},
            "median_decode_seconds": {c: med(c) for c in CONFIGS},
            "speedup_v9_over_w1": {"geomean": round(float(np.exp(logr.mean())), 3) if len(logr) else None,
                                   "ci95": [round(float(np.percentile(boot, 2.5)), 3),
                                            round(float(np.percentile(boot, 97.5)), 3)] if len(boot) else None,
                                   "method": "paired per-seed ratio, geometric mean, percentile bootstrap 10000, "
                                             "rng 10001"},
            "load1_range": [min(r["load1"] for r in rows), max(r["load1"] for r in rows)] if rows else None,
            "commits": sorted({r["commit"] for r in rows})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if a.cmd == "run":
        done = {json.loads(l)["seed"] for l in a.out.read_text().splitlines()} if a.out.exists() else set()  # noqa: E741
        for seed in SEEDS:
            if seed in done:
                continue
            rows = run_seed(seed)
            with a.out.open("a") as f:
                for r in rows:
                    f.write(json.dumps(r, sort_keys=True) + "\n")
            print(seed, [(r["config"], r["outcome"], r["decode_seconds"]) for r in rows], flush=True)
    rows = [json.loads(l) for l in a.out.read_text().splitlines()] if a.out.exists() else []  # noqa: E741
    s = summarize(rows)
    SUMMARY.write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
    print(json.dumps(s, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
