"""``vnx.channel-model/1`` documents: reading rules, validation, provenance labels, conversion (SIMULATED channel models)."""
from __future__ import annotations

import copy
import json

import jsonschema
import pytest

from vnxdna.core import schema
from vnxdna.core.errors import VNXConfigurationError, VNXUnsupportedVersionError
from vnxdna.simulation import model as cm
from vnxdna.simulation import registry


def minimal(**extra) -> dict:
    return {"schema": cm.SCHEMA_V1, "name": "t-model", "version": "1.0.0", **extra}


def test_minimal_document_is_filled_with_identity_defaults():
    m, seed = cm.from_doc(minimal())
    assert seed is None and m.read_as == cm.SCHEMA_V1
    assert m.stages == cm.normalize_stages({})
    assert m.doc["data_source"] == "SIMULATED" and m.doc["evidence_class"] == "SIMULATED"
    seq = m.stages["sequencing"]
    assert seq["coverage"] == {"model": "fixed", "mean": 1.0, "dispersion": 5.0, "sigma": 0.0}
    schema.validate(m.doc, "vnx.channel-model/1")
    # canonical: re-reading the canonical form is the identity, and the SHA-256 ignores formatting
    again, _ = cm.from_doc(json.loads(m.dumps()))
    assert again.doc == m.doc and again.sha256 == m.sha256


@pytest.mark.parametrize("sid", ["vnx.channel-model/2", "vnx.channel-model/17", "vnx.channel-config/3", "vnx.other/1"])
def test_unknown_schema_is_refused_as_unsupported(sid):
    with pytest.raises(VNXUnsupportedVersionError) as e:
        cm.from_doc(minimal(schema=sid))
    assert e.value.code == "SCHEMA_UNSUPPORTED" and e.value.exit_code == 6


def test_model_version_is_an_identifier_never_refused():
    for v in ("0.0.1", "7.3.0", "2.0.0-rc.1"):
        assert cm.from_doc(minimal(version=v))[0].version == v
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(minimal(version="one"))


@pytest.mark.parametrize("bad", [
    {"extra": 1},
    {"stages": {"sequencing": {"bogus": 1}}},
    {"stages": {"sequencing": {"coverage": {"model": "uniform"}}}},
    {"stages": {"sequencing": {"coverage": {"model": "fixed", "mean": 2.5}}}},
    {"stages": {"sequencing": {"substitution": {"rate": 0.3}, "deletion": {"rate": 0.3}}}},
    {"stages": {"sequencing": {"substitution": {"rate": 0.1, "matrix": [[0.1, 0.3, 0.3, 0.3]] * 4}}}},
    {"stages": {"sequencing": {"substitution": {"rate": 0.1, "matrix": [[0, 0.5, 0.5, 0.5], [0.5, 0, 0.25, 0.25],
                                                                         [0.5, 0.25, 0, 0.25], [0.5, 0.25, 0.25, 0]]}}}},
    {"stages": {"sequencing": {"deletion": {"rate": 0.01, "run_length": {"distribution": "single", "mean": 2.0}}}}},
    {"stages": {"sequencing": {"insertion": {"rate": 0.01, "base_weights": [0.5, 0.5, 0.5, 0.0]}}}},
    {"stages": {"sequencing": {"position_profile": {"basis": "cubic", "deletion": [1.0]}}}},
    {"stages": {"sequencing": {"position_profile": {"deletion": [-1.0]}}}},
    {"stages": {"sequencing": {"bursts": {"rate": 0.1, "max_length": 0}}}},
    {"stages": {"sequencing": {"quality": {"correct": 94}}}},
    {"stages": {"synthesis": {"molecules_per_strand": 0}}},
    {"stages": {"storage": {"strand_loss": {"rate": 1.0}}}},
    {"stages": {"storage": {"retention": 0.0}}},
    {"stages": {"storage": {"contamination_rate": 0.6}}},
    {"stages": {"amplification": {"cycles": 30, "substitution_per_cycle": {"rate": 0.02}}}},
    {"stages": {"transport": {}}},
    {"data_source": "MEASURED"},
    {"evidence_class": "PUBLIC-DATA-DERIVED"},                      # data_source SIMULATED cannot carry it
    {"data_source": "LABORATORY", "evidence_class": "PUBLIC-DATA-DERIVED"},    # needs datasets
    {"provenance": {"datasets": [{"accession": "PRJNA1", "sha256": "abc"}]}},
])
def test_invalid_documents_are_refused(bad):
    doc = copy.deepcopy(minimal())
    for k, v in bad.items():
        doc[k] = v
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(doc)


@pytest.mark.parametrize("bad", [
    {"extra": 1}, {"data_source": "MEASURED"},
    {"data_source": "SIMULATED", "evidence_class": "PUBLIC-DATA-DERIVED"},
    {"data_source": "LABORATORY", "evidence_class": "PUBLIC-DATA-DERIVED"},
])
def test_json_schema_agrees_on_top_level_rules(bad):
    """The shipped JSON Schema rejects what the reader rejects (canonical documents)."""
    doc = cm.from_doc(minimal())[0].to_json()
    doc.update(bad)
    with pytest.raises(jsonschema.ValidationError):
        schema.validate(doc, "vnx.channel-model/1")


def test_fitted_parameter_file_labels():
    """A V7-style fitted model: LABORATORY data source, PUBLIC-DATA-DERIVED, datasets with SHA-256."""
    doc = minimal(data_source="LABORATORY", evidence_class="PUBLIC-DATA-DERIVED",
                  provenance={"datasets": [{"accession": "ZENODO:10943282", "sha256": "0" * 64}],
                              "references": ["doi:10.0000/example"], "fitter": {"name": "fit_channel", "commit": "abc"}},
                  stages={"synthesis": {"deletion": {"rate": 0.003, "run_length": {"distribution": "geometric",
                                                                                   "mean": 2.6}},
                                        "position_profile": {"basis": "relative", "deletion": [5.0, 2.0, 1.0, 1.0]}},
                          "sequencing": {"coverage": {"model": "lognormal", "mean": 20.0, "sigma": 0.58},
                                         "substitution": {"rate": 0.0018,
                                                          "matrix": [[0, 0.2, 0.6, 0.2], [0.2, 0, 0.2, 0.6],
                                                                     [0.6, 0.2, 0, 0.2], [0.2, 0.6, 0.2, 0]],
                                                          "from_multipliers": [1.0, 1.2, 1.2, 1.0]},
                                         "position_profile": {"basis": "absolute", "substitution": [2.0] * 5 + [1.0]}}})
    m, _ = cm.from_doc(doc)
    schema.validate(m.doc, "vnx.channel-model/1")
    assert m.describe()["evidence_class"] == "PUBLIC-DATA-DERIVED"
    with pytest.raises(VNXConfigurationError, match="cannot express"):
        m.to_v0()
    assert set(cm.v0_expressible(m.stages)) >= {"synthesis.deletion", "synthesis.position_profile",
                                                "sequencing.substitution.matrix", "sequencing.coverage.sigma"}


def test_channel_config_documents_keep_their_seed(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"substitution_rate": 0.01, "seed": 42}))
    m, seed = cm.read_file(p)
    assert seed == 42 and m.read_as == cm.CONFIG_SCHEMA_V0 and m.stages["sequencing"]["substitution"]["rate"] == 0.01
    p.write_text(json.dumps({"substitution_rate": 0.01, "sneed": 1}))
    with pytest.raises(VNXConfigurationError):
        cm.read_file(p)
    p.write_text("{not json")
    with pytest.raises(VNXConfigurationError):
        cm.read_file(p)


def test_v0_documents_must_be_complete(tmp_path):
    doc = cm.v1_to_v0(registry.load_model("mixed-mild").doc)
    del doc["channel"]["n_rate"]
    with pytest.raises(VNXConfigurationError, match="missing"):
        cm.from_doc(doc)
    doc = cm.v1_to_v0(registry.load_model("mixed-mild").doc)
    doc["classification"] = "MEASURED"
    with pytest.raises(VNXConfigurationError):
        cm.from_doc(doc)


def test_registry_references():
    m = registry.load_model("illumina-like")
    assert registry.load_model("illumina-like@1.0.0") is m
    with pytest.raises(VNXConfigurationError, match="no version"):
        registry.load_model("illumina-like@2.0.0")
    with pytest.raises(VNXConfigurationError, match="unknown channel model"):
        registry.load_model("no-such-model")
    for name, version in registry.available():
        d = registry.load_model(f"{name}@{version}").describe()
        assert d["schema"] == cm.SCHEMA_V1 and d["data_source"] == "SIMULATED" and len(d["sha256"]) == 64


def test_derived_models_record_their_changes():
    m = registry.load_model("clean")
    d = m.with_parameters({"sequencing.substitution.rate": 0.01, "storage.damage.matrix":
                           [[0, 0, 1, 0], [0, 0, 0, 1], [1, 0, 0, 0], [0, 1, 0, 0]]}, label="test")
    assert d.sha256 != m.sha256 and d.name == m.name
    (rec,) = d.doc["provenance"]["derived"]
    assert rec["from"] == "clean@1.0.0" and rec["from_sha256"] == m.sha256 and rec["label"] == "test"
    assert d.stages["sequencing"]["substitution"]["rate"] == 0.01
    assert m.stages["sequencing"]["substitution"]["rate"] == 0.0          # the original is unchanged
    for bad in ({"sequencing.nope": 1}, {"nope.x": 1}, {"sequencing.n_rate.x": 1}, {"sequencing": 1}):
        with pytest.raises(VNXConfigurationError):
            m.with_parameters(bad)
    with pytest.raises(VNXConfigurationError):
        m.with_parameters({"sequencing.substitution.rate": 2.0})
    p = m.with_parameters({"sequencing.position_profile.deletion": [2.0, 1.0]})
    assert p.stages["sequencing"]["position_profile"] == {"basis": "absolute", "substitution": None, "insertion": None,
                                                          "deletion": [2.0, 1.0]}


def test_override_paths_follow_v4_rules():
    m = registry.load_model("clean")                      # fixed coverage 3
    assert cm.override_paths(m, {"coverage": 2.5}) == {"sequencing.coverage.mean": 2.5, "sequencing.coverage.model": "poisson"}
    assert cm.override_paths(m, {"coverage": 4, "substitution_rate": None}) == {"sequencing.coverage.mean": 4}
    assert cm.override_paths(m, {"dropout_rate": 0.1}) == {"synthesis.dropout_rate": 0.1}
    with pytest.raises(VNXConfigurationError):
        cm.override_paths(m, {"bogus": 1})
