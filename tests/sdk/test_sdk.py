"""vnxdna.sdk: every function, with its JSON validated against the shipped schemas (V6 Phase 2.3/2.6).

SYNTHETIC SOFTWARE TEST data; channel results are SIMULATED.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from vnxdna import sdk
from vnxdna.core import schema
from vnxdna.core.errors import CODES, VNXConfigurationError, VNXError
from vnxdna.v4 import datagen


def sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check(res: sdk.Result) -> dict:
    doc = res.to_json()
    schema.validate(doc, "vnx.result/1")
    schema.validate(res.to_cli(), "vnx.result/1")
    assert json.loads(json.dumps(doc, default=str)) is not None
    assert doc["software"]["name"] == "vnxdna" and doc["spec"] == "6.0"
    assert set(doc["provenance"]["backends"]) == {"align", "reads", "rs", "cluster"}
    return doc


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    d = tmp_path_factory.mktemp("sdk")
    (d / "ds").mkdir()
    datagen.generate(d / "ds" / "a.bin", 24_000, "mixed", 7101)
    datagen.generate(d / "ds" / "b.txt", 6_000, "text", 7102)
    res = sdk.archive([d / "ds"], d / "a.vnx")
    sdk.encode(d / "a.vnx", d / "s.fasta")
    sdk.simulate(d / "s.fasta", d / "r.fastq", seed=7103, coverage=3,
                 overrides={"substitution_rate": 0.002, "insertion_rate": 0.0005, "deletion_rate": 0.0005})
    return d, res


def test_archive_and_container_functions(ws, tmp_path):
    d, res = ws
    doc = check(res)
    assert doc["kind"] == "archive" and doc["status"] == "SUCCESS" and doc["result"]["files"] == 2
    assert doc["outputs"][0]["sha256"] == sha(d / "a.vnx")
    assert doc["formats"]["container"] == [4, 0]
    v = check(sdk.verify(d / "a.vnx"))
    assert v["status"] == "VERIFIED" and v["result"]["status"] == "VERIFIED"
    lst = check(sdk.list_entries(d / "a.vnx"))
    assert sorted(e["path"] for e in lst["result"]["entries"]) == ["ds/a.bin", "ds/b.txt"]
    ins = check(sdk.inspect(d / "a.vnx"))
    assert ins["result"]["object"] == "container" and ins["result"]["readable"] == "yes"
    ext = check(sdk.extract(d / "a.vnx", tmp_path / "x"))
    assert (tmp_path / "x" / "ds" / "a.bin").read_bytes() == (d / "ds" / "a.bin").read_bytes() and ext["result"]["files"] == 2
    loc = check(sdk.locate(d / "a.vnx", "ds/b.txt", dna_profile="v4-balanced"))
    assert loc["result"]["dna"]["groups"]


def test_encode_decode_round_trip_envelopes(ws, tmp_path):
    d, _ = ws
    enc = check(sdk.encode(d / "a.vnx", tmp_path / "s.fasta"))
    assert enc["kind"] == "encode" and enc["formats"] == {"container": [4, 0], "frame_version": 4, "superblock_version": 1,
                                                         "codec": "f4-sb1-cauchy-rs"}
    assert enc["outputs"][0]["sha256"] == sha(tmp_path / "s.fasta") == sha(d / "s.fasta")
    assert enc["result"]["verified_after_encode"] is False
    dec = check(sdk.decode(d / "r.fastq", tmp_path / "o.vnx"))
    assert dec["status"] == "SUCCESS" and dec["error"] is None
    assert dec["inputs"][0]["sha256"] == sha(d / "r.fastq") and dec["outputs"][0]["sha256"] == sha(d / "a.vnx")
    assert dec["result"]["encrypted"] is False and dec["result"]["content_verified"] is True
    assert dec["formats"]["superblock_version"] == 1 and dec["formats"]["codec"] == "f4-sb1-cauchy-rs"
    assert dec["provenance"]["config_sha256"] and "stage_seconds" in dec["timings"]
    nohash = sdk.decode(d / "r.fastq", tmp_path / "o2.vnx", input_hash=False).to_json()
    assert nohash["inputs"][0]["sha256"] is None
    schema.validate(sdk.decode(d / "r.fastq", tmp_path / "o3.vnx").to_cli("vnx.decode-report/1"), "vnx.decode-report/1")


def test_decode_failure_carries_an_error_object(ws, tmp_path):
    d, _ = ws
    lines = (d / "s.fasta").read_text().splitlines()
    head = [(lines[i], lines[i + 1]) for i in range(0, len(lines), 2)]
    sb = [r for r in head if "|1|" in r[0]]
    data = [r for r in head if "|1|" not in r[0]]
    keep = sb + data[: len(data) // 3]             # far too few data strands: groups fail, nothing verifies
    (tmp_path / "few.fasta").write_text("".join(f"{h}\n{s}\n" for h, s in keep))
    res = sdk.decode(tmp_path / "few.fasta", tmp_path / "o.vnx")
    doc = check(res)
    assert doc["status"] == "FAILURE" and doc["error"]["code"] == "INSUFFICIENT_REDUNDANCY" and doc["error"]["exit_code"] == 5
    assert not (tmp_path / "o.vnx").exists()


def test_encode_archive_options_are_passed_through_or_refused(ws, tmp_path):
    d, _ = ws
    opts = sdk.ArchiveOptions(chunk_size=4096, compression="none")
    res = check(sdk.encode(d / "ds", tmp_path / "s.fasta", archive_options=opts, keep_archive=tmp_path / "k.vnx"))
    m = sdk.inspect(tmp_path / "k.vnx").body["manifest"]
    assert m["chunking"]["chunk_size"] == 4096 and res["result"]["archive"]["files"] == 2
    assert {o["role"] for o in res["outputs"]} == {"strands", "container"}
    with pytest.raises(VNXConfigurationError) as e:
        sdk.encode(d / "a.vnx", tmp_path / "t.fasta", archive_options=opts)
    assert e.value.code == "CONFIGURATION_ERROR" and e.value.exit_code == 7
    assert not (tmp_path / "t.fasta").exists()


def test_simulate_records_seed_and_evidence_class(ws, tmp_path):
    d, _ = ws
    doc = check(sdk.simulate(d / "s.fasta", tmp_path / "r.fastq", seed=11, coverage=2))
    assert doc["result"]["evidence_class"] == "SIMULATED" and doc["result"]["seed"] == 11
    assert doc["provenance"]["seeds"] == {"channel": 11}
    again = sdk.simulate(d / "s.fasta", tmp_path / "r2.fastq", seed=11, coverage=2)
    assert sha(tmp_path / "r.fastq") == sha(tmp_path / "r2.fastq") and again.status == "SUCCESS"


def test_inspect_reads_gives_a_probe_answer(ws):
    d, _ = ws
    doc = check(sdk.inspect(d / "r.fastq"))
    body = doc["result"]
    schema.validate(body, "vnx.probe/1")
    assert body["readable"] == "yes" and body["frame"]["version"] == 4 and body["strand_profile"] == "v4-balanced"


def test_benchmark_generate_validate_profiles_native_keygen(tmp_path):
    g = check(sdk.generate(tmp_path / "g.bin", "10KB", "text", 5))
    assert g["result"]["sha256"] == sha(tmp_path / "g.bin")
    enc_dir = tmp_path / "e"
    enc_dir.mkdir()
    sdk.archive([tmp_path / "g.bin"], enc_dir / "a.vnx")
    sdk.encode(enc_dir / "a.vnx", enc_dir / "s.fasta")
    v = check(sdk.validate_strands(enc_dir / "s.fasta"))
    assert v["status"] == "SUCCESS" and v["result"]["valid"] is True
    p = check(sdk.profiles())
    assert "v4-balanced" in p["result"]["layouts"] and "maximum-recovery" in p["result"]["redundancy"]
    n = check(sdk.native())
    assert set(n["result"]["kernels"]) == {"align", "reads", "rs", "cluster"}
    k = check(sdk.keygen(tmp_path / "k.key"))
    assert k["status"] == "OK" and (tmp_path / "k.key").stat().st_mode & 0o777 == 0o600
    b = check(sdk.benchmark("safe", (8_192,)))
    assert b["kind"] == "benchmark" and b["result"]["results"]


def test_version_document():
    v = sdk.version()
    schema.validate(v, "vnx.version/1")
    assert v["software"] == sdk.envelope.SOFTWARE["version"] and v["spec"] == "6.0"
    assert v["frame"]["read"] == [4] and v["superblock"]["read"] == [1, 2] and "f4-sb2-cauchy-rs" in v["codecs"]
    assert v["vnx4_format"] == [4, 0]                       # 5.x key kept


def test_conformance_runs_the_packaged_vectors_with_both_backends():
    for backend in ("auto", "reference"):
        res = sdk.conformance(backend=backend)
        doc = check(res)
        assert doc["result"]["schema"] == "vnx.conformance/1" and doc["result"]["verdict"] == "CONFORMANT"
        assert doc["result"]["summary"]["total"] >= 18 and doc["result"]["summary"]["failed"] == 0
    assert sdk.conformance(backend="reference").body["backends"] == {"align": "reference", "reads": "reference",
                                                                      "rs": "reference", "cluster": "reference"}


def test_conformance_reports_a_wrong_expectation(tmp_path):
    import shutil
    from vnxdna.conformance import VECTORS
    shutil.copytree(VECTORS, tmp_path / "v")
    vid = "scrambler.keystream.v001"
    p = tmp_path / "v" / "stage" / vid / "vector.json"
    vec = json.loads(p.read_text())
    vec["expected"]["outputs"]["keystream_hex"] = "00" * 16
    p.write_text(json.dumps(vec))
    doc = sdk.conformance(tmp_path / "v").body
    assert doc["verdict"] == "NONCONFORMANT" and doc["summary"]["failed"] == 1
    assert next(r for r in doc["results"] if r["id"] == vid)["status"] == "FAIL"


def test_every_error_class_code_matches_its_category_and_exit_code():
    from vnxdna.core import errors as e
    from vnxdna.recovery.planner import VNXBudgetExceeded
    classes = [c for c in vars(e).values() if isinstance(c, type) and issubclass(c, VNXError)] + [VNXBudgetExceeded]
    for cls in classes:
        if cls is VNXError:
            continue
        cat, exit_code, _ = CODES[cls.code]
        assert (cat, exit_code) == (cls.category, cls.exit_code), cls
        doc = cls("x").to_dict()
        schema.validate(doc, "vnx.error/1")
        assert doc["code"] == cls.code and doc["error_class"] == cls.__name__ and doc["status"] == "FAILED"
    with pytest.raises(ValueError):
        e.VNXFormatError("x", code="NOT_A_CODE")
    schema.validate(e.error_json(RuntimeError("boom")), "vnx.error/1")


def test_unknown_performance_profile_is_a_configuration_error():
    from vnxdna.sdk.config import decode_options_for
    with pytest.raises(VNXConfigurationError) as e:
        decode_options_for(None, "bogus")
    assert e.value.exit_code == 7 and e.value.code == "CONFIGURATION_ERROR"
