"""V12 orientation pre-pass: marker blocks are matched within a small shift window, so an indel that shifts the later
markers no longer decides the orientation. SIMULATED reads (VNX-DNA channel simulator).

Measured on these fixtures (200 kB archive, 18 933 reads per channel), v11.0.0 → V12:
- forward reads, 1 % sub / 0.2 % ins / 0.2 % del: flipped 4.89 % → 0.01 %;
- the same reads, all reverse complemented: detected 73.3 % → 99.6 %.
Before V12 the reverse-complement retry rescued these reads at the cost of a second alignment."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.native.reads import iter_reads
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.probe import detect_layout
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en

INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002}


@pytest.fixture(scope="module")
def reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("v12orient")
    datagen.generate(d / "in.bin", 200_000, "random", 12101)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    opt = DecodeOptions()
    pass1._p_init(detect_layout(d / "s.fasta", opt), opt.band, opt.sync_costs, 0, True)
    out = {}
    for name, cfg in (("clean", {}), ("sub", {"substitution_rate": 0.01}), ("indel", INDEL),
                      ("indel_rc", {**INDEL, "reverse_complement_rate": 1.0}), ("clean_rc", {"reverse_complement_rate": 1.0})):
        path = d / f"{name}.fastq"
        ch.simulate_file(d / "s.fasta", path, ch.ChannelConfig(seed=5, coverage=3, **cfg))
        out[name] = path
    return out


def _flip_rate(path) -> float:
    flipped = total = 0
    for b in iter_reads(path, 8192):
        f = pass1._orientation(b.codes, b.lengths)
        flipped += int(f.sum())
        total += f.size
    assert total > 10_000
    return flipped / total


def test_forward_reads_are_not_flipped(reads):
    assert _flip_rate(reads["clean"]) == 0.0
    assert _flip_rate(reads["sub"]) == 0.0
    assert _flip_rate(reads["indel"]) < 0.002          # v11.0.0: 0.0489


def test_reverse_complement_reads_are_detected(reads):
    assert _flip_rate(reads["clean_rc"]) == 1.0
    assert _flip_rate(reads["indel_rc"]) > 0.99         # v11.0.0: 0.733


def test_empty_and_short_batches(reads):
    assert pass1._orientation(np.zeros(0, dtype=np.uint8), np.zeros(0, dtype=np.int64)).size == 0
    codes = np.array([0, 1, 2, 3, 0, 1], dtype=np.uint8)
    out = pass1._orientation(codes, np.array([5, 1], dtype=np.int64))
    assert out.shape == (2,) and not out.any()
