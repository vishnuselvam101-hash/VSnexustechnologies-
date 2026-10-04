"""P4-EXP-01 (substitution sweep) and P4-EXP-03 (indel + substitution sweep): read-level verified recovery.

SIMULATED. 4,096 real v4-balanced strands at coverage 1 through the V4 channel; each read file is decoded by every
configuration of p4common.CONFIGS (identical reads). Qualities: the V4 simulator's two-level model (Q35 correct, Q12
on an erroneous base with probability ``quality_informative``), swept over 0, 0.5 and 1.

usage: python experiments/v5/phase4/exp_read_level.py {subs,indel} [--strands 4096]
"""
from __future__ import annotations

import argparse
import time

import numpy as np

import p4common as p4
from p4common import pc
from truth_channel import simulate_with_truth
from vnxdna.v4 import channel as ch
from vnxdna.v5.soft import symbols as ss

SEED, CHANNEL_SEED = 4101, 4102
SUBS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.03]
INDEL = [(0.001, 0.001), (0.002, 0.002), (0.005, 0.005), (0.01, 0.01)]     # (indel rate, substitution rate)
INFORMATIVE = [0.0, 0.5, 1.0, "graded"]      # V4 two-level model at three informativeness levels; synthetic graded model


def run(points: list[tuple[str, dict]], out_name: str, n: int) -> None:
    lay = pc.LAYOUTS["v4-balanced (24/3)"]
    S = pc.make_strands(lay, n, SEED)
    truth_fields = [(0, pc.TAG, int(S["groups"][i]), int(S["symbols"][i])) for i in range(n)]
    summary = {}
    t_all = time.perf_counter()
    for pname, chan in points:
        for inf in INFORMATIVE:
            graded = inf == "graded"
            cfg = ch.ChannelConfig.from_dict({**chan, "quality_informative": 0.0 if graded else inf, "coverage": 1,
                                              "seed": CHANNEL_SEED})
            sim = simulate_with_truth(S["strands"], cfg, 0)            # verified identical to the V4 simulator
            reads, quals = sim["reads"], sim["quals"]
            if graded:
                quals = p4.graded_qualities(p4.error_masks(S["strands"], sim["source"], sim["events"]), CHANNEL_SEED + 7)
            row = {"channel": cfg.to_dict(), "quality_model": "graded (synthetic)" if graded else f"V4 two-level, informative {inf}",
                   "reads_sha256": pc.sha(np.concatenate(reads), np.array([r.size for r in reads])),
                   "quals_sha256": pc.sha(np.concatenate(quals)),
                   "true_errors": {k: sim["stats"][k] for k in ("substitutions", "insertions", "deletions")}}
            # information content of the soft input (posterior entropy of the raw reads' frame bases)
            ent = [float(ss.entropy_bits(ss.from_read(r[:280], q[:280], 0.005)).mean()) for r, q in zip(reads[:512], quals[:512])]
            row["mean_read_entropy_bits_per_base"] = round(float(np.mean(ent)), 4)
            for name in p4.CONFIGS:
                t = time.perf_counter()
                acc, fields, payload, path, st = p4.pass1(lay, reads, quals, name)
                dt = time.perf_counter() - t
                ok = p4.correct_mask(acc, fields, payload, truth_fields, S["payloads"])
                row[name] = {"verified_correct": int(ok.sum()), "false": int((acc & ~ok).sum()), "seconds": round(dt, 3),
                             "reads_per_second": round(n / dt, 1), "by_path": {str(k): int((path == k).sum()) for k in (1, 2, 3, 4)},
                             "soft": {k[5:]: v for k, v in st.items() if k.startswith("soft_")}}
            key = f"{pname} | {'graded Q' if graded else f'informative {inf}'}"
            summary[key] = row
            print(f"{key:44s} " + "  ".join(f"{k} {row[k]['verified_correct']}{'!' + str(row[k]['false']) if row[k]['false'] else ''}"
                                             for k in p4.CONFIGS) + f"  (soft auto {row['V5-soft-auto']['seconds']}s, V5-hard "
                  f"{row['V5-hard']['seconds']}s)", flush=True)
    cfgd = {"layout": lay.to_dict(), "strands": n, "strand_seed": SEED, "channel_seed": CHANNEL_SEED, "coverage": 1,
            "points": points, "quality_informative": INFORMATIVE, "configs": p4.CONFIGS,
            "soft_defaults": p4.SoftDecodeConfig().__dict__, "indel_defaults": p4.IndelRecoveryConfig().__dict__}
    pc.write_result(p4.HERE / out_name, out_name, cfgd, {"summary": summary, "wall_seconds": round(time.perf_counter() - t_all, 1)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["subs", "indel"])
    ap.add_argument("--strands", type=int, default=4096)
    a = ap.parse_args()
    if a.which == "subs":
        run([(f"sub {r * 100:g}%", {"substitution_rate": r}) for r in SUBS], "P4-EXP-01-substitution-sweep", a.strands)
    else:
        run([(f"indel {i * 100:g}% (ins {i * 50:g}% + del {i * 50:g}%) + sub {s * 100:g}%",
              {"insertion_rate": i / 2, "deletion_rate": i / 2, "substitution_rate": s}) for i, s in INDEL],
            "P4-EXP-03-indel-plus-substitution", a.strands)


if __name__ == "__main__":
    main()
