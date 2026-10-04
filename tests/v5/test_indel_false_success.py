"""Adversarial tests (Phase 3 §20): smart recovery must recover correctly or return no result — never a wrong frame.

Each test checks the outputs against truth that the decoder never sees.
"""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.v4.frame import Parsed
from vnxdna.v4.sync import TemplateAligner
from vnxdna.v5.indel import recovery as rv
from vnxdna.v5.indel.consensus import consensus_recover

from .indel_support import LAY, TAG, inject, is_correct, recover, strands

S = strands(80, 9401)
GEOM = rv.Geometry(LAY)


def _no_false(rows, outs, S_=S):
    bad = [k for k, o in zip(rows.tolist(), outs) if o.accepted and not is_correct(o, S_, k)]
    assert not bad, f"false acceptance for reads {bad}"


def test_ambiguity_is_never_resolved_by_choice(monkeypatch):
    """Two hypotheses that verify to different frames (two candidates one base apart): the read is not accepted."""
    real = rv.decode_frames
    calls = {"n": 0}

    def two_answers(layout, frames, erasures, errors_only_retry=True):
        P = real(layout, frames, erasures, errors_only_retry=errors_only_retry)
        if P.ok.sum() >= 2:
            calls["n"] += 1
            pay = P.payload.copy()
            k = np.flatnonzero(P.ok)[-1]
            pay[k, 0] ^= 1                                   # a second verified frame that differs in one byte
            return Parsed(P.ok, P.kind, P.tag, P.group, P.symbol, pay, P.errata)
        return P
    monkeypatch.setattr(rv, "decode_frames", two_answers)
    read = inject(S["strands"][0], [("ins", 140, 2)])[0]
    _, _, _, outs = recover([read], cfg=rv.IndelRecoveryConfig(phases=("T1",)))
    assert calls["n"] and outs[0].phase == "ambiguous" and not outs[0].accepted


def test_heavily_damaged_reads_are_never_accepted_wrongly():
    """Many indels + substitutions + N calls: whatever is accepted must be right (RS bounded distance + CRC + unanimity)."""
    rng = np.random.default_rng(12)
    reads = []
    for i in range(80):
        ev = []
        used = set()
        for _ in range(int(rng.integers(3, 9))):
            p = int(rng.integers(0, 313))
            if p in used:
                continue
            used.add(p)
            r = rng.random()
            ev.append(("del", p) if r < 0.4 else ("ins", p, int(rng.integers(0, 4))) if r < 0.8 else ("sub", p, int(rng.integers(0, 5))))
        reads.append(inject(S["strands"][i], ev)[0])
    _, rows, _, outs = recover(reads, cfg=rv.IndelRecoveryConfig(false_accept_budget=1e-6))   # a looser budget: more trials
    _no_false(rows, outs)


def test_random_frames_with_valid_markers_are_rejected():
    """Template markers around random frame content (no valid codeword): no trial may verify."""
    rng = np.random.default_rng(13)
    tpl = GEOM.tpl
    reads = []
    for _ in range(60):
        s = rng.integers(0, 4, GEOM.T).astype(np.uint8)
        s[tpl >= 0] = tpl[tpl >= 0].astype(np.uint8)
        p = int(rng.integers(0, GEOM.T))
        s = np.delete(s, p) if rng.random() < 0.5 else np.insert(s, p, np.uint8(rng.integers(0, 4)))
        reads.append(s)
    _, _, _, outs = recover(reads, cfg=rv.IndelRecoveryConfig(false_accept_budget=1e-6))
    assert not any(o.accepted for o in outs)


def test_misleading_quality_cannot_force_a_wrong_frame():
    """Quality says a correct base is the inserted one (quality disagrees with the truth)."""
    reads, quals = [], []
    for i in range(20):
        read, q = inject(S["strands"][i], [("ins", 100 + i, 1)])
        q = q.copy()
        q[100 + i + 5] = 2                                     # a correct base flagged as very likely wrong
        reads.append(read)
        quals.append(q)
    _, rows, _, outs = recover(reads, quals)
    _no_false(rows, outs)


@pytest.mark.parametrize("side", ["before", "after"])
def test_indel_next_to_a_corrupted_marker(side):
    """Marker context is ambiguous: a marker substitution next to an indel (the window may merge or misplace)."""
    rng = np.random.default_rng(14)
    reads = []
    for i, (t0, t1) in enumerate(GEOM.markers * 2):
        p = t0 - 1 if side == "before" else t1
        ev = [("sub", t0 + int(rng.integers(0, t1 - t0)), int(rng.integers(0, 4))),
              ("del", p) if i % 2 else ("ins", p, int(rng.integers(0, 4)))]
        reads.append(inject(S["strands"][i], ev)[0])
    _, rows, _, outs = recover(reads)
    _no_false(rows, outs)


def test_chance_marker_copy_inside_a_segment():
    """A substitution writes the next marker's motif one base early, next to a deletion: a plausible false anchor."""
    reads = []
    for i, (t0, t1) in enumerate(GEOM.markers):
        motif = GEOM.tpl[t0:t1].astype(np.uint8)
        ev = [("sub", t0 - len(motif) - 1 + k, int(motif[k])) for k in range(len(motif))] + [("del", t0 - 8)]
        reads.append(inject(S["strands"][i], ev)[0])
    _, rows, _, outs = recover(reads)
    _no_false(rows, outs)


def test_corrupted_majority_in_consensus():
    """Two reads of another strand plus one read of the target, all claiming the target's address: the result is either
    the target's payload or nothing (the group address check and unanimity), never the majority's payload."""
    al = TemplateAligner(LAY, 6)
    a = inject(S["strands"][0], [("del", 30), ("ins", 140, 1), ("del", 250)])[0]
    b1 = inject(S["strands"][1], [("del", 60)])[0]
    b2 = inject(S["strands"][1], [("ins", 200, 2)])[0]
    res = consensus_recover(GEOM, al, [b1, b2, a], None, rv.IndelRecoveryConfig(), expected=(0, TAG, 0, 0))
    if res.accepted:
        assert res.fields == (0, TAG, 0, 0) and np.array_equal(res.payload, S["payloads"][0])
    # and the majority strand is never returned under the target address
    assert not (res.accepted and np.array_equal(res.payload, S["payloads"][1]))


def test_one_corrupted_read_cannot_override_two_agreeing_reads():
    al = TemplateAligner(LAY, 6)
    good1 = inject(S["strands"][2], [("del", 40), ("ins", 150, 3), ("del", 260)])[0]
    good2 = inject(S["strands"][2], [("ins", 70, 0), ("del", 180), ("ins", 290, 1)])[0]
    rogue = inject(S["strands"][3], [])[0]                       # a different strand, clean (strong, but wrong)
    res = consensus_recover(GEOM, al, [rogue, good1, good2], None, rv.IndelRecoveryConfig(), expected=(0, TAG, 0, 2))
    assert res.accepted and np.array_equal(res.payload, S["payloads"][2])


def test_consensus_never_accepts_a_wrong_address():
    al = TemplateAligner(LAY, 6)
    reads = [inject(S["strands"][4], [("del", 30 + 40 * k)])[0] for k in range(3)]
    res = consensus_recover(GEOM, al, reads, None, rv.IndelRecoveryConfig(), expected=(0, TAG, 0, 5))   # truth is symbol 4
    assert not res.accepted


@pytest.mark.parametrize("seed", range(3))
def test_consensus_fuzz_no_false_recovery(seed):
    """Random groups (2–5 reads, mixed indels/substitutions, sometimes a foreign read): accepted ⇒ correct."""
    rng = np.random.default_rng(100 + seed)
    al = TemplateAligner(LAY, 6)
    for g in range(12):
        target = int(rng.integers(0, 70))
        reads = []
        for _ in range(int(rng.integers(2, 6))):
            src = target if rng.random() > 0.2 else int(rng.integers(0, 70))
            ev = []
            for _ in range(int(rng.integers(1, 6))):
                p = int(rng.integers(0, 313))
                r = rng.random()
                ev.append(("del", p) if r < 0.4 else ("ins", p, int(rng.integers(0, 4))) if r < 0.8 else ("sub", p, int(rng.integers(0, 4))))
            reads.append(inject(S["strands"][src], list({e[1]: e for e in ev}.values()))[0])
        exp = (0, TAG, int(S["groups"][target]), int(S["symbols"][target]))
        res = consensus_recover(GEOM, al, reads, None, rv.IndelRecoveryConfig(), expected=exp)
        if res.accepted:
            assert res.fields == exp and np.array_equal(res.payload, S["payloads"][target]), (seed, g)
