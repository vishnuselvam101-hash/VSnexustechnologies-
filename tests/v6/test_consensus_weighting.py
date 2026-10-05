"""V6 Phase 4: opt-in quality-weighted pass-2 consensus (``DecodeOptions.consensus_weighting = "quality"``).

The default ("count") is the V4 vote and must stay bit-for-bit unchanged: same pending records, same report. The
"quality" vote weights each projected read base by its own Phred quality (a Phred-interpreted log-likelihood, NOT a
calibrated probability) and falls back to the count vote for any address group whose reads lack qualities. Every
consensus frame is still RS- and CRC-verified and the container SHA-256 decides SUCCESS, so a wrong vote can only cost
a recovery, never produce wrong output. SIMULATED channel; SYNTHETIC test data.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.recovery import consensus as cs
from vnxdna.recovery import spill as sp
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.errors import VNXError


# ----------------------------------------------------------------------------------------------- the vote itself
def test_weighted_vote_prefers_the_high_quality_base_where_the_count_vote_erases():
    bases = np.array([[0, 1, 2], [1, 1, 2]], dtype=np.uint8)
    quals = np.array([[30, 30, 30], [8, 30, 30]], dtype=np.uint8)
    hb, he = cs.consensus_hard(bases, 0.6)
    assert he.tolist() == [True, False, False]                 # 1 vs 1: the count vote erases
    qb, qe = cs.consensus_quality_weighted(bases, quals, 0.6)
    assert qe.tolist() == [False, False, False]
    assert qb.tolist() == [0, 1, 2]                            # Q30 beats Q8


def test_weighted_vote_erases_ties_and_empty_columns():
    bases = np.array([[0, 4, 3], [1, 4, 3]], dtype=np.uint8)
    quals = np.array([[30, 30, 20], [30, 30, 20]], dtype=np.uint8)
    qb, qe = cs.consensus_quality_weighted(bases, quals, 0.6)
    assert qe.tolist() == [True, True, False]
    assert int(qb[2]) == 3


def test_weighted_vote_with_equal_qualities_agrees_with_clear_majorities():
    rng = np.random.default_rng(11)
    truth = rng.integers(0, 4, 200).astype(np.uint8)
    bases = np.repeat(truth[None, :], 5, axis=0)
    flip = rng.random(bases.shape) < 0.1
    bases[flip] = (bases[flip] + 1) % 4
    quals = np.full(bases.shape, 30, dtype=np.uint8)
    hb, he = cs.consensus_hard(bases, 0.6)
    qb, qe = cs.consensus_quality_weighted(bases, quals, 0.6)
    # wherever the count vote decides (share >= 0.6), the weighted vote decides the same base
    assert (qb[~he] == hb[~he]).all() and not qe[~he].any()


def test_weighted_vote_validates_shapes():
    with pytest.raises(ValueError):
        cs.consensus_quality_weighted(np.zeros((2, 3), np.uint8), np.zeros((2, 4), np.uint8), 0.6)


def test_option_is_validated_and_defaults_to_count():
    assert de.DecodeOptions().consensus_weighting == "count"
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(consensus_weighting="posterior").validate()


def test_pending_records_are_unchanged_by_default():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        s0 = sp.Spill(Path(d), 1, 40, 280)
        s1 = sp.Spill(Path(d), 1, 40, 280, quality_nt=280)
        try:
            assert "pq" not in s0.pend_dtype.names and "pq" in s1.pend_dtype.names
            assert s0.pend_dtype.itemsize + 281 == s1.pend_dtype.itemsize
        finally:
            s0.close()
            s1.close()


def test_groups_without_qualities_fall_back_to_the_count_vote():
    """FASTA reads (no qualities): the quality option must give exactly the count vote and record the fallback."""
    from vnxdna.dnaenc.layout import PROFILES
    lay = PROFILES["v4-balanced"][0]
    dt = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("alt", ">i8", (4,)),
                   ("bases", "u1", (lay.frame_nt,)), ("pq", "u1", (lay.frame_nt,)), ("pqok", "u1")])
    rng = np.random.default_rng(5)
    pend = np.zeros(6, dtype=dt)
    pend["kind"], pend["tag"], pend["group"], pend["symbol"] = 4, 7, 0, np.array([0, 0, 0, 1, 1, 1])
    pend["alt"] = np.stack([pend["kind"], pend["tag"], pend["group"], pend["symbol"]], axis=1)
    pend["bases"] = rng.integers(0, 5, (6, lay.frame_nt))
    pend["pqok"] = 0
    missing = {(4, 7, 0, 0), (4, 7, 0, 1)}
    s_count, s_q = Counter(), Counter()
    a = cs._consensus_symbols(pend, {}, lay, de.DecodeOptions(), s_count, missing)
    b = cs._consensus_symbols(pend, {}, lay, de.DecodeOptions(consensus_weighting="quality"), s_q, missing)
    assert a.keys() == b.keys()
    assert s_q["consensus_weighting_fallback"] == 2 and s_q["consensus_weighted"] == 0
    assert "consensus_weighting_fallback" not in s_count


# ----------------------------------------------------------------------------------------------- end to end
@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    d = tmp_path_factory.mktemp("qw")
    datagen.generate(d / "in.bin", 30_000, "random", 4501)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    out = {"dir": d, "container": (d / "a.vnx").read_bytes()}
    for name, cfg in {"informative": dict(quality_informative=0.8),
                      "inverted": dict(quality_correct=6, quality_error=40, quality_informative=1.0)}.items():
        ch.simulate_file(d / "s.fasta", d / f"{name}.fastq",
                         ch.ChannelConfig(substitution_rate=0.02, insertion_rate=0.003, deletion_rate=0.003, coverage=2.5,
                                          coverage_model="poisson", seed=4502, **cfg))
        out[name] = d / f"{name}.fastq"
    return out


def _decode(path, out, **kw):
    try:
        res = de.decode_reads(path, out, de.DecodeOptions(**kw), overwrite=True)
    except VNXError as error:
        return f"ERROR:{type(error).__name__}", None, {}
    return res.status, (out.read_bytes() if out.exists() else None), res.report


def test_quality_weighting_is_deterministic_across_workers_and_never_returns_wrong_data(pool, tmp_path):
    s1, c1, r1 = _decode(pool["informative"], tmp_path / "w1.vnx", consensus_weighting="quality", workers=1)
    s2, c2, r2 = _decode(pool["informative"], tmp_path / "w2.vnx", consensus_weighting="quality", workers=2)
    assert (s1, c1) == (s2, c2)
    assert r1["reads"] == r2["reads"]
    if s1 == "SUCCESS":
        assert c1 == pool["container"]
    assert r1["reads"]["consensus_weighted"] > 0


def test_misleading_qualities_cannot_produce_a_false_success(pool, tmp_path):
    """Qualities inverted (every error Q40, every correct base Q6): the weighted vote is misled, verification is not."""
    for i, kw in enumerate(({}, {"consensus_weighting": "quality"})):
        status, container, _ = _decode(pool["inverted"], tmp_path / f"inv{i}.vnx", **kw)
        if status == "SUCCESS":
            assert container == pool["container"]
        else:
            assert container is None


def test_default_report_has_no_weighting_keys(pool, tmp_path):
    _, _, rep = _decode(pool["informative"], tmp_path / "d.vnx")
    assert not any(k.startswith("consensus_weight") for k in rep["reads"])


def test_config_file_and_cli_option_reach_the_decoder(tmp_path):
    import json
    from vnxdna.sdk.config import decode_options_for
    assert decode_options_for(None, consensus_weighting="quality").consensus_weighting == "quality"
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"decode": {"consensus_weighting": "quality"}}))
    assert decode_options_for(cfg).consensus_weighting == "quality"
    assert decode_options_for(None).consensus_weighting == "count"
