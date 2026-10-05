"""V6 golden fixtures (tests/fixtures/v6_0): the CURRENT tree must reproduce and decode what the PRE-REFACTOR tree produced.

The fixtures are SYNTHETIC SOFTWARE TEST data: strands are encoder output of the tree at commit 1309554 (before the Phase 2
refactor), reads come from the SIMULATED channel (vnxdna.v4.channel) at recorded seeds. No DNA was synthesised or sequenced.
They are never regenerated afterwards (see the README and generate.py next to them).

Every decoder test asserts the no-false-SUCCESS property: SUCCESS is only acceptable together with the exact original bytes
(container SHA-256 and per-file SHA-256); anything else must be FAILURE/PARTIAL or a typed VNX error, with no output file.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import random
from pathlib import Path

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.errors import VNXError, VNXKeyError
from vnxdna.v4.frame import KIND_DATA

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "v6_0"
CASES = ("stripes-seq", "adaptive-interleaved", "max-recovery", "encrypted-stripes")
PASSPHRASE = "vnx-test-passphrase-NOT-A-SECRET"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def case_of(c: str) -> dict:
    return MANIFEST["cases"][c]


def key_args(c: str) -> dict:
    return {"passphrase": PASSPHRASE} if case_of(c)["encrypted"] else {}


def strands_path(c: str) -> Path:
    return FIXTURES / f"{c}.strands.fasta"


def options_of(c: str) -> en.DNAOptions:
    return en.DNAOptions(**case_of(c)["dna_options"])


@pytest.fixture(scope="module")
def plain_reads(tmp_path_factory):
    """Uncompressed copies of the gzip-stored noisy read files (the decoder does not read gzip)."""
    d = tmp_path_factory.mktemp("v6reads")
    out = {}
    for c in CASES:
        dst = d / f"{c}.fastq"
        dst.write_bytes(gzip.decompress((FIXTURES / f"{c}.reads.fastq.gz").read_bytes()))
        out[c] = dst
    return out


def decode(reads: Path, out: Path, **kw):
    workers = kw.pop("workers", 1)
    return de.decode_reads(reads, out, de.DecodeOptions(workers=workers, profile=kw.pop("profile", None)), overwrite=True, **kw)


def extracted(c: str, container: Path, dest: Path) -> dict:
    ar.extract(container, dest, overwrite=True, **key_args(c))
    return {p.relative_to(dest).as_posix(): sha(p.read_bytes()) for p in dest.rglob("*") if p.is_file()}


def assert_exact(c: str, res, out: Path, tmp: Path) -> None:
    """Strict success: SUCCESS, exact container bytes, every extracted file matches the original payload SHA-256."""
    case = case_of(c)
    assert res.status == "SUCCESS", res.report
    assert res.report["groups_failed"] == 0 and res.report["failed_groups"] == []
    assert sha(out.read_bytes()) == case["container_sha256"] == res.report["container_sha256"]
    assert extracted(c, out, tmp / "x") == case["files"]


def assert_no_false_success(c: str, res_or_exc, out: Path, *, must_fail: bool = True) -> None:
    """A damaged input never yields wrong bytes. With must_fail the damage is beyond the code's reach: not SUCCESS, no output.
    Without it SUCCESS is tolerated only with the exact original container."""
    if isinstance(res_or_exc, VNXError):
        assert not out.exists()
        return
    if res_or_exc.status == "SUCCESS":
        assert not must_fail, "false SUCCESS on an input beyond the correction capacity"
        assert sha(out.read_bytes()) == case_of(c)["container_sha256"]
        return
    assert res_or_exc.status in ("FAILURE", "PARTIAL")
    assert not out.exists()


def attempt(reads: Path, out: Path, **kw):
    try:
        return decode(reads, out, **kw)
    except VNXError as error:
        return error


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


# ------------------------------------------------------------------------------------------------------------- fixture integrity
def test_fixture_files_match_sha256sums_and_are_declared_synthetic():
    sums = [line.split("  ", 1) for line in (FIXTURES / "SHA256SUMS").read_text().splitlines() if line]
    names = {n for _, n in sums}
    assert names >= {f"{c}.{k}" for c in CASES for k in ("vnx", "strands.fasta", "reads.fastq.gz")} | {"manifest.json"}
    assert len(sums) == len(names) == 4 * 3 + 1 + 5          # per-case triple, manifest, five stored input files
    for digest, name in sums:
        assert sha((FIXTURES / name).read_bytes()) == digest, f"v6_0/{name} was modified"
    assert MANIFEST["synthetic"] is True and "no DNA was synthesised or sequenced" in MANIFEST["statement"]
    assert MANIFEST["generated_by_commit"] == "1309554"
    assert set(MANIFEST["cases"]) == set(CASES)
    readme = (FIXTURES / "README.md").read_text()
    assert "SYNTHETIC SOFTWARE TEST" in readme and "SIMULATED" in readme
    assert (FIXTURES / "generate.py").is_file()
    assert sum(p.stat().st_size for p in FIXTURES.rglob("*") if p.is_file()) < 1_600_000


@pytest.mark.parametrize("c", CASES)
def test_manifest_matches_stored_files(c):
    case = case_of(c)
    assert sha((FIXTURES / f"{c}.vnx").read_bytes()) == case["container_sha256"]
    assert sha(strands_path(c).read_bytes()) == case["strands_sha256"]
    heads = [h for h, _ in read_records(strands_path(c))]
    assert len(heads) == case["strands"]
    for rel, digest in case["files"].items():
        assert sha((FIXTURES / "inputs" / c / rel).read_bytes()) == digest
    # the geometry really is the one the case is named after (superblock version 2 = V6 outer code)
    opts = options_of(c)
    assert opts.v6


@pytest.mark.parametrize("c", CASES)
def test_stored_inputs_regenerate_from_recorded_seeds(c, tmp_path):
    for rel, p in case_of(c)["payloads"].items():
        datagen.generate(tmp_path / rel, p["size"], p["pattern"], p["seed"])
        assert (tmp_path / rel).read_bytes() == (FIXTURES / "inputs" / c / rel).read_bytes()


# ------------------------------------------------------------------------------------------------------------- stored containers
@pytest.mark.parametrize("c", CASES)
def test_stored_container_verifies_and_extracts(c, tmp_path):
    path = FIXTURES / f"{c}.vnx"
    ar.verify_container(path, **key_args(c))
    assert extracted(c, path, tmp_path / "x") == case_of(c)["files"]


# ------------------------------------------------------------------------------------------------------------- (a) encoder identity
@pytest.mark.parametrize("c", CASES)
def test_current_encoder_reproduces_fixture_strands_byte_for_byte(c, tmp_path):
    out = tmp_path / "s.fasta"
    en.encode_container(FIXTURES / f"{c}.vnx", out, options_of(c))
    assert out.read_bytes() == strands_path(c).read_bytes()
    assert sha(out.read_bytes()) == case_of(c)["strands_sha256"]


@pytest.mark.parametrize("c", CASES)
def test_encoder_output_independent_of_worker_count(c, tmp_path):
    out = tmp_path / "s.fasta"
    opts = options_of(c)
    opts.workers = 2
    opts.groups_per_task = 1                # many small tasks: any ordering bug would show
    en.encode_container(FIXTURES / f"{c}.vnx", out, opts)
    assert out.read_bytes() == strands_path(c).read_bytes()


# ------------------------------------------------------------------------------------------------------------- (b) decoder identity
@pytest.mark.parametrize("c", CASES)
def test_decode_clean_strands_exact_payload(c, tmp_path):
    out = tmp_path / "o.vnx"
    res = decode(strands_path(c), out, **key_args(c))
    assert_exact(c, res, out, tmp_path)
    r = res.report["reads"]
    assert r["sync"] == 0 and r["reverse_complement"] == 0 and r["unaligned"] == 0      # clean input needs no correction


@pytest.mark.parametrize("c", CASES)
def test_decode_noisy_reads_exact_container_and_files(c, plain_reads, tmp_path):
    out = tmp_path / "o.vnx"
    res = decode(plain_reads[c], out, **key_args(c))
    assert_exact(c, res, out, tmp_path)
    r = res.report["reads"]
    assert r["reads"] == case_of(c)["reads"]
    assert r["sync"] > 0 and r["reverse_complement"] > 0      # the simulated indels / strand flips really had to be corrected
    assert res.report["outer_v6"]                              # decoded through the V6 outer code (superblock version 2)


@pytest.mark.parametrize("c", CASES)
def test_decode_noisy_reads_with_workers_identical(c, plain_reads, tmp_path):
    out = tmp_path / "o.vnx"
    res = decode(plain_reads[c], out, workers=2)
    assert_exact(c, res, out, tmp_path)


@pytest.mark.parametrize("c", CASES)
def test_decode_shuffled_reads_identical(c, plain_reads, tmp_path):
    """Read order carries no information: a different order gives the identical container."""
    recs = plain_reads[c].read_text().splitlines()
    blocks = [recs[i:i + 4] for i in range(0, len(recs), 4)]          # FASTQ: 4 lines per record
    random.Random(3).shuffle(blocks)
    shuffled = tmp_path / "shuffled.fastq"
    shuffled.write_text("\n".join(x for b in blocks for x in b) + "\n")
    out = tmp_path / "o.vnx"
    assert_exact(c, decode(shuffled, out, **key_args(c)), out, tmp_path)


def test_decode_with_reference_rs_backend_identical(plain_reads, tmp_path, monkeypatch):
    """The NumPy reference Reed-Solomon path (VNX_RS_REFERENCE) must give the same container as the default backend."""
    import vnxdna.v4.codecs as codecs
    monkeypatch.setattr(codecs, "_REFERENCE_RS", True)
    out = tmp_path / "o.vnx"
    res = decode(plain_reads["stripes-seq"], out)
    assert_exact("stripes-seq", res, out, tmp_path)


# ------------------------------------------------------------------------------------------------------------- wrong / missing key
def test_encrypted_stripes_wrong_or_missing_passphrase_fails_closed(plain_reads, tmp_path):
    c = "encrypted-stripes"
    case = case_of(c)
    out = tmp_path / "o.vnx"
    res = decode(plain_reads[c], out)                  # container-level recovery needs no key ...
    assert res.status == "SUCCESS" and sha(out.read_bytes()) == case["container_sha256"]
    for bad in ("wrong-passphrase", "", PASSPHRASE + " ", PASSPHRASE.upper()):
        with pytest.raises(VNXKeyError):
            ar.extract(out, tmp_path / "bad", passphrase=bad, overwrite=True)
        with pytest.raises(VNXKeyError):
            ar.verify_container(out, passphrase=bad)
    with pytest.raises(VNXKeyError):
        ar.extract(out, tmp_path / "bad", overwrite=True)
    assert not [p for p in (tmp_path / "bad").rglob("*") if p.is_file()]
    sel = tmp_path / "sel"                             # ... and a key-bearing decode refuses before delivering anything
    with pytest.raises(VNXKeyError):
        de.decode_reads(plain_reads[c], tmp_path / "o2.vnx", de.DecodeOptions(), overwrite=True,
                        passphrase="wrong-passphrase", select=["random.bin"], select_dir=sel)
    assert not [p for p in sel.rglob("*") if p.is_file()]
    res = de.decode_reads(plain_reads[c], tmp_path / "o3.vnx", de.DecodeOptions(), overwrite=True,
                          passphrase=PASSPHRASE, select=["random.bin"], select_dir=sel)
    assert res.status == "SUCCESS"
    assert sha((sel / "random.bin").read_bytes()) == case["files"]["random.bin"]


def test_encrypted_stripes_fixture_does_not_contain_plaintext():
    plain = (FIXTURES / "inputs" / "encrypted-stripes" / "random.bin").read_bytes()[:256]
    assert plain not in (FIXTURES / "encrypted-stripes.vnx").read_bytes()


# ------------------------------------------------------------------------------------------------------------- selective decode
@pytest.mark.parametrize("c", ("adaptive-interleaved", "stripes-seq"))
def test_selective_decode_returns_exact_selected_file(c, plain_reads, tmp_path):
    rel = sorted(case_of(c)["files"])[0]
    sel = tmp_path / "sel"
    res = de.decode_reads(plain_reads[c], tmp_path / "o.vnx", de.DecodeOptions(), overwrite=True, select=[rel], select_dir=sel)
    assert res.status == "SUCCESS", res.report
    assert sha((sel / rel).read_bytes()) == case_of(c)["files"][rel]


# ------------------------------------------------------------------------------------------------------------- corrupted beyond correction
@pytest.mark.parametrize("c", CASES)
def test_truncated_reads_no_false_success(c, tmp_path):
    recs = read_records(strands_path(c))
    for frac, must_fail in ((0.15, True), (0.35, True), (0.6, False)):
        keep = int(len(recs) * frac)
        out = tmp_path / f"o{keep}.vnx"
        res = attempt(write_reads(tmp_path / f"t{keep}.fa", [q for _, q in recs[:keep]]), out, **key_args(c))
        assert_no_false_success(c, res, out, must_fail=must_fail)


@pytest.mark.parametrize("c", CASES)
def test_superblock_only_no_false_success(c, tmp_path):
    recs = split_headers(read_records(strands_path(c)))
    sb = [q for k, q in recs if k[0] != KIND_DATA]
    assert sb
    out = tmp_path / "o.vnx"
    assert_no_false_success(c, attempt(write_reads(tmp_path / "sb.fa", sb), out), out)


@pytest.mark.parametrize("c", CASES)
def test_data_strands_without_superblock_no_false_success(c, tmp_path):
    recs = split_headers(read_records(strands_path(c)))
    out = tmp_path / "o.vnx"
    res = attempt(write_reads(tmp_path / "nosb.fa", [q for k, q in recs if k[0] == KIND_DATA]), out)
    assert_no_false_success(c, res, out)


@pytest.mark.parametrize("c", CASES)
def test_heavily_substituted_reads_no_false_success(c, tmp_path):
    rng = random.Random(7)
    recs = read_records(strands_path(c))
    mutated = ["".join(rng.choice("ACGT") if rng.random() < 0.25 else ch for ch in q) for _, q in recs]
    out = tmp_path / "o.vnx"
    res = attempt(write_reads(tmp_path / "m.fa", mutated), out, profile=case_of(c)["profile"])
    assert_no_false_success(c, res, out)


@pytest.mark.parametrize("c", CASES)
def test_corrupted_noisy_read_file_no_false_success(c, plain_reads, tmp_path):
    """Whole read file damaged mid-way: cut in the middle of a record, and a block of lines replaced by junk."""
    raw = plain_reads[c].read_bytes()
    out = tmp_path / "o.vnx"
    cut = tmp_path / "cut.fastq"
    cut.write_bytes(raw[: len(raw) // 3 + 7])                       # 1/3 of the reads, last record cut mid-line
    assert_no_false_success(c, attempt(cut, out), out)
    lines = raw.decode().splitlines()
    lines[len(lines) // 4: 3 * len(lines) // 4] = ["ACGT" * 3] * (len(lines) // 2)       # half of the file is junk
    junk = tmp_path / "junk.fastq"
    junk.write_text("\n".join(lines) + "\n")
    assert_no_false_success(c, attempt(junk, out), out)


@pytest.mark.parametrize("c", CASES)
def test_reads_of_two_archives_mixed_never_yield_wrong_bytes(c, plain_reads, tmp_path):
    other = next(x for x in CASES if x != c and not case_of(x)["encrypted"])
    mixed = tmp_path / "mixed.fastq"
    mixed.write_bytes(plain_reads[c].read_bytes() + plain_reads[other].read_bytes())
    out = tmp_path / "o.vnx"
    res = attempt(mixed, out)
    if isinstance(res, VNXError):
        assert not out.exists()
    elif res.status == "SUCCESS":           # an ambiguity must not be resolved into bytes of neither archive
        assert sha(out.read_bytes()) in {case_of(c)["container_sha256"], case_of(other)["container_sha256"]}
    else:
        assert not out.exists()


@pytest.mark.parametrize("c,M", (("stripes-seq", 16), ("max-recovery", 32)))
def test_row_and_stripe_erasure_boundaries(c, M, tmp_path):
    """Per-row outer code: exactly M lost strands in each of three rows are corrected by the row code. M + 1 in one or two rows
    is repaired by the column parity (Mc = 2); M + 1 in three rows of one stripe exceeds both and must not give SUCCESS."""
    recs = split_headers(read_records(strands_path(c)))

    def run(lost: dict[int, int], tag: str):
        drop = {(KIND_DATA, g, i) for g, n in lost.items() for i in range(n)}
        assert drop <= {k for k, _ in recs}
        out = tmp_path / f"o{tag}.vnx"
        return attempt(write_reads(tmp_path / f"b{tag}.fa", [q for k, q in recs if k not in drop]), out, **key_args(c)), out

    res, out = run({0: M, 1: M, 2: M}, "row")
    assert not isinstance(res, VNXError) and res.status == "SUCCESS"
    assert sha(out.read_bytes()) == case_of(c)["container_sha256"] and res.report["groups_failed"] == 0
    res, out = run({0: M + 1, 1: M + 1}, "col")
    assert not isinstance(res, VNXError) and res.status == "SUCCESS"
    assert sha(out.read_bytes()) == case_of(c)["container_sha256"]
    res, out = run({0: M + 1, 1: M + 1, 2: M + 1}, "over")
    assert_no_false_success(c, res, out)


@pytest.mark.parametrize("name,body", (("empty.fa", ""), ("garbage.fa", ">a\nACGTACGT\n>b\nTTTT\n"), ("text.fa", "not dna at all\n"),
                                       ("long.fa", ">x\n" + "ACGT" * 30000 + "\n")))
def test_empty_and_garbage_inputs_fail_closed(name, body, tmp_path):
    out = tmp_path / f"{name}.vnx"
    p = tmp_path / name
    p.write_text(body)
    assert_no_false_success("stripes-seq", attempt(p, out), out)


@pytest.mark.parametrize("c", CASES)
def test_corrupted_container_bytes_never_verify(c, tmp_path):
    case = case_of(c)
    raw = (FIXTURES / f"{c}.vnx").read_bytes()
    for pos in (3, 11, len(raw) // 4, len(raw) // 2, len(raw) - 5):
        bad = bytearray(raw)
        bad[pos] ^= 0x01
        p = tmp_path / f"bad{pos}.vnx"
        p.write_bytes(bad)
        with pytest.raises(VNXError):
            ar.verify_container(p, **key_args(c))
        dest = tmp_path / f"x{pos}"
        try:
            ar.extract(p, dest, **key_args(c))
        except VNXError:
            pass
        for q in dest.rglob("*"):               # whatever was delivered must be exact
            if q.is_file():
                assert sha(q.read_bytes()) == case["files"][q.relative_to(dest).as_posix()]


@pytest.mark.parametrize("c", CASES)
def test_truncated_container_never_verifies(c, tmp_path):
    raw = (FIXTURES / f"{c}.vnx").read_bytes()
    for n in (0, 8, 15, 16, 100, len(raw) - 1, len(raw) - 112):
        p = tmp_path / f"t{n}.vnx"
        p.write_bytes(raw[:n])
        with pytest.raises(VNXError):
            ar.verify_container(p, **key_args(c))
