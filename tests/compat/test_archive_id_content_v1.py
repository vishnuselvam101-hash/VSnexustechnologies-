"""Spec §2.3.2 ``content-v1`` archive ID (V6 Phase 6; V6-SEC-11): opt-in, content-derived, for unencrypted archives.

Acceptance (V6_ARCHITECTURE §8 Phase 6): identical body and tables under both derivations; different content gives
different IDs; old readers open it (the released 5.0.0 reader, run from the 6aef3f4 tree); the default derivation is
unchanged. SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vnxdna.archive import container as ct
from vnxdna.archive import operations as ar
from vnxdna.benchmark import datagen
from vnxdna.commands.cli import app
from vnxdna.core import schema
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.core.util import canonical_json
from vnxdna.pipeline import encode as en
from vnxdna.pipeline.decode import decode_reads
from vnxdna.recovery.options import DecodeOptions

REPO = Path(__file__).resolve().parents[2]
V5_COMMIT = "6aef3f4"


def _inputs(d: Path, seed: int = 4101) -> Path:
    src = d / "ds"
    (src / "sub").mkdir(parents=True)
    datagen.generate(src / "a.bin", 11_000, "mixed", seed)
    datagen.generate(src / "sub" / "b.txt", 6_000, "text", seed + 1)
    datagen.generate(src / "c.bin", 5_000, "repetitive", seed + 2)       # dedup + zstd paths
    return src


def _sections(path: Path) -> dict:
    """Header, body, chunk table, file table and refs as stored (everything before the manifest)."""
    _, (body, c, f, r, m), _, _ = ct.read_header_trailer(path)
    raw = path.read_bytes()
    h = ct.HEADER_BYTES
    return {"header": raw[:h], "body": raw[h:h + body], "chunk_table": raw[h + body:h + body + c],
            "file_table": raw[h + body + c:h + body + c + f], "refs": raw[h + body + c + f:h + body + c + f + r]}


def _content_v1(path: Path, opt: ar.ArchiveOptions) -> bytes:
    """Spec §2.3.2, recomputed independently from the stored bytes."""
    s = _sections(path)
    options = f"{opt.chunk_size}|{opt.compression}|{opt.level}|{opt.dedup}|{opt.preserve_metadata}".encode()
    root = bytes.fromhex(ct.open_container(path).manifest["integrity"]["merkle_root"])
    return hashlib.sha256(b"VNX6 archive-id\x00" + options + root + hashlib.sha256(s["file_table"]).digest()).digest()[:16]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = tmp_path_factory.mktemp("cv1")
    src = _inputs(d)
    o = ar.ArchiveOptions(chunk_size=4096)
    c = ar.ArchiveOptions(chunk_size=4096, archive_id="content")
    ar.build_archive([src], d / "options.vnx", o)
    rep = ar.build_archive([src], d / "content.vnx", c)
    return d, src, o, c, rep


def test_default_derivation_is_unchanged(built, tmp_path):
    d, src, o, _, _ = built
    assert ar.ArchiveOptions().archive_id == "options"
    m = ct.open_container(d / "options.vnx").manifest
    assert m["extensions"]["vnx"]["archive_id_derivation"] == "options-v1"
    ar.build_archive([src], tmp_path / "again.vnx", ar.ArchiveOptions(chunk_size=4096, archive_id="options"))
    assert (tmp_path / "again.vnx").read_bytes() == (d / "options.vnx").read_bytes()     # explicit = default, bytewise


def test_identical_body_and_tables_under_both_derivations(built):
    d, *_ = built
    a, b = _sections(d / "options.vnx"), _sections(d / "content.vnx")
    for name in a:
        assert a[name] == b[name], name
    ma, mb = ct.open_container(d / "options.vnx").manifest, ct.open_container(d / "content.vnx").manifest
    assert ma["archive_id"] != mb["archive_id"]
    assert mb["extensions"]["vnx"]["archive_id_derivation"] == "content-v1"
    for m in (ma, mb):                                   # only the ID and its derivation name differ
        m.pop("archive_id")
        m["extensions"]["vnx"].pop("archive_id_derivation")
    assert ma == mb


def test_content_v1_matches_the_specified_formula(built):
    d, _, _, c, rep = built
    want = _content_v1(d / "content.vnx", c)
    assert ct.open_container(d / "content.vnx").archive_id == want and rep.archive_id == want.hex()


def test_identical_inputs_give_identical_ids(built, tmp_path):
    d, src, _, _, _ = built
    ar.build_archive([src], tmp_path / "w4.vnx", ar.ArchiveOptions(chunk_size=4096, archive_id="content", workers=4))
    assert (tmp_path / "w4.vnx").read_bytes() == (d / "content.vnx").read_bytes()


def test_different_content_gives_different_ids_where_options_v1_collides(tmp_path):
    # same paths, same sizes, different bytes: options-v1 gives one ID (V6-SEC-11), content-v1 two
    for seed, name in ((1, "x"), (2, "y")):
        (tmp_path / name).mkdir()
        datagen.generate(tmp_path / name / "f.bin", 8_000, "random", 4200 + seed)
    ids = {}
    for name in ("x", "y"):
        for mode in ("options", "content"):
            out = tmp_path / f"{name}-{mode}.vnx"
            ar.build_archive([tmp_path / name / "f.bin"], out, ar.ArchiveOptions(archive_id=mode))
            ids[name, mode] = ct.open_container(out).archive_id
    assert ids["x", "options"] == ids["y", "options"]
    assert ids["x", "content"] != ids["y", "content"]
    # a one-byte change too, and different options for the same content
    data = bytearray((tmp_path / "x" / "f.bin").read_bytes())
    data[4000] ^= 1
    (tmp_path / "z").mkdir()
    (tmp_path / "z" / "f.bin").write_bytes(bytes(data))
    ar.build_archive([tmp_path / "z" / "f.bin"], tmp_path / "z.vnx", ar.ArchiveOptions(archive_id="content"))
    assert ct.open_container(tmp_path / "z.vnx").archive_id not in (ids["x", "content"], ids["y", "content"])
    ar.build_archive([tmp_path / "x" / "f.bin"], tmp_path / "xl.vnx", ar.ArchiveOptions(archive_id="content", level=9))
    assert ct.open_container(tmp_path / "xl.vnx").archive_id != ids["x", "content"]


def test_content_v1_is_refused_for_encrypted_archives_and_unknown_values(tmp_path):
    datagen.generate(tmp_path / "f.bin", 3_000, "random", 4301)
    with pytest.raises(VNXConfigurationError):
        ar.build_archive([tmp_path / "f.bin"], tmp_path / "e.vnx",
                         ar.ArchiveOptions(archive_id="content", passphrase="pw-TEST-ONLY", scrypt={"n": 1024, "r": 8, "p": 1}))
    with pytest.raises(VNXConfigurationError):
        ar.build_archive([tmp_path / "f.bin"], tmp_path / "u.vnx", ar.ArchiveOptions(archive_id="random"))
    with pytest.raises(VNXConfigurationError):          # a caller-supplied ID cannot also be content-derived
        ar.build_archive([tmp_path / "f.bin"], tmp_path / "s.vnx", ar.ArchiveOptions(archive_id="content"),
                         archive_id=bytes(16))
    assert not any(tmp_path.glob("*.vnx"))


def test_current_reader_and_dna_round_trip(built, tmp_path):
    d, src, *_ = built
    assert ar.verify_container(d / "content.vnx")["status"] == "VERIFIED"
    en.encode_container(d / "content.vnx", tmp_path / "s.fasta", en.DNAOptions())
    res = decode_reads(tmp_path / "s.fasta", tmp_path / "o.vnx", DecodeOptions(
        expect_archive_id=ct.open_container(d / "content.vnx").manifest["archive_id"]))
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == (d / "content.vnx").read_bytes()
    ar.extract(tmp_path / "o.vnx", tmp_path / "x")
    for p in src.rglob("*"):
        if p.is_file():
            assert (tmp_path / "x" / "ds" / p.relative_to(src)).read_bytes() == p.read_bytes()


def test_verify_recomputes_content_v1_and_warns_on_mismatch(built, tmp_path):
    d, *_ = built
    rep = ar.verify_container(d / "content.vnx")
    assert rep["archive_id_derivation"] == {"name": "content-v1", "recomputed": True, "matches": True}
    assert not rep.get("warnings")
    # a manifest that claims content-v1 with another ID: a warning, never an integrity failure (the ID is a name)
    m = ct.open_container(d / "content.vnx").manifest
    m["archive_id"] = "00" * 16
    man = canonical_json(m)
    raw = (d / "content.vnx").read_bytes()
    _, (body, c, f, r, _m), _, _ = ct.read_header_trailer(d / "content.vnx")
    head = raw[: ct.HEADER_BYTES + body + c + f + r]
    data = head + man + struct.pack(">QQQQQ", body, c, f, r, len(man)) + hashlib.sha256(man).digest() + ct.TRAILER_MAGIC
    bad = tmp_path / "bad.vnx"
    bad.write_bytes(data + hashlib.sha256(data).digest())
    rep = ar.verify_container(bad)
    assert rep["status"] == "VERIFIED"
    assert rep["archive_id_derivation"]["matches"] is False
    assert [w["code"] for w in rep["warnings"]] == ["ARCHIVE_ID_DERIVATION_MISMATCH"]
    r = CliRunner().invoke(app, ["verify", str(bad)])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.stdout)
    schema.validate(doc, doc["schema"])
    assert doc["warnings"][0]["code"] == "ARCHIVE_ID_DERIVATION_MISMATCH"


def test_cli_archive_id_option(tmp_path):
    datagen.generate(tmp_path / "f.bin", 3_000, "random", 4401)
    r = CliRunner().invoke(app, ["archive", str(tmp_path / "f.bin"), str(tmp_path / "c.vnx"), "--archive-id", "content"])
    assert r.exit_code == 0, r.output
    assert ct.open_container(tmp_path / "c.vnx").manifest["extensions"]["vnx"]["archive_id_derivation"] == "content-v1"
    r = CliRunner().invoke(app, ["archive", str(tmp_path / "f.bin"), str(tmp_path / "u.vnx"), "--archive-id", "bogus"])
    assert r.exit_code == 7, r.output
    assert not (tmp_path / "u.vnx").exists()


# ------------------------------------------------------------------------------------------------ the 5.0.0 reader
@pytest.fixture(scope="module")
def v5_tree(tmp_path_factory):
    """The released 5.0.0 source tree (6aef3f4), unpacked from this repository's history."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    try:
        tar = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", V5_COMMIT, "src"], check=True,
                             capture_output=True, timeout=120).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        pytest.skip(f"the 5.0.0 commit {V5_COMMIT} is not in this clone: {error}")
    d = tmp_path_factory.mktemp("v5tree")
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(d, filter="data")
    return d / "src"


V5_SCRIPT = r"""
import json, sys
from pathlib import Path
import vnxdna
from vnxdna.v4 import archive as ar, container as ct, decoder as de
assert vnxdna.__version__ == "5.0.0", vnxdna.__version__
assert str(Path(vnxdna.__file__).resolve()).startswith(sys.argv[1]), vnxdna.__file__
c = ct.open_container(sys.argv[2])
v = ar.verify_container(sys.argv[2])
x = ar.extract(sys.argv[2], sys.argv[3])
res = de.decode_reads(sys.argv[4], sys.argv[5], de.DecodeOptions())
print(json.dumps({"archive_id": c.manifest["archive_id"], "verify": v["status"], "files": x["files"],
                  "decode": res.status}))
"""


def test_released_5_0_0_reader_opens_a_content_v1_archive(built, v5_tree, tmp_path):
    d, src, *_ = built
    arc = d / "content.vnx"
    en.encode_container(arc, tmp_path / "s.fasta", en.DNAOptions())
    env = {k: v for k, v in os.environ.items() if not k.startswith("VNXDNA_")}
    env["PYTHONPATH"] = str(v5_tree)
    out = subprocess.run([sys.executable, "-c", V5_SCRIPT, str(v5_tree.resolve()), str(arc), str(tmp_path / "x"),
                          str(tmp_path / "s.fasta"), str(tmp_path / "o.vnx")], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-3000:]
    doc = json.loads(out.stdout.strip().splitlines()[-1])
    assert doc == {"archive_id": ct.open_container(arc).manifest["archive_id"], "verify": "VERIFIED", "files": 3,
                   "decode": "SUCCESS"}
    assert (tmp_path / "o.vnx").read_bytes() == arc.read_bytes()
    for p in src.rglob("*"):
        if p.is_file():
            assert (tmp_path / "x" / "ds" / p.relative_to(src)).read_bytes() == p.read_bytes()
