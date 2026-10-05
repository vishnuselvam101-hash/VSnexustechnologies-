"""``vnxdna.physical`` (V6 Phase 7): the record schemas and validator moved from ``experiments/v6/physical`` into the
package, the old path still works, and the PUBLIC-DATA-DERIVED evidence class (dataset registry, research gate 6)
requires a dataset accession, the SHA-256 of every downloaded file and a DOI or ``unpublished``.

No physical data exist; every record here is test data. The public-dataset fields below are invented test values, not
a claim about any dataset.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vnxdna import physical
from vnxdna.physical import validate as pv

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "experiments" / "v6" / "physical"
EXAMPLE = OLD / "examples" / "synthetic_software_test"


def example() -> dict:
    return json.loads((EXAMPLE / "record.json").read_text())


def public_record(tmp_path: Path) -> dict:
    """The synthetic example relabelled PUBLIC-DATA-DERIVED, with a 'downloaded' file written to tmp_path."""
    f = tmp_path / "download.fastq"
    f.write_bytes(b"@r1\nACGT\n+\nIIII\n")
    rec = example()
    rec["evidence_classification"] = "PUBLIC-DATA-DERIVED"
    rec["public_data"] = {"registry_id": "D99", "accession": "ENA PRJEB0000000 (test value)", "doi": "unpublished",
                          "licence": None, "downloaded_on": "2026-10-05",
                          "files": [{"file_name": "download.fastq", "url": None, "path": str(f),
                                     "sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "size_bytes": 16}]}
    return rec


def errors(r: dict) -> list[str]:
    return r["schema_errors"] + r["rule_errors"]


# ------------------------------------------------------------------------------------------------ the move
def test_old_script_path_is_the_package_validator():
    spec = importlib.util.spec_from_file_location("old_validate", OLD / "validate.py")
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    assert old.validate_record is pv.validate_record and old.main is pv.main and old.CLASSES == physical.CLASSES
    assert Path(old.SCHEMA_DIR) == pv.SCHEMA_DIR == Path(physical.__file__).with_name("schemas")


def test_old_schema_directory_resolves_to_the_package_schemas():
    old = sorted(p.name for p in (OLD / "schema").glob("*.schema.json"))
    new = sorted(p.name for p in pv.SCHEMA_DIR.glob("*.schema.json"))
    assert old == new and len(new) == 9
    for name in new:
        assert (OLD / "schema" / name).read_bytes() == (pv.SCHEMA_DIR / name).read_bytes()


def test_schemas_are_package_data():
    text = (ROOT / "pyproject.toml").read_text()
    assert '"vnxdna.physical" = ["schemas/*.json"]' in text


@pytest.mark.parametrize("entry", [[sys.executable, "-m", "vnxdna.physical"],
                                   [sys.executable, str(OLD / "validate.py")]], ids=["module", "old-script"])
def test_command_line_entry_points(entry):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run(entry + ["check", str(EXAMPLE / "record.json")], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["status"] == "VALID"


def test_layer_of_the_package():
    sys.path.insert(0, str(ROOT / "tests" / "architecture"))
    try:
        import test_layers as tl
    finally:
        sys.path.pop(0)
    assert tl.node_of("vnxdna.physical.validate") == "physical"


# ------------------------------------------------------------------------------------------------ PUBLIC-DATA-DERIVED
def test_public_data_derived_is_a_class_of_the_schema_and_validator():
    rec = json.loads((pv.SCHEMA_DIR / "record.schema.json").read_text())
    assert "PUBLIC-DATA-DERIVED" in rec["properties"]["evidence_classification"]["anyOf"][0]["enum"]
    assert "PUBLIC-DATA-DERIVED" in physical.CLASSES and physical.PUBLIC_DATA == "PUBLIC-DATA-DERIVED"


def test_public_data_derived_record_with_full_provenance_is_valid(tmp_path):
    r = physical.validate_record(public_record(tmp_path), EXAMPLE)
    assert r["status"] == "VALID", r


@pytest.mark.parametrize("doi", ["unpublished", "10.5281/zenodo.10943282", "10.1038/nbt.4079"])
def test_doi_or_unpublished_accepted(tmp_path, doi):
    rec = public_record(tmp_path)
    rec["public_data"]["doi"] = doi
    assert physical.validate_record(rec, EXAMPLE)["status"] == "VALID"


def _mutate(path: str, value):
    def f(rec):
        d = rec
        *head, last = path.split(".")
        for k in head:
            d = d[int(k)] if isinstance(d, list) else d[k]
        d[last] = value
    return f


BAD = {
    "no public_data": (_mutate("public_data", None), "requires public_data"),
    "accession missing": (_mutate("public_data.accession", None), "public_data.accession"),
    "accession placeholder": (_mutate("public_data.accession", "TBD"), "public_data.accession"),
    "doi missing": (_mutate("public_data.doi", None), "public_data.doi"),
    "doi not a doi": (_mutate("public_data.doi", "see paper"), "does not match"),
    "files missing": (_mutate("public_data.files", None), "public_data.files"),
    "files empty": (_mutate("public_data.files", []), "fewer than 1"),
    "file without sha256": (_mutate("public_data.files.0.sha256", None), "SHA-256 for public_data.files[0]"),
    "file sha256 wrong": (_mutate("public_data.files.0.sha256", "0" * 64), "does not match the file"),
    "file path missing": (_mutate("public_data.files.0.path", "nope.fastq"), "not found"),
    "unknown key": (_mutate("public_data.surprise", 1), "unexpected key"),
}


@pytest.mark.parametrize("name", sorted(BAD))
def test_public_data_derived_without_provenance_is_rejected(tmp_path, name):
    change, needle = BAD[name]
    rec = public_record(tmp_path)
    change(rec)
    r = physical.validate_record(rec, EXAMPLE)
    assert r["status"] == "INVALID", (name, r)
    assert any(needle in e for e in errors(r)), (name, errors(r))


def test_other_classes_are_unchanged_by_the_new_field(tmp_path):
    rec = example()
    assert "public_data" not in rec and physical.validate_record(rec, EXAMPLE)["status"] == "VALID"
    t = physical.make_template()
    assert t["public_data"] is None
    assert physical.validate_record(t, tmp_path)["status"] == "INCOMPLETE"
    t["evidence_classification"] = "PUBLIC-DATA-DERIVED"
    r = physical.validate_record(t, tmp_path)
    assert r["status"] == "INVALID" and any("requires public_data" in e for e in r["rule_errors"])


def test_jsonschema_package_agrees_on_public_data(tmp_path):
    js = pytest.importorskip("jsonschema")
    import referencing
    from referencing.jsonschema import DRAFT202012
    reg = referencing.Registry().with_resources(
        [(p.name, DRAFT202012.create_resource(json.loads(p.read_text()))) for p in pv.SCHEMA_DIR.glob("*.schema.json")])
    val = js.Draft202012Validator(json.loads((pv.SCHEMA_DIR / "record.schema.json").read_text()), registry=reg)
    good = public_record(tmp_path)
    assert not list(val.iter_errors(good))
    bad = copy.deepcopy(good)
    bad["public_data"]["doi"] = "see paper"
    assert list(val.iter_errors(bad)) and pv.schema_errors(bad, pv.load_schema("record.schema.json"))
    bad = copy.deepcopy(good)
    bad["public_data"]["files"] = []
    assert list(val.iter_errors(bad)) and pv.schema_errors(bad, pv.load_schema("record.schema.json"))
