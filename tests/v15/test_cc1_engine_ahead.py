"""V15 CC-1: the streaming engine scans the next input window on a helper thread when it runs with two or more threads
(engine.rs scan_map_ahead). The windows, records and their order must be exactly those of the serial scanner, so
pass 1 (spill files, statistics, the per-read decision trace) is identical at every thread count and every IO window
size, including a record longer than the window (the window doubles) and many windows per batch; errors keep their
order (a malformed record after valid ones gives the same error at 1 and 3 threads). SIMULATED reads.
"""
from __future__ import annotations

import filecmp
import os
from pathlib import Path

import pytest

from vnxdna.core.errors import VNXFormatError
from vnxdna.native import engine as ne
from vnxdna.recovery import engine as re_
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.probe import detect_layout
from vnxdna.recovery.spill import Spill
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

pytestmark = pytest.mark.skipif(not ne.available(), reason="engine library not built (python -m vnxdna.native.engine build)")

INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002}


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    d = tmp_path_factory.mktemp("cc1ahead")
    datagen.generate(d / "in.bin", 60_000, "random", 15301)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    ch.simulate_file(d / "s.fasta", d / "r.fastq", ch.ChannelConfig(seed=15307, coverage=4, **INDEL,
                                                                    reverse_complement_rate=0.3))
    # one junk read longer than the smallest IO window (64 KiB) in the middle: the window must double for it
    lines = (d / "r.fastq").read_text().splitlines()
    mid = len(lines) // 8 * 4
    long_read = "ACGT" * 20_000
    lines[mid:mid] = ["@long", long_read, "+", "I" * len(long_read)]
    (d / "r.fastq").write_text("\n".join(lines) + "\n")
    # the same reads as FASTA (sequence lines wrapped at 60, so records also use the scanner's side buffer)
    with open(d / "r.fasta", "w") as f:
        for i in range(0, len(lines) - 3, 4):
            seq = lines[i + 1]
            f.write(f">r{i // 4}\n" + "\n".join(seq[k:k + 60] for k in range(0, len(seq), 60)) + "\n")
    return d


def _run(pool: Path, reads: Path, d: Path, threads: int, fmt: str) -> tuple:
    opt = DecodeOptions()
    lay = detect_layout(reads, opt)
    d.mkdir(parents=True)
    Spill(d, 2, lay.payload_bytes, lay.frame_nt).close()
    tr = d / "trace.txt"
    os.environ["VNX_ENG_TRACE"] = str(tr)
    try:
        st = re_.run_pass1(reads, d, lay, opt, 2, fmt=fmt, threads=threads, dedup=True, batch=500)
    finally:
        del os.environ["VNX_ENG_TRACE"]
    keep = {k: v for k, v in st.items() if not k.startswith("ns_")}
    return keep, sorted(p.name for p in d.iterdir())


def _same(a: Path, b: Path) -> bool:
    names = sorted(p.name for p in a.iterdir())
    return names == sorted(p.name for p in b.iterdir()) and all(filecmp.cmp(a / n, b / n, shallow=False) for n in names)


@pytest.mark.parametrize("fmt", ["fastq", "fasta"])
@pytest.mark.parametrize("block", [1 << 16, 3 << 16, 8 << 20])
def test_read_ahead_scan_gives_the_serial_pass1(pool, tmp_path, monkeypatch, fmt, block):
    # batches are cut per window, so the serial reference uses the same window size
    reads = pool / f"r.{fmt}"
    monkeypatch.setattr(ne, "IO_BLOCK", block)
    want, _ = _run(pool, reads, tmp_path / "t1", 1, fmt)
    for threads in (2, 3):
        d = tmp_path / f"t{threads}"
        got, _ = _run(pool, reads, d, threads, fmt)
        assert got == want, (threads, block)
        assert _same(tmp_path / "t1", d), (threads, block)        # spill files and the per-read decision trace


def test_read_ahead_keeps_the_error_order(pool, tmp_path, monkeypatch):
    good = (pool / "r.fastq").read_bytes()
    bad = tmp_path / "bad.fastq"
    bad.write_bytes(good + b"@broken\nACGT\n+\nII\n")             # quality length differs, after many valid records
    monkeypatch.setattr(ne, "IO_BLOCK", 1 << 16)
    errors = []
    for threads in (1, 3):
        with pytest.raises(Exception) as exc:
            _run(pool, bad, tmp_path / f"t{threads}", threads, "fastq")
        errors.append((type(exc.value).__name__, str(exc.value)))
    assert errors[0] == errors[1] and "malformed FASTQ record" in errors[0][1]
    for workers in (1, 3):                                      # the whole decode: the same refusal
        with pytest.raises(VNXFormatError):
            de.decode_reads(bad, tmp_path / f"o{workers}.vnx", DecodeOptions(workers=workers, profile="v4-balanced"))


def test_read_ahead_whole_decode_and_cancel(pool, tmp_path, monkeypatch):
    from vnxdna.recovery.planner import RecoveryBudget, VNXBudgetExceeded
    monkeypatch.setattr(ne, "IO_BLOCK", 1 << 16)
    res = de.decode_reads(pool / "r.fastq", tmp_path / "o.vnx", DecodeOptions(workers=3))
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == (pool / "a.vnx").read_bytes()
    with pytest.raises(VNXBudgetExceeded):
        de.decode_reads(pool / "r.fastq", tmp_path / "o2.vnx",
                        DecodeOptions(workers=3, recovery_budget=RecoveryBudget(max_wall_seconds=1e-9)))
    assert not (tmp_path / "o2.vnx").exists()
