"""V11-RT-05 (HIGH): ``vnx conformance --vectors DIR`` runs vector files that may come from another implementation's
suite. ``container.build`` wrote each input to ``Path(tmp) / params["root"] / file["path"]``: an absolute or ``..``
path in a vector wrote attacker bytes anywhere the user can write (e.g. a shell rc file). Those paths are now archive
paths (relative, no ``..``) confined to the temporary directory. V11-RT-06 (LOW): gzip-stored reads were decompressed
without a bound; they are now capped. SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import gzip
import hashlib

import pytest

from vnxdna.conformance import operations as ops


def _vec(tmp_path, root="data", path="a.bin"):
    (tmp_path / "in.bin").write_bytes(b"attacker bytes")
    return {"params": {"chunk_size": 4096, "archive_id_hex": "00" * 16, "root": root, "files": [{"path": path, "input": "x"}]},
            "inputs": {"x": {"path": "in.bin", "sha256": hashlib.sha256(b"attacker bytes").hexdigest()}}}


@pytest.mark.parametrize("root, path", [("data", "../../../escaped.txt"), ("data", "{victim}"), ("{victimdir}", "x.txt"),
                                        ("..", "escaped.txt"), ("data", "a/../../b.txt")])
def test_vector_paths_cannot_write_outside_the_temporary_directory(tmp_path, root, path):
    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    vec = _vec(tmp_path, root.format(victimdir=victim_dir), path.format(victim=victim_dir / "rc"))
    with pytest.raises(ValueError, match="vector path"):
        ops.op_container_build(vec, tmp_path)
    assert list(victim_dir.iterdir()) == []


def test_well_formed_vector_paths_still_build(tmp_path):
    out = ops.op_container_build(_vec(tmp_path, "data", "sub/a.bin"), tmp_path)
    assert out


def test_gzip_reads_are_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "MAX_VECTOR_READS_BYTES", 1 << 16)
    (tmp_path / "r.fastq.gz").write_bytes(gzip.compress(bytes(1 << 20)))
    vec = {"inputs": {"reads": {"path": "r.fastq.gz"}}}
    with pytest.raises(ValueError, match="larger than"):
        ops._plain_reads(vec, tmp_path, tmp_path)
    (tmp_path / "s.fastq.gz").write_bytes(gzip.compress(b"@r\nACGT\n+\nIIII\n"))
    assert ops._plain_reads({"inputs": {"reads": {"path": "s.fastq.gz"}}}, tmp_path, tmp_path).read_bytes().startswith(b"@r")
