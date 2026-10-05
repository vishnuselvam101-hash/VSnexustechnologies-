"""Job #56: random access (``select``) must decode the superblock exactly as a full decode does.

V6 stripe archive (stripe depth 4, column parity Mc = 2), reads from the per-read channel of the ``mixed-harsh`` model
(experiments/v6/channel/models, SIMULATED) at coverage 3, smart indel recovery with the deferred schedule. At this
coverage only one superblock frame verifies in the cheap pass; the full decode recovers more in deferred round S
(pending reads whose header says superblock) before it decodes the superblock. Random access used to decode the
superblock at the start of pass 2, before its first recovery stage, so round S never ran first and ``select`` failed
with "no superblock could be decoded" while the full decode of the same reads succeeded.

Fail-closed in both directions: whenever ``select`` reports SUCCESS the extracted file must equal the original.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.errors import VNXError

MODEL = Path(__file__).resolve().parents[2] / "experiments" / "v6" / "channel" / "models" / "mixed-harsh.json"
SELECTED = "ds/f1.bin"


def _make(d: Path, seed: int, coverage: float = 3.0) -> tuple[Path, dict]:
    src = d / "ds"
    src.mkdir()
    sha = {}
    for i, size in enumerate((4_000, 1_000)):
        p = src / f"f{i}.bin"
        datagen.generate(p, size, "random", 5600 + i)
        sha[f"ds/f{i}.bin"] = hashlib.sha256(p.read_bytes()).hexdigest()
    ar.build_archive([src], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions(stripe_depth=4, column_parity=2))
    params = json.loads(MODEL.read_text())["channel"]
    params.update(coverage=coverage, seed=seed)
    ch.simulate_file(d / "s.fasta", d / "r.fastq", ch.ChannelConfig.from_dict(params))
    return d / "r.fastq", sha


def _full(reads: Path, d: Path) -> str:
    try:
        return de.decode_reads(reads, d / "full.vnx", de.DecodeOptions(indel_recovery="smart")).status
    except VNXError:
        return "ERROR"


def _select(reads: Path, d: Path, sha: dict) -> str:
    try:
        res = de.decode_reads(reads, None, de.DecodeOptions(indel_recovery="smart"), select=[SELECTED],
                              select_dir=d / "sel")
    except VNXError:
        return "ERROR"
    if res.status == "SUCCESS":
        got = (d / "sel" / SELECTED).read_bytes()
        assert hashlib.sha256(got).hexdigest() == sha[SELECTED], "select reported SUCCESS with wrong bytes"
    return res.status


def test_select_runs_superblock_round_before_decoding_superblock(tmp_path):
    """Seed 2 needs deferred round S for the superblock: the full decode succeeds, so select must succeed too."""
    reads, sha = _make(tmp_path, seed=2)
    assert _full(reads, tmp_path) == "SUCCESS"
    res = de.decode_reads(reads, None, de.DecodeOptions(indel_recovery="smart"), select=[SELECTED],
                          select_dir=tmp_path / "sel")
    assert res.status == "SUCCESS"
    assert hashlib.sha256((tmp_path / "sel" / SELECTED).read_bytes()).hexdigest() == sha[SELECTED]
    sched = res.report["recovery_schedule"]
    assert sched["random_access_stages"] >= 1      # a small archive: the index stage can already cover the file
    assert sched["rounds"]["S"]["recovered"] > 0, "fixture must need round S for the superblock"
    assert sched["superblock_decoded"]


@pytest.mark.slow
@pytest.mark.parametrize("coverage", [2.0, 3.0])
@pytest.mark.parametrize("seed", range(1, 21))
def test_select_succeeds_iff_full_decode_succeeds(tmp_path, seed, coverage):
    """Property over 20 seeds at the job #56 configuration: select succeeds exactly when the full decode succeeds.
    Before the fix 5 of 20 seeds failed at each coverage (select refused, full decode succeeded)."""
    reads, sha = _make(tmp_path, seed=seed, coverage=coverage)
    full = _full(reads, tmp_path)
    sel = _select(reads, tmp_path, sha)
    assert (full == "SUCCESS") == (sel == "SUCCESS"), f"seed {seed}, coverage {coverage}: full {full}, select {sel}"
