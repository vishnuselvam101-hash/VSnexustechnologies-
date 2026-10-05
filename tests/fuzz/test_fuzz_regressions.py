"""Regression tests for bugs found by the V6 Phase 6 fuzz campaign (docs/security/V6_FUZZ_REPORT.md).

Each test was written, and seen failing, before its fix. The minimal fuzz inputs that found the bugs are kept in
fuzz/regressions/<target>/ and replayed by test_fuzz_smoke.py.
"""
from __future__ import annotations

import copy
import hashlib
import struct

import pytest

from vnxdna.v4 import container as ct
from vnxdna.v4.errors import VNXFormatError
from vnxdna.v4.util import canonical_json

from .harnesses import TEST_ONLY_FUZZ_KEY, TEST_ONLY_SALT, _manifest_mac, _rewrap, seed_archives


def _manifest(src: bytes) -> dict:
    mn = struct.unpack(">Q", src[-ct.TRAILER_BYTES + 32:-ct.TRAILER_BYTES + 40])[0]
    return ct.parse_canonical_json(src[-ct.TRAILER_BYTES - mn:-ct.TRAILER_BYTES], "manifest")


def _with(tmp_path, which: str, edit) -> object:
    """The seed archive ``which`` with an edited manifest behind a valid digest (plain) or a valid HMAC (encrypted)."""
    src = seed_archives()[which]
    m = copy.deepcopy(_manifest(src))
    edit(m)
    man = canonical_json(m)
    mac = _manifest_mac(m, man, TEST_ONLY_SALT) if which == "enc" else hashlib.sha256(man).digest()
    p = tmp_path / f"{which}.vnx"
    p.write_bytes(_rewrap(src, man, mac))
    return p


def _set(path, value):
    def edit(m):
        cur = m
        for k in path[:-1]:
            cur = cur[k]
        cur[path[-1]] = value
    return edit


# V6-FUZZ-01: open_container indexed manifest sub-objects without checking their types, so a manifest with a valid
# digest (unencrypted: anyone can write one, FC-8) or a valid HMAC (encrypted) made it raise TypeError / KeyError /
# ValueError instead of VNXFormatError: the CLI then failed outside the error taxonomy.
TABLE_CASES = {
    "chunk_table-not-object": (("tables", "chunk_table"), "x"),
    "file_table-not-object": (("tables", "file_table"), "x"),
    "refs-not-object": (("tables", "refs"), 7),
    "chunk_table-missing-entries": (("tables", "chunk_table"), {"entry_bytes": 84, "sha256": "00" * 32}),
    "file_table-missing-bytes": (("tables", "file_table"), {"sha256": "00" * 32}),
    "refs-bytes-not-int": (("tables", "refs"), {"bytes": "0", "sha256": "00" * 32}),
    "entries-is-bool": (("tables", "chunk_table", "entries"), True),
    "sha256-not-string": (("tables", "refs", "sha256"), 0),
}


@pytest.mark.parametrize("which", ["plain", "enc"])
@pytest.mark.parametrize("case", sorted(TABLE_CASES))
def test_type_confused_tables_are_format_errors(tmp_path, which, case):
    path, value = TABLE_CASES[case]
    p = _with(tmp_path, which, _set(path, value))
    kw = {"key": TEST_ONLY_FUZZ_KEY} if which == "enc" else {}
    with pytest.raises(VNXFormatError):
        ct.open_container(p, **kw)


ENC_CASES = {
    "salt-not-hex": (("encryption", "salt"), "zz" * 16),
    "key_check-non-ascii": (("encryption", "key_check"), "é" * 32),
    "key_check-not-hex": (("encryption", "key_check"), "g" * 32),
}


@pytest.mark.parametrize("case", sorted(ENC_CASES))
def test_malformed_salt_and_key_check_are_format_errors(tmp_path, case):
    path, value = ENC_CASES[case]
    p = _with(tmp_path, "enc", _set(path, value))
    with pytest.raises(VNXFormatError):
        ct.open_container(p, key=TEST_ONLY_FUZZ_KEY)
    with pytest.raises(VNXFormatError):          # refused structurally, also without a key
        ct.open_container(p)


def test_seed_archives_still_open(tmp_path):
    for which, kw in (("plain", {}), ("enc", {"key": TEST_ONLY_FUZZ_KEY})):
        p = tmp_path / f"{which}.vnx"
        p.write_bytes(seed_archives()[which])
        assert ct.open_container(p, **kw).files


# V6-FUZZ-02: a deeply nested manifest (depth ~1,500) parses with json.loads but the canonical re-serialisation check
# (util.canonical_json -> _reject_floats) recursed past the interpreter limit outside the try block, so
# parse_canonical_json raised RecursionError instead of VNXFormatError. It runs before the manifest MAC is checked, so
# no key is needed to trigger it, also for encrypted archives.
@pytest.mark.parametrize("depth", [1_500, 5_000, 50_000])
def test_deeply_nested_manifest_is_a_format_error(tmp_path, depth):
    from vnxdna.v4.util import parse_canonical_json
    blob = b'{"a":' + b"[" * depth + b"]" * depth + b"}"
    with pytest.raises(VNXFormatError):
        parse_canonical_json(blob, "manifest")
    src = seed_archives()["enc"]
    p = tmp_path / "deep.vnx"
    p.write_bytes(_rewrap(src, blob, hashlib.sha256(blob).digest()))
    with pytest.raises(VNXFormatError):
        ct.open_container(p)
