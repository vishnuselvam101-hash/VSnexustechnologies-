"""Phase 3 decoder integration: indel_recovery="smart" is opt-in, V4 stays the default, results are verified end to end."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.errors import VNXConfigurationError
from vnxdna.v5.indel.recovery import IndelRecoveryConfig


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    d = tmp_path_factory.mktemp("p3pool")
    src = d / "in.bin"
    sha = datagen.generate(src, 160_000, "random", 21)
    ar.build_archive([src], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    return d, sha


def _reads(pool, name, **kw):
    d, _ = pool
    out = d / f"{name}.fastq"
    if not out.exists():
        ch.simulate_file(d / "s.fasta", out, ch.ChannelConfig(**kw))
    return out


def _decode(path, out, **kw):
    res = de.decode_reads(path, out, de.DecodeOptions(**kw), overwrite=True)
    rep = {k: v for k, v in res.report.items() if k not in ("stage_seconds", "seconds", "peak_rss_bytes")}
    return res.status, rep


def test_default_is_v4(pool, tmp_path):
    reads = _reads(pool, "mild", insertion_rate=0.001, deletion_rate=0.001, substitution_rate=0.002, coverage=1, seed=5)
    st, rep = _decode(reads, tmp_path / "v4.vnx", workers=1)
    assert st == "SUCCESS" and "indel_recovery" not in rep and "smart" not in rep["reads"]
    assert de.DecodeOptions().indel_recovery == "segment"


def test_smart_equals_v4_where_v4_succeeds(pool, tmp_path):
    reads = _reads(pool, "mild", insertion_rate=0.001, deletion_rate=0.001, substitution_rate=0.002, coverage=1, seed=5)
    s4, r4 = _decode(reads, tmp_path / "v4.vnx", workers=1)
    s5, r5 = _decode(reads, tmp_path / "v5.vnx", workers=1, indel_recovery="smart")
    assert s4 == s5 == "SUCCESS"
    assert (tmp_path / "v4.vnx").read_bytes() == (tmp_path / "v5.vnx").read_bytes()
    assert r5["indel_recovery"]["mode"] == "smart"


def test_smart_recovers_beyond_the_v4_threshold(pool, tmp_path):
    """Coverage 1, 0.35 % insertions + 0.35 % deletions: above the V4 coverage-1 threshold (EXP-0008)."""
    _, sha = pool
    reads = _reads(pool, "hard", insertion_rate=0.0035, deletion_rate=0.0035, coverage=1, seed=8)
    s4, _ = _decode(reads, tmp_path / "v4.vnx", workers=1)
    s5, r5 = _decode(reads, tmp_path / "v5.vnx", workers=1, indel_recovery="smart")
    assert s4 != "SUCCESS"
    assert s5 == "SUCCESS" and r5["reads"]["smart"] > 0
    ar.extract(tmp_path / "v5.vnx", tmp_path / "out")
    assert hashlib.sha256((tmp_path / "out" / "in.bin").read_bytes()).hexdigest() == sha


def test_worker_count_does_not_change_the_result(pool, tmp_path):
    reads = _reads(pool, "hard", insertion_rate=0.0035, deletion_rate=0.0035, coverage=1, seed=8)
    a = _decode(reads, tmp_path / "w1.vnx", workers=1, indel_recovery="smart", batch_reads=700)
    b = _decode(reads, tmp_path / "w3.vnx", workers=3, indel_recovery="smart", batch_reads=700)
    assert a[0] == b[0]
    assert json.dumps(a[1]["reads"], sort_keys=True) == json.dumps(b[1]["reads"], sort_keys=True)
    ia = {k: v for k, v in a[1]["indel_recovery"].items() if k != "false_accept_bound"}
    ib = {k: v for k, v in b[1]["indel_recovery"].items() if k != "false_accept_bound"}
    assert ia == ib
    if a[0] == "SUCCESS":
        assert (tmp_path / "w1.vnx").read_bytes() == (tmp_path / "w3.vnx").read_bytes()


def test_quality_aware_input(pool, tmp_path):
    reads = _reads(pool, "hardq", insertion_rate=0.0035, deletion_rate=0.0035, coverage=1, quality_informative=1.0, seed=8)
    s5, r5 = _decode(reads, tmp_path / "q.vnx", workers=1, indel_recovery="smart")
    assert s5 == "SUCCESS"


def test_consensus_realignment_runs_and_is_verified(pool, tmp_path):
    _, sha = pool
    reads = _reads(pool, "cov3", insertion_rate=0.008, deletion_rate=0.008, coverage=3, seed=9)
    s5, r5 = _decode(reads, tmp_path / "c.vnx", workers=1, indel_recovery="smart")
    ind = r5["indel_recovery"]
    assert ind.get("consensus_groups", 0) > 0
    if s5 == "SUCCESS":
        ar.extract(tmp_path / "c.vnx", tmp_path / "out")
        assert hashlib.sha256((tmp_path / "out" / "in.bin").read_bytes()).hexdigest() == sha


def test_reverse_complement_reads(pool, tmp_path):
    reads = _reads(pool, "rc", insertion_rate=0.003, deletion_rate=0.003, coverage=1, reverse_complement_rate=0.5, seed=10)
    s5, r5 = _decode(reads, tmp_path / "rc.vnx", workers=1, indel_recovery="smart")
    s4, _ = _decode(reads, tmp_path / "rc4.vnx", workers=1)
    assert s5 == "SUCCESS" or s4 != "SUCCESS"
    assert r5["reads"]["reverse_complement"] > 0


@pytest.mark.parametrize("kw", [{"indel_recovery": "magic"}, {"indel_recovery": "smart", "indel_config": {"max_trials": 5}},
                                {"indel_recovery": "smart", "indel_config": IndelRecoveryConfig(max_shift=9)}])
def test_option_validation(kw):
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(**kw).validate()


def test_cli_flag(pool, tmp_path):
    from typer.testing import CliRunner
    from vnxdna.v4.cli import app
    reads = _reads(pool, "mild", insertion_rate=0.001, deletion_rate=0.001, substitution_rate=0.002, coverage=1, seed=5)
    res = CliRunner().invoke(app, ["decode", str(reads), "-o", str(tmp_path / "x.vnx"), "--indel-recovery", "smart", "-w", "1"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["indel_recovery"]["mode"] == "smart"
    bad = CliRunner().invoke(app, ["decode", str(reads), "-o", str(tmp_path / "y.vnx"), "--indel-recovery", "nope"])
    assert bad.exit_code != 0


def test_spill_records_raw_reads_only_in_smart_mode(tmp_path):
    a = de.Spill(tmp_path, 1, 40, 280)
    b = de.Spill(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, 1, 40, 280, raw_nt=319)
    try:
        assert "raw" not in a.pend_dtype.names
        assert {"raw", "rawq", "rawlen", "hasq"} <= set(b.pend_dtype.names)
    finally:
        a.close()
        b.close()
    assert np.dtype(a.pend_dtype).itemsize < np.dtype(b.pend_dtype).itemsize
