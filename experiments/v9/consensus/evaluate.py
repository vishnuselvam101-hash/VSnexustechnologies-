"""V9 consensus V2 selection (docs/V9_PREREGISTRATION.md §5 selection rule; SIMULATED). Reads the one EVAL evaluation
(``experiments/v9/oracle/results/eval.jsonl``: seeds 91000-91099, primary cells) and applies the pre-registered rule:

* eligibility: 0 false success, 0 false frames, deterministic, median runtime <= 2x the baseline's (same host); false
  frames and determinism come from ``eligibility.jsonl`` (see ``eligibility.py``) when present, else "NOT MEASURED";
* winner: the eligible candidate with the highest pooled ORE that also beats the baseline on paired archive outcomes
  (production EXACT per case): exact two-sided McNemar, Holm-corrected over the candidates tested, adjusted p < 0.05;
  ties on ORE go to the lower median runtime;
* otherwise the V8 consensus stays the default and H3 is rejected.

    PYTHONPATH=src python experiments/v9/consensus/evaluate.py
"""
from __future__ import annotations

import json
import math
import statistics
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EVAL = ROOT / "experiments" / "v9" / "oracle" / "results" / "eval.jsonl"
ELIG = HERE / "results" / "eligibility.jsonl"
OUT = HERE / "results" / "selection.json"
BASELINE, CANDIDATES = "v8", ("A", "E")


def wilson(k: int, n: int) -> list | None:
    if n == 0:
        return None
    z = 1.959963984540054
    p, d = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value: binomial test of b vs c discordant pairs at p = 1/2."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, p)


def holm(ps: dict) -> dict:
    order = sorted(ps, key=lambda k: ps[k])
    m, out, run = len(order), {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (m - i) * ps[k]))
        out[k] = run
    return out


def main() -> int:
    rows = [json.loads(x) for x in EVAL.read_text().splitlines()]
    key = lambda r: (r["channel"], r["coverage"], r["profile"], r["seed"])          # noqa: E731
    oracle = {key(r): r["outcome"] == "EXACT" for r in rows if r["level"] == "oracle_consensus_2reads"}
    prod = {c: {key(r): r for r in rows if r["level"] == "production" and r["candidate"] == c} for c in (BASELINE, *CANDIDATES)}
    cases = sorted(set(oracle).intersection(*(set(v) for v in prod.values())))
    elig_rows = [json.loads(x) for x in ELIG.read_text().splitlines()] if ELIG.exists() else []
    res: dict = {"label": "SIMULATED: one EVAL evaluation per frozen candidate (seeds 91000-91099, primary cells)",
                 "cases": len(cases), "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                                               text=True).stdout.strip(),
                 "source": str(EVAL.relative_to(ROOT)), "candidates": {}}
    base_t = statistics.median(prod[BASELINE][k]["decode_seconds"] for k in cases)
    O = [k for k in cases if oracle[k]]
    for c in (BASELINE, *CANDIDATES):
        P = {k for k in cases if prod[c][k]["outcome"] == "EXACT"}
        cells = {}
        for k in cases:
            cell = f"{k[0]}/cov{k[1]}/{k[2]}"
            d = cells.setdefault(cell, {"n": 0, "exact": 0, "O": 0, "PO": 0})
            d["n"] += 1
            d["exact"] += k in P
            d["O"] += oracle[k]
            d["PO"] += oracle[k] and k in P
        for d in cells.values():
            d["ORE"] = round(d["PO"] / d["O"], 4) if d["O"] else None
            d["ORE_wilson95"] = wilson(d["PO"], d["O"])
            d["exact_wilson95"] = wilson(d["exact"], d["n"])
        t = [prod[c][k]["decode_seconds"] for k in cases]
        er = [e for e in elig_rows if e["candidate"] == c]
        fs = sum(prod[c][k]["outcome"] == "FALSE_SUCCESS" for k in cases)
        entry = {"exact": len(P), "pooled_ORE": round(sum(1 for k in O if k in P) / len(O), 4) if O else None,
                 "pooled_ORE_wilson95": wilson(sum(1 for k in O if k in P), len(O)), "false_success": fs,
                 "median_seconds": round(statistics.median(t), 3), "p95_seconds": round(sorted(t)[int(0.95 * (len(t) - 1))], 3),
                 "runtime_ratio_vs_baseline": round(statistics.median(t) / base_t, 3), "cells": cells,
                 "false_frames": sum(e["false_frames"] for e in er) if er else "NOT MEASURED",
                 "deterministic": all(e["deterministic"] for e in er if "deterministic" in e) if er else "NOT MEASURED"}
        if c != BASELINE:
            b = sum(1 for k in cases if k in P and prod[BASELINE][k]["outcome"] != "EXACT")
            cc = sum(1 for k in cases if k not in P and prod[BASELINE][k]["outcome"] == "EXACT")
            entry.update(discordant_candidate_only=b, discordant_baseline_only=cc, mcnemar_p=mcnemar_exact(b, cc))
        res["candidates"][c] = entry
    adj = holm({c: res["candidates"][c]["mcnemar_p"] for c in CANDIDATES})
    for c in CANDIDATES:
        e = res["candidates"][c]
        e["mcnemar_p_holm"] = adj[c]
        e["eligible"] = (e["false_success"] == 0 and e["runtime_ratio_vs_baseline"] <= 2.0 and e["false_frames"] in (0, "NOT MEASURED")
                         and e["deterministic"] in (True, "NOT MEASURED"))
        e["eligibility_complete"] = e["false_frames"] != "NOT MEASURED" and e["deterministic"] != "NOT MEASURED"
        e["beats_baseline"] = (adj[c] < 0.05 and e["discordant_candidate_only"] > e["discordant_baseline_only"])
    base_ore = res["candidates"][BASELINE]["pooled_ORE"]
    q = [c for c in CANDIDATES if res["candidates"][c]["eligible"] and res["candidates"][c]["beats_baseline"]
         and res["candidates"][c]["pooled_ORE"] > base_ore]
    q.sort(key=lambda c: (-res["candidates"][c]["pooled_ORE"], res["candidates"][c]["median_seconds"]))
    winner = q[0] if q else None
    res["winner"] = winner
    res["winner_eligibility_complete"] = res["candidates"][winner]["eligibility_complete"] if winner else None
    res["H3"] = ("ACCEPTED (pending eligibility measurement)" if winner and not res["winner_eligibility_complete"]
                 else "ACCEPTED" if winner else "REJECTED: the V8 consensus stays the default")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    for c, e in res["candidates"].items():
        print(c, "ORE", e["pooled_ORE"], e["pooled_ORE_wilson95"], "exact", e["exact"], "t", e["median_seconds"],
              {k: e.get(k) for k in ("mcnemar_p", "mcnemar_p_holm", "eligible", "beats_baseline") if k in e})
    print("winner", winner, "H3", res["H3"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
