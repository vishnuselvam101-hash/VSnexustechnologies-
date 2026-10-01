"""The .vxdna file layout and its failure modes."""
import pytest

from v1_support import FAST
from vnxdna.api import store_bytes
from vnxdna.container import vxdna
from vnxdna.errors import InvalidInputError, MetadataError, OutputError, UnsupportedFormatError


def test_layout_roundtrip_and_determinism():
    blob = store_bytes(b"data" * 1000, FAST)
    assert blob == store_bytes(b"data" * 1000, FAST)
    cf = vxdna.parse(blob)
    assert cf.trailer_ok and vxdna.serialize(cf.manifest_bytes, cf.body) == blob


@pytest.mark.parametrize("mutate,error", [
    (lambda b: b[:10], InvalidInputError),
    (lambda b: b"XXXXXXXX" + b[8:], InvalidInputError),
    (lambda b: b[:8] + b"\x00\x02" + b[10:], UnsupportedFormatError),
    (lambda b: b[:10] + b"\x00\x01" + b[12:], UnsupportedFormatError),
    (lambda b: b[:12] + b"\x7f\xff\xff\xff" + b[16:], MetadataError),
    (lambda b: b + b"trailing", InvalidInputError),
    (lambda b: b[:-1], InvalidInputError),
    (lambda b: b[:30] + bytes([b[30] ^ 1]) + b[31:], InvalidInputError),
])
def test_malformed_containers(mutate, error):
    blob = store_bytes(b"x" * 500, FAST)
    with pytest.raises(error):
        vxdna.parse(mutate(blob))


def test_non_strict_parse_reports_trailer_failure():
    blob = bytearray(store_bytes(b"x" * 500, FAST))
    blob[-40] ^= 1
    assert vxdna.parse(bytes(blob), strict=False).trailer_ok is False


def test_atomic_write_refuses_overwrite(tmp_path):
    target = tmp_path / "a"
    vxdna.atomic_write(target, b"1")
    with pytest.raises(OutputError):
        vxdna.atomic_write(target, b"2")
    vxdna.atomic_write(target, b"2", overwrite=True)
    assert target.read_bytes() == b"2" and len(list(tmp_path.iterdir())) == 1
