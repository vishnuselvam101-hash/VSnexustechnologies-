"""V10 W2 acceptance: windowed polish trial costs, slice shift, sorted group sums (SIMULATED; docs/V10_ENGINEERING_PLAN.md).

``run``: per benchmark seed (V9 manifest range 94000-94009) one 20 KB archive, D13-F1 stress channel at coverage 10,
profile v4-balanced, decoder E with 4 workers (kernel threads = workers, V10 W1). The same reads are decoded twice, in
an order that alternates per seed:

    w1    the V9 polish code paths (consensus.trial_costs_reference, polish.shift_reference, np.add.at), in-process
    w2    V10 W2

Acceptance: identical outcome, container and report (minus timings and backend provenance) on every seed; paired
speed-up w1/w2 with a 95 % percentile-bootstrap CI (geometric mean of per-seed ratios, 10,000 resamples, fixed RNG).

``big``: 1 MiB archives on the V9 noisy seeds (93000-), W2 only. Outcome, per-strand and per-row success must equal the
committed V9 rows for the same seeds (experiments/v9/scale/results/noisy.jsonl); decode time is reported against the
plan's 300 s target.

    PYTHONPATH=src python experiments/v10/w2/bench.py run
    PYTHONPATH=src python experiments/v10/w2/bench.py big --seeds 93000-93002
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "experiments/v10/w1"))
import bench as W1                                         # noqa: E402  (shared settings, strip, commit, loader)

OUT = HERE / "results" / "w2.jsonl"
BIG = HERE / "results" / "big.jsonl"
SUMMARY = HERE / "results" / "summary.json"
V9_NOISY = ROOT / "experiments/v9/scale/results/noisy.jsonl"
SEEDS = range(94000, 94010)
CONFIGS = ("w1", "w2")


class _v9_paths:
    """The V9 polish code paths inside the block (module attributes, restored on exit)."""

    def __enter__(self):
        import numpy as np

        from vnxdna.recovery.cluster import consensus as C
        from vnxdna.recovery.cluster import polish as P
        self.saved = (C._trial_costs_native, P.shift, C._add_sorted)
        C._trial_costs_native = lambda *a: None
        P.shift = P.shift_reference
        C._add_sorted = lambda acc, idx, vals: np.add.at(acc, idx, vals)
        return self

    def __exit__(self, *exc):
        from vnxdna.recovery.cluster import consensus as C
        from vnxdna.recovery.cluster import polish as P
        C._trial_costs_native, P.shift, C._add_sorted = self.saved
        return False


def _decode(w: Path, name: str, opts, arc: dict) -> tuple[dict, dict]:
    from vnxdna.benchmark.outcome import classify_outcome, decode_claim
    from vnxdna.pipeline.decode import decode_reads
    out = w / f"o-{name}.vnx"
    t = time.perf_counter()
    claim, res, err = decode_claim(lambda: decode_reads(w / "r.fastq", out, opts), out)
    secs = time.perf_counter() - t
    oc = classify_outcome(claim, {"container": arc["container_sha256"]})
    rep = res.report if res is not None else dict(getattr(err, "details", {}) or {})
    nb = rep.get("native_backends") or {}
    backends = {k: (v.get("backend") if isinstance(v, dict) else v) for k, v in sorted(nb.items())}
    if not backends or any(v in (None, "reference") for v in backends.values()):
        raise SystemExit(f"a native kernel is not active ({backends}); run tools/vnx-lab native first")
    data = out.read_bytes() if out.exists() else b""
    return {"outcome": oc["outcome"], "false_success": oc["outcome"] == "FALSE_SUCCESS",
            "output_sha256": hashlib.sha256(data).hexdigest() if data else None,
            "report_sha256": hashlib.sha256(json.dumps(W1._strip(rep), sort_keys=True, default=str).encode()).hexdigest(),
            "decode_seconds": round(secs, 3), "load1": round(os.getloadavg()[0], 2), "backends": backends,
            "native_threads": (rep.get("clustering") or {}).get("native_threads")}, rep


def _prepare(w: Path, size: int, seed: int):
    import nanofunnel as nf
    from candidates import cluster_config
    from vnxdna.simulation import engine
    MX = W1._load("v8_matrix_run", "experiments/v8/matrix/run.py")
    case = {"profile": W1.PROFILE, "decoder": {"read_clustering": "fallback",
                                               "cluster_config": cluster_config(W1.CANDIDATE)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=W1.WORKERS, stage_counters=True)
    arc = nf.build_archive(w / "a", size, seed, W1.PROFILE)
    truth = engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq", MX.case_model(W1.CHANNEL, W1.COVERAGE),
                                            seed)
    return opts, arc, truth


def _base(seed: int, size: int, truth: dict) -> dict:
    return {"seed": seed, "size": size, "reads_sha256": truth["reads_sha256"], "channel": W1.CHANNEL,
            "coverage": W1.COVERAGE, "profile": W1.PROFILE, "candidate": W1.CANDIDATE, "workers": W1.WORKERS,
            "label": W1.LABEL, "commit": W1._commit(), "time": time.time()}


def run_seed(seed: int) -> list[dict]:
    os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
    order = CONFIGS if seed % 2 == 0 else CONFIGS[::-1]
    rows = []
    with tempfile.TemporaryDirectory(prefix="vnx-v10w2-", dir=W1._TMP) as tmp:
        w = Path(tmp)
        opts, arc, truth = _prepare(w, W1.SIZE, seed)
        for name in order:
            if name == "w1":
                with _v9_paths():
                    row, _ = _decode(w, name, opts, arc)
            else:
                row, _ = _decode(w, name, opts, arc)
            rows.append({**_base(seed, W1.SIZE, truth), "config": name, **row})
    return rows


def big_seed(seed: int) -> dict:
    from vnxdna.dnaenc.layout import KIND_DATA
    os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
    size = 1 << 20
    with tempfile.TemporaryDirectory(prefix="vnx-v10w2big-", dir=W1._TMP) as tmp:
        w = Path(tmp)
        opts, arc, truth = _prepare(w, size, seed)
        row, rep = _decode(w, "w2", opts, arc)
    oe = ((rep.get("stage_counters") or {}).get("stages") or {}).get("outer_ecc", {})
    data_syms = sum(k == KIND_DATA for k, *_ in arc["keys"]) or 1
    verified = oe.get("symbols_verified_pass1", 0) + oe.get("symbols_from_consensus", 0) + oe.get("symbols_from_cluster", 0)
    nrows = oe.get("rows_attempted", 0)
    return {**_base(seed, size, truth), "config": "w2", **row, "strands": len(arc["keys"]),
            "per_strand_success": round(min(1.0, verified / data_syms), 6), "rows": nrows,
            "per_row_success": round(oe.get("rows_decoded", 0) / nrows, 6) if nrows else None}


def summarize(rows: list[dict], big: list[dict]) -> dict:
    import numpy as np
    by: dict = {}
    for r in rows:
        by.setdefault(r["seed"], {})[r["config"]] = r
    seeds = sorted(s for s, d in by.items() if set(d) == set(CONFIGS))
    identical = [s for s in seeds if len({(d["outcome"], d["output_sha256"], d["report_sha256"])
                                          for d in by[s].values()}) == 1]
    from vnxdna.benchmark import stats as S
    ratio = S.paired_ratio([by[s]["w1"]["decode_seconds"] for s in seeds], [by[s]["w2"]["decode_seconds"] for s in seeds],
                           seed=10_002)
    load = S.load_ok([r["load1"] for r in rows])
    med = lambda c: float(np.median([by[s][c]["decode_seconds"] for s in seeds])) if seeds else None  # noqa: E731
    v9 = {}
    if V9_NOISY.exists():
        for line in V9_NOISY.read_text().splitlines():
            r = json.loads(line)
            if r["size"] == 1 << 20:
                v9[r["seed"]] = r
    keys = ("outcome", "per_strand_success", "per_row_success", "reads_sha256")
    big_cmp = [{"seed": b["seed"], "decode_seconds": b["decode_seconds"], "v9_decode_seconds": v9[b["seed"]]["decode_seconds"],
                "same_as_v9": all(b[k] == v9[b["seed"]][k] for k in keys), "outcome": b["outcome"], "load1": b["load1"]}
               for b in big if b["seed"] in v9]
    return {"label": W1.LABEL, "seeds": seeds, "identical": len(identical), "of": len(seeds),
            "outcomes": {c: sorted({by[s][c]["outcome"] for s in seeds}) for c in CONFIGS},
            "false_success": sum(r["false_success"] for r in rows + big),
            "median_decode_seconds": {c: med(c) for c in CONFIGS},
            "speedup_w1_over_w2": ({k: ratio[k] for k in ("geomean", "ci95", "method")} if load["ok"]
                                   else {"refused": "host load above the comparison limit", **load}),
            "load_rule": load,
            "big_1mib": {"rows": big_cmp, "target_seconds": 300,
                         "median_decode_seconds": float(np.median([b["decode_seconds"] for b in big_cmp])) if big_cmp else None,
                         "all_same_as_v9": all(b["same_as_v9"] for b in big_cmp) if big_cmp else None},
            "load1_range": [min(r["load1"] for r in rows + big), max(r["load1"] for r in rows + big)] if rows + big else None,
            "commits": sorted({r["commit"] for r in rows + big})}


def _seeds(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "big", "summary"])
    ap.add_argument("--seeds", default=None)
    a = ap.parse_args(argv)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if a.cmd == "run":
        done = {r["seed"] for r in _read(OUT)}
        for seed in (_seeds(a.seeds) if a.seeds else SEEDS):
            if seed in done:
                continue
            rows = run_seed(seed)
            with OUT.open("a") as f:
                for r in rows:
                    f.write(json.dumps(r, sort_keys=True) + "\n")
            print(seed, [(r["config"], r["outcome"], r["decode_seconds"]) for r in rows], flush=True)
    elif a.cmd == "big":
        done = {r["seed"] for r in _read(BIG)}
        for seed in _seeds(a.seeds or "93000-93002"):
            if seed in done:
                continue
            r = big_seed(seed)
            with BIG.open("a") as f:
                f.write(json.dumps(r, sort_keys=True) + "\n")
            print(seed, r["outcome"], r["decode_seconds"], flush=True)
    s = summarize(_read(OUT), _read(BIG))
    SUMMARY.write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: s[k] for k in ("identical", "of", "outcomes", "false_success", "median_decode_seconds",
                                        "speedup_w1_over_w2", "big_1mib", "load1_range")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
