"""Stronger byte-identity assertions (V6 Phase 2.4 prerequisite), committed BEFORE any version bump.

Two properties, both written so that they keep passing when ``_version.py`` changes and an informational ``extensions.vnx``
block is added to the container manifest:

(a) strand identity: encoding the STORED 5.0.0 containers (tests/fixtures/v5_0) must give the 5.0.0 strand FASTA SHA-256s,
    and encoding the stored pre-refactor V6 fixture containers must give the pinned V6 strand SHA-256s. Container bytes go
    in, so the producing version of the builder is irrelevant. The expected digests are pinned in this file and are also
    checked against the fixtures' SHA256SUMS.

(b) builder identity modulo the informational manifest fields: the container builder, given the fixtures' inputs, must
    produce byte-identical header, body, chunk table, file table and reference table (the sections are also pinned by
    digest), and a manifest equal to the stored one after removing ``extensions.vnx`` and normalising ``encoder.version``.
    The manifest and trailer are the only parts allowed to differ. The comparison is section-wise, never the whole-container
    hash, which necessarily changes with the version string.

SYNTHETIC SOFTWARE TEST data only; no DNA was synthesised or sequenced.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PASSPHRASE = "vnx-test-passphrase-NOT-A-SECRET"
FIXED_ID, FIXED_SALT = bytes(range(1, 17)), bytes(range(101, 117))
HEADER_BYTES, TRAILER_BYTES = 16, 112
SECTION_ORDER = ("header", "body", "chunk_table", "file_table", "refs", "manifest", "trailer")

# strand FASTA SHA-256 and non-manifest section digests (first 16 hex digits) of the PRE-REFACTOR outputs
PINNED = {
    ("v5_0", "balanced"): {"strands": "624c26075f1bc856250a81ed5f7b84ed399fa629a466c16c896ec4ce35d16926",
                           "sections": {"header": "fd0a1ad70dad8a7d", "body": "3bf012e4063131f3", "chunk_table": "bf7831dcfff555d1",
                                        "file_table": "fccd1db515208c3e", "refs": "df3f619804a92fdb"}},
    ("v5_0", "archival"): {"strands": "2f5a0d80363670d21ab09979c1287f9c828a6d5a375ca9348dad90d127cf2f46",
                           "sections": {"header": "fd0a1ad70dad8a7d", "body": "3bf012e4063131f3", "chunk_table": "bf7831dcfff555d1",
                                        "file_table": "fccd1db515208c3e", "refs": "df3f619804a92fdb"}},
    ("v5_0", "encrypted"): {"strands": "173285de41d48504399d427fcc2f01fd49988782cde8818f679587258ea2724b",
                            "sections": {"header": "fd0a1ad70dad8a7d", "body": "8286cde634669113", "chunk_table": "56719764288977d1",
                                         "file_table": "761c11b84e140e00", "refs": "bc0235011f753b99"}},
    ("v5_0", "multifile"): {"strands": "0e7ed747843dbdfff71cc91b9238422f841ff5a3851acadb25b09262ed401d78",
                            "sections": {"header": "fd0a1ad70dad8a7d", "body": "ef271d4e7763f4b2", "chunk_table": "c59043541493092a",
                                         "file_table": "64aef41c4b3bda8e", "refs": "3067c72c5e501c31"}},
    ("v6_0", "stripes-seq"): {"strands": "28ae4ffed1824e1bbfc4bd005ff2cc3fee68cc474dbf282292738a489b6f2a16",
                              "sections": {"header": "fd0a1ad70dad8a7d", "body": "8232c23fcd8a0b54", "chunk_table": "15de30a5a186c96d",
                                           "file_table": "96a42c489d7d22da", "refs": "df3f619804a92fdb"}},
    ("v6_0", "adaptive-interleaved"): {"strands": "c3e85a3144ef4bdfe7a6fc5f4bdb7b41646a03658727a347395966a8877a5afd",
                                       "sections": {"header": "fd0a1ad70dad8a7d", "body": "f2f9a4906f5219f5",
                                                    "chunk_table": "42724222374f8b75", "file_table": "ebbe8308d4a5a6a2",
                                                    "refs": "cd2662154e6d76b2"}},
    ("v6_0", "max-recovery"): {"strands": "befacab56c9399eb2883de8dd7365a1915d1d53ac8afb6fd20860d4fcc943002",
                               "sections": {"header": "fd0a1ad70dad8a7d", "body": "f0dc3914ba9b02f5", "chunk_table": "f3c67eede8b18451",
                                            "file_table": "5dd7fb92b9f7141b", "refs": "df3f619804a92fdb"}},
    ("v6_0", "encrypted-stripes"): {"strands": "c77366e272146011800b0cd3285b514b481f20566fd19ecd8ea97c6eb612487d",
                                    "sections": {"header": "fd0a1ad70dad8a7d", "body": "03ce6391459bdeee",
                                                 "chunk_table": "fd535e673d8b8494", "file_table": "e4c91b969c5114c5",
                                                 "refs": "bc0235011f753b99"}},
}
IDS = [f"{s}-{c}" for s, c in PINNED]
# the V5 encoder options of each case (v4-balanced / v4-archival layouts, default V4/V5 outer code); V6 ones are in manifest.json
V5_PROFILE = {"balanced": "v4-balanced", "archival": "v4-archival", "encrypted": "v4-balanced", "multifile": "v4-balanced"}
V5_INPUTS = {"balanced": ["mixed.bin"], "archival": ["mixed.bin"], "encrypted": ["mixed.bin"],
             "multifile": ["mixed.bin", "text.txt", "random.bin", "sub"]}
V5_PAYLOADS = {"mixed.bin": ("mixed", 6144, 1), "text.txt": ("text", 3072, 2), "random.bin": ("random", 2048, 3),
               "sub/bin.dat": ("binary", 1500, 4)}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------------------------------------------------- section helpers
def container_sections(raw: bytes) -> dict[str, bytes]:
    """Split a VNX4 container into header, body, chunk table, file table, refs, manifest and trailer (from the trailer sizes)."""
    assert len(raw) >= HEADER_BYTES + TRAILER_BYTES
    body, ct, ft, rf, mn = struct.unpack(">QQQQQ", raw[-TRAILER_BYTES:-TRAILER_BYTES + 40])
    assert HEADER_BYTES + body + ct + ft + rf + mn + TRAILER_BYTES == len(raw), "section sizes do not add up"
    out, off = {"header": raw[:HEADER_BYTES]}, HEADER_BYTES
    for name, size in (("body", body), ("chunk_table", ct), ("file_table", ft), ("refs", rf), ("manifest", mn)):
        out[name] = raw[off:off + size]
        off += size
    out["trailer"] = raw[off:]
    assert list(out) == list(SECTION_ORDER)
    return out


def normalised_manifest(manifest_bytes: bytes) -> dict:
    """The manifest without its informational version fields: ``extensions.vnx`` removed, ``encoder.version`` replaced."""
    m = json.loads(manifest_bytes)
    m["extensions"] = {k: v for k, v in m["extensions"].items() if k != "vnx"}
    m["encoder"] = {**m["encoder"], "version": "<normalised>"}
    return m


def compare_containers(new: bytes, old: bytes) -> list[str]:
    """Names of the sections that differ in a way that is NOT allowed. Empty = identical modulo the informational fields."""
    a, b = container_sections(new), container_sections(old)
    bad = [n for n in ("header", "body", "chunk_table", "file_table", "refs") if a[n] != b[n]]
    if normalised_manifest(a["manifest"]) != normalised_manifest(b["manifest"]):
        bad.append("manifest")
    # trailer: the section sizes (except the manifest's) and the end magic are structure; the MAC and digest follow the manifest
    if a["trailer"][:32] != b["trailer"][:32] or a["trailer"][72:80] != b["trailer"][72:80]:
        bad.append("trailer")
    return bad


def with_manifest(raw: bytes, edit) -> bytes:
    """A copy of the container whose manifest is edited (as a later version would write it); sizes fixed, MAC/digest stale."""
    s = container_sections(raw)
    m = json.loads(s["manifest"])
    edit(m)
    man = json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    sizes = struct.pack(">QQQQQ", len(s["body"]), len(s["chunk_table"]), len(s["file_table"]), len(s["refs"]), len(man))
    return s["header"] + s["body"] + s["chunk_table"] + s["file_table"] + s["refs"] + man + sizes + s["trailer"][40:]


def stored(s: str, c: str) -> bytes:
    return (FIXTURES / s / f"{c}.vnx").read_bytes()


def dna_options(s: str, c: str) -> en.DNAOptions:
    if s == "v6_0":
        return en.DNAOptions(**json.loads((FIXTURES / s / "manifest.json").read_text())["cases"][c]["dna_options"])
    return en.DNAOptions(profile=V5_PROFILE[c])


# ------------------------------------------------------------------------------------------------------------- pins vs fixtures
@pytest.mark.parametrize("s,c", PINNED, ids=IDS)
def test_pins_agree_with_fixture_sha256sums_and_stored_sections(s, c):
    sums = {n: d for d, n in (line.split("  ", 1) for line in (FIXTURES / s / "SHA256SUMS").read_text().splitlines() if line)}
    assert sums[f"{c}.strands.fasta"] == PINNED[(s, c)]["strands"]
    sec = container_sections(stored(s, c))
    assert {k: sha(sec[k])[:16] for k in PINNED[(s, c)]["sections"]} == PINNED[(s, c)]["sections"]
    assert sha(stored(s, c)) == sums[f"{c}.vnx"]


# ------------------------------------------------------------------------------------------------------------- (a) strand identity
@pytest.mark.parametrize("s,c", PINNED, ids=IDS)
def test_encoding_stored_containers_reproduces_pinned_strand_sha256(s, c, tmp_path):
    out = tmp_path / "s.fasta"
    en.encode_container(FIXTURES / s / f"{c}.vnx", out, dna_options(s, c))
    assert sha(out.read_bytes()) == PINNED[(s, c)]["strands"]


@pytest.mark.parametrize("c", ("balanced", "archival"))
def test_v5_balanced_and_archival_strands_are_the_5_0_0_digests(c, tmp_path):
    """The named acceptance case: tests/fixtures/v5_0/{balanced,archival}.vnx in, the 5.0.0 FASTA SHA-256 out."""
    out = tmp_path / "s.fasta"
    en.encode_container(FIXTURES / "v5_0" / f"{c}.vnx", out, en.DNAOptions(profile=V5_PROFILE[c]))
    assert sha(out.read_bytes()) == PINNED[("v5_0", c)]["strands"]


def test_strand_identity_does_not_depend_on_the_package_version(tmp_path, monkeypatch):
    """The strands carry the container, not the version of the package that encodes them."""
    import vnxdna.v4.encoder as encoder_module
    for name in ("__version__",):
        if hasattr(encoder_module, name):
            monkeypatch.setattr(encoder_module, name, "9.9.9-test")
    out = tmp_path / "s.fasta"
    en.encode_container(FIXTURES / "v5_0" / "balanced.vnx", out, en.DNAOptions(profile="v4-balanced"))
    assert sha(out.read_bytes()) == PINNED[("v5_0", "balanced")]["strands"]


# ------------------------------------------------------------------------------------------------------------- (b) builder identity
def build_like_fixture(s: str, c: str, tmp: Path) -> bytes:
    encrypted = "encrypted" in c
    kw = dict(archive_id=FIXED_ID, salt=FIXED_SALT) if encrypted else {}
    if s == "v6_0":
        inp = FIXTURES / s / "inputs" / c
        paths = sorted(p for p in inp.iterdir())
    else:
        for name, (pattern, size, off) in V5_PAYLOADS.items():
            datagen.generate(tmp / "in" / name, size, pattern, 5000 + off)
        paths = [tmp / "in" / n for n in V5_INPUTS[c]]
    out = tmp / "a.vnx"
    ar.build_archive(paths, out, ar.ArchiveOptions(passphrase=PASSPHRASE if encrypted else None), **kw)
    return out.read_bytes()


@pytest.mark.parametrize("s,c", PINNED, ids=IDS)
def test_builder_sections_identical_to_pre_refactor_output(s, c, tmp_path):
    new = build_like_fixture(s, c, tmp_path)
    assert compare_containers(new, stored(s, c)) == []
    sec = container_sections(new)                       # and against the pinned digests, independent of the stored file
    assert {k: sha(sec[k])[:16] for k in PINNED[(s, c)]["sections"]} == PINNED[(s, c)]["sections"]


@pytest.mark.parametrize("s,c", [k for k in PINNED if k[0] == "v6_0"], ids=[i for i in IDS if i.startswith("v6_0")])
def test_manifest_fields_other_than_the_informational_ones_are_unchanged(s, c, tmp_path):
    new = container_sections(build_like_fixture(s, c, tmp_path))["manifest"]
    old = container_sections(stored(s, c))["manifest"]
    mn, mo = json.loads(new), json.loads(old)
    assert mo["encoder"]["name"] == "vnxdna"
    for k in mo:
        if k not in ("encoder", "extensions"):
            assert mn[k] == mo[k], k
    assert "vnx" not in mo["extensions"]            # the pre-refactor manifests carry no extensions.vnx block
    assert {k: v for k, v in mn["extensions"].items() if k != "vnx"} == mo["extensions"]


# ------------------------------------------------------------------------------------------------------------- the helpers are not vacuous
@pytest.mark.parametrize("s,c", [("v5_0", "balanced"), ("v6_0", "stripes-seq"), ("v6_0", "encrypted-stripes")])
def test_comparison_tolerates_version_bump_and_extensions_vnx_only(s, c):
    old = stored(s, c)

    def later_version(m):
        m["encoder"]["version"] = "6.0.0.dev0"
        m["extensions"]["vnx"] = {"software": "6.0.0.dev0", "spec": "6.0", "container": [4, 0], "informational": True}
    new = with_manifest(old, later_version)
    assert new != old and sha(new) != sha(old)              # the whole-container hash does change ...
    assert compare_containers(new, old) == []               # ... and the section comparison still passes


@pytest.mark.parametrize("s,c", [("v5_0", "multifile"), ("v6_0", "adaptive-interleaved")])
def test_comparison_detects_every_kind_of_real_difference(s, c):
    old = stored(s, c)
    sec = container_sections(old)
    for name in ("header", "body", "chunk_table", "file_table", "refs"):
        parts = dict(sec)
        parts[name] = bytes([parts[name][0] ^ 1]) + parts[name][1:]
        assert compare_containers(b"".join(parts[n] for n in SECTION_ORDER), old) == [name]
    assert compare_containers(with_manifest(old, lambda m: m["counts"].update(files=m["counts"]["files"] + 1)), old) == ["manifest"]
    assert compare_containers(with_manifest(old, lambda m: m["extensions"].update(other={"x": 1})), old) == ["manifest"]
    assert compare_containers(with_manifest(old, lambda m: m["encoder"].update(name="other")), old) == ["manifest"]
    assert compare_containers(with_manifest(old, lambda m: m.update(archive_id="00" * 16)), old) == ["manifest"]
    flipped = bytearray(old)
    flipped[-40] ^= 1                                          # first byte of the trailer end magic
    assert compare_containers(bytes(flipped), old) == ["trailer"]
