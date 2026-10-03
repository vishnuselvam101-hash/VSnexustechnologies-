"""P3-EXP-01 — exactly one indel per read (Phase 3 §12, step 3). SIMULATED.

Every deletion position and every insertion position of real V4 strands, for several marker periods, with and
without informative qualities. No other errors. Compares, on identical reads:

  V4         marker alignment → whole-segment erasure → inner RS + CRC
  V5-T0      local evidence only (marker agreement, qualities); unverified bytes kept only if P(wrong) < 1e-3
  V5-T0/0.5  ablation: keep bytes with P(wrong) < 0.5 (RS-load optimum); counts the kept bytes that were wrong
  V5-T1      code arbitration: every placement in the window is a hypothesis, verified by inner RS + CRC, unanimity

Truth (the injected edit, the original strand) is used only to score the outputs.

usage: python experiments/v5/phase3/exp01_single_indel.py [--strands 6] [--quick]
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict

import numpy as np

import p3common as pc
from vnxdna.v5.indel import recovery as rv
from vnxdna.v5.indel.path import align_with_path

OUT = pc.HERE / "P3-EXP-01-single-indel"
SEED = 3101


def position_class(geom: rv.Geometry, p: int, kind: str) -> str:
    T = geom.T
    tpl = geom.tpl
    if kind == "del" and tpl[p] >= 0:
        return "marker base"
    near = [q for q in (p - 1, p) if 0 <= q < T and tpl[q] >= 0] if kind == "ins" else \
        [q for q in (p - 1, p + 1) if 0 <= q < T and tpl[q] >= 0]
    if near:
        return "marker-adjacent"
    f = geom.tmap[min(p, T - 1)]
    seg = geom.seg_of_frame[f] if f >= 0 else -1
    if seg == 0:
        return "first segment"
    if seg == geom.seg_of_frame[-1]:
        return "last segment"
    return "interior segment"


def true_window_content(strand: np.ndarray, w: rv.Window) -> np.ndarray:
    return strand[w.ta:w.tb]


def consistent(cand: np.ndarray, truth: np.ndarray) -> bool:
    k = cand != rv.UNK
    return cand.size == truth.size and bool(np.array_equal(cand[k], truth[k]))


def run_cases(layout_name: str, n_strands: int, quick: bool) -> list[dict]:
    lay = pc.LAYOUTS[layout_name]
    geom = rv.Geometry(lay)
    al = pc.aligner(lay)
    S = pc.make_strands(lay, n_strands, SEED)
    rng = np.random.default_rng(SEED + 1)
    T = geom.T
    tpl_true_frames = S["strands"][:, geom.frame_pos]                    # frame nt of each strand (truth)
    reads, quals, meta = [], [], []
    positions = range(0, T, 7 if quick else 1)
    for s in range(n_strands):
        for p in positions:
            for kind in ("del", "ins"):
                ev = [("del", p)] if kind == "del" else [("ins", p, int(rng.integers(0, 4)))]
                r, q = pc.inject(S["strands"][s], ev, informative_quality=True)
                reads.append(r)
                quals.append(q)
                meta.append({"strand": s, "pos": p, "kind": kind, "class": position_class(geom, p, kind)})
    n = len(reads)
    rows = np.arange(n)
    proj, rpos = align_with_path(al, reads)
    ok4, _ = pc.v4_decode(lay, proj, rows)
    results = [dict(m) for m in meta]
    for i in range(n):
        results[i]["v4_ok"] = bool(ok4[i])
        results[i]["v4_erased_nt"] = int(proj.erased[i].sum())
    variants = {
        "V5-T0": (rv.IndelRecoveryConfig(phases=("T0",)), None),
        "V5-T0/0.5": (rv.IndelRecoveryConfig(phases=("T0",), keep_byte_error=0.5), None),
        "V5-T1": (rv.IndelRecoveryConfig(phases=("T1",)), None),
        "V5-T0+Q": (rv.IndelRecoveryConfig(phases=("T0",)), quals),
        "V5-T1+Q": (rv.IndelRecoveryConfig(phases=("T1",)), quals),
    }
    for name, (cfg, qv) in variants.items():
        t0 = time.perf_counter()
        plans = rv.plan_reads(geom, reads, qv, proj, rpos, rows, cfg)
        outs = rv.recover_batch(geom, plans, cfg)
        dt = time.perf_counter() - t0
        for i, (pl, o) in enumerate(zip(plans, outs)):
            m = meta[i]
            s = m["strand"]
            correct = o.accepted and o.fields == (0, pc.TAG, int(S["groups"][s]), int(S["symbols"][s])) and \
                np.array_equal(o.payload, S["payloads"][s])
            act = [w for w in pl.windows if w.active]
            rec = {"accepted": o.accepted, "correct": bool(correct), "false": bool(o.accepted and not correct), "phase": o.phase,
                   "trials": o.trials, "erased_nt": o.erased_nt, "status": [w.status for w in act]}
            if name == "V5-T1":            # localisation diagnostics (independent of the variant)
                p = m["pos"]
                inside = [w for w in act if w.ta <= p <= w.tb]
                rec["detected"] = bool(act)
                rec["window_contains_truth"] = bool(inside)
                rec["truth_in_candidates"] = bool(inside and inside[0].cands is not None and
                                                  any(consistent(c, true_window_content(S["strands"][s], inside[0]))
                                                      for c in inside[0].cands))
                rec["n_candidates"] = int(inside[0].cands.shape[0]) if inside and inside[0].cands is not None else 0
            if name.startswith("V5-T0"):    # unverified kept bytes that are wrong (truth comparison, harness only)
                kept = ~pl.base_erased.reshape(-1, 4).any(axis=1)
                fb = pl.base_frame.reshape(-1, 4)
                tb = tpl_true_frames[s].reshape(-1, 4)
                rec["wrong_kept_bytes"] = int((kept & (fb != tb).any(axis=1)).sum())
            results[i][name] = rec
        results[0].setdefault("_seconds", {})[name] = round(dt, 3)
    return results


def summarize(results: list[dict]) -> dict:
    out: dict = {}
    by = defaultdict(list)
    for r in results:
        by[(r["kind"], r["class"])].append(r)
        by[(r["kind"], "all")].append(r)
    for key, rs in sorted(by.items()):
        k = f"{key[0]} | {key[1]}"
        row = {"cases": len(rs), "V4_ok": sum(r["v4_ok"] for r in rs), "V4_erased_nt": pc.dist([r["v4_erased_nt"] for r in rs])}
        for v in ("V5-T0", "V5-T0/0.5", "V5-T1", "V5-T0+Q", "V5-T1+Q"):
            vs = [r[v] for r in rs]
            row[v] = {"accepted": sum(x["accepted"] for x in vs), "correct": sum(x["correct"] for x in vs),
                      "false": sum(x["false"] for x in vs), "erased_nt": pc.dist([x["erased_nt"] for x in vs]),
                      "trials": pc.dist([x["trials"] for x in vs])}
            if "wrong_kept_bytes" in vs[0]:
                row[v]["wrong_kept_bytes"] = int(sum(x["wrong_kept_bytes"] for x in vs))
                row[v]["reads_with_wrong_kept_bytes"] = int(sum(x["wrong_kept_bytes"] > 0 for x in vs))
            st = defaultdict(int)
            for x in vs:
                for s_ in x["status"]:
                    st[s_] += 1
            row[v]["window_status"] = dict(st)
        t1 = [r["V5-T1"] for r in rs]
        row["detected"] = sum(x["detected"] for x in t1)
        row["window_contains_truth"] = sum(x["window_contains_truth"] for x in t1)
        row["truth_in_candidates"] = sum(x["truth_in_candidates"] for x in t1)
        row["n_candidates"] = pc.dist([x["n_candidates"] for x in t1 if x["n_candidates"]])
        out[k] = row
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strands", type=int, default=6)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    t0 = time.perf_counter()
    summary, seconds = {}, {}
    for name in pc.LAYOUTS:
        res = run_cases(name, a.strands, a.quick)
        seconds[name] = res[0].pop("_seconds")
        summary[name] = summarize(res)
        s = summary[name]
        for kind in ("del", "ins"):
            r = s[f"{kind} | all"]
            print(f"{name:26s} {kind}: cases {r['cases']}  V4 ok {r['V4_ok']} erased {r['V4_erased_nt']['mean']:5.1f} nt | "
                  + " | ".join(f"{v} ok {r[v]['correct']} false {r[v]['false']} erased {r[v]['erased_nt']['mean']:5.1f}"
                               for v in ("V5-T0", "V5-T0/0.5", "V5-T1", "V5-T0+Q", "V5-T1+Q")))
    cfg = {"layouts": {k: v.to_dict() for k, v in pc.LAYOUTS.items()}, "strands_per_layout": a.strands, "seed": SEED,
           "positions": "every template position" if not a.quick else "every 7th position", "band": 6,
           "quality_model": {"correct": pc.Q_CORRECT, "error": pc.Q_ERROR, "informative": "inserted base gets Q_ERROR"},
           "recovery_defaults": rv.IndelRecoveryConfig().__dict__}
    pc.write_result(OUT, "P3-EXP-01 single indel", cfg, {"summary": summary, "seconds": seconds,
                                                         "wall_seconds": round(time.perf_counter() - t0, 1)})
    print("wrote", OUT / "results.json")


if __name__ == "__main__":
    main()
