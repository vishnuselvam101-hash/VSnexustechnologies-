"""Helpers for the V5 Phase 3 (smart indel recovery) tests: real V4 strands and controlled edits with known truth."""
from __future__ import annotations

import numpy as np

from vnxdna.v4.constraints import ConstraintConfig
from vnxdna.v4.frame import KIND_DATA, PROFILES, build_strands
from vnxdna.v4.sync import TemplateAligner
from vnxdna.v5.indel import recovery as rv
from vnxdna.v5.indel.path import align_with_path

TAG = 0x5A5A
LAY = PROFILES["v4-balanced"][0]


def strands(n: int, seed: int, layout=LAY) -> dict:
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (n, layout.payload_bytes), dtype=np.uint8)
    groups = np.arange(n, dtype=np.int64) // 80
    symbols = np.arange(n, dtype=np.int64) % 80
    s, _ = build_strands(layout, ConstraintConfig(), TAG, KIND_DATA, groups, symbols, payloads)
    return {"strands": s, "payloads": payloads, "groups": groups, "symbols": symbols}


def inject(strand: np.ndarray, events: list[tuple], q_err: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """events in strand coordinates: ("del", p) | ("ins", p, base) before p | ("sub", p, base); applied right to left."""
    seq = [int(b) for b in strand]
    err = [False] * len(seq)
    for ev in sorted(events, key=lambda e: (e[1], 0 if e[0] == "sub" else 1), reverse=True):
        if ev[0] == "del":
            del seq[ev[1]]
            del err[ev[1]]
        elif ev[0] == "ins":
            seq.insert(ev[1], int(ev[2]))
            err.insert(ev[1], True)
        else:
            seq[ev[1]] = int(ev[2])
            err[ev[1]] = True
    read = np.asarray(seq, dtype=np.uint8)
    q = np.full(read.size, 35, dtype=np.uint8)
    if q_err is not None:
        q[np.asarray(err, dtype=bool)] = q_err
    return read, q


def recover(reads: list, quals=None, cfg: rv.IndelRecoveryConfig | None = None, layout=LAY, band: int = 6):
    cfg = cfg or rv.IndelRecoveryConfig()
    geom = rv.Geometry(layout)
    al = TemplateAligner(layout, band)
    proj, rpos = align_with_path(al, reads, quals)
    rows = np.flatnonzero(proj.ok)
    plans = rv.plan_reads(geom, reads, quals, proj, rpos, rows, cfg)
    outs = rv.recover_batch(geom, plans, cfg)
    return proj, rows, plans, outs


def is_correct(o: rv.ReadOutcome, S: dict, i: int) -> bool:
    return bool(o.accepted and o.fields == (0, TAG, int(S["groups"][i]), int(S["symbols"][i]))
                and np.array_equal(o.payload, S["payloads"][i]))
