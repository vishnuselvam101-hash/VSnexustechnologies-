"""The V15 hardware lane: the H-series documents, the LR-1 golden corpus and the LR-3 reference decoder.

WHAT THIS TEST IS
    A consistency gate over the hardware/OS lane:

    1. the H-series documents exist and carry the disclaimers they must carry (no document may
       claim that DNA was synthesised, stored or sequenced, or that hardware was measured);
    2. the LR-1 golden corpus exists, and every artefact in it still has the digest recorded in
       ``reference/golden/index.json``;
    3. every golden archive decodes to exactly the bytes it was made from -- checked against the
       recorded payload SHA-256, from the container *and* from the strand pool;
    4. the production decoder and the LR-3 reference decoder agree bit for bit on the corpus;
    5. the reference decoder imports nothing outside the Python standard library.

THE TEST MUST FAIL WHEN
    a golden archive is missing, altered, truncated or replaced; when a recorded digest does not
    match the file; when a decoder returns bytes whose SHA-256 is not the expected one; when the
    two decoders disagree; or when the reference decoder grows a third-party import. Every one
    of those is a direct consequence of the check, not an accident of the implementation.

EVIDENCE
    MEASURED for the software behaviour: the corpus is decoded and digested here, on every run.
    SIMULATED for the channel: every read set in the corpus is software-produced. No DNA was
    synthesised, stored or sequenced, and no hardware was built or measured.

references:
    docs/V15_REFERENCE_DECODER.md    (the compatibility contract and the rule)
    docs/V15_FINALIZATION_PLAN.md     (LR-1, LR-3)
    reference/vnx_ref_decoder.py     (the reference decoder)
    reference/make_golden.py         (the corpus builder)
    reference/golden/index.json      (the frozen digests)
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "reference" / "golden"
INDEX = GOLDEN / "index.json"
REF_DECODER = REPO / "reference" / "vnx_ref_decoder.py"
MAKE_GOLDEN = REPO / "reference" / "make_golden.py"
HARDWARE = REPO / "hardware"
CONTRACT = REPO / "docs" / "V15_REFERENCE_DECODER.md"

#: the H-series documents, by their place in the tree
H1 = HARDWARE / "h1-carrier" / "netlist.md"
H3_FILES = ("rs_decode.v", "tb_rs_decode.v", "differential_check.py", "Makefile")
H5 = HARDWARE / "h5-dna-io" / "DNA_IO_SPEC.md"

#: phrases that would claim a physical result this project does not have
FORBIDDEN_CLAIMS = (
    "dna was synthesised",
    "dna was synthesized",
    "dna was stored",
    "dna was sequenced",
    "we synthesised",
    "we synthesized",
    "we sequenced",
    "was sequenced in",
    "measured on hardware",
    "measured on a sequencer",
    "measured on the fpga",
)

#: markers that turn a claim into a disclaimer.  A claim is allowed only when the sentence
#: that carries it also carries one of these; a bare claim in a sentence without a negation
#: is an overstatement and fails the test.
NEGATIONS = ("no ", "not ", "never ", "nothing ", "neither ", "without ", "cannot ", "n't ", "zero ")

#: modules that are part of the standard library but may be newer than the running
#: interpreter.  ``compression`` (which holds ``compression.zstd``) is standard library
#: from Python 3.14; the reference decoder imports it inside a guarded try/except and
#: raises a typed refusal when it is absent.
FUTURE_STDLIB = {"compression"}


def _sentences(text: str) -> list[str]:
    """Split a text into sentences, keeping the sentence terminators."""
    out: list[str] = []
    current: list[str] = []
    for char in text:
        current.append(char)
        if char in ".!?":
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(scope="module")
def index() -> dict:
    """The golden corpus index: the frozen digests."""
    assert INDEX.is_file(), f"the golden corpus index is missing: {INDEX}"
    return json.loads(INDEX.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entries(index: dict) -> list[dict]:
    return index["entries"]


# --------------------------------------------------------------------------------------
# 1. the H-series documents
# --------------------------------------------------------------------------------------

def test_h_series_documents_exist():
    """Every H-series document the lane delivers is present."""
    assert H1.is_file(), f"missing: {H1}"
    assert H5.is_file(), f"missing: {H5}"
    for name in H3_FILES:
        path = HARDWARE / "h3-fpga" / name
        assert path.is_file(), f"missing: {path}"


def test_h3_fpga_rtl_and_testbench_are_present_and_non_trivial():
    """The RTL and the testbench are real files with real content."""
    for name, minimum in (("rs_decode.v", 200), ("tb_rs_decode.v", 100)):
        path = HARDWARE / "h3-fpga" / name
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= minimum, f"{name} has only {len(lines)} lines"
    rtl = (HARDWARE / "h3-fpga" / "rs_decode.v").read_text(encoding="utf-8")
    # the decoder must implement the field, the syndromes and the error locator
    for token in ("module rs_decode", "gf_mul", "gf_inv", "syndrome", "erasure", "chien", "forney"):
        assert token in rtl.lower(), f"rs_decode.v does not mention {token!r}"


@pytest.mark.parametrize("path", [H1, H5], ids=["h1-netlist", "h5-dna-io"])
def test_h_documents_state_their_evidence_rung(path: Path):
    """Each H document states what it is: a design, not a measurement."""
    text = path.read_text(encoding="utf-8")
    head = text[:2000].upper()
    assert "DESIGN ONLY" in head or "SPECIFICATION ONLY" in head, (
        f"{path.name} does not state its status in its first lines")
    assert "NOT MEASURED" in head or "NOTHING WAS" in head, (
        f"{path.name} does not state that nothing was measured")


@pytest.mark.parametrize("path", [H1, H5, HARDWARE / "h3-fpga" / "differential_check.py"],
                         ids=["h1-netlist", "h5-dna-io", "h3-differential-check"])
def test_h_documents_never_claim_a_physical_result(path: Path):
    """No document may claim that DNA was synthesised, stored or sequenced, or that
    hardware was measured.

    The wording is checked sentence by sentence: a claim is an overstatement unless the
    sentence that carries it also carries a negation ("no DNA was synthesised").  A
    disclaimer is exactly what these documents are required to carry, so a bare
    substring check would flag the disclaimers themselves.
    """
    text = path.read_text(encoding="utf-8").lower()
    for sentence in _sentences(text):
        for claim in FORBIDDEN_CLAIMS:
            if claim not in sentence:
                continue
            assert any(marker in sentence for marker in NEGATIONS), (
                f"{path.name} claims {claim!r} without a disclaimer: {sentence.strip()!r}")


def test_h5_spec_covers_the_required_sections():
    """The DNA I/O interface description covers the four things it must cover."""
    text = H5.read_text(encoding="utf-8")
    for heading in ("Oligo plate format", "Strand addressing", "Barcode scheme",
                    "synthesis / sequencing round trip"):
        assert heading.lower() in text.lower(), f"the DNA I/O spec does not cover {heading!r}"


def test_h5_spec_labels_every_physical_quantity():
    """The spec marks assumptions and theoretical values; a bare number is not enough."""
    text = H5.read_text(encoding="utf-8")
    assert "ASSUMPTION" in text
    assert "THEORETICAL" in text
    assert "Evidence rung" in text


def test_reference_decoder_contract_document_exists():
    """The compatibility contract is a committed document, not a chat message."""
    assert CONTRACT.is_file(), f"missing: {CONTRACT}"
    text = CONTRACT.read_text(encoding="utf-8")
    for token in ("compatibility rule", "standard library", "golden corpus"):
        assert token.lower() in text.lower(), f"the contract does not mention {token!r}"


# --------------------------------------------------------------------------------------
# 2. the golden corpus: presence and pinned digests
# --------------------------------------------------------------------------------------

def test_golden_index_is_well_formed(index: dict):
    """The index names at least one archive and pins its digests."""
    assert index["format_version"] == 4
    assert index["entries"], "the golden corpus is empty"
    for entry in index["entries"]:
        for key in ("profile", "container", "container_sha256", "fasta", "fasta_sha256", "payload_sha256"):
            assert key in entry, f"a golden entry lacks {key!r}"


def test_every_golden_archive_is_present(entries: list[dict]):
    """Every file the index names is on disk.  A missing file fails here."""
    for entry in entries:
        for kind in ("container", "fasta"):
            path = GOLDEN / entry[kind]
            assert path.is_file(), f"the golden archive is missing: {path}"


def test_golden_archive_digests_match_the_index(entries: list[dict]):
    """Every golden artefact still has the digest the index records.

    A single altered byte in a container or in a strand pool fails this test.
    """
    for entry in entries:
        for kind in ("container", "fasta"):
            path = GOLDEN / entry[kind]
            assert path.is_file(), f"the golden archive is missing: {path}"
            actual = sha256(path.read_bytes())
            assert actual == entry[f"{kind}_sha256"], (
                f"{path.name} was altered: sha256 {actual} != recorded {entry[f'{kind}_sha256']}")
            size = path.stat().st_size
            assert size == entry[f"{kind}_bytes"], f"{path.name} has the wrong size: {size}"


def test_golden_payload_digests_are_distinct_and_recorded(entries: list[dict]):
    """The recorded payload digests are well-formed and the corpus is not a single archive
    copied four times."""
    seen = set()
    for entry in entries:
        digest = entry["payload_sha256"]
        assert len(digest) == 64, f"a malformed payload digest: {digest!r}"
        assert all(c in "0123456789abcdef" for c in digest)
        seen.add(digest)
    assert len(seen) >= 2, "the golden corpus carries only one distinct payload"


# --------------------------------------------------------------------------------------
# 3. the round trip: golden archive -> exactly the bytes it was made from
# --------------------------------------------------------------------------------------

def _reference_decoder():
    """Import the reference decoder by path (it is not an installed package)."""
    sys.path.insert(0, str(REPO))
    from reference import vnx_ref_decoder  # noqa: PLC0415
    return vnx_ref_decoder


def _key_for(entry: dict) -> bytes | None:
    """The key of an encrypted golden archive (the published test key)."""
    return bytes(range(32)) if entry["encrypted"] else None


@pytest.mark.parametrize("profile", ["plain-2bit", "zlib-rotation3", "codebook8-plain", "encrypted-2bit"])
def test_golden_archive_decodes_to_its_expected_digest_from_the_container(profile: str, entries: list[dict]):
    """The reference decoder recovers exactly the bytes each golden archive was made from."""
    entry = next(e for e in entries if e["profile"] == profile)
    ref = _reference_decoder()
    blob = (GOLDEN / entry["container"]).read_bytes()
    report = ref.decode_container(blob, key=_key_for(entry))
    assert report["status"] == "SUCCESS"
    assert report["data"] is not None, "the decoder returned no bytes"
    assert sha256(report["data"]) == entry["payload_sha256"], (
        f"{profile}: the decoded bytes do not have the recorded digest")


@pytest.mark.parametrize("profile", ["plain-2bit", "zlib-rotation3", "codebook8-plain", "encrypted-2bit"])
def test_golden_archive_decodes_to_its_expected_digest_from_the_strands(profile: str, entries: list[dict]):
    """The same, starting from the committed strand pool rather than the container."""
    entry = next(e for e in entries if e["profile"] == profile)
    ref = _reference_decoder()
    sequences = ref.read_sequences(GOLDEN / entry["fasta"])
    assert sequences, f"{profile}: the strand pool is empty"
    report = ref.decode_reads(sequences, key=_key_for(entry))
    assert report["status"] == "SUCCESS"
    assert report["data"] is not None, "the decoder returned no bytes"
    assert sha256(report["data"]) == entry["payload_sha256"], (
        f"{profile}: the decoded bytes do not have the recorded digest")


@pytest.mark.parametrize("profile", ["plain-2bit", "zlib-rotation3", "codebook8-plain", "encrypted-2bit"])
def test_the_two_decoders_agree_bit_for_bit(profile: str, entries: list[dict]):
    """The production decoder and the reference decoder return the same bytes.

    This is the LR-3 acceptance criterion: bit-identical output on the whole corpus.
    """
    entry = next(e for e in entries if e["profile"] == profile)
    ref = _reference_decoder()
    blob = (GOLDEN / entry["container"]).read_bytes()
    key = _key_for(entry)

    from vnxdna.api import restore  # noqa: PLC0415

    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.bin"
        report = restore(GOLDEN / entry["container"], out, key=key, overwrite=True)
        assert report["status"] == "SUCCESS", report
        production = out.read_bytes()

    reference = ref.decode_container(blob, key=key)
    assert production == reference["data"], (
        f"{profile}: the production decoder and the reference decoder disagree")
    assert sha256(production) == entry["payload_sha256"]


def test_the_two_decoders_agree_on_the_strands(entries: list[dict]):
    """The same agreement, starting from the strand pool."""
    ref = _reference_decoder()
    from vnxdna.api import recover  # noqa: PLC0415

    import tempfile  # noqa: PLC0415

    for entry in entries:
        key = _key_for(entry)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.bin"
            report = recover(GOLDEN / entry["fasta"], out, key=key, overwrite=True)
            assert report["status"] == "SUCCESS", f"{entry['profile']}: {report}"
            production = out.read_bytes()
        reference = ref.decode_reads(ref.read_sequences(GOLDEN / entry["fasta"]), key=key)
        assert production == reference["data"], f"{entry['profile']}: the decoders disagree on the strands"
        assert sha256(production) == entry["payload_sha256"]


def test_an_altered_golden_archive_is_rejected(entries: list[dict], tmp_path: Path):
    """A golden archive with one byte changed does not decode to the expected digest.

    This is the negative control for the round-trip checks above: it shows that those checks
    would fail on an altered archive, so a passing test really does mean something.
    """
    entry = next(e for e in entries if e["profile"] == "plain-2bit")
    ref = _reference_decoder()
    blob = bytearray((GOLDEN / entry["container"]).read_bytes())
    blob[len(blob) // 2] ^= 0x01
    corrupted = tmp_path / "corrupted.vxdna"
    corrupted.write_bytes(bytes(blob))

    with pytest.raises(ref.ReferenceDecodeError):
        ref.decode_container(bytes(blob))
    # the digest of the corrupted file is of course not the recorded one
    assert sha256(bytes(blob)) != entry["container_sha256"]


# --------------------------------------------------------------------------------------
# 4. the reference decoder: standard library only
# --------------------------------------------------------------------------------------

def test_reference_decoder_imports_nothing_outside_the_standard_library():
    """LR-3: no imports outside the language's standard library.

    The check is mechanical: the module's import statements are parsed and every imported
    top-level module must be a standard-library module.
    """
    tree = ast.parse(REF_DECODER.read_text(encoding="utf-8"), filename=str(REF_DECODER))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    outside = sorted(m for m in imported if m not in sys.stdlib_module_names
                     and m not in FUTURE_STDLIB and m != "reference")
    assert not outside, f"the reference decoder imports outside the standard library: {outside}"


def test_the_future_stdlib_import_is_guarded():
    """``compression.zstd`` is standard library from Python 3.14 only.

    The reference decoder may import it, but only inside a guard: on an interpreter without
    it, a zstd archive must be a typed refusal, never a wrong answer.
    """
    text = REF_DECODER.read_text(encoding="utf-8")
    assert "from compression.zstd import" in text
    guard = text.split("from compression.zstd import", 1)[1]
    window = guard[:guard.find("\n\n") if "\n\n" in guard else len(guard)]
    assert "except ImportError" in window, "the zstd import is not guarded by an ImportError handler"
    assert "ReferenceUnsupportedError" in window, "a missing zstd decoder must be a typed refusal"


def test_reference_decoder_does_not_import_the_production_package():
    """The reference decoder is a second implementation, not a wrapper around the first."""
    text = REF_DECODER.read_text(encoding="utf-8")
    for forbidden in ("import vnxdna", "from vnxdna", "import numpy", "from numpy"):
        assert forbidden not in text, f"the reference decoder refers to {forbidden!r}"


def test_reference_decoder_has_a_documented_size():
    """LR-3 requires the line count to be documented; the contract must still state it."""
    text = CONTRACT.read_text(encoding="utf-8")
    count = len(REF_DECODER.read_text(encoding="utf-8").splitlines())
    assert f"{count:,}".replace(",", " ") in text or str(count) in text, (
        f"the contract does not state the reference decoder's line count ({count})")


def test_the_documented_runtime_is_still_the_measured_runtime():
    """The contract's runtime table must match a measurement, not a remembered number.

    LR-3 requires the runtime to be documented.  A documented number that has
    drifted from the code is worse than no number, so the table is checked
    against a live measurement: each per-archive strand time in the contract
    must be within a factor of two of the time measured here.
    """
    import time  # noqa: PLC0415

    sys.path.insert(0, str(REPO / "reference"))
    import vnx_ref_decoder as ref  # noqa: PLC0415

    text = CONTRACT.read_text(encoding="utf-8")
    names = ("plain-2bit", "zlib-rotation3", "codebook8-plain", "encrypted-2bit")
    for name in names:
        seqs = ref.read_sequences(str(GOLDEN / f"{name}.fasta"))
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            ref.decode_reads(seqs)
            elapsed = time.perf_counter() - t0
            best = elapsed if best is None else min(best, elapsed)
        measured_ms = best * 1000.0
        # the contract states the per-archive time in a table row:
        # | `name` | <strands> | <n> ms |
        stated = re.search(rf"^\|\s*`{re.escape(name)}`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*ms\s*\|",
                           text, re.MULTILINE)
        assert stated is not None, f"the contract does not state a runtime for {name}"
        stated_strands = int(stated.group(1))
        stated_ms = float(stated.group(2))
        assert stated_strands == len(seqs), (
            f"the contract states {stated_strands} strands for {name} but the "
            f"corpus has {len(seqs)}")
        assert 0.5 * stated_ms <= measured_ms <= 2.0 * stated_ms, (
            f"the contract states {stated_ms:.0f} ms for {name} but the measured "
            f"time is {measured_ms:.1f} ms; the table must be updated in the "
            f"same commit that changes the number")


# --------------------------------------------------------------------------------------
# 5. the corpus builder
# --------------------------------------------------------------------------------------

def test_golden_builder_is_committed_and_reports_the_corpus_intact():
    """``make_golden.py --check`` must pass on the committed corpus."""
    import subprocess  # noqa: PLC0415

    result = subprocess.run([sys.executable, str(MAKE_GOLDEN), "--check"],
                            capture_output=True, text=True, cwd=str(REPO))
    assert result.returncode == 0, f"the golden corpus check failed:\n{result.stdout}\n{result.stderr}"
    assert "PASS" in result.stdout
