"""Tables for docs/V5_PHASE3_OPTIMIZATION.md from P3O-EXP-01 results (SIMULATED).

usage: python experiments/v5/phase3opt/summarize.py
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
R = json.loads((HERE / "P3O-EXP-01-schedule" / "results.json").read_text())["results"]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def f(x, nd=1):
    return "—" if x is None else f"{x:,.{nd}f}"


def main() -> None:
    print("| channel | cov | decoder | SUCCESS eager / deferred | same outcome | time eager → deferred (s) | speed-up | "
          "smart attempts eager → deferred | reads tried / skipped | peak RSS eager → deferred (MB, coordinator + worker) |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    tot = {}
    for key, cell in R["summary"].items():
        ch, cov = key.split(" | cov ")
        for base in ("V5-hard", "V5-soft-auto"):
            e, d = cell[f"{base} eager"], cell[f"{base} deferred"]
            te, td = mean(e["seconds"]), mean(d["seconds"])
            re = mean([a + (b or 0) for a, b in zip(e["peak_rss_mb"], e["peak_rss_workers_mb"])])
            rd = mean([a + (b or 0) for a, b in zip(d["peak_rss_mb"], d["peak_rss_workers_mb"])])
            print(f"| {ch} | {cov} | {base} | {e['verified_success']} / {d['verified_success']} of {e['trials']} | "
                  f"{'yes' if cell[f'{base} same_outcome'] else '**no**'} | {f(te, 2)} → {f(td, 2)} | ×{f(te / td, 1)} | "
                  f"{f(mean(e['smart_attempts']), 0)} → {f(mean(d['smart_attempts']), 0)} | "
                  f"{f(mean(d['reads_tried']), 0)} / {f(mean(d['reads_skipped']), 0)} | {f(re, 0)} → {f(rd, 0)} |")
            t = tot.setdefault(base, {"eager_s": 0, "deferred_s": 0, "eager_ok": 0, "deferred_ok": 0, "false": 0,
                                      "eager_cpu": 0, "deferred_cpu": 0, "differ": 0, "decodes": 0})
            t["eager_s"] += sum(e["seconds"])
            t["deferred_s"] += sum(d["seconds"])
            t["eager_cpu"] += sum(e["cpu_seconds"])
            t["deferred_cpu"] += sum(d["cpu_seconds"])
            t["eager_ok"] += e["verified_success"]
            t["deferred_ok"] += d["verified_success"]
            t["false"] += e["false_success"] + d["false_success"]
            t["differ"] += 0 if cell[f"{base} same_outcome"] else 1
            t["decodes"] += 2 * e["trials"]
    print()
    print("| decoder | verified SUCCESS eager / deferred | total wall s eager / deferred | verified archives per hour "
          "eager / deferred | CPU s eager / deferred | cells with different outcome | false SUCCESS |")
    print("|---|---|---|---|---|---|---|")
    for base, t in tot.items():
        print(f"| {base} | {t['eager_ok']} / {t['deferred_ok']} | {f(t['eager_s'])} / {f(t['deferred_s'])} | "
              f"{f(3600 * t['eager_ok'] / t['eager_s'])} / {f(3600 * t['deferred_ok'] / t['deferred_s'])} | "
              f"{f(t['eager_cpu'])} / {f(t['deferred_cpu'])} | {t['differ']} | {t['false']} of {t['decodes']} |")
    # stage split for the deferred decoder
    print()
    print("| channel | cov | V5-hard eager pass 1 (s) | deferred: cheap pass / deferred stage / pass 2 (s) | needed addresses | "
          "groups decodable after cheap pass | round B | unique addresses tried | addresses skipped |")
    print("|---|---|---|---|---|---|---|---|---|")
    for key, cell in R["summary"].items():
        ch, cov = key.split(" | cov ")
        e, d = cell["V5-hard eager"], cell["V5-hard deferred"]
        print(f"| {ch} | {cov} | {f(mean(e['pass1_seconds']), 2)} | {f(mean(d['pass1_seconds']), 2)} / "
              f"{f(mean(d['deferred_seconds']), 2)} / {f(mean(d['pass2_seconds']), 2)} | {f(mean(d['needed_addresses']), 0)} | "
              f"{d['groups_decodable_after_cheap_pass']} | {d['round_b']} | {f(mean(d['unique_addresses_tried']), 0)} | "
              f"{f(mean(d['addresses_skipped']), 0)} |")


if __name__ == "__main__":
    main()
