"""Backward compatibility: the CURRENT tree must decode archives produced by the RELEASED V4 (d235239) and V5 (6aef3f4) trees.

Fixtures in tests/fixtures/v4_0 and tests/fixtures/v5_0 are SYNTHETIC SOFTWARE TEST data (encoder output and a SIMULATED
channel at fixed seeds). No DNA was synthesised or sequenced. See the README and generate.py next to each fixture set.

Every decoder test asserts the no-false-SUCCESS property: a SUCCESS status is only acceptable together with the exact original
bytes (container SHA-256 and per-file SHA-256); anything else must be FAILURE/PARTIAL or a typed VNX error, with no output.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import container as ct
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.errors import VNXError, VNXKeyError
from vnxdna.v4.frame import KIND_DATA

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SETS = ("v4_0", "v5_0")
CASES = ("balanced", "archival", "encrypted", "multifile")
PARAMS = [(s, c) for s in SETS for c in CASES]
IDS = [f"{s}-{c}" for s, c in PARAMS]
PASSPHRASE = "vnx-test-passphrase-NOT-A-SECRET"
FIXED_ID, FIXED_SALT = bytes(range(1, 17)), bytes(range(101, 117))
# payload generator seeds recorded in each generate.py (name -> (pattern, size, seed offset))
PAYLOADS = {"mixed.bin": ("mixed", 6144, 1), "text.txt": ("text", 3072, 2), "random.bin": ("random", 2048, 3),
            "sub/bin.dat": ("binary", 1500, 4)}
SEED_BASE = {"v4_0": 4000, "v5_0": 5000}
INPUTS = {"balanced": ["mixed.bin"], "archival": ["mixed.bin"], "encrypted": ["mixed.bin"],
          "multifile": ["mixed.bin", "text.txt", "random.bin", "sub"]}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def manifest(s: str) -> dict:
    return json.loads((FIXTURES / s / "manifest.json").read_text())


def case_of(s: str, c: str) -> dict:
    return manifest(s)["cases"][c]


def key_args(case: dict) -> dict:
    return {"passphrase": PASSPHRASE} if case["encrypted"] else {}


@pytest.fixture(scope="module")
def plain_reads(tmp_path_factory):
    """Uncompressed copies of the gzip-stored noisy read files (the decoder does not read gzip)."""
    d = tmp_path_factory.mktemp("reads")
    out = {}
    for s, c in PARAMS:
        dst = d / f"{s}-{c}.fastq"
        dst.write_bytes(gzip.decompress((FIXTURES / s / f"{c}.reads.fastq.gz").read_bytes()))
        out[(s, c)] = dst
    return out


def strands_path(s: str, c: str) -> Path:
    return FIXTURES / s / f"{c}.strands.fasta"


def decode(reads: Path, out: Path, **kw):
    return de.decode_reads(reads, out, de.DecodeOptions(profile=kw.pop("profile", None)), overwrite=True, **kw)


def assert_exact(s: str, c: str, res, out: Path, tmp: Path) -> None:
    """Strict success: SUCCESS, exact container bytes, every extracted file matches the original payload SHA-256."""
    case = case_of(s, c)
    assert res.status == "SUCCESS", res.report
    assert res.report["groups_failed"] == 0 and res.report["failed_groups"] == []
    assert sha(out.read_bytes()) == case["container_sha256"] == res.report["container_sha256"]
    ar.extract(out, tmp / "x", overwrite=True, **key_args(case))
    got = {p.relative_to(tmp / "x").as_posix(): sha(p.read_bytes()) for p in (tmp / "x").rglob("*") if p.is_file()}
    assert got == case["files"]


def assert_no_false_success(res_or_exc, out: Path) -> None:
    """A damaged input must never yield SUCCESS or an output file."""
    if isinstance(res_or_exc, VNXError):
        assert not out.exists()
        return
    assert res_or_exc.status != "SUCCESS", "false SUCCESS on a corrupted input"
    assert res_or_exc.status in ("FAILURE", "PARTIAL")
    assert not out.exists()


def attempt(reads: Path, out: Path, **kw):
    try:
        return decode(reads, out, **kw)
    except VNXError as error:
        return error


# ------------------------------------------------------------------------------------------------------------- fixture integrity
@pytest.mark.parametrize("s", SETS)
def test_fixture_files_match_sha256sums_and_are_declared_synthetic(s):
    d = FIXTURES / s
    sums = [line.split("  ", 1) for line in (d / "SHA256SUMS").read_text().splitlines() if line]
    assert len(sums) == 13 and {n for _, n in sums} >= {f"{c}.{k}" for c in CASES for k in ("vnx", "strands.fasta")} | {"manifest.json"}
    for digest, name in sums:
        assert sha((d / name).read_bytes()) == digest, f"{s}/{name} was modified"
    m = manifest(s)
    assert m["synthetic"] is True and "no DNA was synthesised or sequenced" in m["statement"]
    assert m["generated_by_commit"] == {"v4_0": "d235239", "v5_0": "6aef3f4"}[s]
    assert "SYNTHETIC SOFTWARE TEST" in (d / "README.md").read_text()
    assert sum(p.stat().st_size for p in d.iterdir()) < 1_000_000


# ------------------------------------------------------------------------------------------------------------- stored containers
@pytest.mark.parametrize("s,c", PARAMS, ids=IDS)
def test_stored_container_verifies_and_extracts(s, c, tmp_path):
    case = case_of(s, c)
    path = FIXTURES / s / f"{c}.vnx"
    assert sha(path.read_bytes()) == case["container_sha256"]
    ar.verify_container(path, **key_args(case))
    ar.extract(path, tmp_path / "x", **key_args(case))
    got = {p.relative_to(tmp_path / "x").as_posix(): sha(p.read_bytes()) for p in (tmp_path / "x").rglob("*") if p.is_file()}
    assert got == case["files"]


# ------------------------------------------------------------------------------------------------------------- decoding strands
@pytest.mark.parametrize("s,c", PARAMS, ids=IDS)
def test_decode_clean_strands_exact_payload(s, c, tmp_path):
    out = tmp_path / "o.vnx"
    res = decode(strands_path(s, c), out, **key_args(case_of(s, c)))
    assert_exact(s, c, res, out, tmp_path)
    r = res.report["reads"]
    assert r["sync"] == 0 and r["reverse_complement"] == 0 and r["unaligned"] == 0      # clean input needs no correction


@pytest.mark.parametrize("s,c", PARAMS, ids=IDS)
def test_decode_noisy_reads_exact_payload(s, c, plain_reads, tmp_path):
    out = tmp_path / "o.vnx"
    res = decode(plain_reads[(s, c)], out, **key_args(case_of(s, c)))
    assert_exact(s, c, res, out, tmp_path)
    r = res.report["reads"]
    assert r["reads"] == case_of(s, c)["reads"]
    assert r["sync"] > 0 and r["reverse_complement"] > 0      # the simulated indels / strand flips really had to be corrected


@pytest.mark.parametrize("s,c", PARAMS, ids=IDS)
def test_decode_noisy_reads_with_workers_identical(s, c, plain_reads, tmp_path):
    out = tmp_path / "o.vnx"
    res = de.decode_reads(plain_reads[(s, c)], out, de.DecodeOptions(workers=2), overwrite=True)
    assert_exact(s, c, res, out, tmp_path)


# ------------------------------------------------------------------------------------------------------------- encoder / builder compat
@pytest.mark.parametrize("s,c", PARAMS, ids=IDS)
def test_current_encoder_reproduces_fixture_strands_byte_for_byte(s, c, tmp_path):
    case = case_of(s, c)
    out = tmp_path / "s.fasta"
    opts = en.DNAOptions(profile=case["profile"])
    assert not opts.v6                                    # V6 outer code is opt-in; the default must stay the V4/V5 format
    en.encode_container(FIXTURES / s / f"{c}.vnx", out, opts)
    assert out.read_bytes() == strands_path(s, c).read_bytes()


@pytest.mark.parametrize("s,c", PARAMS, ids=IDS)
def test_current_builder_reproduces_fixture_container(s, c, tmp_path, monkeypatch):
    """Identical container bytes, except the informational manifest fields that name the producing software.

    Restated in V6 Phase 2.4 (founder-approved, V6_ARCHITECTURE §8 decision 1): 5.0.0 compared the whole-container
    SHA-256 with the package version pinned to the generator's; from 6.0.0.dev0 the manifest also carries the writer
    provenance block ``extensions.vnx`` (spec §2.3.1), so the whole hash necessarily differs. The intent is kept in
    full: header, body, chunk table, file table and reference table are byte-identical to the stored fixture, the
    manifest equals it after removing ``extensions.vnx`` and normalising ``encoder.version``, and the trailer's
    structural fields are identical (tests/compat/test_byte_identity.compare_containers).
    """
    from compat.test_byte_identity import compare_containers
    case = case_of(s, c)
    monkeypatch.setattr(ct, "__version__", {"v4_0": "4.0.0", "v5_0": "5.0.0"}[s])
    for name, (pattern, size, off) in PAYLOADS.items():
        datagen.generate(tmp_path / "in" / name, size, pattern, SEED_BASE[s] + off)
    out = tmp_path / "a.vnx"
    kw = dict(archive_id=FIXED_ID, salt=FIXED_SALT) if case["encrypted"] else {}
    ar.build_archive([tmp_path / "in" / n for n in INPUTS[c]], out,
                     ar.ArchiveOptions(passphrase=PASSPHRASE if case["encrypted"] else None), **kw)
    stored = (FIXTURES / s / f"{c}.vnx").read_bytes()
    assert sha(stored) == case["container_sha256"]
    assert compare_containers(out.read_bytes(), stored) == []


# ------------------------------------------------------------------------------------------------------------- wrong / missing key
@pytest.mark.parametrize("s", SETS)
def test_wrong_or_missing_passphrase_fails_closed(s, plain_reads, tmp_path):
    """The DNA decode recovers the (encrypted) container; every key-bearing step must refuse a wrong key and write nothing."""
    case = case_of(s, "encrypted")
    out = tmp_path / "o.vnx"
    res = decode(plain_reads[(s, "encrypted")], out)             # container-level recovery needs no key ...
    assert res.status == "SUCCESS" and sha(out.read_bytes()) == case["container_sha256"]
    plaintext = case["files"]["mixed.bin"]
    for bad in ("wrong-passphrase", "", PASSPHRASE + " ", PASSPHRASE.upper()):
        with pytest.raises(VNXKeyError):
            ar.extract(out, tmp_path / "bad", passphrase=bad, overwrite=True)
        with pytest.raises(VNXKeyError):
            ar.verify_container(out, passphrase=bad)
    with pytest.raises(VNXKeyError):
        ar.extract(out, tmp_path / "bad", overwrite=True)        # no key at all
    with pytest.raises(VNXKeyError):
        ar.extract(out, tmp_path / "bad", key=bytes(32), overwrite=True)    # wrong raw key
    assert not [p for p in (tmp_path / "bad").rglob("*") if p.is_file()]
    # ... and a key-bearing decode (selective extraction) must refuse before delivering anything
    sel = tmp_path / "sel"
    with pytest.raises(VNXKeyError):
        de.decode_reads(plain_reads[(s, "encrypted")], tmp_path / "o2.vnx", de.DecodeOptions(), overwrite=True,
                        passphrase="wrong-passphrase", select=["mixed.bin"], select_dir=sel)
    assert not [p for p in sel.rglob("*") if p.is_file()]
    # the right passphrase works on the same input and returns exactly the original bytes
    res = de.decode_reads(plain_reads[(s, "encrypted")], tmp_path / "o3.vnx", de.DecodeOptions(), overwrite=True,
                          passphrase=PASSPHRASE, select=["mixed.bin"], select_dir=sel)
    assert res.status == "SUCCESS"
    assert sha((sel / "mixed.bin").read_bytes()) == plaintext


@pytest.mark.parametrize("s", SETS)
def test_encrypted_fixture_does_not_contain_plaintext(s):
    plain = gzip_free_payload(s)
    assert plain not in (FIXTURES / s / "encrypted.vnx").read_bytes()


def gzip_free_payload(s: str) -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        datagen.generate(Path(d) / "p", 6144, "mixed", SEED_BASE[s] + 1)
        return (Path(d) / "p").read_bytes()[:256]


# ------------------------------------------------------------------------------------------------------------- corrupted beyond correction
def read_records(path: Path) -> list[tuple[str, str]]:
    lines = path.read_text().splitlines()
    return [(lines[i][1:], lines[i + 1]) for i in range(0, len(lines), 2)]


def write_reads(path: Path, seqs: list[str]) -> Path:
    path.write_text("".join(f">read{i}\n{q}\n" for i, q in enumerate(seqs)))
    return path


def split_headers(recs):
    out = []
    for head, seq in recs:
        _, _, kind, g, sym = head.split("|")
        out.append(((int(kind), int(g), int(sym)), seq))
    return out


@pytest.mark.parametrize("s,c", [(s, c) for s in SETS for c in ("balanced", "archival", "encrypted")])
def test_truncated_reads_no_false_success(s, c, tmp_path):
    recs = read_records(strands_path(s, c))
    for keep in (len(recs) // 5, len(recs) * 2 // 5, len(recs) * 3 // 5):
        out = tmp_path / f"o{keep}.vnx"
        res = attempt(write_reads(tmp_path / f"t{keep}.fa", [q for _, q in recs[:keep]]), out)
        assert_no_false_success(res, out)


@pytest.mark.parametrize("s,c", [(s, c) for s in SETS for c in ("balanced", "encrypted")])
def test_superblock_only_no_false_success(s, c, tmp_path):
    recs = split_headers(read_records(strands_path(s, c)))
    sb = [q for k, q in recs if k[0] != KIND_DATA]
    assert sb
    out = tmp_path / "o.vnx"
    assert_no_false_success(attempt(write_reads(tmp_path / "sb.fa", sb), out), out)


@pytest.mark.parametrize("s", SETS)
def test_heavily_substituted_reads_no_false_success(s, tmp_path):
    import random
    rng = random.Random(7)
    recs = read_records(strands_path(s, "balanced"))
    mutated = ["".join(rng.choice("ACGT") if rng.random() < 0.25 else ch for ch in q) for _, q in recs]
    out = tmp_path / "o.vnx"
    res = attempt(write_reads(tmp_path / "m.fa", mutated), out, profile="v4-balanced")
    assert_no_false_success(res, out)


@pytest.mark.parametrize("s", SETS)
def test_outer_code_boundary_M_erasures_recover_M_plus_one_does_not(s, tmp_path):
    """K=64, M=16 per group: exactly 16 lost strands are corrected; 17 must not give SUCCESS."""
    case = case_of(s, "balanced")
    recs = split_headers(read_records(strands_path(s, "balanced")))
    for lost, ok in ((16, True), (17, False)):
        drop = {(KIND_DATA, 0, i) for i in range(lost)}
        out = tmp_path / f"o{lost}.vnx"
        res = attempt(write_reads(tmp_path / f"b{lost}.fa", [q for k, q in recs if k not in drop]), out)
        if ok:
            assert not isinstance(res, VNXError) and res.status == "SUCCESS"
            assert sha(out.read_bytes()) == case["container_sha256"]
            assert res.report["groups_failed"] == 0
        else:
            assert_no_false_success(res, out)


@pytest.mark.parametrize("s", SETS)
def test_empty_and_garbage_inputs_fail_closed(s, tmp_path):
    for name, body in (("empty.fa", ""), ("garbage.fa", ">a\nACGTACGT\n>b\nTTTT\n"), ("text.fa", "not dna at all\n")):
        out = tmp_path / f"{name}.vnx"
        p = tmp_path / name
        p.write_text(body)
        assert_no_false_success(attempt(p, out), out)


@pytest.mark.parametrize("s,c", [(s, c) for s in SETS for c in ("balanced", "encrypted", "multifile")])
def test_corrupted_container_bytes_never_verify(s, c, tmp_path):
    case = case_of(s, c)
    raw = (FIXTURES / s / f"{c}.vnx").read_bytes()
    for pos in (3, len(raw) // 4, len(raw) // 2, len(raw) - 5):
        bad = bytearray(raw)
        bad[pos] ^= 0x01
        p = tmp_path / f"bad{pos}.vnx"
        p.write_bytes(bad)
        with pytest.raises(VNXError):
            ar.verify_container(p, **key_args(case))
        # extract() verifies chunk IDs and file SHA-256, not the container trailer: a flip that only touches the trailer may
        # still extract, but then the bytes must be exactly the originals. Wrong data may never be delivered.
        try:
            ar.extract(p, tmp_path / f"x{pos}", **key_args(case))
        except VNXError:
            # files are verified one by one before being renamed into place, so files before the damage may exist; each exact
            for q in (tmp_path / f"x{pos}").rglob("*"):
                if q.is_file():
                    assert sha(q.read_bytes()) == case["files"][q.relative_to(tmp_path / f"x{pos}").as_posix()]
        else:
            got = {q.relative_to(tmp_path / f"x{pos}").as_posix(): sha(q.read_bytes())
                   for q in (tmp_path / f"x{pos}").rglob("*") if q.is_file()}
            assert got == case["files"]
