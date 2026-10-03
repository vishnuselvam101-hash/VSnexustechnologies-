"""Phase 4 decoder integration: soft decoding is opt-in, bounded, verified end to end and worker-invariant."""
from __future__ import annotations

import hashlib
import json

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.errors import VNXConfigurationError
from vnxdna.v5.soft.decoder import SoftDecodeConfig


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    d = tmp_path_factory.mktemp("p4pool")
    src = d / "in.bin"
    sha = datagen.generate(src, 120_000, "random", 31)
    ar.build_archive([src], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    return d, sha


def _reads(pool, name, fmt="fastq", **kw):
    d, _ = pool
    out = d / f"{name}.{fmt}"
    if not out.exists():
        ch.simulate_file(d / "s.fasta", out, ch.ChannelConfig(**kw))
    return out


def _decode(path, out, **kw):
    res = de.decode_reads(path, out, de.DecodeOptions(**kw), overwrite=True)
    rep = {k: v for k, v in res.report.items() if k not in ("stage_seconds", "seconds", "peak_rss_bytes")}
    return res.status, rep


SUBS = dict(substitution_rate=0.03, quality_informative=0.8, coverage=1, seed=41)


def test_default_is_off(pool, tmp_path):
    reads = _reads(pool, "subs", **SUBS)
    st, rep = _decode(reads, tmp_path / "a.vnx", workers=1)
    assert "soft_decoding" not in rep and "soft" not in rep["reads"]
    assert de.DecodeOptions().soft_decoding == "off"


@pytest.mark.parametrize("mode", ["erasure", "chase", "auto"])
def test_soft_recovers_more_frames_and_stays_verified(pool, tmp_path, mode):
    _, sha = pool
    reads = _reads(pool, "subs", **SUBS)
    st4, r4 = _decode(reads, tmp_path / "v4.vnx", workers=1)
    st5, r5 = _decode(reads, tmp_path / "v5.vnx", workers=1, soft_decoding=mode)
    assert r5["soft_decoding"]["mode"] == mode
    assert r5["reads"]["soft"] > 0
    assert r5.get("groups_failed", 0) <= r4.get("groups_failed", 0)
    if st5 == "SUCCESS":
        ar.extract(tmp_path / "v5.vnx", tmp_path / "out")
        assert hashlib.sha256((tmp_path / "out" / "in.bin").read_bytes()).hexdigest() == sha


def test_with_smart_indel_recovery(pool, tmp_path):
    reads = _reads(pool, "mixed", substitution_rate=0.008, insertion_rate=0.003, deletion_rate=0.003, quality_informative=0.8,
                   coverage=1, seed=42)
    s3, r3 = _decode(reads, tmp_path / "h.vnx", workers=1, indel_recovery="smart")
    s4, r4 = _decode(reads, tmp_path / "s.vnx", workers=1, indel_recovery="smart", soft_decoding="auto")
    assert r4.get("groups_failed", 0) <= r3.get("groups_failed", 0)
    assert "indel_recovery" in r4 and "soft_decoding" in r4


def test_worker_invariance(pool, tmp_path):
    reads = _reads(pool, "subs", **SUBS)
    a = _decode(reads, tmp_path / "w1.vnx", workers=1, soft_decoding="auto", batch_reads=600)
    b = _decode(reads, tmp_path / "w3.vnx", workers=3, soft_decoding="auto", batch_reads=600)
    assert a[0] == b[0]
    assert json.dumps(a[1]["reads"], sort_keys=True) == json.dumps(b[1]["reads"], sort_keys=True)
    sa = {k: v for k, v in a[1]["soft_decoding"].items() if k not in ("false_accept_bound", "entropy_bits_sum")}
    sb = {k: v for k, v in b[1]["soft_decoding"].items() if k not in ("false_accept_bound", "entropy_bits_sum")}
    assert sa == sb


def test_fasta_without_qualities(pool, tmp_path):
    reads = _reads(pool, "subsfa", fmt="fasta", substitution_rate=0.03, coverage=1, seed=41)
    st, rep = _decode(reads, tmp_path / "f.vnx", workers=1, soft_decoding="auto")
    assert "soft_decoding" in rep


def test_soft_consensus_runs(pool, tmp_path):
    reads = _reads(pool, "cov3", substitution_rate=0.035, insertion_rate=0.004, deletion_rate=0.004, quality_informative=0.5,
                   coverage=3, seed=43)
    st, rep = _decode(reads, tmp_path / "c.vnx", workers=1, indel_recovery="smart", soft_decoding="auto")
    assert "consensus_recovered_soft" in rep["reads"]          # pass 2 ran the soft consensus stage
    assert rep["reads"]["consensus_recovered_soft"] >= 0


@pytest.mark.parametrize("kw", [{"soft_decoding": "magic"}, {"soft_decoding": "auto", "soft_config": {"x": 1}},
                                {"soft_decoding": "chase", "soft_config": SoftDecodeConfig(chase_bytes=99)}])
def test_option_validation(kw):
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(**kw).validate()


def test_mode_from_option_overrides_config_mode():
    o = de.DecodeOptions(soft_decoding="erasure", soft_config=SoftDecodeConfig(mode="chase", chase_bytes=3))
    o.validate()
    assert o.soft_config.mode == "erasure" and o.soft_config.chase_bytes == 3


def test_cli_flag(pool, tmp_path):
    from typer.testing import CliRunner
    from vnxdna.v4.cli import app
    reads = _reads(pool, "subs", **SUBS)
    res = CliRunner().invoke(app, ["decode", str(reads), "-o", str(tmp_path / "x.vnx"), "--indel-recovery", "smart",
                                   "--soft-decoding", "chase", "-w", "1"])
    out = json.loads(res.output)                                  # exit code follows SUCCESS / PARTIAL / FAILURE
    assert out["soft_decoding"]["mode"] == "chase" and out["indel_recovery"]["mode"] == "smart"
    bad = CliRunner().invoke(app, ["decode", str(reads), "-o", str(tmp_path / "y.vnx"), "--soft-decoding", "nope"])
    assert bad.exit_code != 0
