"""Physical-validation interface tests. No physical data exist; the only complete example is a SYNTHETIC SOFTWARE TEST."""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

PHYS = Path(__file__).resolve().parents[3] / "experiments" / "v6" / "physical"
sys.path.insert(0, str(PHYS))
import validate as v  # noqa: E402

EXAMPLE = PHYS / "examples" / "synthetic_software_test"
SCHEMAS = sorted((PHYS / "schema").glob("*.schema.json"))


def load_example() -> dict:
    return json.loads((EXAMPLE / "record.json").read_text())


def status(rec, base=EXAMPLE) -> dict:
    return v.validate_record(rec, base)


def real_record(tmp_path: Path) -> tuple[dict, Path]:
    """A structurally complete REAL PHYSICAL RESULT-shaped record built from the example files with invented provider
    names. It is test data only and does not describe any real run."""
    shutil.copy(EXAMPLE / "reads.fastq", tmp_path / "reads.fastq")
    shutil.copy(EXAMPLE / "strands.fasta", tmp_path / "strands.fasta")
    shutil.copy(EXAMPLE / "decode_report.json", tmp_path / "decode_report.json")
    r = load_example()
    r["evidence_classification"] = "REAL PHYSICAL RESULT"
    r["synthesis"]["provider_name"] = "TestProvider A"
    r["synthesis"]["order_id"] = "ORD-TEST"
    r["sequencing"]["provider_name"] = "TestProvider B"
    r["storage"]["temperature_c"], r["storage"]["relative_humidity_percent"] = 4.0, 30.0
    r["attestations"]["signed_off_by"]["name"] = "Test Person"
    return r, tmp_path


def errors_of(rec, base=EXAMPLE) -> list[str]:
    r = status(rec, base)
    return r["schema_errors"] + r["rule_errors"]


def test_schemas_are_draft_2020_12_and_resolvable():
    assert len(SCHEMAS) == 9
    for p in SCHEMAS:
        d = json.loads(p.read_text())
        assert d["$schema"] == "https://json-schema.org/draft/2020-12/schema" and d["$id"] == p.name
    rec = json.loads((PHYS / "schema" / "record.schema.json").read_text())
    for k in ("synthesis", "sample", "storage", "sequencing", "decode", "result", "attestations"):
        assert (PHYS / "schema" / f"{k}.schema.json").is_file() and rec["properties"][k]["$ref"] == f"{k}.schema.json"
    assert set(json.loads((PHYS / "schema" / "record.schema.json").read_text())["properties"]["evidence_classification"]["anyOf"][0]["enum"]) == set(v.CLASSES)


def test_jsonschema_package_agrees_if_installed():
    js = pytest.importorskip("jsonschema")
    import referencing
    from referencing.jsonschema import DRAFT202012
    reg = referencing.Registry().with_resources(
        [(p.name, DRAFT202012.create_resource(json.loads(p.read_text()))) for p in SCHEMAS])
    validator = js.Draft202012Validator(json.loads((PHYS / "schema" / "record.schema.json").read_text()), registry=reg)
    assert not list(validator.iter_errors(load_example()))
    assert list(validator.iter_errors({"record_version": "1"}))


def test_template_validates_as_incomplete(tmp_path):
    out = tmp_path / "t.json"
    assert v.main(["template", "--out", str(out)]) == 0
    t = json.loads(out.read_text())
    r = status(t, tmp_path)
    assert r["status"] == "INCOMPLETE" and not r["schema_errors"] and not r["rule_errors"]
    assert t["evidence_classification"] is None and "No DNA has been synthesised" in t["statement"]
    assert "$.sequencing.fastq_files" in r["missing_fields"] and "$.attestations.raw_data_location" in r["missing_fields"]
    assert v.main(["check", str(out)]) == 3


def test_example_validates_and_is_labelled_synthetic():
    rec = load_example()
    r = status(rec)
    assert r["status"] == "VALID", r
    assert rec["evidence_classification"] == "SYNTHETIC SOFTWARE TEST"
    for k in ("synthesis", "sequencing"):
        assert rec[k]["provider_name"] == "none (simulation)"
    assert rec["result"]["recovered_sha256"] == rec["result"]["expected_sha256"] and rec["result"]["verification_result"] == "SUCCESS"
    assert v.main(["check", str(EXAMPLE / "record.json")]) == 0


def test_example_checksums_match_files():
    rec = load_example()
    f = rec["sequencing"]["fastq_files"][0]
    assert v.sha256_file(EXAMPLE / "reads.fastq") == f["sha256"] and (EXAMPLE / "reads.fastq").stat().st_size == f["size_bytes"]
    assert v.sha256_file(EXAMPLE / "strands.fasta") == rec["synthesis"]["ordered_fasta"]["sha256"]
    assert v.sha256_file(EXAMPLE / "decode_report.json") == rec["decode"]["report_sha256"]


def mut(path: str, value):
    def f(rec):
        d = rec
        *head, last = path.split(".")
        for k in head:
            d = d[k]
        d[last] = value
    return f


BAD = {
    "bad sha256 format": (mut("result.recovered_sha256", "ABC123"), "does not match"),
    "uppercase sha256": (mut("sequencing.fastq_files", [{"file_name": "reads.fastq", "sha256": "A" * 64, "size_bytes": 1, "read_count": 1, "read_role": "single"}]), "does not match"),
    "unknown key": (mut("surprise", 1), "unexpected key"),
    "bad classification": (mut("evidence_classification", "REAL"), "none of the allowed"),
    "dates out of order": (mut("storage.end_date", "2001-01-01"), "out of order"),
    "impossible date": (mut("storage.start_date", "2026-02-30"), "not a real calendar date"),
    "duration mismatch": (mut("storage.duration_days", 9), "duration_days"),
    "fastq sha mismatch": (lambda r: r["sequencing"]["fastq_files"][0].__setitem__("sha256", "0" * 64), "does not match the file"),
    "fastq size mismatch": (lambda r: r["sequencing"]["fastq_files"][0].__setitem__("size_bytes", 5), "size_bytes"),
    "fastq read count mismatch": (lambda r: r["sequencing"]["fastq_files"][0].__setitem__("read_count", 5), "read_count"),
    "fastq path missing": (lambda r: r["sequencing"]["fastq_files"][0].__setitem__("path", "nope.fastq"), "not found"),
    "total read count mismatch": (mut("sequencing.read_counts.total", 3), "read_counts.total"),
    "success with differing sha": (mut("result.recovered_sha256", "1" * 64), "SUCCESS but recovered_sha256 != expected"),
    "failure with identical sha": (mut("result.verification_result", "FAILURE"), "== expected_sha256"),
    "success with missing recovered sha": (mut("result.recovered_sha256", None), "requires both"),
    "success with fewer files": (mut("result.files_expected", 2), "files_recovered == files_expected"),
    "partial with all files": (mut("result.verification_result", "PARTIAL"), "== expected_sha256"),
    "more recovered than expected": (mut("result.files_recovered", 3), "exceeds"),
    "decode report sha mismatch": (mut("decode.report_sha256", "2" * 64), "report_sha256"),
    "library not in synthesis": (mut("sample.library_id", "OTHER"), "library_ids"),
    "synthetic names a provider": (mut("synthesis.provider_name", "Acme Bio"), "only REAL PHYSICAL RESULT may name a provider"),
    "synthetic has order id": (mut("synthesis.order_id", "ORD-1"), "must not carry a synthesis order ID"),
    "paired with one file": (mut("sequencing.layout", "paired"), "paired"),
    "passing exceeds total": (mut("sequencing.read_counts.passing_filter", 10**6), "passing_filter"),
    "bad humidity": (mut("storage.relative_humidity_percent", 140), "maximum"),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_cross_field_rule_rejects(name):
    change, needle = BAD[name]
    rec = copy.deepcopy(load_example())
    change(rec)
    r = status(rec)
    assert r["status"] == "INVALID", (name, r)
    assert any(needle in e for e in r["schema_errors"] + r["rule_errors"]), (name, r["schema_errors"], r["rule_errors"])


def test_cli_rejects_invalid_with_status_2(tmp_path):
    rec = load_example()
    rec["result"]["recovered_sha256"] = "1" * 64
    p = tmp_path / "r.json"
    p.write_text(json.dumps(rec))
    assert v.main(["check", str(p)]) == 2
    p.write_text("{not json")
    assert v.main(["check", str(p)]) == 2


def test_real_shaped_record_with_full_evidence_is_valid(tmp_path):
    rec, base = real_record(tmp_path)
    assert status(rec, base)["status"] == "VALID"   # structure only; this is test data, not a result


@pytest.mark.parametrize("field,setter", [
    ("synthesis provider", mut("synthesis.provider_name", None)),
    ("synthesis provider is simulation", mut("synthesis.provider_name", "none (simulation)")),
    ("synthesis order id", mut("synthesis.order_id", None)),
    ("synthesis order id placeholder", mut("synthesis.order_id", "TBD")),
    ("synthesis date", mut("synthesis.synthesis_date", None)),
    ("ordered fasta checksum", lambda r: r["synthesis"]["ordered_fasta"].__setitem__("sha256", None)),
    ("sequencing provider", mut("sequencing.provider_name", None)),
    ("sequencing provider is simulation", mut("sequencing.provider_name", "none (simulation)")),
    ("sequencing run id", mut("sequencing.run_id", None)),
    ("fastq files list", mut("sequencing.fastq_files", [])),
    ("fastq files null", mut("sequencing.fastq_files", None)),
    ("fastq checksum", lambda r: r["sequencing"]["fastq_files"][0].__setitem__("sha256", None)),
    ("synthesis attestation", lambda r: r["attestations"]["synthesis"].__setitem__("performed_by", None)),
    ("sequencing attestation", lambda r: r["attestations"]["sequencing"].__setitem__("performed_by", None)),
    ("sign-off", lambda r: r["attestations"]["signed_off_by"].__setitem__("name", None)),
    ("raw data location", mut("attestations.raw_data_location", None)),
])
def test_real_physical_claim_without_evidence_is_rejected(tmp_path, field, setter):
    rec, base = real_record(tmp_path)
    setter(rec)
    r = status(rec, base)
    assert r["status"] != "VALID", (field, r)
    assert r["status"] == "INVALID" and any("REAL PHYSICAL RESULT" in e or "null" in e or "type" in e or "expected" in e
                                           for e in r["schema_errors"] + r["rule_errors"]), (field, r)


def test_real_claim_on_the_example_as_shipped_is_rejected():
    rec = load_example()
    rec["evidence_classification"] = "REAL PHYSICAL RESULT"
    r = status(rec)
    assert r["status"] == "INVALID"
    assert sum("REAL PHYSICAL RESULT" in e for e in r["rule_errors"]) >= 2


def test_real_claim_on_template_is_rejected(tmp_path):
    t = v.make_template()
    t["evidence_classification"] = "REAL PHYSICAL RESULT"
    r = status(t, tmp_path)
    assert r["status"] == "INVALID" and any("fastq_files" in e for e in r["rule_errors"])
