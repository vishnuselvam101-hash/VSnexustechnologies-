"""V11 red-team regression: an encrypted container opened with the wrong key, a passphrase instead of its key, a
tampered MAC / manifest / Merkle chunk table / file table / chunk, a truncation, a header version change, or a forged
unencrypted copy never extracts (EXTRACTED) data that differs from the original. SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import random
import shutil
import struct

import pytest

from vnxdna.archive import container as ct, operations as ar
from vnxdna.core.errors import VNXError

KEY = bytes(range(32))           # test-only key, obviously fake


@pytest.fixture(scope="module")
def enc(tmp_path_factory):
    d = tmp_path_factory.mktemp("tamper")
    (d / "src").mkdir()
    rng = random.Random(9001)
    (d / "src" / "f.bin").write_bytes(bytes(rng.randrange(256) for _ in range(20_000)))
    (d / "src" / "g.txt").write_text("hello " * 400)
    ar.build_archive([d / "src"], d / "a.vnx", ar.ArchiveOptions(chunk_size=4096, key=KEY))
    return d


def _outcome(d, data: bytes, **kw):
    p, out = d / "t.vnx", d / "out"
    p.write_bytes(data)
    shutil.rmtree(out, ignore_errors=True)
    try:
        ar.extract(p, out, **kw)
    except VNXError:
        return "refused"
    same = all((out / "src" / n).read_bytes() == (d / "src" / n).read_bytes() for n in ("f.bin", "g.txt"))
    return "identical" if same else "WRONG"


@pytest.mark.parametrize("kw", [{"key": bytes(32)}, {"key": KEY[:-1] + b"\x00"}, {"passphrase": "not-the-key"}, {}])
def test_wrong_or_missing_key_is_refused(enc, kw):
    assert _outcome(enc, (enc / "a.vnx").read_bytes(), **kw) == "refused"


def test_bit_flips_in_every_section_never_yield_wrong_data(enc):
    raw = (enc / "a.vnx").read_bytes()
    _, (body, ctb, ft, rf, mn), _, _ = ct.read_header_trailer(enc / "a.vnx")
    # header, body (chunks), chunk table (Merkle leaves), file table, refs, manifest, trailer (sizes, MAC, magic)
    edges = [0, ct.HEADER_BYTES, ct.HEADER_BYTES + body, ct.HEADER_BYTES + body + ctb, ct.HEADER_BYTES + body + ctb + ft,
             ct.HEADER_BYTES + body + ctb + ft + rf, len(raw) - ct.TRAILER_BYTES, len(raw)]
    rng = random.Random(1)
    seen = {}
    for lo, hi in zip(edges, edges[1:]):
        for i in sorted({lo, hi - 1, *(rng.randrange(lo, hi) for _ in range(12))}):
            b = bytearray(raw)
            b[i] ^= 1 << rng.randrange(8)
            got = _outcome(enc, bytes(b), key=KEY)
            assert got != "WRONG", f"flip at {i} extracted wrong data"
            seen.setdefault((lo, got), 0)
            seen[(lo, got)] += 1
    # every section except the final whole-file digest (checked by `vnx verify`, redundant for extract) is authenticated
    trailer_digest = len(raw) - 32
    for (lo, got), n in seen.items():
        assert got == "refused" or lo >= len(raw) - ct.TRAILER_BYTES, (lo, got, n)
    for i in range(trailer_digest, len(raw)):
        assert _outcome(enc, raw[:i] + bytes([raw[i] ^ 1]) + raw[i + 1:], key=KEY) != "WRONG"


def test_truncations_are_refused(enc):
    raw = (enc / "a.vnx").read_bytes()
    for n in list(range(0, len(raw), 251)) + [len(raw) - 1]:
        assert _outcome(enc, raw[:n], key=KEY) == "refused"


@pytest.mark.parametrize("major, minor", [(0, 0), (99, 0), (None, 99)])
def test_header_version_change_is_refused(enc, major, minor):
    raw = bytearray((enc / "a.vnx").read_bytes())
    old_major, _old_minor, flags = struct.unpack(">HHI", raw[8:16])
    raw[8:16] = struct.pack(">HHI", old_major if major is None else major, minor, flags)
    assert _outcome(enc, bytes(raw), key=KEY) == "refused"


def test_downgrade_to_an_unencrypted_forgery_is_refused_with_a_key(enc, tmp_path):
    # an attacker who replaces the encrypted archive with a clear one of their choosing: a key holder is refused
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "f.bin").write_bytes(b"forged")
    ar.build_archive([tmp_path / "src"], tmp_path / "clear.vnx", ar.ArchiveOptions())
    assert _outcome(enc, (tmp_path / "clear.vnx").read_bytes(), key=KEY) == "refused"
