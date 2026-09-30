"""VNX-DNA 2.0 → 3 compatibility, checked against files written by the released 2.0.0 code (tests/fixtures/v2_0)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vnxdna import __version__
from vnxdna.v2 import api
from vnxdna.v2 import archive as arc
from vnxdna.v2.encoder import encode_file
from vnxdna.v2.profiles import options_for

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "v2_0"
KEY = bytes.fromhex((FIX / "key.hex").read_text().strip())
OPTS = options_for("balanced", chunk_size=4096, data_shards=8, parity_shards=4, payload_bytes=24, inner_parity_bytes=8)
INPUT = (FIX / "input.bin").read_bytes()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixtures_are_intact():
    sums = json.loads((FIX / "SHA256SUMS.json").read_text())["files"]
    for name, digest in sums.items():
        assert sha(FIX / name) == digest, name


@pytest.mark.parametrize("name,key", [("plain.vxdna", None), ("encrypted.vxdna", KEY), ("resumed.vxdna", KEY)])
def test_v2_containers_restore_and_verify(tmp_path, name, key):
    r = arc.restore_file(FIX / name, tmp_path / "o.bin", key=key)
    assert r["status"] == "SUCCESS" and (tmp_path / "o.bin").read_bytes() == INPUT
    report = arc.verify_container(FIX / name, key=key, against=tmp_path / "o.bin")
    assert report["status"] == "PASS", report["checks"]


def test_v2_strands_are_recovered_by_the_v3_decoder(tmp_path):
    r = api.recover(FIX / "plain.fasta", tmp_path / "o.bin", workers=1)
    assert r["status"] == "SUCCESS" and (tmp_path / "o.bin").read_bytes() == INPUT


def test_random_access_on_v2_containers(tmp_path):
    arc.extract_range(FIX / "encrypted.vxdna", tmp_path / "s.bin", offset=5000, length=3000, key=KEY)
    assert (tmp_path / "s.bin").read_bytes() == INPUT[5000:8000]


def test_fresh_v3_stores_equal_v2_output_except_the_encoder_version(tmp_path):
    """Unencrypted, never-resumed archives: V3 writes what V2 wrote, apart from the manifest's ``encoder.version``
    (which no reader interprets), so VNX-DNA 2.0 readers read V3 archives."""
    src = tmp_path / "input.bin"
    src.write_bytes(INPUT)
    arc.store_file(src, tmp_path / "plain.vxdna", options=OPTS, workers=1)
    new_cf, new = arc.open_container(tmp_path / "plain.vxdna", None)
    old_cf, old = arc.open_container(FIX / "plain.vxdna", None)
    try:
        assert new_cf.read_stored(0, new_cf.body_bytes) == old_cf.read_stored(0, old_cf.body_bytes)
        assert new.index_bytes == old.index_bytes and new.plain_stored == old.plain_stored
        m_new, m_old = json.loads(new.manifest_bytes), json.loads(old.manifest_bytes)
        assert m_old["encoder"] == {"name": "vnxdna", "version": "2.0.0"} and m_new["encoder"]["version"] == __version__
        for m in (m_new, m_old):
            m.pop("encoder")
            m.pop("seal")
        assert m_new == m_old
    finally:
        new_cf.close()
        old_cf.close()
    encode_file(tmp_path / "plain.vxdna", tmp_path / "plain.fasta", workers=1, write_index=False)
    def records(path: Path) -> dict[str, str]:
        lines = path.read_text().split()
        return dict(zip(lines[0::2], lines[1::2]))  # label -> sequence; labels are vnx5:<tag>:<d|m>:<stripe>:<shard>

    new_strands, old_strands = records(tmp_path / "plain.fasta"), records(FIX / "plain.fasta")
    assert new_strands.keys() == old_strands.keys()
    data = [label for label in old_strands if ":d:" in label]
    assert data and all(new_strands[label] == old_strands[label] for label in data)  # data strands identical
    # only metadata strands (which carry the manifest, and so the encoder version) may differ
