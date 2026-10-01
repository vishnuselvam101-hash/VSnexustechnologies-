"""V1 compatibility through the V2 API, and V1 → V2 migration with verification before and after."""
import pytest

from v2_support import FAST, KEY, OTHER_KEY, mixed_bytes, write
from vnxdna import api as v1
from vnxdna.container.builder import StoreOptions
from vnxdna.errors import IntegrityError, InvalidInputError, WrongKeyError
from vnxdna.v2 import api
from vnxdna.v2.archive import open_container

V1_FAST = StoreOptions(chunk_size=4096, data_shards=8, parity_shards=4, payload_bytes=24, inner_parity_bytes=8)


@pytest.fixture
def v1_archive(tmp_path):
    data = mixed_bytes(25_000, seed=31)
    write(tmp_path / "in.bin", data)
    v1.store(tmp_path / "in.bin", tmp_path / "v1.vxdna", options=V1_FAST)
    v1.encode(tmp_path / "v1.vxdna", tmp_path / "v1.fasta")
    return tmp_path, data


def test_v2_detects_and_reads_v1_containers_and_reads(v1_archive):
    tmp, data = v1_archive
    assert api.detect(tmp / "v1.vxdna") == "container-v1"
    assert api.detect(tmp / "v1.fasta") == "reads-v1"
    for source in ("v1.vxdna", "v1.fasta"):
        r = api.restore(tmp / source, tmp / f"{source}.out")
        assert (tmp / f"{source}.out").read_bytes() == data and r["format_version"] == 4
        assert api.verify(tmp / source)["status"] == "PASS"
        assert api.info(tmp / source)["format_version"] == 4
    api.decode(tmp / "v1.fasta", tmp / "back.vxdna")
    assert (tmp / "back.vxdna").read_bytes() == (tmp / "v1.vxdna").read_bytes()
    part = api.extract(tmp / "v1.vxdna", tmp / "part.bin", offset=5000, length=100)
    assert (tmp / "part.bin").read_bytes() == data[5000:5100] and part["format_version"] == 4


@pytest.mark.parametrize("source", ["v1.vxdna", "v1.fasta"])
def test_migrate_v1_to_v2_verifies_before_and_after(v1_archive, source):
    tmp, data = v1_archive
    r = api.migrate(tmp / source, tmp / "v2.vxdna", options=FAST)
    assert r["sha256_match"] and api.detect(tmp / "v2.vxdna") == "container-v2"
    api.restore(tmp / "v2.vxdna", tmp / "o.bin")
    assert (tmp / "o.bin").read_bytes() == data
    _, loaded = open_container(tmp / "v2.vxdna", None)
    assert loaded.content.name == "in.bin"


def test_migrate_encrypted_keeps_or_rotates_the_key(tmp_path):
    data = mixed_bytes(12_000, seed=32)
    write(tmp_path / "in.bin", data)
    v1.store(tmp_path / "in.bin", tmp_path / "v1.vxdna", options=V1_FAST, key=KEY)
    api.migrate(tmp_path / "v1.vxdna", tmp_path / "same.vxdna", options=FAST, key=KEY)
    api.restore(tmp_path / "same.vxdna", tmp_path / "a.bin", key=KEY)
    api.migrate(tmp_path / "v1.vxdna", tmp_path / "rotated.vxdna", options=FAST, key=KEY, new_key=OTHER_KEY)
    with pytest.raises(WrongKeyError):
        api.restore(tmp_path / "rotated.vxdna", tmp_path / "b.bin", key=KEY)
    api.restore(tmp_path / "rotated.vxdna", tmp_path / "b.bin", key=OTHER_KEY)
    assert (tmp_path / "a.bin").read_bytes() == (tmp_path / "b.bin").read_bytes() == data


def test_migrate_refuses_a_corrupted_v1_archive_and_writes_nothing(v1_archive):
    tmp, _ = v1_archive
    blob = bytearray((tmp / "v1.vxdna").read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    (tmp / "bad.vxdna").write_bytes(blob)
    with pytest.raises((IntegrityError, InvalidInputError)):
        api.migrate(tmp / "bad.vxdna", tmp / "v2.vxdna", options=FAST)
    assert not (tmp / "v2.vxdna").exists()


def test_migrate_rejects_non_v1_inputs(tmp_path):
    write(tmp_path / "in.bin", b"abc" * 1000)
    api.store(tmp_path / "in.bin", tmp_path / "v2.vxdna", options=FAST)
    with pytest.raises(InvalidInputError):
        api.migrate(tmp_path / "v2.vxdna", tmp_path / "x.vxdna")


def test_v0_1_legacy_archives_are_still_refused_by_the_main_decoder(fixtures_dir):
    import os
    from vnxdna.errors import UnsupportedFormatError
    candidates = [os.path.join(fixtures_dir, name) for name in sorted(os.listdir(fixtures_dir))
                  if os.path.isdir(os.path.join(fixtures_dir, name)) and not name.startswith("__")]
    legacy = [c for c in candidates if api.detect(c).startswith("legacy")]
    assert legacy
    with pytest.raises(UnsupportedFormatError):
        api.restore(legacy[0], os.path.join(fixtures_dir, "never-written.bin"))
