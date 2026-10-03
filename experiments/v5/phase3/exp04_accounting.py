"""P3-EXP-04 — per-strand erasure accounting under the V4 channel model (Phase 3 §11, §16). SIMULATED.

4,096 real v4-balanced strands, coverage 1, the V4 channel at several indel rates (truth from the verified twin).
Acceptance comes from the decoder's own pass-1 function (``decoder._try``) in both modes; erasures are attributed to
the true indels by the harness:

  production view    V4-decoded reads keep the V4 mask (V5 runs only where V4 fails, as in the decoder);
                     V5-only reads use the mask of the accepted V5 trial
  localisation view  every read with an indel goes through the code-arbitrated phases (T1, then T2) — what the local
                     primitive needs to erase when it is used

Per-indel classes: fully recovered (only the bytes of genuinely missing bases erased), partially recovered (less than
the V4 erasure of that window), complete segment erasure, unresolved (read not decoded), undetected (no indel window:
cancelling / absorbed indels, left to the inner code as substitutions).

usage: python experiments/v5/phase3/exp04_accounting.py [--strands 4096]
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from collections import Counter

import numpy as np

import p3common as pc
from truth_channel import simulate_with_truth
from vnxdna.v4 import channel as ch
from vnxdna.v4 import decoder as de
from vnxdna.v4.sync import SyncCosts
from vnxdna.v5.indel import recovery as rv
from vnxdna.v5.indel.path import align_with_path

OUT = pc.HERE / "P3-EXP-04-accounting"
SEED, CHANNEL_SEED = 3401, 3402
POINTS = {
    "0.05%+0.05%": {"insertion_rate": 0.0005, "deletion_rate": 0.0005},
    "0.1%+0.1%": {"insertion_rate": 0.001, "deletion_rate": 0.001},
    "0.2%+0.2%": {"insertion_rate": 0.002, "deletion_rate": 0.002},
    "0.3%+0.3%": {"insertion_rate": 0.003, "deletion_rate": 0.003},
    "0.5%+0.5%": {"insertion_rate": 0.005, "deletion_rate": 0.005},
    "1%+1%": {"insertion_rate": 0.01, "deletion_rate": 0.01},
    "mixed-L2 (0.5% sub, 0.2%+0.2%)": {"substitution_rate": 0.005, "insertion_rate": 0.002, "deletion_rate": 0.002},
}


def decoder_pass1(lay, reads, quals, smart):
    de._P.clear()
    de._p_init(lay, 6, SyncCosts(), 0, False, rv.IndelRecoveryConfig() if smart else None)
    acc, fields, payload, _, _, path, _, _ = de._try(reads, quals)
    return acc, fields, payload, path


def window_of(windows, kind, p):
    for wi, w in enumerate(windows):
        if w.active and (w.ta <= p < w.tb or (kind == "ins" and w.ta <= p <= w.tb)):
            return wi
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strands", type=int, default=4096)
    a = ap.parse_args()
    lay = pc.LAYOUTS["v4-balanced (24/3)"]
    geom = rv.Geometry(lay)
    al = pc.aligner(lay)
    S = pc.make_strands(lay, a.strands, SEED)
    summary, records_all = {}, []
    t_all = time.perf_counter()
    for name, chan in POINTS.items():
        cfg = ch.ChannelConfig.from_dict({**chan, "coverage": 1, "seed": CHANNEL_SEED})
        sim = simulate_with_truth(S["strands"], cfg, 0)
        reads, quals, src, events = sim["reads"], sim["quals"], sim["source"], sim["events"]
        n = len(reads)
        t0 = time.perf_counter()
        acc4, f4, p4, path4 = decoder_pass1(lay, reads, None, False)
        t4 = time.perf_counter() - t0
        t0 = time.perf_counter()
        acc5, f5, p5, path5 = decoder_pass1(lay, reads, quals, True)
        t5 = time.perf_counter() - t0
        def correct(acc, f, p, i):
            s = src[i]
            return bool(acc[i] and tuple(int(x) for x in f[i]) == (0, pc.TAG, int(S["groups"][s]), int(S["symbols"][s]))
                        and np.array_equal(p[i], S["payloads"][s]))
        ok4 = np.array([correct(acc4, f4, p4, i) for i in range(n)])
        ok5 = np.array([correct(acc5, f5, p5, i) for i in range(n)])
        false4 = int((acc4 & ~ok4).sum())
        false5 = int((acc5 & ~ok5).sum())
        # erasure attribution (harness)
        proj, rpos = align_with_path(al, reads)
        rows = np.flatnonzero(proj.ok)
        cfg_prod = rv.IndelRecoveryConfig()
        cfg_loc = rv.IndelRecoveryConfig(phases=("T1", "T2"))
        plans = rv.plan_reads(geom, reads, None, proj, rpos, rows, cfg_prod)
        has_indel = np.array([any(e[0] != "sub" for e in events[i]) for i in rows.tolist()], dtype=bool)
        loc_outs = rv.recover_batch(geom, [plans[j] for j in np.flatnonzero(has_indel)], cfg_loc)
        loc_by_row = {int(rows[j]): o for j, o in zip(np.flatnonzero(has_indel).tolist(), loc_outs)}
        prod_need = [j for j, i in enumerate(rows.tolist()) if not ok4[i]]
        prod_outs = rv.recover_batch(geom, [plans[j] for j in prod_need], cfg_prod)
        prod_by_row = {int(rows[j]): o for j, o in zip(prod_need, prod_outs)}
        per_indel = {"V4": [], "V5-production": [], "V5-localisation": []}
        classes = {"V5-production": Counter(), "V5-localisation": Counter(), "V4": Counter()}
        n_true = 0
        for j, i in enumerate(rows.tolist()):
            ev = [e for e in events[i] if e[0] != "sub"]
            if not ev:
                continue
            p = plans[j]
            s = src[i]
            v4_mask_b = proj.erased[i].reshape(-1, 4).any(axis=1)
            masks = {"V4": (v4_mask_b, bool(ok4[i]))}
            po = prod_by_row.get(i)
            masks["V5-production"] = (v4_mask_b, True) if ok4[i] else \
                ((po.erased_byte_mask, bool(ok5[i] and po is not None and po.accepted)) if po is not None else (v4_mask_b, False))
            lo = loc_by_row.get(i)
            loc_ok = bool(lo is not None and lo.accepted and lo.fields == (0, pc.TAG, int(S["groups"][s]), int(S["symbols"][s]))
                          and np.array_equal(lo.payload, S["payloads"][s]))
            masks["V5-localisation"] = (lo.erased_byte_mask if lo is not None else v4_mask_b, loc_ok)
            groups: dict = {}
            for e in ev:
                n_true += 1
                wi = window_of(p.windows, e[0], e[1])
                groups.setdefault(wi, []).append(e)
            rec = {"rate": name, "read": i, "strand": int(s), "true_indels": len(ev), "true_subs": len(events[i]) - len(ev),
                   "windows": sum(w.active for w in p.windows), "v4_ok": bool(ok4[i]), "v5_ok": bool(ok5[i]),
                   "v4_erased_nt": int(proj.erased[i].sum()), "v5_erased_nt": int(4 * masks["V5-production"][0].sum()),
                   "v5_localised_erased_nt": int(4 * masks["V5-localisation"][0].sum()) if loc_ok else None,
                   "rs_errata": int(po.rs_errata) if po is not None and po.accepted else None,
                   "rs_erasure_bytes": int(po.erased_bytes) if po is not None and po.accepted else None, "classes": {}}
            for view, (mask_b, decoded) in masks.items():
                for wi, evs in groups.items():
                    k = len(evs)
                    if wi is None:
                        classes[view]["undetected"] += k
                        continue
                    w = p.windows[wi]
                    fr = geom.tmap[w.ta:w.tb]
                    fb = np.unique(fr[fr >= 0] // 4)
                    er_nt = 4 * int(mask_b[fb].sum())
                    v4_nt = 4 * int(v4_mask_b[fb].sum())
                    n_del = sum(1 for e in evs if e[0] == "del")
                    if not decoded:
                        cls = "unresolved"
                    elif er_nt <= 4 * n_del:
                        cls = "fully recovered"
                    elif er_nt < v4_nt:
                        cls = "partially recovered"
                    else:
                        cls = "complete segment erasure"
                    classes[view][cls] += k
                    if decoded:
                        per_indel[view].extend([er_nt / k] * k)
                    rec["classes"].setdefault(view, Counter())[cls] += k
            rec["classes"] = {v: dict(c) for v, c in rec["classes"].items()}
            records_all.append(rec)
        row = {"reads": n, "true_indels": n_true, "channel": cfg.to_dict(), "reads_sha256": pc.sha(np.concatenate(reads)) if n else None,
               "V4_reads_ok": int(ok4.sum()), "V5_reads_ok": int(ok5.sum()), "V4_false": false4, "V5_false": false5,
               "V5_smart_path": int((path5 == 3).sum()),
               "erased_nt_per_true_indel_phase1_style": {   # Σ erased frame nt of reads with indels / Σ true indels
                   "V4": round(float(sum(int(proj.erased[i].sum()) for i in rows.tolist() if any(e[0] != "sub" for e in events[i])))
                               / max(n_true, 1), 2)},
               "per_indel_erased_nt": {v: pc.dist(x) for v, x in per_indel.items()},
               "indel_classes": {v: dict(c) for v, c in classes.items()},
               "seconds": {"V4_pass1": round(t4, 3), "V5_pass1": round(t5, 3)},
               "reads_per_second": {"V4": round(n / t4, 1), "V5": round(n / t5, 1)}}
        recs = [r for r in records_all if r["rate"] == name]
        v5only = [r for r in recs if r["v5_ok"] and not r["v4_ok"] and r["rs_erasure_bytes"] is not None]
        row["rs_load"] = {   # what the inner code had to spend on reads with ≥ 1 true indel
            "V4_decoded_reads_erased_bytes": pc.dist([r["v4_erased_nt"] / 4 for r in recs if r["v4_ok"]]),
            "V5_only_reads_erased_bytes": pc.dist([r["rs_erasure_bytes"] for r in v5only]),
            "V5_only_reads_rs_errata": pc.dist([r["rs_errata"] for r in v5only]),
            "V5_localised_erased_bytes": pc.dist([r["v5_localised_erased_nt"] / 4 for r in recs
                                                  if r["v5_localised_erased_nt"] is not None])}
        row["bases_per_second"] = {k: round(v * lay.strand_nt, 0) for k, v in row["reads_per_second"].items()}
        row["local_search_seconds"] = round(t5 - t4, 3)
        summary[name] = row
        d = row["per_indel_erased_nt"]
        print(f"{name:32s} reads ok V4 {row['V4_reads_ok']:4d} V5 {row['V5_reads_ok']:4d} (false {false4}/{false5})  "
              f"erased/indel mean V4 {d['V4'].get('mean')} V5-prod {d['V5-production'].get('mean')} V5-loc {d['V5-localisation'].get('mean')}  "
              f"{row['reads_per_second']}")
    cfgd = {"layout": lay.to_dict(), "strands": a.strands, "strand_seed": SEED, "channel_seed": CHANNEL_SEED, "coverage": 1,
            "points": POINTS, "band": 6, "recovery": rv.IndelRecoveryConfig().__dict__,
            "truth": "truth_channel.simulate_with_truth (verified byte-identical to vnxdna.v4.channel.simulate_batch)"}
    pc.write_result(OUT, "P3-EXP-04 per-strand accounting", cfgd, {"summary": summary, "wall_seconds": round(time.perf_counter() - t_all, 1)})
    with gzip.open(OUT / "strand_records.jsonl.gz", "wt") as f:
        for r in records_all:
            f.write(json.dumps(r) + "\n")
    print("records:", len(records_all))


if __name__ == "__main__":
    main()
