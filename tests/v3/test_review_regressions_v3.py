"""Regressions for defects found while reviewing the V3 changes themselves (docs/V3_AUDIT.md, section R)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import numpy as np
import pytest

from v2_support import FAST, KEY, mixed_bytes, write
from vnxdna.dna.constraints import ConstraintSpec
from vnxdna.errors import ConfigurationError, InsufficientRedundancyError, MetadataError, OutputError
from vnxdna.v2 import api
from vnxdna.v2 import archive as arc
from vnxdna.v2 import container as cont
from vnxdna.v2.archive import store_file
from vnxdna.v2.decoder import recover_metadata, record_dtype
from vnxdna.v2.encoder import META_MAGIC, encode_file
from vnxdna.v2.frame import KIND_META, FrameGeometry, build_strands
from vnxdna.v2.profiles import options_for
from vnxdna.v2.sync import burst_span, repair_burst
from vnxdna.v3.sweep import run_sweep

CS, HEADER = 4096, 16
RAW = options_for("balanced", chunk_size=CS, compression="none", compression_level=0)


class Stop(BaseException):
    pass


def stop_at(k):
    def progress(done, n):
        if done == k:
            raise Stop()
    return progress


def xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def test_replaying_an_older_authentic_checkpoint_does_not_reuse_nonces(tmp_path):
    """R1: the checkpoint HMAC stops forgery, not the replay of a genuine older checkpoint."""
    src, out = tmp_path / "in.bin", tmp_path / "a.vxdna"
    src.write_bytes(os.urandom(CS * 16))
    with pytest.raises(Stop):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, checkpoint_interval=4, progress=stop_at(6))
    ckpt = tmp_path / "a.vxdna.partial.ckpt"
    old = ckpt.read_bytes()
    with pytest.raises(Stop):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, resume=True, checkpoint_interval=4, progress=stop_at(7))
    chunk4 = slice(HEADER + 4 * (CS + 16), HEADER + 5 * (CS + 16))
    c1, p1 = (tmp_path / "a.vxdna.partial").read_bytes()[chunk4], src.read_bytes()[4 * CS:5 * CS]
    ckpt.write_bytes(old)  # put the older, still-authentic epoch-0 checkpoint back
    data = bytearray(src.read_bytes())
    data[4 * CS:5 * CS] = os.urandom(CS)
    st = src.stat()
    src.write_bytes(bytes(data))
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))
    with pytest.raises(Stop):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, resume=True, checkpoint_interval=4, progress=stop_at(5))
    c2, p2 = (tmp_path / "a.vxdna.partial").read_bytes()[chunk4], bytes(data[4 * CS:5 * CS])
    assert p1 != p2 and xor(c1[:CS], c2[:CS]) != xor(p1, p2)
    assert json.loads(ckpt.read_text())["epoch"] == 2


def test_one_forged_metadata_strand_cannot_unlock_a_huge_allocation():
    """R2: VNX-DNA 3.0-dev bounded the metadata length by the highest stripe seen, which one forged strand raised."""
    p = 40
    geometry = FrameGeometry("2bit", p, 8)
    i_len = 4_000_000_000
    stream = (META_MAGIC + (100).to_bytes(4, "big") + i_len.to_bytes(4, "big") + bytes(4)).ljust(8 * p, b"\0")
    need = -(-(16 + 100 + i_len) // (8 * p))
    recs = np.zeros(9, dtype=record_dtype(p))
    recs["tag"], recs["kind"] = 0x1234, KIND_META
    for s in range(8):
        recs[s]["stripe"], recs[s]["shard"] = 0, s
        recs[s]["payload"] = np.frombuffer(stream[s * p:(s + 1) * p], np.uint8)
    recs[8]["stripe"], recs[8]["shard"] = need - 1, 0
    started = time.perf_counter()
    with pytest.raises(InsufficientRedundancyError, match="metadata"):
        recover_metadata(recs, geometry)
    assert time.perf_counter() - started < 5


def test_verify_can_overwrite_its_report_with_force(tmp_path):
    """R3: `verify --report` had no --force, so a rerun could not replace its own report."""
    src = write(tmp_path / "in.bin", b"z" * 2000)
    run = lambda *a: subprocess.run([sys.executable, "-m", "vnxdna", *map(str, a)], capture_output=True, text=True)  # noqa: E731
    assert run("store", src, "-o", tmp_path / "a.vxdna").returncode == 0
    assert run("verify", tmp_path / "a.vxdna", "--report", tmp_path / "r.json").returncode == 0
    assert run("verify", tmp_path / "a.vxdna", "--report", tmp_path / "r.json").returncode == 8
    assert run("verify", tmp_path / "a.vxdna", "--report", tmp_path / "r.json", "--force").returncode == 0


def _strands(geometry: FrameGeometry, n: int, seed: int):
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (n, geometry.payload_bytes), dtype=np.uint8)
    codes, _ = build_strands(geometry, ConstraintSpec(gc_min_percent=0, gc_max_percent=100, max_homopolymer=0), 0x55, np.zeros(n, np.uint8),
                             np.arange(n, dtype=np.uint32), np.zeros(n, np.uint8), payloads)
    return codes, payloads


def test_insertion_bursts_need_only_one_erased_byte():
    """R4: insertion bursts were refused when ⌈(L + b − 1)/b⌉ exceeded r, although one byte suffices."""
    geometry = FrameGeometry("2bit", 20, 2)
    assert burst_span(geometry, +8) == 1 and burst_span(geometry, -8) == 3
    codes, payloads = _strands(geometry, 20, 1)
    rng = np.random.default_rng(2)
    for i in range(20):
        start = int(rng.integers(0, codes.shape[1]))
        read = np.concatenate([codes[i, :start], rng.integers(0, 4, 8).astype(np.uint8), codes[i, start:]])
        result = repair_burst(read, geometry, max_burst=16)
        assert result is not None and result[0][4] == payloads[i].tobytes()


def test_rotation3_bursts_flag_the_byte_after_the_run():
    """R5: rotation3 digits depend on the previous base, so the byte after a deletion can be wrong too."""
    geometry = FrameGeometry("rotation3", 20, 4)
    codes, payloads = _strands(geometry, 60, 3)
    rng = np.random.default_rng(4)
    for i in range(60):
        start = int(rng.integers(0, codes.shape[1] - 1))
        read = np.concatenate([codes[i, :start], codes[i, start + 1:]])
        result = repair_burst(read, geometry, max_burst=4)
        assert result is not None and result[0][4] == payloads[i].tobytes()


def test_retagged_metadata_cannot_select_another_archive(tmp_path):
    """R6: the metadata strands' tag must match the archive their manifest describes."""
    data = mixed_bytes(12_000, 1)
    src = write(tmp_path / "in.bin", data)
    store_file(src, tmp_path / "a.vxdna", options=FAST, workers=1)
    encode_file(tmp_path / "a.vxdna", tmp_path / "a.fasta", workers=1)
    from vnxdna.v2 import decoder as dec
    real = dec.recover_metadata

    def retag(meta, geometry, stats=None, archive_tag=None):
        manifest, index, plain, tag = real(meta, geometry, stats, archive_tag)
        return manifest, index, plain, tag ^ 0xFFFFFFFF  # as if the strands carried another archive's tag

    import unittest.mock as mock
    with mock.patch.object(dec, "recover_metadata", retag):
        with pytest.raises(MetadataError, match="archive tag"):
            api.recover(tmp_path / "a.fasta", tmp_path / "o.bin", workers=1)


def test_publish_falls_back_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    """R7: file systems without hard links returned errnos outside the fallback list."""
    import errno

    def no_links(a, b):
        raise OSError(errno.ENOSYS, "hard links not supported")

    monkeypatch.setattr(cont.os, "link", no_links)
    out = arc.AtomicOutput(tmp_path / "o.bin")
    out.write(b"data")
    out.commit()
    assert (tmp_path / "o.bin").read_bytes() == b"data"
    second = arc.AtomicOutput(tmp_path / "p.bin")
    second.write(b"new")
    (tmp_path / "p.bin").write_bytes(b"theirs")
    with pytest.raises(OutputError):
        second.commit()
    assert (tmp_path / "p.bin").read_bytes() == b"theirs"


def test_sweep_rejects_invalid_decoder_options_before_any_work(tmp_path):
    """R8: an invalid decoder option was counted as a detected failure in every trial."""
    src = write(tmp_path / "in.bin", b"q" * 100)
    with pytest.raises(ConfigurationError):
        run_sweep(src, tmp_path / "s", sweep=["substitution=0"], burst_repair=100, workers=1)
    assert not (tmp_path / "s").exists()
