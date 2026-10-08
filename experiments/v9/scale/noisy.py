"""V9 Phase 7 noisy decodes by archive size (SIMULATED; docs/V9_PREREGISTRATION.md §9).

Archive sizes 20 KB, 1 MB, 10 MB, 100 MB and 1 GiB; D13-F1 stress channel at mean coverage 10 (negative binomial,
dispersion 4); profile v4-balanced; the V9 production decoder (``--candidate``) with 4 workers; seeds from 93000.

A size is decoded only if its projected single-decode time on this host is <= 2 h. The projection is linear in the
strand count, from the slowest measured seconds-per-strand at 20 KB and 1 MB. Larger sizes are reported as the analytic
row model with the measured per-strand loss q, labelled ANALYTIC EXTRAPOLATION (SIMULATED), never as decoded.

Per decode: per-strand success (verified data symbols / data symbols, from the decoder's own counters), per-row success
(rows decoded / rows) and whole-archive success (EXACT), kept separate.

    PYTHONPATH=src python experiments/v9/scale/noisy.py run --sizes 20000,1048576 --seeds 93000-93029 --candidate v8
    PYTHONPATH=src python experiments/v9/scale/noisy.py project
"""
from __future__ import annotations

import argparse
import dataclasses
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
OUT = HERE / "results" / "noisy.jsonl"
PROJ = HERE / "results" / "noisy-projection.json"
CHANNEL, COVERAGE, PROFILE, WORKERS = "d13-f1", 10, "v4-balanced", 4
SIZES = (20_000, 1 << 20, 10 << 20, 100 << 20, 1 << 30)
LIMIT_S = 7200.0
LABEL = "SIMULATED: reads from a software channel model; no DNA was synthesised, stored or sequenced"


def _load(name: str, rel: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _commit() -> str:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def run_one(size: int, seed: int, candidate: str) -> dict:
    import nanofunnel as nf
    from candidates import cluster_config
    from vnxdna.benchmark.outcome import classify_outcome, decode_claim
    from vnxdna.pipeline.decode import decode_reads
    from vnxdna.simulation import engine
    MX = _load("v8_matrix_run", "experiments/v8/matrix/run.py")
    case = {"profile": PROFILE, "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(candidate)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=WORKERS)
    with tempfile.TemporaryDirectory(prefix="vnx-v9noisy-", dir="/root/vnx-dna-lab/tmp") as tmp:
        w = Path(tmp)
        t0 = time.perf_counter()
        arc = nf.build_archive(w / "a", size, seed, PROFILE)
        truth = engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq", MX.case_model(CHANNEL, COVERAGE), seed)
        prep = time.perf_counter() - t0
        t = time.perf_counter()
        claim, res, err = decode_claim(lambda: decode_reads(w / "r.fastq", w / "o.vnx", opts), w / "o.vnx")
        secs = time.perf_counter() - t
        oc = classify_outcome(claim, {"container": arc["container_sha256"]})
        rep = res.report if res is not None else dict(getattr(err, "details", {}) or {})
    sc = rep.get("stage_counters") or {}
    oe = (sc.get("stages") or {}).get("outer_ecc", {})
    from vnxdna.dnaenc.layout import KIND_DATA
    data_syms = sum(k == KIND_DATA for k, *_ in arc["keys"]) or 1
    verified = oe.get("symbols_verified_pass1", 0) + oe.get("symbols_from_consensus", 0) + oe.get("symbols_from_cluster", 0)
    rows = oe.get("rows_attempted", 0)
    return {"size": size, "seed": seed, "candidate": candidate, "channel": CHANNEL, "coverage": COVERAGE, "profile": PROFILE,
            "workers": WORKERS, "strands": len(arc["keys"]), "reads_sha256": truth["reads_sha256"],
            "container_sha256": arc["container_sha256"], "outcome": oc["outcome"], "false_success": oc["outcome"] == "FALSE_SUCCESS",
            "per_strand_success": round(min(1.0, verified / data_syms), 6), "rows": rows,
            "per_row_success": round(oe.get("rows_decoded", 0) / rows, 6) if rows else None,
            "decode_seconds": round(secs, 2), "prepare_seconds": round(prep, 2), "peak_rss_bytes": rep.get("peak_rss_bytes"),
            "label": LABEL, "load1": round(os.getloadavg()[0], 2), "commit": _commit(), "time": time.time()}


def project(rows: list) -> dict:
    """Projected single-decode seconds per size from the measured 20 KB and 1 MB decodes (linear in strands)."""
    base = [r for r in rows if r["size"] in (SIZES[0], SIZES[1])]
    if not base:
        raise SystemExit("measure 20 KB and 1 MB first")
    rate = max(r["decode_seconds"] / r["strands"] for r in base)
    per_byte_strands = max(r["strands"] / r["size"] for r in base)
    out = {"seconds_per_strand": rate, "limit_seconds": LIMIT_S, "sizes": {}}
    for s in SIZES:
        proj = rate * per_byte_strands * s
        out["sizes"][str(s)] = {"projected_seconds": round(proj, 1), "decode": proj <= LIMIT_S}
    return out


def extrapolate(rows: list, sizes_left: list) -> dict:
    """ANALYTIC EXTRAPOLATION (SIMULATED): per-row decode probability from the measured per-strand loss q at the largest
    decoded size, applied to the row count of each larger size (rows scale with the strand count)."""
    from math import comb
    from vnxdna.dnaenc.layout import PROFILES
    lay, _, M = PROFILES[PROFILE]
    done = [r for r in rows if r["per_row_success"] is not None]
    big = max(r["size"] for r in done)
    ref = [r for r in done if r["size"] == big]
    q = 1 - sum(r["per_strand_success"] for r in ref) / len(ref)
    rows_ref = sum(r["rows"] for r in ref) / len(ref)
    K = round(sum(r["strands"] for r in ref) / len(ref) / max(rows_ref, 1)) - M
    p_row = sum(comb(K + M, k) * q ** k * (1 - q) ** (K + M - k) for k in range(M + 1))
    out = {"label": "ANALYTIC EXTRAPOLATION (SIMULATED)", "q_measured_at_size": big, "q": q, "row_strands": K + M, "M": M,
           "p_row": p_row, "sizes": {}}
    for s in sizes_left:
        n_rows = rows_ref * s / big
        out["sizes"][str(s)] = {"rows": round(n_rows), "p_archive": math.exp(n_rows * math.log(max(p_row, 1e-300)))}
    return out


def _seeds(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["run", "project"])
    ap.add_argument("--sizes", default="20000,1048576")
    ap.add_argument("--seeds", default="93000-93029")
    ap.add_argument("--candidate", default="v8")
    ap.add_argument("--out", type=Path, default=OUT, help="rows file (default: the committed results file)")
    ap.add_argument("--projection", type=Path, default=PROJ, help="projection file (default: the committed one)")
    a = ap.parse_args(argv)
    out, proj = a.out, a.projection
    rows = [json.loads(x) for x in out.read_text().splitlines()] if out.exists() else []
    if a.cmd == "run":
        sizes = [int(x) for x in a.sizes.split(",")]
        if any(s not in SIZES for s in sizes):
            raise SystemExit(f"sizes must be among {SIZES}")
        if any(s > SIZES[1] for s in sizes):
            pj = project(rows)["sizes"]
            bad = [s for s in sizes if s > SIZES[1] and not pj[str(s)]["decode"]]
            if bad:
                raise SystemExit(f"projected single-decode time above 2 h for {bad}: analytic extrapolation only")
        done = {(r["size"], r["seed"], r["candidate"]) for r in rows}
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a") as fh:
            for s in sizes:
                for seed in _seeds(a.seeds):
                    if (s, seed, a.candidate) in done:
                        continue
                    r = run_one(s, seed, a.candidate)
                    fh.write(json.dumps(r, sort_keys=True) + "\n")
                    fh.flush()
                    rows.append(r)
                    print(f"{s} s{seed}: {r['outcome']} strand={r['per_strand_success']} row={r['per_row_success']} {r['decode_seconds']}s",
                          flush=True)
    pj = project(rows)
    decoded = sorted({r["size"] for r in rows})
    left = [s for s in SIZES if s not in decoded]
    pj["extrapolation"] = extrapolate(rows, left) if left else None
    pj["commit"] = _commit()
    proj.write_text(json.dumps(pj, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in pj.items() if k != "extrapolation"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
