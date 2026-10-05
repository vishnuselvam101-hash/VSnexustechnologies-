"""The conformance vector set (spec §6) and the ``vnx conformance`` answer (spec §6.3).

Properties asserted here:

* the committed set is complete against the required-vector table of spec §6.2, indexed without orphans or duplicates, and
  byte-identical to what ``generate_vectors.py`` produces (deterministic golden vectors);
* ``vnx conformance`` is CONFORMANT for both ``--backend native`` and ``--backend reference``, each negative vector produces
  exactly its error code, category, exit code and retryable flag, and no decoder vector can report SUCCESS where an error is
  expected (the no-false-SUCCESS property);
* the runner itself is strict: a wrong expectation, a swapped input, an index that disagrees with a vector, a skipped vector
  or a forced backend that is not available each make the verdict NONCONFORMANT (exit 1), never CONFORMANT.

SYNTHETIC SOFTWARE TEST data. No DNA was synthesised, stored or sequenced.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from vnxdna import sdk
from vnxdna.commands import app
from vnxdna.conformance import VECTORS, _backend_record, default_vectors, run

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INDEX = json.loads((HERE / "index.json").read_text())
BY_ID = {e["id"]: e for e in INDEX["vectors"]}


def vec(vid: str) -> dict:
    return json.loads((HERE / BY_ID[vid]["path"] / "vector.json").read_text())


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def _native_kernels():
    """Build the native kernels once if this tree has none, so that ``--backend native`` can be answered."""
    from vnxdna.native import backend_summary
    if any(v.get("backend") != "native" for v in backend_summary().values()):
        subprocess.run([sys.executable, "-m", "vnxdna.native", "build"], capture_output=True, check=False)
        for mod in ("vnxdna.native.reads", "vnxdna.native.rs", "vnxdna.native.align"):
            try:
                sys.modules[mod]._reset_for_tests()
            except Exception:  # noqa: BLE001 - a kernel without a reset hook is probed again on first use
                pass


@pytest.fixture(scope="module")
def answers(_native_kernels):
    return {b: run(HERE, backend=b, services={"version": sdk.version}) for b in ("native", "reference")}


# ------------------------------------------------------------------------------------------------------------ the set itself
def test_index_matches_the_directories_exactly():
    assert INDEX["schema"] == "vnx.conformance-index/1" and INDEX["evidence"] == "SYNTHETIC SOFTWARE TEST"
    ids = [e["id"] for e in INDEX["vectors"]]
    assert len(ids) == len(set(ids))
    on_disk = {p.parent.relative_to(HERE).as_posix() for p in HERE.glob("*/*/vector.json")}
    assert on_disk == {e["path"] for e in INDEX["vectors"]}
    for e in INDEX["vectors"]:
        v = vec(e["id"])
        assert v["schema"] == "vnx.conformance-vector/1" and v["id"] == e["id"] and v["stage"] == e["stage"]
        assert v["kind"] == e["kind"] and v["formats"] == e["formats"]
        assert e["path"].split("/")[0] in ("stage", "e2e", "negative")
        assert (v["kind"] == "negative") == (list(v["expected"]) == ["error"]), e["id"]
        assert v["evidence"] == "SYNTHETIC SOFTWARE TEST" and v["since_spec"] == "6.0"


def test_every_negative_vector_names_a_code_of_the_error_table():
    codes = {"FORMAT_ERROR": ("INVALID_INPUT", 3), "LAYOUT_UNDETECTED": ("INVALID_INPUT", 3),
             "ARCHIVE_TAG_AMBIGUOUS": ("INVALID_INPUT", 3), "RESOURCE_LIMIT": ("INVALID_INPUT", 3),
             "CONTAINER_VERSION_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6), "FEATURE_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6),
             "LEGACY_FORMAT": ("UNSUPPORTED_FORMAT", 6), "FRAME_VERSION_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6),
             "SUPERBLOCK_VERSION_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6),
             "INSUFFICIENT_REDUNDANCY": ("INSUFFICIENT_REDUNDANCY", 5), "INTEGRITY_ERROR": ("VERIFICATION_FAILED", 1),
             "WRONG_KEY": ("AUTHENTICATION_FAILED", 4), "KEY_FOR_UNENCRYPTED": ("AUTHENTICATION_FAILED", 4),
             "CONSTRAINT_ERROR": ("CONFIGURATION_ERROR", 7)}
    negatives = [e for e in INDEX["vectors"] if e["kind"] == "negative"]
    assert len(negatives) >= 90
    for e in negatives:
        err = vec(e["id"])["expected"]["error"]
        assert set(err) == {"code", "category", "exit_code", "retryable"}, e["id"]
        assert codes[err["code"]] == (err["category"], err["exit_code"]), e["id"]
        assert (err["code"] == "INSUFFICIENT_REDUNDANCY") == err["retryable"], e["id"]


REQUIRED = {   # spec §6.2: area -> id prefixes that must exist (positive, negative)
    "GF(256) and RS": (["rs.encode.r08", "rs.encode.r12", "rs.encode.r16", "rs.encode.r20", "rs.decode.r08", "rs.decode.r20",
                        "rs.golden."], ["frame4.beyond-rs-bound.", "outer.row-decode.k64-m16.beyond-bound"]),
    "CRC-32, scrambler": (["crc32.", "scrambler.keystream.v000", "scrambler.keystream.v001", "scrambler.keystream.v255",
                           "scrambler.byte0.vnx4", "scrambler.byte0.v1-frame4", "scrambler.byte0.v3-frame5"], []),
    "mapping, markers": (["mapping.bytes_to_nt.v4-balanced", "mapping.bytes_to_nt.v4-dense", "mapping.bytes_to_nt.v4-indel",
                          "mapping.bytes_to_nt.v4-archival"] + [f"markers.insert.l{i}" for i in range(1, 7)], []),
    "frame 4": (["frame4.build.v4-balanced.k0", "frame4.build.v4-balanced.k1", "frame4.build.v4-balanced.variant-gt0",
                 "frame4.parse."], ["frame4.nibble7", "frame4.kind2", "frame4.nibble5-reserved", "frame4.build.constraint-failure"]),
    "superblock": (["superblock.pack.v1", "superblock.pack.v2", "superblock.unpack.v1", "superblock.unpack.v2"],
                   ["superblock.version.000", "superblock.version.003", "superblock.version.255", "superblock.v2.depth0",
                    "superblock.v2.depth-plus-parity-over-256", "superblock.v2.order2", "superblock.forged."]),
    "outer code": (["outer.row-encode.k64-m16", "outer.row-encode.k32-m32", "outer.row-encode.k48-m16", "outer.row-encode.short.",
                    "outer.column-parity.d4-mc2", "outer.column-parity.d8-mc2", "outer.stripe-decode.d4-mc2.columns-repair-rows"],
                   ["outer.stripe-decode.d4-mc2.beyond-bound"]),
    "strand order": (["strand.order.sequential", "strand.order.interleaved"], []),
    "container": (["container.build.clear", "container.build.encrypted", "merkle.proofs.n1", "merkle.proofs.n8",
                   "manifest.canonical.accepts", "aead.seal.domain0", "aead.seal.domain1", "aead.seal.domain2",
                   "container.read.clear", "container.read.zstd", "container.read.encrypted"],
                  ["container.rule1.wrong-magic", "container.rule1.major-5", "container.rule1.minor-1",
                   "container.rule1.flags-nonzero", "container.rule2.truncated-trailer", "container.rule3.manifest-not-canonical",
                   "container.rule3.unknown-required-feature", "container.rule4.manifest-mac-mismatch",
                   "container.rule7.merkle-root-mismatch", "container.rule9.chunk-id-mismatch",
                   "container.rule9.zstd-output-over-plain-size"] + [f"container.rule{i}." for i in range(1, 10)]),
    "end to end": ([f"e2e.{op}.{s}.{c}" for op in ("encode", "decode") for s, cs in
                    (("v4_0", ("balanced", "archival", "encrypted", "multifile")), ("v5_0", ("balanced", "archival", "encrypted", "multifile")),
                     ("v6_0", ("stripes-seq", "adaptive-interleaved", "max-recovery", "encrypted-stripes"))) for c in cs],
                   ["e2e.decode.frame-nibble7-pool", "e2e.decode.v3-frame5-pool", "e2e.decode.two-archives-one-tag",
                    "e2e.decode.read-over-100000-nt", "e2e.decode.wrong-key", "e2e.decode.key-for-unencrypted-archive"]),
    "version reporting": (["version.report"], []),
}


@pytest.mark.parametrize("area", sorted(REQUIRED))
def test_required_vectors_of_spec_6_2_exist(area):
    positive, negative = REQUIRED[area]
    for want in positive:
        hits = [e for e in INDEX["vectors"] if e["id"].startswith(want) and e["kind"] == "positive"]
        assert hits, f"{area}: no positive vector {want!r}"
    for want in negative:
        hits = [e for e in INDEX["vectors"] if e["id"].startswith(want) and e["kind"] == "negative"]
        assert hits, f"{area}: no negative vector {want!r}"


def test_set_size_and_split():
    pos = sum(e["kind"] == "positive" for e in INDEX["vectors"])
    neg = sum(e["kind"] == "negative" for e in INDEX["vectors"])
    assert pos >= 120 and neg >= 90 and pos + neg == len(INDEX["vectors"])


def test_packaged_subset_is_a_byte_identical_part_of_the_full_set():
    packaged = json.loads((VECTORS / "index.json").read_text())
    for e in packaged["vectors"]:
        assert e in INDEX["vectors"], e["id"]
        a, b = sorted(p.name for p in (VECTORS / e["path"]).iterdir()), sorted(p.name for p in (HERE / e["path"]).iterdir())
        assert a == b
        for name in a:
            assert (VECTORS / e["path"] / name).read_bytes() == (HERE / e["path"] / name).read_bytes(), (e["id"], name)


def test_every_recorded_input_hash_matches_its_file():
    seen = 0
    for e in INDEX["vectors"]:
        v = vec(e["id"])
        for name, ref in v["inputs"].items():
            assert sha((HERE / e["path"] / ref["path"]).read_bytes()) == ref["sha256"], (e["id"], name)
            seen += 1
    assert seen > 150


def test_e2e_vectors_reference_the_fixtures_by_path_and_hash_without_copying():
    for e in INDEX["vectors"]:
        if e["path"].startswith("e2e/"):
            assert [p.name for p in (HERE / e["path"]).iterdir()] == ["vector.json"], e["id"]
            for ref in vec(e["id"])["inputs"].values():
                assert ref["path"].startswith("../../../fixtures/v"), e["id"]


def test_golden_vectors_are_reproduced_byte_for_byte_by_the_generator(tmp_path):
    out = tmp_path / "tests" / "conformance"
    for name in ("fixtures", "v6"):
        (tmp_path / "tests").mkdir(exist_ok=True)
        (tmp_path / "tests" / name).symlink_to(ROOT / "tests" / name)
    env = {**os.environ, "VNX_CONFORMANCE_OUT": str(out), "PYTHONPATH": str(ROOT / "src")}
    done = subprocess.run([sys.executable, str(HERE / "generate_vectors.py")], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    mine = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    committed = {p.relative_to(HERE).as_posix(): p.read_bytes() for p in HERE.rglob("*")
                 if p.is_file() and p.relative_to(HERE).parts[0] in ("stage", "e2e", "negative", "index.json")}
    assert mine.keys() == committed.keys()
    assert [k for k in mine if mine[k] != committed[k]] == []


# ------------------------------------------------------------------------------------------------------------ independent values
def test_frozen_values_agree_with_independent_computations():
    import reedsolo
    assert vec("crc32.check-value")["expected"]["outputs"]["crc32"] == [f"{zlib.crc32(b'123456789'):08x}"]
    for dom, vid in (("VNX4 scrambler", "vnx4"), ("VNX-DNA/4 scrambler", "v1-frame4"), ("VNX-DNA/5 scrambler", "v3-frame5")):
        want = bytes(hashlib.shake_128(dom.encode() + bytes([v])).digest(1)[0] for v in range(256)).hex()
        assert vec(f"scrambler.byte0.{vid}")["expected"]["outputs"]["byte0_hex"] == want
    for r in (8, 12, 16, 20):
        v = vec(f"rs.encode.r{r:02d}")
        msg = (HERE / BY_ID[v["id"]]["path"] / "message.dat").read_bytes()
        rs = reedsolo.RSCodec(r, nsize=255, fcr=0, prim=0x11D, generator=2)
        assert v["expected"]["outputs"]["parity_hex"] == bytes(rs.encode(msg)[len(msg):]).hex()
    # the Cauchy parity of the first row of (K, M) = (32, 32): C[i][j] = 1 / ((K + i) xor j) over GF(2^8)/0x11D
    v = vec("outer.row-encode.k32-m32")
    data = np.frombuffer((HERE / BY_ID[v["id"]]["path"] / "data.dat").read_bytes(), dtype=np.uint8).reshape(32, 4)
    exp, log = [0] * 512, [0] * 256
    x = 1
    for i in range(255):
        exp[i], log[x] = x, i
        x = (x << 1) ^ (0x11D if x & 0x80 else 0)
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    row0 = [0] * 4
    for j in range(32):
        c = exp[255 - log[(32 + 0) ^ j]]
        for t in range(4):
            if data[j, t]:
                row0[t] ^= exp[log[c] + log[int(data[j, t])]]
    assert bytes(row0).hex() == v["expected"]["outputs"]["parity_hex"][:8]


# ------------------------------------------------------------------------------------------------------------ the answer
@pytest.mark.parametrize("backend", ["native", "reference"])
def test_vnx_conformance_is_conformant_on_each_backend(answers, backend):
    doc = answers[backend]
    assert doc["schema"] == "vnx.conformance/1" and doc["backend"] == backend
    bad = [(r["id"], r["status"], r["observed"]) for r in doc["results"] if r["status"] != "PASS"]
    assert not bad, bad[:3]
    assert doc["verdict"] == "CONFORMANT"
    s = doc["summary"]
    assert s["failed"] == s["skipped"] == 0 and s["total"] == s["passed"] == len(INDEX["vectors"])
    assert s["positive"] + s["negative"] == s["total"] and s["negative"] >= 90
    assert [r["id"] for r in doc["results"]] == [e["id"] for e in INDEX["vectors"]]
    assert doc["index_sha256"] == sha((HERE / "index.json").read_bytes())
    expected_backend = {"native": {"align": "native", "reads": "native"}, "reference": {"align": "reference", "reads": "reference",
                                                                                        "rs": "reference"}}[backend]
    assert {k: doc["backends"][k] for k in expected_backend} == expected_backend
    if backend == "native":
        assert doc["backends"]["rs"] in ("scalar", "avx2", "avx512")


@pytest.mark.parametrize("backend", ["native", "reference"])
def test_each_negative_vector_produces_exactly_its_error_and_never_an_output(answers, backend):
    for r in answers[backend]["results"]:
        if r["kind"] != "negative":
            continue
        assert list(r["observed"]) == ["error"], (r["id"], "a negative vector produced an output (false SUCCESS)")
        assert r["observed"]["error"] == vec(r["id"])["expected"]["error"], r["id"]
        assert r["observed"]["error"]["exit_code"] == r["expected"]["error"]["exit_code"]


def test_decoder_vectors_report_success_only_with_the_golden_hash(answers):
    """The no-false-SUCCESS property over the end-to-end set: a decode vector either carries the committed container hash,
    or it is a typed error; there is no third outcome."""
    for backend, doc in answers.items():
        for r in doc["results"]:
            if not r["id"].startswith("e2e.decode."):
                continue
            if r["kind"] == "negative":
                assert "outputs" not in r["observed"], (backend, r["id"])
            else:
                out = r["observed"]["outputs"]
                assert out["status"] == "SUCCESS" and out["container_sha256"] == r["expected"]["outputs"]["container_sha256"]
                fixture, case = r["id"].split(".")[2:4]
                golden = json.loads((ROOT / "tests" / "fixtures" / fixture / "manifest.json").read_text())["cases"][case]
                assert out["container_sha256"] == golden["container_sha256"] and out["files"] == golden["files"]


def test_both_backends_give_identical_observations(answers):
    a, b = ({r["id"]: r["observed"] for r in answers[k]["results"]} for k in ("native", "reference"))
    assert a == b


def test_default_vectors_are_the_full_set_of_a_checkout_and_the_package_keeps_a_subset():
    assert default_vectors() == HERE
    assert json.loads((VECTORS / "index.json").read_text())["vectors"] and len(INDEX["vectors"]) > 200


# ------------------------------------------------------------------------------------------------------------ the runner is strict
def mini(tmp_path: Path, ids: list[str], edit=None) -> Path:
    """A vector directory with only the named stage/negative vectors (no fixture references), optionally edited."""
    root = tmp_path / "set"
    entries = []
    for vid in ids:
        e = BY_ID[vid]
        shutil.copytree(HERE / e["path"], root / e["path"])
        entries.append(dict(e))
    (root / "index.json").write_text(json.dumps({"schema": "vnx.conformance-index/1", "vectors": entries}))
    if edit:
        edit(root, entries)
    return root


def test_a_wrong_expected_output_is_a_failure(tmp_path):
    def edit(root, _):
        p = root / BY_ID["crc32.check-value"]["path"] / "vector.json"
        v = json.loads(p.read_text())
        v["expected"]["outputs"]["crc32"] = ["00000000"]
        p.write_text(json.dumps(v))
    doc = run(mini(tmp_path, ["crc32.check-value", "crc32.rows.002"], edit))
    assert doc["verdict"] == "NONCONFORMANT" and doc["summary"]["failed"] == 1 and doc["summary"]["passed"] == 1


def test_a_negative_vector_with_a_wrong_exit_code_is_a_failure(tmp_path):
    def edit(root, _):
        p = root / BY_ID["superblock.version.000"]["path"] / "vector.json"
        v = json.loads(p.read_text())
        v["expected"]["error"]["exit_code"] = 3
        p.write_text(json.dumps(v))
    doc = run(mini(tmp_path, ["superblock.version.000"], edit))
    assert doc["verdict"] == "NONCONFORMANT" and doc["results"][0]["status"] == "FAIL"
    assert doc["results"][0]["observed"]["error"]["exit_code"] == 6


def test_a_negative_vector_that_would_succeed_is_a_failure(tmp_path):
    """A defect that no longer fails (here: the forged vector made valid) is NONCONFORMANT, not a silent pass."""
    def edit(root, _):
        d = root / BY_ID["superblock.forged.k-plus-m-over-256"]["path"]
        good = (HERE / BY_ID["superblock.unpack.v2.interleaved"]["path"] / "superblock.dat").read_bytes()
        (d / "superblock.dat").write_bytes(good)
        v = json.loads((d / "vector.json").read_text())
        v["inputs"]["superblock"]["sha256"] = sha(good)
        (d / "vector.json").write_text(json.dumps(v))
    doc = run(mini(tmp_path, ["superblock.forged.k-plus-m-over-256"], edit))
    r = doc["results"][0]
    assert doc["verdict"] == "NONCONFORMANT" and r["status"] == "FAIL" and "outputs" in r["observed"]


def test_a_swapped_input_file_is_not_a_pass(tmp_path):
    def edit(root, _):
        p = root / BY_ID["rs.encode.r16"]["path"] / "message.dat"
        p.write_bytes(bytes(30))
    doc = run(mini(tmp_path, ["rs.encode.r16"], edit))
    assert doc["verdict"] == "NONCONFORMANT" and doc["results"][0]["status"] == "FAIL"
    assert doc["results"][0]["observed"]["error"]["code"] == "INTERNAL_ERROR"


def test_an_unimplemented_operation_is_a_skip_and_a_skip_is_not_conformant(tmp_path):
    def edit(root, _):
        p = root / BY_ID["crc32.check-value"]["path"] / "vector.json"
        v = json.loads(p.read_text())
        v["operation"] = "crc64"
        p.write_text(json.dumps(v))
    doc = run(mini(tmp_path, ["crc32.check-value", "crc32.rows.002"], edit))
    assert doc["summary"] == {**doc["summary"], "skipped": 1, "passed": 1, "failed": 0}
    assert doc["verdict"] == "NONCONFORMANT"


def test_an_index_that_disagrees_with_the_vector_is_a_failure(tmp_path):
    def edit(root, entries):
        entries[0]["stage"] = "D99"
        (root / "index.json").write_text(json.dumps({"schema": "vnx.conformance-index/1", "vectors": entries}))
    doc = run(mini(tmp_path, ["crc32.check-value"], edit))
    assert doc["verdict"] == "NONCONFORMANT" and doc["results"][0]["status"] == "FAIL"


def test_a_duplicate_id_in_the_index_is_a_failure(tmp_path):
    def edit(root, entries):
        (root / "index.json").write_text(json.dumps({"schema": "vnx.conformance-index/1", "vectors": entries + entries}))
    doc = run(mini(tmp_path, ["crc32.check-value"], edit))
    assert doc["verdict"] == "NONCONFORMANT" and [r["status"] for r in doc["results"]] == ["PASS", "FAIL"]


def test_an_unknown_vector_schema_is_a_failure(tmp_path):
    def edit(root, _):
        p = root / BY_ID["crc32.check-value"]["path"] / "vector.json"
        v = json.loads(p.read_text())
        v["schema"] = "vnx.conformance-vector/2"
        p.write_text(json.dumps(v))
    assert run(mini(tmp_path, ["crc32.check-value"], edit))["verdict"] == "NONCONFORMANT"


def test_an_empty_set_is_not_conformant(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"schema": "vnx.conformance-index/1", "vectors": []}))
    doc = run(tmp_path)
    assert doc["summary"]["total"] == 0 and doc["verdict"] == "NONCONFORMANT"


def test_an_index_of_another_schema_is_refused(tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({"schema": "vnx.conformance-index/2", "vectors": []}))
    from vnxdna.core.errors import VNXFormatError
    with pytest.raises(VNXFormatError) as e:
        run(tmp_path)
    assert e.value.code == "SCHEMA_UNSUPPORTED" and e.value.exit_code == 3


def test_select_runs_only_the_named_vectors():
    doc = run(HERE, select=["crc32.check-value", "superblock.version.000"])
    assert [r["id"] for r in doc["results"]] == [e["id"] for e in INDEX["vectors"]
                                                  if e["id"] in ("crc32.check-value", "superblock.version.000")]
    assert len(doc["results"]) == 2
    assert doc["verdict"] == "CONFORMANT" and doc["summary"]["positive"] == doc["summary"]["negative"] == 1


def test_a_forced_backend_that_is_not_available_is_a_skip_that_names_the_component():
    summary = {"align": {"backend": "native"}, "reads": {"backend": "reference"}, "rs": {"backend": "reference"}}
    rec = _backend_record("native", summary)
    assert rec["status"] == "SKIP" and "reads" in rec["observed"]["reason"] and "rs" in rec["observed"]["reason"]
    assert _backend_record("reference", {k: {"backend": "reference"} for k in summary}) is None
    assert _backend_record("auto", summary) is None


def test_a_missing_native_kernel_makes_the_run_nonconformant(monkeypatch, tmp_path):
    import vnxdna.native as native
    real = native.backend_summary
    monkeypatch.setattr(native, "backend_summary", lambda: {k: {**v, "backend": "reference", "simd_level": None}
                                                              for k, v in real().items()})
    doc = run(mini(tmp_path, ["crc32.check-value"]), backend="native")
    assert doc["verdict"] == "NONCONFORMANT" and doc["summary"]["skipped"] == 1 and doc["summary"]["passed"] == 1
    assert doc["results"][0]["id"] == "backend.native" and doc["results"][0]["status"] == "SKIP"


def test_the_forced_backend_is_restored_afterwards():
    before = {k: os.environ.get(k) for k in ("VNXDNA_RS_BACKEND", "VNXDNA_READS_BACKEND", "VNXDNA_ALIGN_BACKEND")}
    run(HERE, select=["crc32.check-value"], backend="reference")
    assert {k: os.environ.get(k) for k in before} == before


def test_the_version_operation_needs_the_sdk_service():
    doc = run(HERE, select=["version.report"])           # no services given: the runner may not import the SDK itself
    assert doc["verdict"] == "NONCONFORMANT" and doc["results"][0]["observed"]["error"]["code"] == "CONFIGURATION_ERROR"
    assert run(HERE, select=["version.report"], services={"version": sdk.version})["verdict"] == "CONFORMANT"


# ------------------------------------------------------------------------------------------------------------ the command
@pytest.mark.parametrize("backend", ["native", "reference"])
def test_cli_exit_zero_and_a_conformant_answer(backend):
    res = CliRunner().invoke(app, ["conformance", "--vectors", str(HERE), "--backend", backend, "--select", "crc32.check-value",
                                   "--select", "frame4.nibble7", "--select", "version.report"])
    assert res.exit_code == 0, res.output
    doc = json.loads(res.stdout)
    assert doc["status"] == "SUCCESS" and doc["result"]["verdict"] == "CONFORMANT" and doc["result"]["summary"]["total"] == 3


def test_cli_exit_one_when_a_vector_fails(tmp_path):
    def edit(root, _):
        p = root / BY_ID["crc32.check-value"]["path"] / "vector.json"
        v = json.loads(p.read_text())
        v["expected"]["outputs"]["crc32"] = ["deadbeef"]
        p.write_text(json.dumps(v))
    res = CliRunner().invoke(app, ["conformance", "--vectors", str(mini(tmp_path, ["crc32.check-value"], edit))])
    assert res.exit_code == 1
    assert json.loads(res.stdout)["result"]["verdict"] == "NONCONFORMANT"
