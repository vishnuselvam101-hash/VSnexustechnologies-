"""P3O-EXP-01: eager vs deferred per-read recovery on identical read files (SIMULATED).

BEFORE  recovery_schedule="eager"     Phase 3/4 behaviour: smart (and soft) per-read recovery inside pass 1 for every
                                      read the V4 paths fail
AFTER   recovery_schedule="deferred"  the cheap pass first; per-read recovery only for reads that can still change a
                                      group that is not yet decodable (decoder._deferred_recovery)

Same input, channel, seed, options, worker count and machine for both; every decode runs in a fresh spawned process.
Peak RSS is the decode process's own VmHWM plus the largest worker's ru_maxrss.

usage: python experiments/v5/phase3opt/exp_schedule.py [--seeds 2] [--workers 8] [--size 32768]
"""
from __future__ import annotations

import argparse
import hashlib
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "experiments" / "v5" / "phase3"))
import p3archive as pa  # noqa: E402
import p3common as pc  # noqa: E402

NAME = "P3O-EXP-01-schedule"
CHANNELS = {
    "clean": {},
    "indel-heavy (0.5%+0.5%, sub 0.1%)": {"insertion_rate": 0.005, "deletion_rate": 0.005, "substitution_rate": 0.001,
                                          "quality_informative": 0.5},
    "mixed (0.5%+0.5%, sub 1%)": {"insertion_rate": 0.005, "deletion_rate": 0.005, "substitution_rate": 0.01,
                                  "quality_informative": 0.5},
    "mixed, substitution-heavy (0.25%+0.25%, sub 4%)": {"insertion_rate": 0.0025, "deletion_rate": 0.0025,
                                                        "substitution_rate": 0.04, "quality_informative": 0.5},
}
COVERAGES = (1, 2, 3, 5, 10)
DECODERS = {
    "V4": {},
    "V5-hard eager": {"indel_recovery": "smart", "recovery_schedule": "eager"},
    "V5-hard deferred": {"indel_recovery": "smart", "recovery_schedule": "deferred"},
    "V5-soft-auto eager": {"indel_recovery": "smart", "soft_decoding": "auto", "recovery_schedule": "eager"},
    "V5-soft-auto deferred": {"indel_recovery": "smart", "soft_decoding": "auto", "recovery_schedule": "deferred"},
}


def _child(reads, workdir, opts, workers, input_sha, q):
    import resource
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import decoder as de

    def vm_hwm_mb():
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
    keep = {k: rep.get(k) for k in ("reads", "groups_decoded", "groups_failed", "stage_seconds", "error",
                                    "recovery_schedule", "container_sha256") if k in rep}
    for k in ("indel_recovery", "soft_decoding"):
        if rep.get(k):
            keep[k] = {a: b for a, b in rep[k].items() if a != "config"}
    sb = rep.get("superblock") or {}
    q.put({"status": status, "verified": sha == input_sha, "false_success": status == "SUCCESS" and sha != input_sha,
           "output_sha256": sha, "seconds": round(secs, 3),
           "cpu_seconds": round(ru.ru_utime + ru.ru_stime + rc.ru_utime + rc.ru_stime, 2),
           "peak_rss_mb": vm_hwm_mb(), "peak_rss_workers_mb": round(rc.ru_maxrss / 1024, 1) if workers > 1 else None,
           "groups": sb.get("groups"), "report": keep})


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


def _metrics(r: dict) -> dict:
    rep = r["report"]
    st = rep.get("stage_seconds") or {}
    sch = rep.get("recovery_schedule") or {}
    ir = rep.get("indel_recovery") or {}
    rounds = sch.get("rounds") or {}
    return {"status": r["status"], "verified": r["verified"], "false_success": r["false_success"],
            "groups_recovered_fraction": r["groups_recovered_fraction"], "seconds": r["seconds"],
            "cpu_seconds": r["cpu_seconds"], "peak_rss_mb": r["peak_rss_mb"], "peak_rss_workers_mb": r["peak_rss_workers_mb"],
            "pass1_seconds": st.get("pass1_reads"), "deferred_seconds": st.get("deferred_recovery"),
            "pass2_seconds": st.get("pass2_decode"),
            "smart_attempts": ir.get("attempted", 0) if ir else None,
            "reads_recovered_per_read": {k: (rep.get("reads") or {}).get(k) for k in ("smart", "soft")},
            "reads_tried": sch.get("reads_tried"), "reads_skipped": sch.get("reads_skipped"),
            "addresses_skipped": sch.get("addresses_skipped"),
            "unique_addresses_tried": sum(v.get("unique_addresses", 0) for v in rounds.values()) if rounds else None,
            "addresses_recovered_per_read": sum(v.get("recovered_addresses", 0) for v in rounds.values()) if rounds else None,
            "round_b": sch.get("round_b"), "groups_decodable_after_cheap_pass": sch.get("groups_decodable_after_cheap_pass"),
            "needed_addresses": sch.get("needed_addresses"), "output_sha256": r["output_sha256"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--size", type=int, default=32768)
    a = ap.parse_args()
    t_all = time.perf_counter()
    rows = []
    with tempfile.TemporaryDirectory(prefix="p3o-") as tmp:
        work = Path(tmp)
        info = pa.build(work, a.size, 4801)
        for cname, chan in CHANNELS.items():
            for cov in COVERAGES:
                for k in range(a.seeds):
                    c = {"substitution_rate": 0.0, "insertion_rate": 0.0, "deletion_rate": 0.0, **chan,
                         "coverage": cov, "coverage_model": "fixed", "seed": 44000 + 10 * k}
                    reads = pa.simulate(work, f"r{len(rows)}", c)
                    row = {"channel_name": cname, "coverage": cov, "channel": c, "seed_index": k,
                           "reads_sha256": hashlib.sha256(reads.read_bytes()).hexdigest()}
                    line = f"{cname[:34]:34s} cov {cov:2d} s{k}"
                    for d, opts in DECODERS.items():
                        r = decode(reads, work, opts, a.workers, info["input_sha256"])
                        row[d] = _metrics(r)
                        line += f"  {d} {r['status'][:4]} {r['groups_recovered_fraction']} {r['seconds']}s"
                    rows.append(row)
                    print(line, flush=True)
    summary = {}
    for cname in CHANNELS:
        for cov in COVERAGES:
            rs = [r for r in rows if r["channel_name"] == cname and r["coverage"] == cov]
            cell = {}
            for d in DECODERS:
                xs = [r[d] for r in rs]
                cell[d] = {"verified_success": sum(x["verified"] for x in xs), "trials": len(xs),
                           "false_success": sum(x["false_success"] for x in xs),
                           **{m: [x[m] for x in xs] for m in xs[0] if m not in ("verified", "false_success")}}
            for base in ("V5-hard", "V5-soft-auto"):
                e, f = cell[f"{base} eager"], cell[f"{base} deferred"]
                cell[f"{base} same_outcome"] = (e["status"] == f["status"] and e["output_sha256"] == f["output_sha256"]
                                                and e["groups_recovered_fraction"] == f["groups_recovered_fraction"])
            summary[f"{cname} | cov {cov}"] = cell
    cfg = {"input": {"size": a.size, "pattern": "random", "seed": 4801}, "decoders": DECODERS, "workers": a.workers,
           "seeds": a.seeds, "channel_seed_rule": "44000 + 10·seed_index", "channels": CHANNELS, "coverages": COVERAGES,
           "coverage_model": "fixed"}
    pc.write_result(HERE / NAME, NAME, cfg, {"summary": summary, "rows": rows, "encode": info,
                                             "wall_seconds": round(time.perf_counter() - t_all, 1)})


if __name__ == "__main__":
    main()
