#!/usr/bin/env python3
"""Aggregate raw per-trial records (trials.jsonl written by the lab driver) into per-point statistics and a markdown table.
Pure standard library; reads JSON only. Every trial is counted, failures included.
usage: aggregate.py TRIALS.jsonl [TRIALS2.jsonl ...] --json OUT.json --md OUT.md"""
import argparse, json, math, statistics
from collections import defaultdict


def wilson(k, n, z=1.959964):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def aggregate(trials):
    groups = defaultdict(list)
    for t in trials:
        groups[(t["participant"], t["error_rate"], t["coverage"], t["dropout"])].append(t)
    rows = []
    for (pid, err, cov, drop), ts in sorted(groups.items()):
        n = len(ts)
        k = sum(1 for t in ts if t["exact_sha256"])
        lo, hi = wilson(k, n)
        ss = [t["strand_stats"] for t in ts if "strand_stats" in t]
        def step(t, name, key):
            return (t["steps"].get(name) or {}).get(key)
        rows.append({
            "participant": pid, "error_rate": err, "coverage": cov, "dropout": drop, "trials": n,
            "exact": k, "success_rate": k / n, "wilson95_lo": lo, "wilson95_hi": hi,
            "exact_with_trailing_bytes": sum(1 for t in ts if t.get("exact_with_trailing_bytes")),
            "prefix_exact_harness_criterion": sum(1 for t in ts if t.get("prefix_exact_harness_criterion")),
            "false_success": sum(1 for t in ts if t["false_success"]),
            "harness_success": sum(1 for t in ts if t.get("harness_decoding_success")),
            "harness_exception": sum(1 for t in ts if t.get("harness_error")),
            "timeout_suspected": sum(1 for t in ts if t.get("timeout_suspected")),
            "recovery_fraction_mean": statistics.mean(t["recovery_fraction_positional"] for t in ts),
            "encode_s_median": med([step(t, "encoding", "duration") for t in ts]),
            "decode_s_median": med([step(t, "decoding", "duration") for t in ts]),
            "encode_rss_mib_max": max([step(t, "encoding", "peak_rss_mib") or 0 for t in ts]),
            "decode_rss_mib_max": max([step(t, "decoding", "peak_rss_mib") or 0 for t in ts]),
            "strand_len_nt": ss[0]["mean_len"] if ss else None,
            "nt_per_byte": ss[0]["nt_per_byte"] if ss else None,
            "bits_per_nt": ss[0]["bits_per_nt"] if ss else None,
            "gc_mean": statistics.mean(s["gc_mean"] for s in ss) if ss else None,
            "frac_gc_outside_40_60": statistics.mean(s["frac_strands_gc_outside_40_60"] for s in ss) if ss else None,
            "homopolymer_max": max(s["homopolymer_max"] for s in ss) if ss else None,
            "frac_homopolymer_ge4": statistics.mean(s["frac_strands_homopolymer_ge4"] for s in ss) if ss else None,
            "load_avg_1m_max": max(t["loadavg"][0] for t in ts),
        })
    return rows


def f(x, nd=3):
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def markdown(rows, title):
    out = [f"# {title}", "", "Channel results are SIMULATED. Times and memory are MEASURED on this VPS. Wilson 95 % intervals. `exact` = output SHA-256 equals input SHA-256 (success). `(+n)` = n further trials whose output starts with the identical bytes but carries extra trailing bytes (counted by the harness as success, not counted as exact here). false-SUCCESS = exit 0 with different content.", "",
           "| participant | err | cov | n | exact (+padded) | success (95 % CI) | false-SUCCESS | recov. frac | enc s | dec s | enc/dec RSS MiB | strand nt | nt/byte | bits/nt | GC mean | GC outside 40-60 % | max homopoly | load1 max |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| {p} | {e} | {c} | {n} | {k}{pd} | {s:.2f} ({lo:.2f}-{hi:.2f}) | {fs} | {rf:.3f} | {es} | {ds} | {er:.0f}/{dr:.0f} | {sl} | {nb} | {bn} | {gc} | {go} | {hp} | {ld:.1f} |".format(
            p=r["participant"], e=f"{r['error_rate']*100:g}%", c=r["coverage"], n=r["trials"], k=r["exact"], pd=(f" (+{r['exact_with_trailing_bytes']})" if r["exact_with_trailing_bytes"] else ""), s=r["success_rate"], lo=r["wilson95_lo"], hi=r["wilson95_hi"],
            fs=r["false_success"], rf=r["recovery_fraction_mean"], es=f(r["encode_s_median"], 1), ds=f(r["decode_s_median"], 1), er=r["encode_rss_mib_max"], dr=r["decode_rss_mib_max"],
            sl=f(r["strand_len_nt"], 0), nb=f(r["nt_per_byte"], 2), bn=f(r["bits_per_nt"], 3), gc=f(r["gc_mean"], 3), go=f(r["frac_gc_outside_40_60"], 3), hp=r["homopolymer_max"], ld=r["load_avg_1m_max"]))
    out += ["", f"Total false-SUCCESS across all trials: {sum(r['false_success'] for r in rows)}."]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("trials", nargs="+"); ap.add_argument("--json"); ap.add_argument("--md"); ap.add_argument("--title", default="Lab results")
    a = ap.parse_args()
    trials = [json.loads(l) for fn in a.trials for l in open(fn) if l.strip()]
    rows = aggregate(trials)
    if a.json: json.dump({"label": "SIMULATED (channel); timing MEASURED", "trials": len(trials), "rows": rows}, open(a.json, "w"), indent=1)
    md = markdown(rows, a.title)
    if a.md: open(a.md, "w").write(md)
    else: print(md)
