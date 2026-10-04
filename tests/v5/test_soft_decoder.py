"""Phase 4 bounded soft decoding: recovery, bounds, determinism, and the adversarial list of mission §14.

Truth (the original frame) is used only in assertions; the decoder sees posteriors and nothing else.
"""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.v4.errors import VNXConfigurationError
from vnxdna.v4.frame import Parsed, decode_frames, nt_to_bytes
from vnxdna.v5.indel.recovery import Geometry
from vnxdna.v5.soft import decoder as sd
from vnxdna.v5.soft import symbols as ss

from .indel_support import LAY, TAG, strands

S = strands(120, 9501)
GEOM = Geometry(LAY)
FRAMES = S["strands"][:, GEOM.frame_pos]                  # true frame bases


def _noisy(i, n_err_bytes, rng, mark_errors=True, q_bad=12, q_good=35):
    fb = FRAMES[i].copy()
    q = np.full(fb.size, q_good, np.uint8)
    for b in rng.choice(70, n_err_bytes, replace=False):
        p = 4 * int(b) + int(rng.integers(0, 4))
        fb[p] = (fb[p] + int(rng.integers(1, 4))) % 4
        if mark_errors:
            q[p] = q_bad
    return fb, q


def _ok(o, i):
    return o.accepted and o.fields == (0, TAG, int(S["groups"][i]), int(S["symbols"][i])) and np.array_equal(o.payload, S["payloads"][i])


def _assert_no_false(outs, idx):
    bad = [i for o, i in zip(outs, idx) if o.accepted and not _ok(o, i)]
    assert not bad, f"false acceptance: {bad}"


# ---------------------------------------------------------------- recovery and bounds
@pytest.mark.parametrize("mode", ["erasure", "chase", "auto"])
def test_informative_soft_information_recovers_beyond_hard(mode):
    rng = np.random.default_rng(1)
    idx = list(range(40))
    posts, hard_ok = [], 0
    for i in idx:
        fb, q = _noisy(i, 10, rng)                           # 10 byte errors > t = 8: hard decoding fails
        posts.append(ss.from_read(fb, q, 0.005))
        hard_ok += int(decode_frames(LAY, nt_to_bytes(fb[None, :]), None).ok[0])
    outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode=mode))
    _assert_no_false(outs, idx)
    assert hard_ok == 0
    good = sum(_ok(o, i) for o, i in zip(outs, idx))
    assert good >= (30 if mode != "chase" else 1)
    assert all(o.trials <= sd.SoftDecodeConfig().max_soft_trials for o in outs)


def test_no_soft_information_means_no_magic():
    """Uniform qualities: GMD cannot rank bytes; beyond t = 8 errors nothing (correct) is gained at 12 errors."""
    rng = np.random.default_rng(2)
    idx = list(range(30))
    posts = [ss.from_read(*_noisy(i, 12, rng, mark_errors=False), 0.005) for i in idx]
    outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode="auto"))
    _assert_no_false(outs, idx)
    assert sum(o.accepted for o in outs) == 0


@pytest.mark.parametrize("kw,limit", [({"max_soft_trials": 1}, 1), ({"max_soft_trials": 5}, 5),
                                      ({"chase_bytes": 8, "chase_values": 4, "max_soft_trials": 4096}, 4096)])
def test_trial_bounds_are_hard_limits(kw, limit):
    rng = np.random.default_rng(3)
    posts = [ss.from_read(*_noisy(i, 12, rng), 0.005) for i in range(6)]
    outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode="auto", **kw))
    assert all(o.trials <= limit for o in outs)
    assert all(o.false_accept_bound <= sd.SoftDecodeConfig().false_accept_budget * (1 + 1e-9) for o in outs)


def test_extremely_long_candidate_lists_are_cut():
    """8 bytes × 4 values = 65,536 patterns: generation stops at max_soft_trials and max_bytes_per_trial."""
    rng = np.random.default_rng(4)
    post = ss.from_read(*_noisy(0, 12, rng), 0.005)
    cfg = sd.SoftDecodeConfig(mode="chase", chase_bytes=8, chase_values=4, max_soft_trials=300, max_bytes_per_trial=2)
    o = sd.soft_decode(LAY, [post], cfg)[0]
    assert o.trials <= 300


def test_deterministic():
    rng = np.random.default_rng(5)
    posts = [ss.from_read(*_noisy(i, int(rng.integers(6, 13)), rng), 0.005) for i in range(30)]
    a = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode="auto"))
    b = sd.soft_decode(LAY, [p.copy() for p in posts], sd.SoftDecodeConfig(mode="auto"))
    for x, y in zip(a, b):
        assert (x.accepted, x.mode, x.trials, x.accepted_rank, x.erased_bytes) == (y.accepted, y.mode, y.trials, y.accepted_rank,
                                                                                    y.erased_bytes)
        if x.accepted:
            assert np.array_equal(x.payload, y.payload)


@pytest.mark.parametrize("kw", [{"mode": "magic"}, {"chase_bytes": 9}, {"chase_values": 1}, {"max_soft_trials": 0},
                                {"max_bytes_per_trial": 0}, {"false_accept_budget": 0.5}, {"default_error": 0.0}])
def test_config_validation(kw):
    with pytest.raises(VNXConfigurationError):
        sd.SoftDecodeConfig(**kw).validate()


# ---------------------------------------------------------------- RS boundary honesty (mission §13)
@pytest.mark.parametrize("e,f", [(8, 0), (7, 2), (6, 4), (9, 0), (8, 1), (7, 3)])
def test_hard_rs_bound_is_unchanged(e, f):
    """The hard decoder is untouched: 2e + f ≤ 16 decodes, beyond it does not."""
    rng = np.random.default_rng(10 + e + f)
    i = 7
    word = nt_to_bytes(FRAMES[i][None, :])[0].copy()
    pos = rng.choice(70, e + f, replace=False)
    for p in pos[:e]:
        word[p] ^= int(rng.integers(1, 256))
    er = np.zeros(70, bool)
    er[pos[e:]] = True
    word[pos[e:]] ^= 0x5A
    ok = decode_frames(LAY, word[None, :], er[None, :], errors_only_retry=False).ok[0]
    assert ok == (2 * e + f <= 16)


# ---------------------------------------------------------------- adversarial (§14)
def test_1_wrong_high_probability_candidate():
    """Soft evidence says the *correct* bytes are unreliable and the errors are certain: GMD erases correct bytes."""
    rng = np.random.default_rng(11)
    idx = list(range(20))
    posts = []
    for i in idx:
        fb, _ = _noisy(i, 10, rng, mark_errors=False)
        q = np.full(fb.size, 60, np.uint8)
        q[(fb == FRAMES[i])] = 5                               # forged: correct bases low quality
        q[(fb != FRAMES[i])] = 60                              # wrong bases very high quality
        posts.append(ss.from_read(fb, q, 0.005))
    outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode="auto"))
    _assert_no_false(outs, idx)


def test_2_correct_low_probability_candidate():
    """The correct byte value is the *second* most likely at the erroneous positions: Chase may find it; never wrong."""
    rng = np.random.default_rng(12)
    idx = list(range(20))
    posts = []
    for i in idx:
        fb, _ = _noisy(i, 9, rng, mark_errors=False)
        P = np.full((fb.size, 4), 1e-4)
        P[np.arange(fb.size), fb] = 1 - 3e-4
        wrong = np.flatnonzero(fb != FRAMES[i])
        P[wrong] = 0.0
        P[wrong, fb[wrong]] = 0.55
        P[wrong, FRAMES[i][wrong]] = 0.45                     # truth is the runner-up
        posts.append(ss.from_probabilities(P))
    outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode="chase", chase_bytes=8, max_soft_trials=512))
    _assert_no_false(outs, idx)
    assert sum(_ok(o, i) for o, i in zip(outs, idx)) > 0


@pytest.mark.parametrize("differ", ["payload", "address"])
def test_3_4_two_verified_candidates_are_rejected(monkeypatch, differ):
    """Two RS-valid, CRC-valid candidates that disagree → ambiguous, never ranked by likelihood."""
    real = sd.decode_frames

    def two(layout, words, ers, errors_only_retry=True):
        P = real(layout, words, ers, errors_only_retry=errors_only_retry)
        okk = np.flatnonzero(P.ok)
        if okk.size >= 2:
            pay, sym = P.payload.copy(), P.symbol.copy()
            if differ == "payload":
                pay[okk[-1], 3] ^= 0x40
            else:
                sym[okk[-1]] += 1
            return Parsed(P.ok, P.kind, P.tag, P.group, sym, pay, P.errata)
        return P
    monkeypatch.setattr(sd, "decode_frames", two)
    rng = np.random.default_rng(13)
    post = ss.from_read(*_noisy(3, 4, rng), 0.005)
    o = sd.soft_decode(LAY, [post], sd.SoftDecodeConfig(mode="erasure"))[0]
    assert o.mode == "ambiguous" and not o.accepted and o.distinct_verified == 2


def test_5_conflicting_posterior_and_consensus():
    from vnxdna.v5.soft import frames as sf
    rng = np.random.default_rng(14)
    good = ss.observation_loglik(*_noisy(5, 9, rng)[::1], 0.005)
    other = ss.observation_loglik(FRAMES[6], np.full(280, 40, np.uint8), 0.005)   # a different frame, confidently
    post = ss.normalise(sf.consensus_evidence([good, other]))
    outs = sd.soft_decode(LAY, [post, post], sd.SoftDecodeConfig(mode="auto"),
                          expected=[(0, TAG, int(S["groups"][5]), int(S["symbols"][5])), None])
    assert not (outs[0].accepted and not _ok(outs[0], 5))
    assert not (outs[1].accepted and not (_ok(outs[1], 5) or _ok(outs[1], 6)))


def test_6_7_forged_qualities_all_high_but_wrong():
    rng = np.random.default_rng(15)
    idx = list(range(15))
    posts = []
    for i in idx:
        fb = rng.integers(0, 4, 280).astype(np.uint8)          # a random frame, every base at Q93
        posts.append(ss.from_read(fb, np.full(280, 93, np.uint8), 0.005))
    outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode="auto"))
    assert not any(o.accepted for o in outs)


@pytest.mark.parametrize("make", [
    lambda L: np.where(np.arange(L.size).reshape(L.shape) == 5, np.nan, L),          # 9 NaN
    lambda L: np.where(np.arange(L.size).reshape(L.shape) == 5, np.inf, L),          # 10 Inf
    lambda L: L + 0.3,                                                               # 8/11 not normalised
    lambda L: np.full_like(L, -np.inf),                                              # 12 all zero probability
    lambda L: L[:-4],                                                                # 15 length mismatch
    lambda L: L[:, :3],                                                              # 14 malformed
    lambda L: L.astype(np.int64),                                                    # 14 wrong type
])
def test_8_to_15_malformed_soft_input_fails_closed(make):
    rng = np.random.default_rng(16)
    L = ss.from_read(*_noisy(1, 3, rng), 0.005)
    o = sd.soft_decode(LAY, [make(L)], sd.SoftDecodeConfig(mode="auto"))[0]
    assert o.mode == "invalid" and not o.accepted and o.trials == 0


def test_10_negative_probabilities_rejected():
    with pytest.raises(ss.SoftInputError):
        ss.from_probabilities(np.array([[1.2, -0.2, 0.0, 0.0]]))


def test_12_13_zero_and_tiny_probabilities_are_valid_and_safe():
    rng = np.random.default_rng(17)
    fb, q = _noisy(2, 9, rng)
    L = ss.from_read(fb, q, 0.005)
    L = ss.normalise(np.where(L < -3, -700.0, L))             # extremely small (denormal-range) probabilities
    o = sd.soft_decode(LAY, [L], sd.SoftDecodeConfig(mode="auto"))[0]
    assert not (o.accepted and not _ok(o, 2))


def test_16_wrong_expected_address_is_never_accepted():
    rng = np.random.default_rng(18)
    post = ss.from_read(*_noisy(4, 5, rng), 0.005)
    o = sd.soft_decode(LAY, [post], sd.SoftDecodeConfig(mode="auto"), expected=[(0, TAG, 0, 99)])[0]
    assert not o.accepted


def test_17_corrupted_marker_plus_misleading_soft_evidence():
    """End to end through alignment and read evidence: a corrupted marker next to an indel and forged qualities."""
    from vnxdna.v4.sync import TemplateAligner
    from vnxdna.v5.indel import recovery as rv
    from vnxdna.v5.indel.path import align_with_path
    from vnxdna.v5.soft import frames as sf
    from .indel_support import inject
    al = TemplateAligner(LAY, 6)
    reads, quals, idx = [], [], []
    for i, (t0, t1) in enumerate(GEOM.markers):
        r, q = inject(S["strands"][i], [("sub", t0 + 1, (int(S["strands"][i][t0 + 1]) + 1) % 4), ("del", t0 - 3),
                                        ("sub", 40, 0), ("sub", 120, 1), ("sub", 200, 2)])
        q = q.copy()
        q[::7] = 3                                              # misleading: random correct bases flagged bad
        reads.append(r)
        quals.append(q)
        idx.append(i)
    proj, rpos = align_with_path(al, reads, quals)
    cfg = rv.IndelRecoveryConfig()
    posts, keep = [], []
    for j in np.flatnonzero(proj.ok).tolist():
        plan = rv.plan_read(GEOM, reads[j], quals[j], proj.bases[j], proj.erased[j], rpos[j], cfg)
        posts.append(ss.normalise(sf.read_evidence(GEOM, reads[j], quals[j], proj.bases[j], proj.erased[j], rpos[j], plan, 0.005)))
        keep.append(idx[j])
    outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode="auto"))
    _assert_no_false(outs, keep)


def test_random_fuzz_no_false_acceptance():
    """Random damage levels and random (uninformative or informative) qualities: accepted ⇒ correct."""
    rng = np.random.default_rng(19)
    idx, posts = [], []
    for k in range(200):
        i = k % 120
        fb, q = _noisy(i, int(rng.integers(0, 20)), rng, mark_errors=bool(rng.random() < 0.5))
        if rng.random() < 0.3:
            q = rng.integers(2, 41, q.size).astype(np.uint8)
        posts.append(ss.from_read(fb, q, 0.005))
        idx.append(i)
    outs = sd.soft_decode(LAY, posts, sd.SoftDecodeConfig(mode="auto", false_accept_budget=1e-6))
    _assert_no_false(outs, idx)
