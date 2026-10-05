"""``vnx.experiment/1`` manifests and ``vnx experiment reproduce`` (V6 directive §24; docs/CONFORMANCE.md).

* the strict validator: valid, invalid (every field) and malformed (bytes, JSON, duplicates, size, depth) manifests, each
  rejected with a typed error and the stable exit code, never an assertion;
* write → read round trips; an invalid manifest is never written;
* reproduce: the committed manifest of one EXP-SIM-1 cell (clean × v6-max-recovery × seed 9100) reproduces the read-file
  SHA-256 recorded in ``experiments/v6/phase3/EXP-SIM-1/results.json``; a changed result hash, input or seed does not;
* the CLI exit codes 0 / 1 / 3 / 6 / 7 / 8.

SYNTHETIC SOFTWARE TEST data; channel results are SIMULATED. No DNA was synthesised, stored or sequenced.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vnxdna.benchmark import experiment
from vnxdna.benchmark import manifest as mf
from vnxdna.commands import app
from vnxdna.core import schema
from vnxdna.core.errors import VNXConfigurationError, VNXError, VNXOutputError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "experiment" / "exp-sim-1.clean.s9100.manifest.json"
STRANDS = ROOT / "tests" / "fixtures" / "v6_0" / "max-recovery.strands.fasta"
EXP_SIM_1 = ROOT / "experiments" / "v6" / "phase3" / "EXP-SIM-1" / "results.json"
SMALL_CONFIG = {"id": "manifest-test", "type": "sweep", "purpose": "SYNTHETIC SOFTWARE TEST of vnx.experiment/1",
                "input": {"size": 4096, "pattern": "random", "seed": 5}, "dna": {"profile": "v4-balanced"},
                "channel": {"coverage_model": "fixed", "seed": 6},
                "sweep": {"grid": {"substitution_rate": [0.0], "coverage": [2]}}, "trials": 1, "workers": 1}


def fixture_doc() -> dict:
    return json.loads(FIXTURE.read_text())


def cli(*args, code=0):
    r = CliRunner().invoke(app, [str(a) for a in args])
    assert r.exit_code == code, (r.exit_code, r.stdout[-3000:], r.stderr[-3000:] if r.stderr_bytes else "")
    return r


def err_json(r) -> dict:
    doc = json.loads(r.stderr)
    schema.validate(doc, "vnx.error/1")
    return doc


@pytest.fixture(scope="module")
def exp_dir(tmp_path_factory):
    """One small experiment run (encode → SIMULATED channel → decode) with its manifest.json."""
    d = tmp_path_factory.mktemp("exp") / "manifest-test"
    d.mkdir()
    (d / "config.json").write_text(json.dumps(SMALL_CONFIG))
    res = experiment.run(d / "config.json")
    assert res["manifest"]["written"] is True
    return d


# ================================================================================================================ valid
def test_committed_manifest_is_valid_and_matches_the_committed_exp_sim_1_cell():
    doc = mf.read(FIXTURE)
    schema.validate(doc, mf.SCHEMA)
    assert set(mf.FIELDS) <= set(doc) and doc["kind"] == "channel-simulation" and doc["source_class"] == "SIMULATED"
    cell = next(c for c in json.loads(EXP_SIM_1.read_text())["cells"]
                if c["model"] == "clean" and c["strands"] == "v6-max-recovery" and c["seed"] == 9100)
    assert doc["result_hash"] == cell["v1_sha256"] and doc["seed"] == 9100
    assert doc["simulator_version"]["model"]["sha256"] == cell["model_sha256_v1"]
    assert doc["input_hash"] == json.loads(EXP_SIM_1.read_text())["inputs"]["v6-max-recovery"]["sha256"]
    assert doc["simulator_version"]["model_schema"] == "vnx.channel-model/1"


def test_experiment_run_writes_a_valid_manifest(exp_dir):
    doc = mf.read(exp_dir / "manifest.json")
    schema.validate(doc, mf.SCHEMA)
    assert doc["kind"] == "experiment" and doc["experiment_id"] == "manifest-test"
    assert doc["input_hash"] == mf.sha256_json(SMALL_CONFIG) and doc["seed"] == 6 and doc["workers"] == 1
    results = json.loads((exp_dir / "results.json").read_text())
    assert doc["result_hash"] == mf.experiment_result_hash(results)


def test_experiment_ids_outside_the_allowed_characters_are_made_safe():
    assert mf._safe_id("EXP 1/a") == "EXP-1-a" and mf._safe_id("") is None and mf._safe_id(7) is None
    assert mf._safe_id("-x") is None


# ================================================================================================================ invalid
def _set(path: str, value):
    def change(doc):
        *parents, last = path.split(".")
        node = doc
        for p in parents:
            node = node[p]
        if value is _DELETE:
            del node[last]
        else:
            node[last] = value
    return change


_DELETE = object()

INVALID = {
    "missing-field": _set("result_hash", _DELETE),
    "missing-seed": _set("seed", _DELETE),
    "unknown-field": _set("extra", 1),
    "id-empty": _set("experiment_id", ""),
    "id-slash": _set("experiment_id", "a/b"),
    "id-int": _set("experiment_id", 5),
    "kind-unknown": _set("kind", "wet-lab"),
    "source-unknown": _set("source_class", "MEASURED"),
    "source-laboratory": _set("source_class", "LABORATORY"),
    "input-hash-short": _set("input_hash", "ab" * 31),
    "input-hash-upper": _set("input_hash", "AB" * 32),
    "result-hash-int": _set("result_hash", 0),
    "commit-short": _set("commit", "e27bf2e"),
    "commit-int": _set("commit", 1),
    "codec-missing-spec": _set("codec_version.spec", _DELETE),
    "codec-extra": _set("codec_version.vendor", "x"),
    "codec-string": _set("codec_version", "6.0.0"),
    "simulator-missing-model": _set("simulator_version.model", _DELETE),
    "simulator-null-model": _set("simulator_version.model", None),
    "simulator-model-sha": _set("simulator_version.model.sha256", "0" * 64),
    "simulator-model-name": _set("simulator_version.model.name", "illumina-like"),
    "seed-negative": _set("seed", -1),
    "seed-bool": _set("seed", True),
    "seed-float": _set("seed", 9100.0),
    "seed-too-big": _set("seed", 2 ** 63),
    "workers-zero": _set("workers", 0),
    "workers-huge": _set("workers", 4096),
    "hardware-list": _set("hardware", []),
    "hardware-no-cpu": _set("hardware.cpu_model", _DELETE),
    "hardware-cpus-zero": _set("hardware.logical_cpus", 0),
    "parameters-extra": _set("parameters.extra", 1),
    "parameters-no-input": _set("parameters.input", _DELETE),
    "parameters-input-empty": _set("parameters.input.file", ""),
    "parameters-input-nul": _set("parameters.input.file", "a\x00b"),
    "parameters-format": _set("parameters.format", "bam"),
    "model-tampered": _set("parameters.model.stages.sequencing.substitution.rate", 0.05),
    "model-not-v1": _set("parameters.model.schema", "vnx.channel-model/0"),
    "model-invalid": _set("parameters.model.stages.sequencing.substitution.rate", 2.0),
    "statement-int": _set("statement", 3),
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_invalid_manifests_are_typed_format_errors(name):
    doc = fixture_doc()
    INVALID[name](doc)
    with pytest.raises(mf.ManifestError) as info:
        mf.validate(doc)
    err = info.value
    assert err.code == "FORMAT_ERROR" and err.exit_code == 3 and err.stage == "manifest" and err.details.get("field")


EXPERIMENT_INVALID = {
    "input-hash": lambda d: d.update(input_hash="0" * 64),
    "seed": lambda d: d.update(seed=7),
    "workers": lambda d: d.update(workers=2),
    "config-type": lambda d: d["parameters"]["config"].update(type="nonsense"),
    "config-list": lambda d: d["parameters"].update(config=[]),
    "config-changed": lambda d: d["parameters"]["config"].update(trials=2),
}


@pytest.mark.parametrize("name", sorted(EXPERIMENT_INVALID))
def test_inconsistent_experiment_manifests_are_rejected(exp_dir, name):
    doc = mf.read(exp_dir / "manifest.json")
    EXPERIMENT_INVALID[name](doc)
    with pytest.raises(mf.ManifestError):
        mf.validate(doc)


def test_unknown_schema_major_is_unsupported_and_other_schemas_are_format_errors():
    doc = fixture_doc()
    doc["schema"] = "vnx.experiment/2"
    with pytest.raises(VNXError) as info:
        mf.validate(doc)
    assert info.value.code == "SCHEMA_UNSUPPORTED" and info.value.exit_code == 6
    for bad in ("vnx.experiment/1.1", "vnx.simulation-metadata/1", None, 1):
        doc["schema"] = bad
        with pytest.raises(mf.ManifestError):
            mf.validate(doc)
    with pytest.raises(mf.ManifestError):
        mf.validate([fixture_doc()])


# ================================================================================================================ malformed
MALFORMED = {
    "empty": b"",
    "truncated": FIXTURE.read_bytes()[:200],
    "not-utf8": b"\xff\xfe{}",
    "list": b"[]",
    "string": b'"vnx.experiment/1"',
    "nan": FIXTURE.read_bytes().replace(b'"seed": 9100', b'"seed": NaN'),
    "infinity": FIXTURE.read_bytes().replace(b'"seed": 9100', b'"seed": Infinity'),
    "duplicate-key": FIXTURE.read_bytes().replace(b'"seed": 9100', b'"seed": 9100, "seed": 9101'),
    "deep": b"[" * 100_000 + b"]" * 100_000,
    "deep-object": b'{"schema": "vnx.experiment/1", "hardware": ' + b'{"a": ' * 2000 + b"1" + b"}" * 2000 + b"}",
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_manifest_bytes_are_typed_errors(name, tmp_path):
    with pytest.raises(mf.ManifestError) as info:
        mf.loads(MALFORMED[name])
    assert info.value.exit_code == 3
    p = tmp_path / "m.json"
    p.write_bytes(MALFORMED[name])
    with pytest.raises(mf.ManifestError):
        mf.read(p)


def test_oversized_and_missing_manifests_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "MAX_BYTES", 1000)
    with pytest.raises(mf.ManifestError, match="larger than"):
        mf.loads(FIXTURE.read_bytes())
    with pytest.raises(mf.ManifestError, match="larger than"):
        mf.read(FIXTURE)
    with pytest.raises(mf.ManifestError, match="cannot read"):
        mf.read(tmp_path / "absent.json")


# ================================================================================================================ round trip
def test_write_read_round_trip(tmp_path):
    doc = fixture_doc()
    out = mf.write(doc, tmp_path / "m.json")
    assert mf.read(out) == doc and mf.loads(mf.dumps(doc)) == doc
    with pytest.raises(VNXOutputError):
        mf.write(doc, out)
    mf.write(doc, out, overwrite=True)
    assert mf.read(out) == doc


def test_an_invalid_manifest_is_never_written(tmp_path):
    doc = fixture_doc()
    doc["seed"] = -5
    with pytest.raises(mf.ManifestError):
        mf.write(doc, tmp_path / "bad.json")
    assert list(tmp_path.iterdir()) == []


def test_simulation_manifest_records_a_relative_input_inside_the_manifest_directory(tmp_path):
    from vnxdna import sdk
    strands = tmp_path / "s.fasta"
    strands.write_bytes(STRANDS.read_bytes())
    res = sdk.simulate(strands, tmp_path / "r.fastq", model="clean", seed=3, manifest=tmp_path / "m.json")
    doc = mf.read(tmp_path / "m.json")
    assert doc["parameters"]["input"]["file"] == "s.fasta" and doc["seed"] == 3
    assert doc["result_hash"] == res.body["metadata"]["output"]["sha256"]
    assert doc["experiment_id"].startswith("sim-clean-s3-")
    other = tmp_path / "elsewhere"
    other.mkdir()
    sdk.simulate(strands, tmp_path / "r2.fastq", model="clean", seed=3, manifest=other / "m.json")
    assert mf.read(other / "m.json")["parameters"]["input"]["file"] == str(strands.resolve())


# ================================================================================================================ reproduce
def test_committed_manifest_reproduces_for_any_worker_count():
    for workers in (None, 2):
        rep = mf.reproduce(FIXTURE, workers=workers)
        assert rep["reproduced"] is True, rep
        assert rep["checks"] == {"input_hash": "MATCH", "result_hash": "MATCH"}
        assert rep["schema"] == mf.REPRODUCTION_SCHEMA and rep["evidence_class"] == "SIMULATED"
        assert rep["observed"]["result_hash"] == fixture_doc()["result_hash"]


def test_a_different_result_hash_is_a_mismatch(tmp_path):
    doc = fixture_doc()
    doc["result_hash"] = "0" * 64
    rep = mf.reproduce(doc, input_path=STRANDS)
    assert rep["reproduced"] is False and rep["checks"]["result_hash"] == "MISMATCH"
    assert rep["observed"]["result_hash"] == fixture_doc()["result_hash"]


def test_a_different_seed_does_not_reproduce(tmp_path):
    """(The committed ``clean`` cell draws no random numbers, so a seed-sensitive model is used here.)"""
    from vnxdna import sdk
    sdk.simulate(STRANDS, tmp_path / "r.fastq", model="illumina-like", seed=11, manifest=tmp_path / "m.json")
    assert mf.reproduce(tmp_path / "m.json")["reproduced"] is True
    doc = mf.read(tmp_path / "m.json")
    doc["seed"] = 12
    rep = mf.reproduce(doc, input_path=STRANDS)
    assert rep["reproduced"] is False and rep["checks"] == {"input_hash": "MATCH", "result_hash": "MISMATCH"}


def test_a_different_input_stops_before_the_rerun(tmp_path):
    other = tmp_path / "other.fasta"
    other.write_text(">x\n" + "ACGT" * 30 + "\n")
    rep = mf.reproduce(FIXTURE, input_path=other)
    assert rep["reproduced"] is False and rep["checks"] == {"input_hash": "MISMATCH", "result_hash": "NOT_RUN"}
    with pytest.raises(mf.ManifestError, match="input not found"):
        mf.reproduce(FIXTURE, input_path=tmp_path / "absent.fasta")
    with pytest.raises(VNXConfigurationError):
        mf.reproduce(FIXTURE, workers=0)


def test_experiment_manifest_reproduces_and_detects_a_changed_result(exp_dir):
    rep = mf.reproduce(exp_dir / "manifest.json")
    assert rep["reproduced"] is True, rep
    doc = mf.read(exp_dir / "manifest.json")
    doc["result_hash"] = "f" * 64
    rep = mf.reproduce(doc)
    assert rep["reproduced"] is False and rep["checks"]["result_hash"] == "MISMATCH"


def test_context_differences_are_reported_not_judged():
    doc = fixture_doc()
    doc["codec_version"]["software"] = "5.0.0"
    doc["commit"] = "0" * 40
    rep = mf.reproduce(doc, input_path=STRANDS)
    assert rep["reproduced"] is True
    assert rep["context"]["codec_version.software"] == "DIFFERENT" and rep["context"]["commit"] in ("DIFFERENT", "UNKNOWN")
    doc["commit"] = None
    assert mf.reproduce(doc, input_path=STRANDS)["context"]["commit"] == "UNKNOWN"


# ================================================================================================================ CLI
def test_cli_reproduce_exit_codes(tmp_path):
    doc = json.loads(cli("experiment", "reproduce", FIXTURE).stdout)
    schema.validate(doc, doc["schema"])
    assert doc["kind"] == "experiment" and doc["status"] == "SUCCESS" and doc["reproduced"] is True

    bad = fixture_doc()
    bad["result_hash"] = "0" * 64
    (tmp_path / "mismatch.json").write_text(json.dumps(bad))
    doc = json.loads(cli("experiment", "reproduce", tmp_path / "mismatch.json", "--input", STRANDS, code=1).stdout)
    assert doc["status"] == "FAILURE" and doc["reproduced"] is False

    (tmp_path / "malformed.json").write_text("{not json")
    assert err_json(cli("experiment", "reproduce", tmp_path / "malformed.json", code=3))["code"] == "FORMAT_ERROR"
    invalid = fixture_doc()
    invalid["workers"] = 0
    (tmp_path / "invalid.json").write_text(json.dumps(invalid))
    assert err_json(cli("experiment", "reproduce", tmp_path / "invalid.json", code=3))["code"] == "FORMAT_ERROR"
    future = fixture_doc()
    future["schema"] = "vnx.experiment/2"
    (tmp_path / "future.json").write_text(json.dumps(future))
    assert err_json(cli("experiment", "reproduce", tmp_path / "future.json", code=6))["code"] == "SCHEMA_UNSUPPORTED"
    moved = fixture_doc()
    (tmp_path / "moved.json").write_text(json.dumps(moved))           # relative input path no longer resolves
    assert err_json(cli("experiment", "reproduce", tmp_path / "moved.json", code=3))["code"] == "FORMAT_ERROR"
    cli("experiment", "reproduce", tmp_path / "moved.json", "--input", STRANDS, "--workers", "2")
    assert err_json(cli("experiment", "reproduce", tmp_path / "absent.json", code=3))["code"] == "FORMAT_ERROR"
    err = err_json(cli("experiment", "reproduce", FIXTURE, "--workers", "0", code=7))
    assert err["code"] == "CONFIGURATION_ERROR"


def test_cli_directory_mode_is_unchanged_and_refuses_manifest_options(exp_dir):
    doc = json.loads(cli("experiment", "reproduce", exp_dir).stdout)
    assert doc["reproduced"] is True and "differences" in doc["result"]
    assert err_json(cli("experiment", "reproduce", exp_dir, "--workers", "2", code=7))["code"] == "CONFIGURATION_ERROR"


def test_cli_simulate_manifest_then_reproduce(tmp_path):
    strands = tmp_path / "s.fasta"
    strands.write_bytes(STRANDS.read_bytes())
    m = tmp_path / "m.json"
    doc = json.loads(cli("channel", "simulate", strands, tmp_path / "r.fastq", "--model", "illumina-like", "--seed", "4",
                         "--param", "sequencing.coverage.mean=2", "--manifest", m, "--experiment-id", "cli-sim").stdout)
    assert any(o.get("role") == "manifest" for o in doc["outputs"])
    rec = mf.read(m)
    assert rec["experiment_id"] == "cli-sim" and rec["parameters"]["model"]["stages"]["sequencing"]["coverage"]["mean"] == 2
    assert cli("experiment", "reproduce", m).exit_code == 0
    # an existing manifest is not overwritten without --force, and nothing else is written either
    err = err_json(cli("channel", "simulate", strands, tmp_path / "r2.fastq", "--model", "clean", "--manifest", m, code=8))
    assert err["code"] == "OUTPUT_ERROR" and not (tmp_path / "r2.fastq").exists() and mf.read(m) == rec


def test_manifest_schema_document_is_registered():
    s = schema.load(mf.SCHEMA)
    assert s["$id"] == "urn:vnx:vnx.experiment/1" and set(s["required"]) == set(mf.FIELDS)
    doc = copy.deepcopy(fixture_doc())
    schema.validate(doc)
