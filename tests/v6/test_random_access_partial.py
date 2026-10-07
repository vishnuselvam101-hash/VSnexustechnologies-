"""Job #66: random access must not refuse because of failed groups outside the index and the selected files.

V6 stripe archive (stripe depth 4, column parity Mc = 2; K = 64, M = 16, 17 data groups, five stripes). The index is
groups {0, 16}: group 0 holds the container header and group 16 the index section. Strands are dropped on purpose
(test-only use of the strand headers; the decoder sees neutral read names):

* group 0 loses M + 1 symbols at positions B, so it fails row-wise but its stripe's columns recover it;
* data groups 1 and 2 and column-parity group 17 (all in stripe 0) lose M + 1 symbols at positions A, disjoint from
  B: three erasures per column at A exceed Mc = 2, so groups 1 and 2 stay unrecovered.

Groups 1 and 2 hold only bytes of ``ds/f0.bin``. The full decode is therefore not SUCCESS, while the index and
``ds/f1.bin`` (groups 15 and 16) are recoverable. Random access used to refuse with "the archive index could not be
decoded" because the column pass for the index's stripe pulled groups 1 and 2 into ``failed`` and the selective path
refused on any failed group. Selecting ``ds/f0.bin`` must still be refused: its groups did not decode.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.constraints import iter_fasta
from vnxdna.v4.errors import VNXDecodeError
from vnxdna.v4.frame import KIND_DATA

M = 16
B = set(range(0, M + 1))                 # positions lost in group 0 (index header group)
A = set(range(20, 20 + M + 1))           # positions lost in groups 1, 2 and column-parity group 17


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    d = tmp_path_factory.mktemp("ra66")
    src = d / "ds"
    src.mkdir()
    sha = {}
    for i, size in enumerate((40_000, 1_000)):
        p = src / f"f{i}.bin"
        datagen.generate(p, size, "random", 6600 + i)
        sha[f"ds/f{i}.bin"] = hashlib.sha256(p.read_bytes()).hexdigest()
    ar.build_archive([src], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions(stripe_depth=4, column_parity=2))
    kept = []
    for head, seq in iter_fasta(d / "s.fasta"):
        _, _, kind, g, s = head.split("|")
        kind, g, s = int(kind), int(g), int(s)
        if kind == KIND_DATA and ((g == 0 and s in B) or (g in (1, 2, 17) and s in A)):
            continue
        kept.append(seq)
    reads = d / "r.fasta"
    reads.write_text("".join(f">read{i}\n{seq}\n" for i, seq in enumerate(kept)))
    return {"dir": d, "reads": reads, "sha": sha}


def test_fixture_full_decode_fails_only_outside_index(pool):
    res = de.decode_reads(pool["reads"], pool["dir"] / "full.vnx", de.DecodeOptions(), overwrite=True,
                          partial_dir=pool["dir"] / "partial")
    assert res.status != "SUCCESS"
    assert res.report["failed_groups"] == [1, 2]
    assert res.report["outer_v6"]["rows_recovered_by_columns"] >= 1          # group 0, recovered by its columns
    assert res.report["files_recovered"] == ["ds/f1.bin"]


@pytest.mark.parametrize("workers", [1, 2])
def test_select_succeeds_when_failed_groups_are_outside_index_and_selection(pool, tmp_path, workers):
    res = de.decode_reads(pool["reads"], None, de.DecodeOptions(workers=workers), select=["ds/f1.bin"],
                          select_dir=tmp_path / "sel")
    assert res.status == "SUCCESS"
    got = (tmp_path / "sel" / "ds" / "f1.bin").read_bytes()
    assert hashlib.sha256(got).hexdigest() == pool["sha"]["ds/f1.bin"], "select reported SUCCESS with wrong bytes"


def test_select_still_refuses_when_selected_groups_failed(pool, tmp_path):
    with pytest.raises(VNXDecodeError, match="selected files could not be decoded"):
        de.decode_reads(pool["reads"], None, de.DecodeOptions(), select=["ds/f0.bin"], select_dir=tmp_path / "sel")
    assert not (tmp_path / "sel" / "ds" / "f0.bin").exists()


def test_select_still_refuses_when_index_group_fails(tmp_path, pool):
    """Group 16 (index section) fully lost and not recoverable by columns: refuse with the index error."""
    kept = []
    for head, seq in iter_fasta(Path(pool["dir"]) / "s.fasta"):
        _, _, kind, g, s = head.split("|")
        if int(kind) == KIND_DATA and int(g) in (13, 16, 25) and int(s) in A:
            continue
        kept.append(seq)
    reads = tmp_path / "r.fasta"
    reads.write_text("".join(f">read{i}\n{seq}\n" for i, seq in enumerate(kept)))
    with pytest.raises(VNXDecodeError, match="archive index could not be decoded"):
        de.decode_reads(reads, None, de.DecodeOptions(), select=["ds/f1.bin"], select_dir=tmp_path / "sel")
