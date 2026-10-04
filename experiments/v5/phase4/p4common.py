"""Shared harness for the V5 Phase 4 experiments (soft-decision inner decoding). All results are SIMULATED.

Decoder configurations compared on *identical* reads (never different channel realisations):

  V4          V4 decoder (segment erasure, no quality use)
  V4+minQ     V4 with min_quality = 13: bases below Q13 become erasures (V4's existing hard use of qualities;
              the fairest hard baseline when qualities are informative)
  V5-hard     V5 Phase 3 smart indel recovery, hard decisions
  V5-soft-*   V5 Phase 3 + Phase 4 soft decoding (erasure = GMD, chase, auto)

Read-level acceptance comes from the decoder's own pass-1 function (``decoder._try``); the harness compares accepted
frames with the truth afterwards.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "experiments" / "v5" / "phase3"))
import p3common as pc  # noqa: E402,F401  (re-exported helpers: make_strands, inject, write_result, dist, sha)

from vnxdna.v4 import decoder as de  # noqa: E402
from vnxdna.v4.sync import SyncCosts  # noqa: E402
from vnxdna.v5.indel.recovery import IndelRecoveryConfig  # noqa: E402
from vnxdna.v5.soft.decoder import SoftDecodeConfig  # noqa: E402

CONFIGS = {
    "V4": dict(min_q=0, smart=False, soft=None),
    "V4+minQ": dict(min_q=13, smart=False, soft=None),
    "V4+minQ20": dict(min_q=20, smart=False, soft=None),
    "V5-hard": dict(min_q=0, smart=True, soft=None),
    "V5-hard+minQ20": dict(min_q=20, smart=True, soft=None),
    "V5-soft-erasure": dict(min_q=0, smart=True, soft="erasure"),
    "V5-soft-chase": dict(min_q=0, smart=True, soft="chase"),
    "V5-soft-auto": dict(min_q=0, smart=True, soft="auto"),
    "V5-soft-auto+minQ20": dict(min_q=20, smart=True, soft="auto"),
}


def pass1(layout, reads, quals, name, soft_cfg: SoftDecodeConfig | None = None):
    c = CONFIGS[name]
    de._P.clear()
    soft = None
    if c["soft"]:
        soft = (soft_cfg or SoftDecodeConfig()).__class__(**{**(soft_cfg or SoftDecodeConfig()).__dict__, "mode": c["soft"]})
    de._p_init(layout, 6, SyncCosts(), c["min_q"], False, IndelRecoveryConfig() if c["smart"] else None, soft)
    use_q = quals if (c["min_q"] or c["smart"] or soft is not None) else None
    acc, fields, payload, _, _, path, _, st = de._try(reads, use_q)
    return acc, fields, payload, path, dict(st)


def correct_mask(acc, fields, payload, truth_fields, truth_payloads):
    ok = np.zeros(acc.size, dtype=bool)
    for i in np.flatnonzero(acc).tolist():
        ok[i] = tuple(int(x) for x in fields[i]) == tuple(truth_fields[i]) and np.array_equal(payload[i], truth_payloads[i])
    return ok


def error_masks(strands: np.ndarray, source: np.ndarray, events: list) -> list[np.ndarray]:
    """Per read, True at every read base that is an error (inserted or substituted), from the truth events.
    Harness only (used to draw graded qualities); the decoder never sees it."""
    out = []
    for r, ev in enumerate(events):
        L = strands.shape[1]
        dels = {e[1] for e in ev if e[0] == "del"}
        ins = {}
        for e in ev:
            if e[0] == "ins":
                ins[e[1]] = ins.get(e[1], 0) + 1
        subs = {e[1] for e in ev if e[0] == "sub"}
        m = []
        for p in range(L):
            m.extend([True] * ins.get(p, 0))
            if p not in dels:
                m.append(p in subs)
        out.append(np.asarray(m, dtype=bool))
    return out


def graded_qualities(err_masks: list[np.ndarray], seed: int, correct=(20, 40), error=(4, 24)) -> list[np.ndarray]:
    """A synthetic graded quality model (NOT calibrated to any platform): correct bases Q ~ U{20..40}, erroneous bases
    Q ~ U{4..24}. The ranges overlap, so no single threshold separates errors from correct bases."""
    rng = np.random.default_rng(seed)
    out = []
    for m in err_masks:
        q = rng.integers(correct[0], correct[1] + 1, m.size)
        q[m] = rng.integers(error[0], error[1] + 1, int(m.sum()))
        out.append(q.astype(np.uint8))
    return out
