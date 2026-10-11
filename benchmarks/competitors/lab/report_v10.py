#!/usr/bin/env python3
"""Summarise the v10 competitor benchmark (lanes A, B, C, N) from the raw per-trial records of the lab driver.

Pure standard library, reads JSON only; every trial is counted, none is filtered. Writes
  results/v10/summary.json   per-point and pooled statistics, failure behaviour, v6 -> v10 comparison, provenance
  results/v10/tables.md      the markdown tables used in docs/COMPETITOR_BENCHMARK_V10.md
usage: python benchmarks/competitors/lab/report_v10.py [RESULTS_DIR]  (default: results/v10 next to this file)

Outcome of one trial (exactly one of):
  exact            output SHA-256 equals input SHA-256
  exact_padded     output starts with the input and carries extra trailing bytes (harness counts it as success)
  false_success    every step exited 0 and an output file exists, but its content differs (silent corruption)
  timeout          a step ran into the per-step limit (no output)
  explicit_failure a step exited non-zero or the codec/wrapper wrote no output (the failure is visible to the caller)
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from aggregate import wilson  # noqa: E402

LANES = {"A": "trials_a.jsonl", "B": "trials_b.jsonl", "C": "trials_c.jsonl", "N": "trials_n.jsonl"}
V6 = {"A": "aggregate_a.json", "B": "aggregate_b.json", "C": "aggregate_c.json"}
FAMILY = [("vnx-", "VNX-DNA"), ("dna-rs", "DNA-RS"), ("dna-fountain", "DNA Fountain"), ("dna-aeon", "DNA-Aeon"),
          ("hedges", "HEDGES"), ("yyc", "YYC")]


def family(pid: str) -> str:
    return next(name for pre, name in FAMILY if pid.startswith(pre))


def outcome(t: dict) -> str:
    if t["exact_sha256"]:
        return "exact"
    if t.get("exact_with_trailing_bytes"):
        return "exact_padded"
    if t["false_success"]:
        return "false_success"
    if t.get("timeout_suspected"):
        return "timeout"
    return "explicit_failure"


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def step(t, name, key):
    return (t["steps"].get(name) or {}).get(key)


def summarise(ts: list[dict]) -> dict:
    n = len(ts)
    oc = defaultdict(int)
    for t in ts:
        oc[outcome(t)] += 1
    lo, hi = wilson(oc["exact"], n)
    ss = [t["strand_stats"] for t in ts if "strand_stats" in t]
    loads = [t["loadavg"][0] for t in ts] + [t["loadavg_trial_start"][0] for t in ts if t.get("loadavg_trial_start")]
    return {"trials": n, "exact": oc["exact"], "wilson95": [round(lo, 3), round(hi, 3)],
            "exact_padded": oc["exact_padded"], "false_success": oc["false_success"], "timeout": oc["timeout"],
            "explicit_failure": oc["explicit_failure"],
            "recovery_fraction_mean": round(statistics.mean(t["recovery_fraction_positional"] for t in ts), 4),
            "bits_per_nt": round(ss[0]["bits_per_nt"], 4) if ss else None,
            "nt_per_byte": round(ss[0]["nt_per_byte"], 3) if ss else None,
            "strand_nt": round(ss[0]["mean_len"], 1) if ss else None,
            "n_strands": ss[0]["n_strands"] if ss else None,
            "gc_outside_40_60": round(statistics.mean(s["frac_strands_gc_outside_40_60"] for s in ss), 4) if ss else None,
            "homopolymer_max": max(s["homopolymer_max"] for s in ss) if ss else None,
            "encode_s_median": med([step(t, "encoding", "duration") for t in ts]),
            "decode_s_median": med([step(t, "decoding", "duration") for t in ts]),
            "decode_s_max": max([step(t, "decoding", "duration") or 0 for t in ts]),
            "encode_rss_mib_max": max([step(t, "encoding", "peak_rss_mib") or 0 for t in ts]),
            "decode_rss_mib_max": max([step(t, "decoding", "peak_rss_mib") or 0 for t in ts]),
            "load1_median": round(statistics.median(loads), 2), "load1_max": round(max(loads), 2)}


def channel_key(t: dict):
    return (t["error_rate"], t["coverage"], t["dropout"])


def lane_rows(trials: list[dict]) -> tuple[list[dict], list[dict]]:
    per_point, pooled = defaultdict(list), defaultdict(list)
    for t in trials:
        per_point[(t["participant"],) + channel_key(t)].append(t)
        pooled[(t["participant"], t["error_rate"], t["dropout"])].append(t)
    pts = [{"participant": k[0], "family": family(k[0]), "error_rate": k[1], "coverage": k[2], "dropout": k[3],
            "seeds": sorted(t["seed"] for t in v), **summarise(v)} for k, v in sorted(per_point.items(), key=str)]
    pool = [{"participant": k[0], "family": family(k[0]), "error_rate": k[1], "dropout": k[2],
             "coverages": sorted({t["coverage"] for t in v}), **summarise(v)} for k, v in sorted(pooled.items(), key=str)]
    return pts, pool


def v6_compare(lane: str, pool: list[dict], b0: Path) -> list[dict]:
    f = b0 / V6.get(lane, "-")
    if lane not in V6 or not f.exists():
        return []
    old = defaultdict(lambda: {"trials": 0, "exact": 0, "false_success": 0})
    for r in json.loads(f.read_text())["rows"]:
        o = old[(r["participant"], r["error_rate"], r["dropout"])]
        o["trials"] += r["trials"]
        o["exact"] += r["exact"]
        o["false_success"] += r["false_success"]
        o["bits_per_nt"] = r["bits_per_nt"]
    out = []
    for r in pool:
        k = (r["participant"], r["error_rate"], r["dropout"])
        if k in old:
            o = old[k]
            out.append({"participant": k[0], "error_rate": k[1], "dropout": k[2], "v6_exact": o["exact"],
                        "v6_trials": o["trials"], "v6_false_success": o["false_success"],
                        "v6_bits_per_nt": round(o["bits_per_nt"], 4) if o.get("bits_per_nt") else None,
                        "v10_exact": r["exact"], "v10_trials": r["trials"], "v10_false_success": r["false_success"],
                        "v10_bits_per_nt": r["bits_per_nt"]})
    return out


def pct(x):
    return x if isinstance(x, str) else f"{x * 100:g} %"


def fnum(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"


def md_pool(lane: str, rows: list[dict]) -> str:
    out = ["| codec / profile | error | dropout | cov | exact / n (Wilson 95 %) | + padded | false success | explicit fail | timeout "
           "| bits/nt | strand nt | dec s median [max] | dec RSS MiB max |", "|" + "---|" * 13]
    for r in rows:
        out.append(f"| {r['participant']} | {pct(r['error_rate'])} | {pct(r['dropout'])} | {','.join(map(str, r['coverages']))} "
                   f"| {r['exact']}/{r['trials']} ({r['wilson95'][0]:.2f}-{r['wilson95'][1]:.2f}) | {r['exact_padded']} "
                   f"| **{r['false_success']}** | {r['explicit_failure']} | {r['timeout']} | {fnum(r['bits_per_nt'], 3)} "
                   f"| {fnum(r['strand_nt'], 0)} | {fnum(r['decode_s_median'])} [{fnum(r['decode_s_max'], 0)}] "
                   f"| {r['decode_rss_mib_max']:.0f} |")
    return "\n".join(out)


def md_family(fam: dict) -> str:
    out = ["| codec family | trials | exact | + padded | false success | explicit fail | timeout |", "|" + "---|" * 7]
    for k, v in fam.items():
        out.append(f"| {k} | {v['trials']} | {v['exact']} | {v['exact_padded']} | **{v['false_success']}** "
                   f"| {v['explicit_failure']} | {v['timeout']} |")
    return "\n".join(out)


def md_v6(rows: list[dict]) -> str:
    out = ["| participant | error | dropout | v6 exact | v10 exact | v6 false success | v10 false success | v6 bits/nt | v10 bits/nt |",
           "|" + "---|" * 9]
    for r in rows:
        out.append(f"| {r['participant']} | {pct(r['error_rate'])} | {pct(r['dropout'])} | {r['v6_exact']}/{r['v6_trials']} "
                   f"| {r['v10_exact']}/{r['v10_trials']} | {r['v6_false_success']} | {r['v10_false_success']} "
                   f"| {fnum(r['v6_bits_per_nt'], 3)} | {fnum(r['v10_bits_per_nt'], 3)} |")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    res = Path(argv[0]) if argv else HERE / "results" / "v10"
    b0 = HERE / "results" / "b0"
    lanes, all_trials = {}, []
    for lane, fn in LANES.items():
        p = res / fn
        if not p.exists():
            continue
        trials = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
        all_trials += trials
        pts, pool = lane_rows(trials)
        lanes[lane] = {"trials": len(trials), "per_point": pts, "pooled": pool, "v6_vs_v10": v6_compare(lane, pool, b0)}
    fam: dict = {}
    for name in [n for _, n in FAMILY]:
        ts = [t for t in all_trials if family(t["participant"]) == name]
        if ts:
            s = summarise(ts)
            fam[name] = {k: s[k] for k in ("trials", "exact", "exact_padded", "false_success", "explicit_failure", "timeout")}
    meta = json.loads((res / "provenance.json").read_text()) if (res / "provenance.json").exists() else None
    summary = {"label": "SIMULATED (software channels); timing and memory MEASURED on a shared host",
               "trials_total": len(all_trials), "by_family": fam, "lanes": lanes, "provenance_file": "provenance.json",
               "provenance_vnx_commit": (meta or {}).get("vnx", {}).get("commit") if isinstance(meta, dict) else None}
    (res / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    md = ["<!-- generated by benchmarks/competitors/lab/report_v10.py from results/v10/trials_*.jsonl; do not edit -->",
          "", "### All lanes, by codec family (SIMULATED)", "", md_family(fam)]
    for lane, d in lanes.items():
        md += ["", f"### Lane {lane} (SIMULATED), pooled over coverages and seeds", "", md_pool(lane, d["pooled"])]
        if d["v6_vs_v10"]:
            md += ["", f"#### Lane {lane}: v6 (B0, c76009d) vs v10 on the same grid and seeds", "", md_v6(d["v6_vs_v10"])]
    (res / "tables.md").write_text("\n".join(md) + "\n")
    print(json.dumps(fam, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
