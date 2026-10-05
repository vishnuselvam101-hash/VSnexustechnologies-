#!/usr/bin/env python3
"""b0-dropout report: per-cell exact counts with Wilson 95 % CIs, rate/cost columns, pairwise separation rules of PREREG.md.
Pure standard library. usage: dropout_report.py TRIALS.jsonl [...] --md OUT.md --json OUT.json
Every trial is counted, failures included. SIMULATED channel; timings/RSS MEASURED on a shared host."""
import argparse, json, statistics, sys, pathlib
from collections import defaultdict
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from aggregate import wilson


def fmt(k, n):
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({lo:.2f}-{hi:.2f})"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("trials", nargs="+"); ap.add_argument("--md"); ap.add_argument("--json")
    ap.add_argument("--title", default="b0-dropout (SIMULATED)")
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
    text = "\n".join(out) + "\n"
    if a.md: pathlib.Path(a.md).write_text(text)
    if a.json: pathlib.Path(a.json).write_text(json.dumps(js, indent=1))
    print(text)


if __name__ == "__main__":
    main()
