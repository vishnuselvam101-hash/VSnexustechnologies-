"""V3 capabilities: single-read burst repair, error-channel sweeps (simulate-errors) and the ECC engine registry."""
from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pytest

from v2_support import FAST, mixed_bytes, write
from vnxdna.dna.constraints import ConstraintSpec
from vnxdna.ecc import engine
from vnxdna.ecc.cauchy import CauchyErasureCode
from vnxdna.errors import ConfigurationError, UnsupportedFormatError
from vnxdna.v2 import api
from vnxdna.v2.archive import store_file
from vnxdna.v2.decoder import DecodeOptionsV2
from vnxdna.v2.encoder import encode_file
from vnxdna.v2.frame import FrameGeometry, build_strands
from vnxdna.v2.sequencing import SequencingConfig, sequence_file
from vnxdna.v2.sync import repair_burst
from vnxdna.v3.sweep import parse_sweep, run_sweep

GEOMETRY = FrameGeometry("2bit", 40, 8)


def _strands(n: int, seed: int):
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (n, 40), dtype=np.uint8)
    codes, _ = build_strands(GEOMETRY, ConstraintSpec(), 0x0BADF00D, np.zeros(n, np.uint8), np.arange(n, dtype=np.uint32),
                             np.zeros(n, np.uint8), payloads)
    return codes, payloads


# ---------------------------------------------------------------- burst repair
@pytest.mark.parametrize("kind", ["deletion", "insertion"])
@pytest.mark.parametrize("length", [1, 2, 5, 9, 16, 24, 29])
def test_one_contiguous_burst_is_repaired_from_a_single_read(kind, length):
    codes, payloads = _strands(15, seed=length)
    rng = np.random.default_rng(100 + length)
    for i in range(15):
        start = int(rng.integers(0, codes.shape[1] - length))
        if kind == "deletion":
            read = np.concatenate([codes[i, :start], codes[i, start + length:]])
        else:
            read = np.concatenate([codes[i, :start], rng.integers(0, 4, length).astype(np.uint8), codes[i, start:]])
        result = repair_burst(read, GEOMETRY, max_burst=32, reverse_complement=True)
        assert result is not None, (kind, length, i, start)
        assert result[0][2] == i and result[0][4] == payloads[i].tobytes()


def test_burst_repair_respects_its_bounds():
    codes, _ = _strands(3, seed=1)
    long_burst = np.concatenate([codes[0, :50], codes[0, 50 + 30:]])  # 30 nt: ⌈33/4⌉ = 9 bytes to erase > r = 8
    assert repair_burst(long_burst, GEOMETRY, max_burst=64) is None
    assert repair_burst(np.concatenate([codes[1, :10], codes[1, 14:]]), GEOMETRY, max_burst=3) is None  # longer than max_burst
    assert repair_burst(codes[2], GEOMETRY, max_burst=8) is None  # nothing to repair
    with pytest.raises(ConfigurationError):
        DecodeOptionsV2(burst_repair=65)


def test_burst_repair_recovers_a_coverage_1_pool_with_deletion_bursts(tmp_path):
    data = mixed_bytes(25_000, 3)
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=FAST, workers=1)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta", workers=1)
    channel = SequencingConfig(seed=4, coverage=1, coverage_model="fixed", burst_rate=0.5, burst_length_mean=6, burst_kind="deletion")
    seq = sequence_file(tmp_path / "s.fasta", tmp_path / "r.fastq", channel)
    assert seq["errors"]["bursts_deletion"] > 0
    with pytest.raises(Exception):  # without burst repair the burst-damaged strands are erasures beyond the outer code
        api.recover(tmp_path / "r.fastq", tmp_path / "plain.bin", workers=1)
    r = api.recover(tmp_path / "r.fastq", tmp_path / "o.bin", options=DecodeOptionsV2(burst_repair=24), workers=1)
    assert r["status"] == "RECOVERED" and r["reads"]["reads_burst_repaired"] > 0
    assert (tmp_path / "o.bin").read_bytes() == data


# ---------------------------------------------------------------- error-channel sweeps
def test_sweep_specifications_are_parsed_and_validated():
    points = parse_sweep(["substitution=0,0.01", "mixed=substitution:0.002+deletion:0.001", "burst-deletion=0.3"])
    assert [p[0] for p in points] == ["substitution:0", "substitution:0.01", "mixed:substitution:0.002+deletion:0.001",
                                      "burst-deletion:0.3"]
    for bad in (["nonsense=0.1"], ["substitution"], ["substitution=2"], ["substitution=x"], ["mixed=burst-deletion:0.1+burst-insertion:0.1"],
                ["mixed=substitution"], []):
        with pytest.raises(ConfigurationError):
            parse_sweep(bad)


def _points(summary):
    return [{k: v for k, v in p.items()} for p in summary["points"]]


def test_a_sweep_is_reproducible_and_never_reports_wrong_data(tmp_path):
    src = write(tmp_path / "in.bin", mixed_bytes(12_000, 5))
    kwargs = dict(sweep=["substitution=0,0.004", "burst-substitution=0.5", "dropout=0.05"], trials=2, options=FAST)
    a = run_sweep(src, tmp_path / "a", workers=1, **kwargs)
    b = run_sweep(src, tmp_path / "b", workers=3, **kwargs)
    assert _points(a) == _points(b)  # same seeds, same results, whatever the worker count
    assert a["undetected_corruption_total"] == 0 and a["internal_errors_total"] == 0
    assert all((tmp_path / "a" / name).is_file() for name in ("sweep.json", "sweep.csv", "sweep.md"))
    clean = next(p for p in a["points"] if p["point"] == "substitution:0")
    assert clean["exact"] == 2
    detail = json.loads((tmp_path / "a" / "sweep.json").read_text())
    assert len(detail["points_with_trials"][0]["records"]) == 2


def test_simulate_errors_cli_contract(tmp_path):
    src = write(tmp_path / "in.bin", mixed_bytes(8_000, 6))
    bad = subprocess.run([sys.executable, "-m", "vnxdna", "simulate-errors", str(src), "-o", str(tmp_path / "s"), "--sweep", "bogus=1"],
                         capture_output=True, text=True)
    assert bad.returncode == 7 and "Traceback" not in bad.stderr
    ok = subprocess.run([sys.executable, "-m", "vnxdna", "simulate-errors", str(src), "-o", str(tmp_path / "s"), "--sweep",
                         "substitution=0.002", "--trials", "2", "--json", "-j", "1"], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["points"][0]["trials"] == 2


# ---------------------------------------------------------------- ECC engine
def test_the_engine_returns_the_declared_codes_and_refuses_unknown_ones():
    outer = engine.outer_code("cauchy-rs-gf256", 8, 4)
    assert isinstance(outer, CauchyErasureCode) and isinstance(outer, engine.OuterCode)
    inner = engine.inner_code("reed-solomon-gf256", 8)
    assert isinstance(inner, engine.InnerCode) and inner.nsym == 8
    with pytest.raises(UnsupportedFormatError):
        engine.outer_code("fountain-lt", 8, 4)
    with pytest.raises(UnsupportedFormatError):
        engine.inner_code("ldpc", 8)
    rng = np.random.default_rng(0)
    msgs = rng.integers(0, 256, (20, 50), dtype=np.uint8)
    cw = np.concatenate([msgs, inner.encode_batch(msgs)], axis=1)
    bad = cw.copy()
    bad[:, 3] ^= 0x5A
    fixed, ok, count = inner.decode_batch(bad)
    assert ok.all() and (fixed == cw).all() and (count == 1).all()
    data = rng.integers(0, 256, (3, 8, 16), dtype=np.uint8)
    shards = np.concatenate([data, outer.encode(data)], axis=1)
    present = np.ones((3, 12), dtype=bool)
    present[:, [0, 5, 9, 11]] = False
    assert (outer.decode(np.where(present[:, :, None], shards, 0), present) == data).all()
