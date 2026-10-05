#!/usr/bin/env python3
"""b0-dropout report: per-cell exact counts with Wilson 95 % CIs, rate/cost columns, pairwise separation rules of PREREG.md.
Pure standard library. usage: dropout_report.py TRIALS.jsonl [...] --md OUT.md --json OUT.json
Every trial is counted, failures included. SIMULATED channel; timings/RSS MEASURED on a shared host."""
import argparse, json, statistics, sys, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from aggregate import wilson


def fmt(k, n):
    if n == 0:
        return "-"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({lo:.2f}-{hi:.2f})"


def decide(cell, part, errs, drops, base, finalists):
    """PREREG rule 3: lead profile = most exact trials among finalists (tie: shorter strand); accepted as a named profile only
    if (a) bits/nt in [0.995, 1.005], (b) false-SUCCESS 0, (c) exact count >= baseline in every cell and Wilson-separated
    above it in at least one, (d) >= 8/10 exact in every cell with dropout <= 10 %."""
    def ex(p, e, d):
        ts = cell.get((p, e, d), []); return sum(t["exact_sha256"] for t in ts), len(ts)
    def strand_len(p):
        return min(t["strand_stats"]["mean_len"] for t in part[p])
    lead = sorted(finalists, key=lambda p: (-sum(t["exact_sha256"] for t in part[p]), strand_len(p)))[0]
    bits = part[lead][0]["strand_stats"]["bits_per_nt"]
    fs = sum(t["false_success"] for t in part[lead])
    ge_all = True; sep = []; low = []
    for e in errs:
        for d in drops:
            (k, n), (kb, nb) = ex(lead, e, d), ex(base, e, d)
            if k < kb: ge_all = False
            if n and nb and wilson(k, n)[0] > wilson(kb, nb)[1]: sep.append([e, d])
            if d <= 0.10 + 1e-12 and (n == 0 or k / n < 0.8): low.append([e, d, k, n])
    crit = {"a_bits_per_nt_in_0.995_1.005": 0.995 <= bits <= 1.005, "b_false_success_0": fs == 0,
            "c_ge_baseline_every_cell": ge_all, "c_separated_above_baseline_cells": sep,
            "d_ge_8_of_10_dropout_le_10pct": not low}
    ok = crit["a_bits_per_nt_in_0.995_1.005"] and crit["b_false_success_0"] and ge_all and bool(sep) and not low
    return {"lead": lead, "lead_exact": sum(t["exact_sha256"] for t in part[lead]), "lead_trials": len(part[lead]),
            "bits_per_nt": bits, "false_success": fs, "criteria": crit, "cells_below_8_of_10": low,
            "decision": "ACCEPT" if ok else "REJECT"}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("trials", nargs="+"); ap.add_argument("--md"); ap.add_argument("--json")
    ap.add_argument("--title", default="b0-dropout (SIMULATED)")
    ap.add_argument("--baseline", help="PREREG rule 3: baseline participant (e.g. vnx-s184)")
    ap.add_argument("--finalists", help="PREREG rule 3: comma-separated VNX finalists")
    a = ap.parse_args()
    T = []
    for f in a.trials:
        T += [json.loads(l) for l in open(f) if l.strip()]
    seen = set()
    for t in T:
        key = (t["participant"], t["error_rate"], t["coverage"], t["dropout"], t["seed"])
        if key in seen:
            raise SystemExit(f"duplicate trial {key}")
        seen.add(key)
    cell = defaultdict(list); part = defaultdict(list)
    for t in T:
        cell[(t["participant"], t["error_rate"], t["dropout"])].append(t); part[t["participant"]].append(t)
    parts = sorted(part); errs = sorted({t["error_rate"] for t in T}); drops = sorted({t["dropout"] for t in T})
    out = [f"# {a.title}", "", "All channel results SIMULATED; time and RSS MEASURED on a shared host. Cells: exact (SHA-256) / n (Wilson 95 %).", ""]
    # per participant summary
    out += ["## Participants: rate and cost", "",
            "| participant | strand nt | nt/byte | bits/nt | trials | exact | false-SUCCESS | decode median s [max] | decode peak RSS MiB (max) | encode peak RSS MiB |", "|---|---|---|---|---|---|---|---|---|---|"]
    js = {"participants": {}, "cells": []}
    for p in parts:
        ts = part[p]; ss = [t["strand_stats"] for t in ts if "strand_stats" in t]
        nt = ss[0]["nt_per_byte"]; bits = ss[0]["bits_per_nt"]; ln = sorted({round(s["mean_len"]) for s in ss})
        dec = [(t["steps"].get("decoding") or {}).get("duration") for t in ts]; dec = [d for d in dec if d is not None]
        rss = [(t["steps"].get("decoding") or {}).get("peak_rss_mib") or 0 for t in ts]
        rse = [(t["steps"].get("encoding") or {}).get("peak_rss_mib") or 0 for t in ts]
        k = sum(t["exact_sha256"] for t in ts); fs = sum(t["false_success"] for t in ts)
        out.append(f"| {p} | {'/'.join(map(str, ln))} | {nt:.2f} | {bits:.4f} | {len(ts)} | {k} | {fs} | {statistics.median(dec):.1f} [{max(dec):.0f}] | {max(rss):.0f} | {max(rse):.0f} |")
        js["participants"][p] = {"strand_nt": ln, "nt_per_byte": nt, "bits_per_nt": bits, "trials": len(ts), "exact": k, "false_success": fs,
                                 "decode_median_s": statistics.median(dec), "decode_max_s": max(dec), "decode_peak_rss_mib_max": max(rss)}
    out += ["", "## Exact recovery per cell", ""]
    for e in errs:
        out += [f"### error {e*100:g} %", "", "| participant | " + " | ".join(f"dropout {d*100:g} %" for d in drops) + " | all cells |", "|---|" + "---|" * (len(drops) + 1)]
        for p in parts:
            row = []; K = N = 0
            for d in drops:
                ts = cell.get((p, e, d), [])
                k = sum(t["exact_sha256"] for t in ts); row.append(fmt(k, len(ts)) if ts else "-"); K += k; N += len(ts)
                if ts:
                    lo, hi = wilson(k, len(ts)); js["cells"].append({"participant": p, "error_rate": e, "dropout": d, "n": len(ts), "exact": k, "lo": lo, "hi": hi,
                                                                    "false_success": sum(t["false_success"] for t in ts)})
            out.append(f"| {p} | " + " | ".join(row) + f" | {fmt(K, N)} |")
        out.append("")
    out += ["## Pooled over error rates, per dropout", "", "| participant | " + " | ".join(f"dropout {d*100:g} %" for d in drops) + " | all |", "|---|" + "---|" * (len(drops) + 1)]
    pooled = {}
    for p in parts:
        row = []; K = N = 0
        for d in drops:
            ts = [t for e in errs for t in cell.get((p, e, d), [])]
            k = sum(t["exact_sha256"] for t in ts); row.append(fmt(k, len(ts)) if ts else "-"); K += k; N += len(ts); pooled[(p, d)] = (k, len(ts))
        out.append(f"| {p} | " + " | ".join(row) + f" | {fmt(K, N)} |")
    # separation vs public codecs, per cell
    refs = [p for p in parts if p.startswith("dna-")]; vn = [p for p in parts if p.startswith("vnx-")]
    out += ["", "## Per-cell separation (PREREG rule: beats = Wilson lower bound above the other's upper bound)", "",
            "| VNX profile | reference | beats (cells) | loses (cells) | no separation (cells) |", "|---|---|---|---|---|"]
    for v in vn:
        for r in refs:
            b = l = s = 0
            for e in errs:
                for d in drops:
                    tv, tr = cell.get((v, e, d), []), cell.get((r, e, d), [])
                    if not tv or not tr: continue
                    lv = wilson(sum(t["exact_sha256"] for t in tv), len(tv)); lr = wilson(sum(t["exact_sha256"] for t in tr), len(tr))
                    if lv[0] > lr[1]: b += 1
                    elif lr[0] > lv[1]: l += 1
                    else: s += 1
            out.append(f"| {v} | {r} | {b} | {l} | {s} |")
    if a.baseline and a.finalists:
        dec = decide(cell, part, errs, drops, a.baseline, a.finalists.split(","))
        js["decision_rule3"] = dec
        out += ["", "## PREREG rule 3 (computed)", "", "```", json.dumps(dec, indent=1), "```"]
    text = "\n".join(out) + "\n"
    if a.md: pathlib.Path(a.md).write_text(text)
    if a.json: pathlib.Path(a.json).write_text(json.dumps(js, indent=1))
    print(text)


if __name__ == "__main__":
    main()
