"""V8.3 schema additions to vnx.channel-model/2: the V8 evidence vocabulary (observed / fitted / derived / assumed) and the
optional model identity hashes in provenance.fitting (parameter_sha256 checked against the stages). Existing models keep
their canonical form and SHA-256."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.simulation import model as cm, model2

ROOT = Path(__file__).resolve().parents[2]
FITTED = ROOT / "experiments/v7/fit-nano/d03-a7c/models/ont-guppy-hac-pass-fwd-fit.json"


def _doc():
    return json.loads(FITTED.read_text())


def test_existing_models_are_unchanged():
    m = cm.from_doc(_doc())[0]
    assert m.sha256 == "d6f40cda48b86a61d422c8345f4384a594a4a3792e0f3bf507ea1d06e4c29802"      # recorded by the V7 pre-check
    assert "parameter_sha256" not in m.doc["provenance"]["fitting"]


def test_v8_basis_labels_are_accepted_and_mapped():
    d = _doc()
    d["parameters"]["sequencing.substitution.rate"]["basis"] = "fitted"
    d["parameters"]["sequencing.insertion.base_weights"]["basis"] = "observed"
    m = cm.from_doc(d)[0]
    assert m.doc["parameters"]["sequencing.substitution.rate"]["basis"] == "fitted"
    assert {model2.V8_BASIS[b] for b in model2.BASES} == {"observed", "fitted", "derived", "assumed", "synthetic"}
    d["parameters"]["sequencing.insertion.rate"]["basis"] = "guessed"
    with pytest.raises(VNXConfigurationError, match="basis"):
        cm.from_doc(d)


def test_parameter_hash_is_checked():
    d = _doc()
    m = cm.from_doc(d)[0]
    h = model2.parameter_sha256(m.doc["stages"])
    d["provenance"]["fitting"]["parameter_sha256"] = h
    d["provenance"]["fitting"]["configuration_sha256"] = "a" * 64
    m2 = cm.from_doc(copy.deepcopy(d))[0]
    assert m2.doc["provenance"]["fitting"]["parameter_sha256"] == h
    d["stages"]["sequencing"]["insertion"]["rate"] *= 1.01
    with pytest.raises(VNXConfigurationError, match="parameter_sha256"):
        cm.from_doc(d)
    d = _doc()
    d["provenance"]["fitting"]["configuration_sha256"] = "xyz"
    with pytest.raises(VNXConfigurationError, match="64-hex"):
        cm.from_doc(d)


def test_parameter_hash_ignores_key_order():
    s = cm.from_doc(_doc())[0].doc["stages"]
    shuffled = json.loads(json.dumps(s, sort_keys=False))
    assert model2.parameter_sha256(dict(reversed(list(shuffled.items())))) == model2.parameter_sha256(s)


def test_a_derived_model_drops_the_fitted_parameter_hash():
    d = _doc()
    m = cm.from_doc(d)[0]
    d["provenance"]["fitting"]["parameter_sha256"] = model2.parameter_sha256(m.doc["stages"])
    fitted = cm.from_doc(d)[0]
    derived = fitted.with_parameters({"sequencing.coverage": {"model": "fixed", "mean": 3}}, label="validation")
    assert "parameter_sha256" not in derived.doc["provenance"]["fitting"]
    assert derived.doc["provenance"]["derived"][-1]["from_sha256"] == fitted.sha256          # the origin is still recorded
    assert fitted.doc["provenance"]["fitting"]["parameter_sha256"] == d["provenance"]["fitting"]["parameter_sha256"]
