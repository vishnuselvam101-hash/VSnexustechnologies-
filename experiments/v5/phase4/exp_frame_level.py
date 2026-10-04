"""Controlled frame-level experiments for Phase 4 (SIMULATED; truth known to the harness only).

  P4-EXP-02  k base substitutions per frame, k = 1, 2, 4, 6, 8, 10, 12; three quality models
  P4-EXP-04  byte errors e and erasures f around the RS bound 2e + f = r − 1, r, r + 1 (r = 16) and beyond
  P4-EXP-05  controlled posterior shapes on the erroneous bases (0.99/…, 0.90/…, 0.70/0.10…, 0.55/0.45, 0.51/0.49)
  P4-EXP-06  adversarial ambiguity: misleading posteriors, random frames, wrong expected address; counts of
             wrong-but-verified, ambiguous and rejected frames and of RS-valid trials that the CRC rejected

Real v4-balanced frames (random payloads, default constraints). "Hard" decoders are the unchanged V4 inner path
(``decode_frames``): plain hard decision, and hard decision with quality erasures (V4's min_quality rule at Q13 / Q20).

usage: python experiments/v5/phase4/exp_frame_level.py {exp02,exp04,exp05,exp06,all} [--frames 500]
"""
from __future__ import annotations

import argparse
import time
from contextlib import contextmanager

import numpy as np

import p4common as p4
from p4common import pc
from vnxdna.v4 import codecs
from vnxdna.v4.frame import decode_frames, nt_to_bytes
from vnxdna.v5.indel.recovery import Geometry
from vnxdna.v5.soft import decoder as sd
from vnxdna.v5.soft import symbols as ss

SEED = 4201
LAY = pc.LAYOUTS["v4-balanced (24/3)"]
GEOM = Geometry(LAY)
MODES = ("erasure", "chase", "auto")


@contextmanager
def rs_counter():
    """Count inner-RS successes (before CRC) to measure how many RS-valid trials the CRC / metadata checks reject."""
    real = codecs.InnerRS.decode
    box = {"rs_ok": 0}

    def wrapped(self, cw, er=None):
        out = real(self, cw, er)
        box["rs_ok"] += int(out[1].sum())
        return out
    codecs.InnerRS.decode = wrapped
    try:
        yield box
    finally:
        codecs.InnerRS.decode = real


def setup(n):
    S = pc.make_strands(LAY, n, SEED)
    frames = S["strands"][:, GEOM.frame_pos]
    truth = [(0, pc.TAG, int(S["groups"][i]), int(S["symbols"][i])) for i in range(n)]
    return S, frames, truth


def score(outs, S, truth):
    ok = [o.accepted and o.fields == truth[i] and np.array_equal(o.payload, S["payloads"][i]) for i, o in enumerate(outs)]
    return {"verified_correct": int(sum(ok)), "false": int(sum(o.accepted for o in outs) - sum(ok)),
            "ambiguous": int(sum(o.mode == "ambiguous" for o in outs)), "rejected": int(sum(not o.accepted for o in outs)),
            "wrong_but_verified_frames": int(sum(1 for i, o in enumerate(outs) for key, pay in o.verified_frames
                                                 if key != truth[i] or pay != S["payloads"][i].tobytes())),
            "trials_mean": round(float(np.mean([o.trials for o in outs])), 2),
            "accepted_rank_mean": round(float(np.mean([o.accepted_rank for o in outs if o.accepted] or [0])), 3),
            "entropy_bits_mean": round(float(np.mean([o.entropy_bits for o in outs])), 4)}


def hard(frames_nt, quals, min_q):
    er = None
    if min_q:
        er = (np.stack(quals) < min_q).reshape(len(quals), -1, 4).any(axis=2)
    return decode_frames(LAY, nt_to_bytes(np.stack(frames_nt)), er, errors_only_retry=False)


def hard_score(P, S, truth):
    ok = [bool(P.ok[i]) and (int(P.kind[i]), int(P.tag[i]), int(P.group[i]), int(P.symbol[i])) == truth[i]
          and np.array_equal(P.payload[i], S["payloads"][i]) for i in range(P.ok.size)]
    return {"verified_correct": int(sum(ok)), "false": int(P.ok.sum() - sum(ok))}


# ---------------------------------------------------------------- EXP-02
def exp02(n):
    S, F, truth = setup(n)
    rng = np.random.default_rng(SEED + 2)
    res = {}
    for k in (1, 2, 4, 6, 8, 10, 12):
        for qm in ("uninformative", "two-level", "graded"):
            fb_all, q_all = [], []
            for i in range(n):
                fb = F[i].copy()
                pos = rng.choice(fb.size, k, replace=False)
                fb[pos] = (fb[pos] + rng.integers(1, 4, k)) % 4
                err = np.zeros(fb.size, bool)
                err[pos] = True
                if qm == "uninformative":
                    q = np.full(fb.size, 35)
                elif qm == "two-level":
                    q = np.where(err, 12, 35)
                else:
                    q = rng.integers(20, 41, fb.size)
                    q[err] = rng.integers(4, 25, k)
                fb_all.append(fb)
                q_all.append(q.astype(np.uint8))
            row = {"hard": hard_score(hard(fb_all, q_all, 0), S, truth), "hard+minQ13": hard_score(hard(fb_all, q_all, 13), S, truth),
                   "hard+minQ20": hard_score(hard(fb_all, q_all, 20), S, truth)}
            posts = [ss.from_read(fb, q, 0.005) for fb, q in zip(fb_all, q_all)]
            for m in MODES:
                t = time.perf_counter()
                row[f"soft-{m}"] = score(sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode=m)), S, truth)
                row[f"soft-{m}"]["seconds"] = round(time.perf_counter() - t, 3)
            res[f"k={k} | {qm}"] = row
            print(f"EXP-02 k={k:2d} {qm:13s} " + "  ".join(f"{a} {b['verified_correct']}" + (f"!{b['false']}" if b["false"] else "")
                                                           for a, b in row.items()), flush=True)
    return res


# ---------------------------------------------------------------- EXP-04
def _byte_case(F, i, e, f, rng, scenario):
    """Hard word with e byte errors and f erased bytes; posterior per the scenario."""
    fb = F[i].copy()
    P = np.full((fb.size, 4), 0.001 / 3)
    bytes_ = rng.choice(70, e + f, replace=False)
    err_b, era_b = bytes_[:e], bytes_[e:]
    for b in err_b:
        p = 4 * int(b) + int(rng.integers(0, 4))
        fb[p] = (fb[p] + int(rng.integers(1, 4))) % 4
    P[np.arange(fb.size), fb] = 0.999
    if scenario == "informative":                    # erroneous bases: the read is only 70 % sure
        for b in err_b:
            for p in range(4 * int(b), 4 * int(b) + 4):
                if fb[p] != F[i][p]:
                    P[p] = 0.1
                    P[p, fb[p]] = 0.7
    elif scenario == "misleading":                   # some correct bytes look unreliable, errors look certain
        for b in rng.choice(np.setdiff1d(np.arange(70), bytes_), e, replace=False):
            p = 4 * int(b)
            P[p] = 0.1
            P[p, fb[p]] = 0.7
    for b in era_b:
        P[4 * int(b):4 * int(b) + 4] = 0.25
    unk = np.zeros(70, bool)
    unk[era_b] = True
    return fb, ss.from_probabilities(P / P.sum(axis=1, keepdims=True)), unk


def exp04(n):
    S, F, truth = setup(n)
    rng = np.random.default_rng(SEED + 4)
    res = {}
    for budget in (15, 16, 17, 18, 20):
        for e in sorted({max(0, (budget - f) // 2) for f in (0, 2, 4, 6, 8, 10)}):
            f = budget - 2 * e
            if f < 0 or f > 16 or e < 0:
                continue
            for scen in ("uninformative", "informative", "misleading"):
                fbs, posts, unks = [], [], []
                for i in range(n):
                    fb, L, unk = _byte_case(F, i, e, f, rng, scen)
                    fbs.append(fb)
                    posts.append(L)
                    unks.append(unk)
                words = nt_to_bytes(np.stack(fbs))
                P = decode_frames(LAY, words, np.stack(unks), errors_only_retry=False)
                row = {"2e+f": 2 * e + f, "e": e, "f": f, "hard": hard_score(P, S, truth)}
                with rs_counter() as box:
                    for m in MODES:
                        outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode=m), unknown_bytes=unks)
                        row[f"soft-{m}"] = score(outs, S, truth)
                row["rs_valid_trials_all_modes"] = box["rs_ok"]
                res[f"2e+f={2 * e + f} (e={e}, f={f}) | {scen}"] = row
                print(f"EXP-04 2e+f={2 * e + f:2d} e={e:2d} f={f:2d} {scen:13s} hard {row['hard']['verified_correct']:4d}  "
                      + "  ".join(f"{m} {row['soft-' + m]['verified_correct']}" + (f"!{row['soft-' + m]['false']}" if row['soft-' + m]['false'] else "")
                                  for m in MODES), flush=True)
    return res


# ---------------------------------------------------------------- EXP-05
SHAPES = {
    "0.99/0.0033×3 (truth 0.0033)": (0.99, {"truth": 0.01 / 3, "other": 0.01 / 3}),
    "0.90/0.033×3 (truth 0.033)": (0.90, {"truth": 0.1 / 3, "other": 0.1 / 3}),
    "0.70/0.10×3 (truth 0.10)": (0.70, {"truth": 0.1, "other": 0.1}),
    "0.55/0.45/0/0 (truth 0.45)": (0.55, {"truth": 0.45, "other": 0.0}),
    "0.51/0.49/0/0 (truth 0.49)": (0.51, {"truth": 0.49, "other": 0.0}),
    "0.55/0.45/0/0 (truth 0)": (0.55, {"truth": 0.0, "other": 0.45}),
}


def exp05(n):
    S, F, truth = setup(n)
    rng = np.random.default_rng(SEED + 5)
    res = {}
    for e in (6, 9, 10, 12):
        for name, (p_obs, rest) in SHAPES.items():
            posts = []
            for i in range(n):
                fb = F[i].copy()
                P = np.full((fb.size, 4), 0.01 / 3)              # correct bases: 0.99 on the read base
                pos = 4 * rng.choice(70, e, replace=False) + rng.integers(0, 4, e)
                fb[pos] = (fb[pos] + rng.integers(1, 4, e)) % 4
                P[np.arange(fb.size), fb] = 0.99
                for p in pos.tolist():
                    P[p] = 0.0
                    P[p, fb[p]] = p_obs
                    P[p, F[i][p]] = rest["truth"]
                    others = [b for b in range(4) if b not in (fb[p], F[i][p])]
                    if rest["other"] and rest["truth"]:
                        for b in others:
                            P[p, b] = rest["other"]
                    elif rest["other"]:
                        P[p, others[0]] = rest["other"]
                P = P / P.sum(axis=1, keepdims=True)
                posts.append(ss.from_probabilities(P))
            row = {"hard": hard_score(decode_frames(LAY, nt_to_bytes(np.stack([np.exp(L).argmax(1) for L in posts])), None,
                                                    errors_only_retry=False), S, truth)}
            for m in MODES:
                row[f"soft-{m}"] = score(sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode=m)), S, truth)
            res[f"e={e} | {name}"] = row
            print(f"EXP-05 e={e:2d} {name:30s} hard {row['hard']['verified_correct']:4d}  "
                  + "  ".join(f"{m} {row['soft-' + m]['verified_correct']}" + (f"!{row['soft-' + m]['false']}" if row['soft-' + m]['false'] else "")
                              for m in MODES), flush=True)
    return res


# ---------------------------------------------------------------- EXP-06
def exp06(n):
    S, F, truth = setup(n)
    rng = np.random.default_rng(SEED + 6)
    res = {}
    loose = sd.SoftDecodeConfig(mode="auto", false_accept_budget=1e-6, max_soft_trials=256, chase_bytes=6)

    def run(name, posts, expected=None, unks=None):
        with rs_counter() as box:
            outs = sd.soft_decode(LAY, posts, loose, unknown_bytes=unks, expected=expected)
        sc = score(outs, S, truth)
        if expected is not None:
            sc["accepted_wrong_address"] = int(sum(o.accepted and o.fields != tuple(x) for o, x in zip(outs, expected)))
        sc["trials"] = int(sum(o.trials for o in outs))
        sc["rs_valid_trials"] = box["rs_ok"]
        sc["rs_valid_rejected_by_crc_or_checks"] = box["rs_ok"] - sum(o.verified_trials for o in outs)
        res[name] = sc
        print(f"EXP-06 {name:58s} {sc}", flush=True)

    # A: misleading — correct bases flagged unreliable, errors confident (e = 6 … 12)
    for e in (6, 9, 12):
        posts = []
        for i in range(n):
            fb = F[i].copy()
            pos = 4 * rng.choice(70, e, replace=False) + rng.integers(0, 4, e)
            fb[pos] = (fb[pos] + rng.integers(1, 4, e)) % 4
            q = np.full(fb.size, 30)
            q[rng.choice(np.setdiff1d(np.arange(fb.size), pos), 3 * e, replace=False)] = 4
            q[pos] = 60
            posts.append(ss.from_read(fb, q.astype(np.uint8), 0.005))
        run(f"A misleading qualities, e={e}", posts)
    # B: correct candidate is less likely than a wrong one at every error (truth is the runner-up at 0.45)
    for e in (9, 12):
        posts = []
        for i in range(n):
            fb = F[i].copy()
            pos = 4 * rng.choice(70, e, replace=False) + rng.integers(0, 4, e)
            fb[pos] = (fb[pos] + rng.integers(1, 4, e)) % 4
            P = np.full((fb.size, 4), 0.01 / 3)
            P[np.arange(fb.size), fb] = 0.99
            for p in pos.tolist():
                P[p] = 0.0
                P[p, fb[p]] = 0.55
                P[p, F[i][p]] = 0.45
            posts.append(ss.from_probabilities(P))
        run(f"B wrong candidate more likely than the correct one, e={e}", posts)
    # C: near-identical posteriors everywhere (0.26/0.25/0.25/0.24) — almost no information
    posts = [ss.from_probabilities(np.tile([[0.26, 0.25, 0.25, 0.24]], (280, 1))[:, rng.permutation(4)]) for _ in range(n)]
    run("C near-identical posteriors (no information)", posts)
    # D: random frames, all bases confident — no codeword anywhere near
    posts = [ss.from_read(rng.integers(0, 4, 280).astype(np.uint8), np.full(280, 40, np.uint8), 0.005) for _ in range(n)]
    run("D random frames, confident qualities", posts)
    # E: heavy noise near the decoding radius with informative qualities, wrong expected address
    posts, exp_wrong = [], []
    for i in range(n):
        fb = F[i].copy()
        pos = 4 * rng.choice(70, 10, replace=False)
        fb[pos] = (fb[pos] + 1) % 4
        q = np.full(fb.size, 35)
        q[pos] = 12
        posts.append(ss.from_read(fb, q.astype(np.uint8), 0.005))
        exp_wrong.append((0, pc.TAG, truth[i][2], truth[i][3] + 1))
    run("E decodable frames, wrong expected address", posts, expected=exp_wrong)
    # F: consensus favouring the wrong frame (two confident reads of another strand + one noisy read of the target)
    from vnxdna.v5.soft import frames as sf
    posts, exp_t = [], []
    for i in range(n):
        j = (i + 1) % n
        target = F[i].copy()
        pos = 4 * rng.choice(70, 10, replace=False)
        target[pos] = (target[pos] + 1) % 4
        q = np.full(280, 35)
        q[pos] = 12
        ev = [ss.observation_loglik(target, q, 0.005)] + [ss.observation_loglik(F[j], np.full(280, 30), 0.005)] * 2
        posts.append(ss.normalise(sf.consensus_evidence(ev)))
        exp_t.append(truth[i])
    run("F consensus favours another strand (expected = target address)", posts, expected=exp_t)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["exp02", "exp04", "exp05", "exp06", "all"])
    ap.add_argument("--frames", type=int, default=500)
    a = ap.parse_args()
    todo = ["exp02", "exp04", "exp05", "exp06"] if a.which == "all" else [a.which]
    names = {"exp02": "P4-EXP-02-multiple-substitutions", "exp04": "P4-EXP-04-rs-boundary", "exp05": "P4-EXP-05-posterior-shapes",
             "exp06": "P4-EXP-06-adversarial"}
    for w in todo:
        t = time.perf_counter()
        res = globals()[w](a.frames)
        cfg = {"layout": LAY.to_dict(), "frames_per_cell": a.frames, "seed": SEED, "soft_defaults": sd.SoftDecodeConfig().__dict__,
               "hard": "decode_frames (V4 inner RS + CRC), optional min_quality erasures"}
        pc.write_result(p4.HERE / names[w], names[w], cfg, {"summary": res, "wall_seconds": round(time.perf_counter() - t, 1)})


if __name__ == "__main__":
    main()
