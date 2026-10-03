"""Archive-level Phase 4 experiments on identical read files (SIMULATED).

  P4-EXP-07  V4 / V4+minQ / V5-hard / V5-soft on the same read files: groups recovered, archive SUCCESS (output SHA-256
             verified), runtime, CPU time, peak RSS, soft trials and candidates, posterior entropy
  P4-EXP-08  coverage 1/2/3/5/10 with indel + substitution channels: does soft consensus add recovery beyond Phase 3?

Every decode runs in a fresh spawned process. Qualities come from the V4 simulator (two-level model).

usage: python experiments/v5/phase4/exp_archive.py {exp07,exp08} [--seeds 3]
"""
from __future__ import annotations

import argparse
import hashlib
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path

import p4common as p4
from p4common import pc

sys.path.insert(0, str(p4.REPO / "experiments" / "v5" / "phase3"))
import p3archive as pa  # noqa: E402

DECODERS = {
    "V4": {},
    "V4+minQ": {"min_quality": 13},
    "V5-hard": {"indel_recovery": "smart"},
    "V5-hard+minQ": {"indel_recovery": "smart", "min_quality": 13},
    "V5-soft-erasure": {"indel_recovery": "smart", "soft_decoding": "erasure"},
    "V5-soft-auto": {"indel_recovery": "smart", "soft_decoding": "auto"},
    "V5-soft-auto+minQ": {"indel_recovery": "smart", "soft_decoding": "auto", "min_quality": 13},
}


def _child(reads, workdir, opts, workers, input_sha, q):
    import resource
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import decoder as de

    def vm_hwm_mb():
        # this process image's own peak RSS. ru_maxrss is not used: on Linux it survives fork + exec, so a spawned
        # child would report the driver's high-water mark (measured: 413 MB inherited vs 14 MB own).
        for line in open("/proc/self/status"):
            if line.startswith("VmHWM:"):
                return round(int(line.split()[1]) / 1024, 1)
        return None
    out = Path(workdir) / f"o-{time.time_ns()}.vnx"
    t = time.perf_counter()
    try:
        res = de.decode_reads(reads, out, de.DecodeOptions(workers=workers, **opts), overwrite=True)
        status, rep = res.status, res.report
    except Exception as error:  # noqa: BLE001
        status, rep = "FAILURE", {"error": f"{type(error).__name__}: {str(error)[:300]}"}
    secs = time.perf_counter() - t
    sha = None
    if status == "SUCCESS":
        with tempfile.TemporaryDirectory() as x:
            ar.extract(out, x)
            sha = hashlib.sha256((Path(x) / "input.bin").read_bytes()).hexdigest()
    if out.exists():
        out.unlink()
    ru, rc = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    keep = {k: rep.get(k) for k in ("reads", "groups_decoded", "groups_failed", "stage_seconds", "error") if k in rep}
    for k in ("indel_recovery", "soft_decoding"):
        if rep.get(k):
            keep[k] = {a: b for a, b in rep[k].items() if a != "config"}
    sb = rep.get("superblock") or {}
    q.put({"status": status, "verified": sha == input_sha, "false_success": status == "SUCCESS" and sha != input_sha,
           "seconds": round(secs, 3), "cpu_seconds": round(ru.ru_utime + ru.ru_stime + rc.ru_utime + rc.ru_stime, 2),
           "peak_rss_mb": vm_hwm_mb(), "peak_rss_workers_mb": round(rc.ru_maxrss / 1024, 1) if workers > 1 else None, "groups": sb.get("groups"), "report": keep})


def decode(reads, workdir, opts, workers, input_sha):
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_child, args=(str(reads), str(workdir), opts, workers, input_sha, q))
    p.start()
    try:
        r = q.get(timeout=7200)
    finally:
        p.join(timeout=60)
    g, f = r["groups"], (r["report"].get("groups_failed") or 0)
    r["groups_recovered_fraction"] = None if not g else round(1 - f / g, 4)
    return r


EXP07 = {
    "sub 3%, informative 0.5, cov 1": {"substitution_rate": 0.03, "quality_informative": 0.5, "coverage": 1},
    "sub 4%, informative 1.0, cov 1": {"substitution_rate": 0.04, "quality_informative": 1.0, "coverage": 1},
    "sub 2.5%, uninformative, cov 1": {"substitution_rate": 0.025, "quality_informative": 0.0, "coverage": 1},
    "indel 0.25%+0.25% + sub 0.5%, informative 0.5, cov 1": {"insertion_rate": 0.0025, "deletion_rate": 0.0025,
                                                             "substitution_rate": 0.005, "quality_informative": 0.5, "coverage": 1},
    "indel 0.4%+0.4% + sub 1%, informative 0.7, cov 1": {"insertion_rate": 0.004, "deletion_rate": 0.004,
                                                         "substitution_rate": 0.01, "quality_informative": 0.7, "coverage": 1},
    "EXP-0011 (0.2% sub, 0.05%+0.05%, 2% dropout), Poisson cov 3": {"substitution_rate": 0.002, "insertion_rate": 0.0005,
                                                                    "deletion_rate": 0.0005, "dropout_rate": 0.02,
                                                                    "coverage": 3, "coverage_model": "poisson"},
}
EXP08_CHANNELS = {
    "indel 0.5%+0.5% + sub 1%, informative 0.5": {"insertion_rate": 0.005, "deletion_rate": 0.005, "substitution_rate": 0.01,
                                                  "quality_informative": 0.5},
    "indel 0.25%+0.25% + sub 4%, informative 0.5": {"insertion_rate": 0.0025, "deletion_rate": 0.0025, "substitution_rate": 0.04,
                                                    "quality_informative": 0.5},
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["exp07", "exp08"])
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--size", type=int, default=None)
    a = ap.parse_args()
    t_all = time.perf_counter()
    rows = []
    if a.which == "exp07":
        size, decoders, workers, name = a.size or 131072, list(DECODERS), 1, "P4-EXP-07-identical-reads"
        cells = [(n, c) for n, c in EXP07.items()]
    else:
        size, decoders, workers, name = a.size or 32768, ["V4", "V5-hard", "V5-soft-auto"], 8, "P4-EXP-08-coverage"
        cells = [(f"{n}, cov {cov}", {**c, "coverage": cov, "coverage_model": "fixed"})
                 for n, c in EXP08_CHANNELS.items() for cov in (1, 2, 3, 5, 10)]
    with tempfile.TemporaryDirectory(prefix=f"p4{a.which}-") as tmp:
        work = Path(tmp)
        info = pa.build(work, size, 4401 if a.which == "exp07" else 4801)
        for cname, chan in cells:
            for k in range(a.seeds):
                c = {**chan, "seed": 44000 + 10 * k}
                reads = pa.simulate(work, f"r{len(rows)}", c)
                row = {"channel_name": cname, "channel": c, "seed_index": k,
                       "reads_sha256": hashlib.sha256(reads.read_bytes()).hexdigest()}
                for d in decoders:
                    row[d] = decode(reads, work, DECODERS[d], workers, info["input_sha256"])
                reads.unlink()
                rows.append(row)
                print(f"{cname:62s} s{k} " + "  ".join(
                    f"{d} {'OK' if row[d]['verified'] else row[d]['status'][:4]} {row[d]['groups_recovered_fraction']} {row[d]['seconds']}s"
                    for d in decoders) + ("  FALSE SUCCESS" if any(row[d]["false_success"] for d in decoders) else ""), flush=True)
    summary = {}
    for cname, _ in cells:
        rs = [r for r in rows if r["channel_name"] == cname]
        summary[cname] = {d: {"verified_success": sum(r[d]["verified"] for r in rs), "trials": len(rs),
                              "false_success": sum(r[d]["false_success"] for r in rs),
                              "groups_recovered_fraction": [r[d]["groups_recovered_fraction"] for r in rs],
                              "seconds": [r[d]["seconds"] for r in rs], "cpu_seconds": [r[d]["cpu_seconds"] for r in rs],
                              "peak_rss_mb": [r[d]["peak_rss_mb"] for r in rs],
                              "peak_rss_workers_mb": [r[d].get("peak_rss_workers_mb") for r in rs],
                              "reads_soft_path": [(r[d]["report"].get("reads") or {}).get("soft") for r in rs],
                              "consensus_recovered_soft": [(r[d]["report"].get("reads") or {}).get("consensus_recovered_soft")
                                                           for r in rs],
                              "consensus_recovered_smart": [(r[d]["report"].get("reads") or {}).get("consensus_recovered_smart")
                                                            for r in rs],
                              "soft": [r[d]["report"].get("soft_decoding") for r in rs]}
                          for d in decoders}
    cfg = {"input": {"size": size, "pattern": "random"}, "decoders": {d: DECODERS[d] for d in decoders}, "workers": workers,
           "seeds": a.seeds, "channel_seed_rule": "44000 + 10·seed_index", "cells": dict(cells)}
    pc.write_result(p4.HERE / name, name, cfg, {"summary": summary, "rows": rows, "wall_seconds": round(time.perf_counter() - t_all, 1)})


if __name__ == "__main__":
    main()
