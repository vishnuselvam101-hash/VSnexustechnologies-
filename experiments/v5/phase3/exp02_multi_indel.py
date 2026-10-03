"""P3-EXP-02 — several indels per read (Phase 3 §13). SIMULATED.

Patterns (v4-balanced, 313 nt, no other errors unless stated):
  separated-k     k indels (random kind) in k different segments, k = 2, 3, 4, 5, 10
  adjacent        two indels of the same kind at neighbouring positions (net shift ±2 in one window)
  ins+del-near    an insertion then a deletion 3–10 nt apart in one segment (net shift 0)
  del+ins-near    the same, deletion first
  ins|del-split   an insertion and a deletion in two neighbouring segments
  marker-adjacent indels directly next to a marker (k = 2)
  separated-3+sub three separated indels plus 2 substitutions

V4 = whole-segment erasure; V5 = the production phases T0 → T1 → T2 with the default bounds. The decoder never sees
the truth; it is used only to score acceptance (correct / false).

usage: python experiments/v5/phase3/exp02_multi_indel.py [--reads 300]
"""
from __future__ import annotations

import argparse
import time
from collections import defaultdict

import numpy as np

import p3common as pc
from vnxdna.v5.indel import recovery as rv
from vnxdna.v5.indel.path import align_with_path

OUT = pc.HERE / "P3-EXP-02-multi-indel"
SEED = 3201
LAYOUT = "v4-balanced (24/3)"


def frame_segments(geom: rv.Geometry) -> list[tuple[int, int]]:
    """Template ranges of each segment's frame bases."""
    out = []
    for s in range(int(geom.seg_of_frame.max()) + 1):
        tp = geom.frame_pos[geom.seg_of_frame == s]
        out.append((int(tp[0]), int(tp[-1]) + 1))
    return out


def gen_events(pattern: str, k: int, geom: rv.Geometry, rng: np.random.Generator) -> list[tuple]:
    segs = frame_segments(geom)
    def rnd_kind(p):
        return ("del", p) if rng.random() < 0.5 else ("ins", p, int(rng.integers(0, 4)))
    if pattern == "separated":
        chosen = rng.choice(len(segs), size=k, replace=False)
        return [rnd_kind(int(rng.integers(segs[s][0], segs[s][1]))) for s in chosen]
    if pattern == "adjacent":
        s = segs[int(rng.integers(1, len(segs) - 1))]
        p = int(rng.integers(s[0], s[1] - 1))
        if rng.random() < 0.5:
            return [("del", p), ("del", p + 1)]
        return [("ins", p, int(rng.integers(0, 4))), ("ins", p, int(rng.integers(0, 4)))]
    if pattern in ("ins+del-near", "del+ins-near"):
        s = segs[int(rng.integers(1, len(segs) - 1))]
        p = int(rng.integers(s[0], s[1] - 11))
        q = p + int(rng.integers(3, 11))
        a, b = (("ins", p, int(rng.integers(0, 4))), ("del", q)) if pattern == "ins+del-near" else \
            (("del", p), ("ins", q, int(rng.integers(0, 4))))
        return [a, b]
    if pattern == "ins|del-split":
        i = int(rng.integers(1, len(segs) - 2))
        return [("ins", int(rng.integers(*segs[i])), int(rng.integers(0, 4))), ("del", int(rng.integers(*segs[i + 1])))]
    if pattern == "marker-adjacent":
        mk = [m for m in geom.markers]
        chosen = rng.choice(len(mk), size=k, replace=False)
        ev = []
        for c in chosen:
            t0, t1 = mk[int(c)]
            p = t0 - 1 if rng.random() < 0.5 else t1          # the frame base just before / after the marker
            ev.append(rnd_kind(p))
        return ev
    if pattern == "separated+sub":
        ev = gen_events("separated", k, geom, rng)
        used = {e[1] for e in ev}
        for _ in range(2):
            p = int(rng.integers(0, geom.T))
            while p in used:
                p = int(rng.integers(0, geom.T))
            used.add(p)
            ev.append(("sub", p, int(rng.integers(0, 4))))
        return ev
    raise ValueError(pattern)


CASES = [("separated", 2), ("separated", 3), ("separated", 4), ("separated", 5), ("separated", 10), ("adjacent", 2),
         ("ins+del-near", 2), ("del+ins-near", 2), ("ins|del-split", 2), ("marker-adjacent", 2), ("separated+sub", 3)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reads", type=int, default=300)
    a = ap.parse_args()
    lay = pc.LAYOUTS[LAYOUT]
    geom = rv.Geometry(lay)
    al = pc.aligner(lay)
    S = pc.make_strands(lay, a.reads, SEED)
    rng = np.random.default_rng(SEED + 1)
    cfg = rv.IndelRecoveryConfig()
    summary = {}
    t_all = time.perf_counter()
    for pattern, k in CASES:
        reads, evs = [], []
        for i in range(a.reads):
            ev = gen_events(pattern, k, geom, rng)
            reads.append(pc.inject(S["strands"][i], ev)[0])
            evs.append(ev)
        rows = np.arange(a.reads)
        proj, rpos = align_with_path(al, reads)
        ok4, _ = pc.v4_decode(lay, proj, rows)
        t0 = time.perf_counter()
        plans = rv.plan_reads(geom, reads, None, proj, rpos, rows, cfg)
        outs = rv.recover_batch(geom, plans, cfg)
        dt = time.perf_counter() - t0
        n_indel = sum(sum(1 for e in ev if e[0] != "sub") for ev in evs)
        correct = [o.accepted and o.fields == (0, pc.TAG, int(S["groups"][i]), int(S["symbols"][i]))
                   and np.array_equal(o.payload, S["payloads"][i]) for i, o in enumerate(outs)]
        false = [o.accepted and not c for o, c in zip(outs, correct)]
        v5_ok = [bool(ok4[i]) or correct[i] for i in range(a.reads)]          # production: V4 first, V5 on failure
        st = defaultdict(int)
        reasons = defaultdict(int)
        for p in plans:
            for w in p.windows:
                if w.active:
                    st[w.status] += 1
                    if w.reason:
                        reasons[w.reason] += 1
        phases = defaultdict(int)
        for o in outs:
            phases[o.phase] += 1
        per_read_v4 = proj.erased.sum(axis=1)
        per_read_v5 = np.array([o.erased_nt for o in outs])
        row = {"reads": a.reads, "true_indels": n_indel, "V4_ok": int(ok4.sum()), "V5_ok": int(sum(v5_ok)),
               "V5_alone_ok": int(sum(correct)), "V5_false": int(sum(false)),
               "V4_erased_nt_per_indel": round(float(per_read_v4.sum()) / max(n_indel, 1), 2),
               "V5_erased_nt_per_indel": round(float(per_read_v5.sum()) / max(n_indel, 1), 2),
               "V5_erased_nt_per_indel_accepted_reads": round(float(per_read_v5[np.asarray(correct)].sum()) /
                                                              max(1, sum(sum(1 for e in evs[i] if e[0] != "sub")
                                                                         for i in range(a.reads) if correct[i])), 2),
               "detected_windows": int(sum(st.values())), "window_status": dict(st), "unrecoverable_reasons": dict(reasons),
               "phase": dict(phases), "trials": pc.dist([o.trials for o in outs]),
               "false_accept_bound_max": max(o.false_accept_bound for o in outs), "seconds": round(dt, 3),
               "reads_per_second_v5": round(a.reads / dt, 1)}
        name = f"{pattern} k={k}"
        summary[name] = row
        print(f"{name:22s} V4 {row['V4_ok']:4d}/{a.reads}  V5 {row['V5_ok']:4d}  false {row['V5_false']}  "
              f"erased/indel V4 {row['V4_erased_nt_per_indel']:5.1f}  V5 {row['V5_erased_nt_per_indel']:5.1f}  "
              f"phases {dict(phases)}  status {dict(st)}  {dt:.1f}s")
    cfgd = {"layout": lay.to_dict(), "reads_per_case": a.reads, "seed": SEED, "cases": CASES, "band": 6,
            "recovery": rv.IndelRecoveryConfig().__dict__}
    pc.write_result(OUT, "P3-EXP-02 multiple indels", cfgd, {"summary": summary, "wall_seconds": round(time.perf_counter() - t_all, 1)})


if __name__ == "__main__":
    main()
