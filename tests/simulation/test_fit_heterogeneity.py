"""Protocol 5.5 (amendment 2): the opt-in /2 ``read_heterogeneity`` effect (schema, simulator, estimator, calibration) and the
matched read-length selection of the fitter's tally (SIMULATED, software tests)."""
from __future__ import annotations

import copy
import json
import random

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.simulation import engine, model as cm, registry
from vnxdna.simulation.model2 import dataset_digest

FILES = [{"name": "a.txt", "sha256": "1" * 64}]


def fitted(het=None, **over):
    seq = {"substitution": {"rate": 0.01}, "insertion": {"rate": 0.01}, "deletion": {"rate": 0.01}}
    if het is not None:
        seq["read_heterogeneity"] = het
    doc = {
        "schema": cm.SCHEMA_V2, "name": "unit-fit", "version": "1.0.0", "model_id": "unit-fit-F",
        "data_source": "LABORATORY", "evidence_class": "PUBLIC-DATA-DERIVED",
        "provenance": {
            "datasets": [{"id": "D04", "accession": "test:cnr", "url": "https://example.org", "files": FILES,
                          "sha256": dataset_digest(FILES)}],
            "split": {"name": "FIT", "manifest_sha256": "0" * 64},
            "fitting": {"method": "unit", "version": "1", "commit": "a" * 40, "dirty": False, "seed": 1,
                        "timestamp_utc": "2026-10-05T12:00:00Z", "software": {"numpy": "2"}}},
        "stages": {"sequencing": seq},
    }
    doc.update(over)
    return doc


def refs_of(n, L, seed=1):
    rnd = random.Random(seed)
    return [bytes(rnd.choice(b"ACGT") for _ in range(L)) for _ in range(n)]


# -- schema ------------------------------------------------------------------------------------------------------------
def test_absent_heterogeneity_leaves_the_canonical_document_and_hash_unchanged():
    m, _ = cm.from_doc(fitted())
    assert "read_heterogeneity" not in m.doc["stages"]["sequencing"]
    again, _ = cm.from_doc(json.loads(m.dumps()))
    assert again.sha256 == m.sha256
    assert m.to_v1()["schema"] == cm.SCHEMA_V1


def test_heterogeneity_is_validated_honoured_and_blocks_v1_conversion():
    m, _ = cm.from_doc(fitted({"distribution": "gamma", "shape": 2.5}))
    assert m.doc["stages"]["sequencing"]["read_heterogeneity"] == {"distribution": "gamma", "shape": 2.5}
    assert m.unsupported_effects() == []
    engine.Simulator(m.stages)                                     # honoured: not refused
    with pytest.raises(VNXConfigurationError, match="cannot express"):
        m.to_v1()
    for bad in ({"distribution": "lognormal", "shape": 2.0}, {"distribution": "gamma", "shape": 0.0},
                {"distribution": "gamma", "shape": float("nan")}, {"distribution": "gamma", "shape": 1e7},
                {"distribution": "gamma"}, {"distribution": "gamma", "shape": 2.0, "extra": 1}, [1, 2], "gamma"):
        with pytest.raises(VNXConfigurationError):
            cm.from_doc(fitted(copy.deepcopy(bad)))


def test_with_parameters_can_set_the_heterogeneity_shape():
    m, _ = cm.from_doc(fitted({"distribution": "gamma", "shape": 2.5}))
    d = m.with_parameters({"sequencing.read_heterogeneity.shape": 4.0})
    assert d.stages["sequencing"]["read_heterogeneity"]["shape"] == 4.0


# -- simulator ---------------------------------------------------------------------------------------------------------
def _per_read_errors(model, n=4000, L=100, seed=3):
    from vnxdna.simulation.fit import simulate as S
    stats: dict = {}
    refs = refs_of(n, L, seed=1)
    cl = S.simulate_clusters(model, refs, 1, seed, stats=stats)
    import edlib
    d = np.array([edlib.align(r[0], ref)["editDistance"] for ref, r in zip(refs, cl)], dtype=float)
    return d, stats


def test_gamma_multiplier_keeps_the_mean_and_inflates_the_per_read_variance():
    pytest.importorskip("edlib")
    base, _ = cm.from_doc(fitted())
    het, _ = cm.from_doc(fitted({"distribution": "gamma", "shape": 2.0}))
    d0, s0 = _per_read_errors(base)
    d1, s1 = _per_read_errors(het)
    assert abs(d1.mean() - d0.mean()) <= 0.06 * d0.mean()
    assert d1.var() > 2.0 * d0.var()                               # Var adds about mean^2 / shape = 4.5 to ~3
    # deterministic: same seed, same reads
    d1b, _ = _per_read_errors(het)
    assert np.array_equal(d1, d1b)


def test_models_without_the_effect_simulate_exactly_as_before(tmp_path):
    """The multiplier has its own random stream: a shipped model's reads do not change (byte identity is also covered by the
    shipped-model golden hashes in test_sim_model2)."""
    from vnxdna.simulation.fit import simulate as S
    m = registry.load_model("nanopore-like")
    refs = refs_of(200, 120, seed=2)
    a = S.simulate_clusters(m, refs, 3, 5)
    assert a == S.simulate_clusters(m, refs, 3, 5)
    sim = engine.Simulator(m.stages)
    assert sim.het_shape is None


def test_site_cap_keeps_scaled_probabilities_valid():
    rng = np.random.default_rng(1)
    s = np.full((50, 10), 0.3)
    i = np.full((50, 10), 0.3)
    d = np.full((50, 10), 0.3)
    a, b, c = engine.read_heterogeneity((s, i, d), 0.2, rng)        # very dispersed: many rows would exceed 1
    assert np.all(a + b + c <= engine.HETEROGENEITY_SITE_CAP + 1e-12) and np.all(a >= 0)


# -- tally selection and estimator -------------------------------------------------------------------------------------
def test_length_window_removes_only_reads_outside_it_and_counts_them():
    pytest.importorskip("edlib")
    from vnxdna.simulation.fit import tally as T
    ref = refs_of(1, 60, seed=4)[0]
    reads = [ref, ref[:-3], ref + b"AAAAA", ref[:-8]]               # drift 0, -3, +5, -8
    lay = T.Layout(60)
    v_all, rs_all = T.tally_reference(ref, reads, lay)
    v, rs = T.tally_reference(ref, reads, lay, length_window=(-4, 5))
    assert int(lay.get(v, "window_out")[0]) == 1 and int(lay.get(v, "n_reads")[0]) == 3
    assert int(lay.get(v_all, "n_reads")[0]) == 4 and int(lay.get(v_all, "window_out")[0]) == 0
    assert int(lay.get(v, "ed_n")[0]) == 3 and int(lay.get(v, "ed_sum")[0]) == 0 + 3 + 5
    assert int(lay.get(v, "ed_sq")[0]) == 9 + 25
    assert rs.sum() == 2 * 3 and rs_all.sum() == 2 * 4


@pytest.mark.parametrize("shape", [None, 2.5])
def test_moment_estimator_separates_homogeneous_from_heterogeneous_reads(shape):
    pytest.importorskip("edlib")
    from vnxdna.simulation.fit import estimate as est, pipeline as P, simulate as S, tally as T
    doc = fitted() if shape is None else fitted({"distribution": "gamma", "shape": shape})
    doc["stages"]["sequencing"].update({"substitution": {"rate": 0.02}, "insertion": {"rate": 0.015}, "deletion": {"rate": 0.02}})
    m, _ = cm.from_doc(doc)
    refs = refs_of(1500, 100, seed=6)
    lay = T.Layout(100)
    M, _ = P.tally_matrix(zip(refs, S.simulate_clusters(m, refs, 4, 9)), lay)
    s2 = est.heterogeneity_moment(lay, M.sum(axis=0).astype(float))
    if shape is None:
        assert s2 <= est.HETEROGENEITY_MIN
    else:
        assert 0.5 / shape <= s2 <= 1.5 / shape


def test_calibrated_fit_recovers_a_simulated_heterogeneity_shape():
    pytest.importorskip("edlib")
    from vnxdna.simulation.fit import fit as F, pipeline as P, simulate as S, tally as T
    doc = fitted({"distribution": "gamma", "shape": 3.0})
    doc["stages"]["sequencing"].update({"substitution": {"rate": 0.02}, "insertion": {"rate": 0.015}, "deletion": {"rate": 0.02}})
    m, _ = cm.from_doc(doc)
    refs = refs_of(2000, 100, seed=7)
    lay = T.Layout(100)
    M, _ = P.tally_matrix(zip(refs, S.simulate_clusters(m, refs, 6, 4)), lay)
    fit = F.fit_tallies(M, lay, seed=3, bootstrap=20, with_coverage=False,
                        calibration=dict(refs=refs[:1500], coverage=8, iterations=5))
    assert fit["design"].heterogeneity
    shape = fit["values"]["sequencing.read_heterogeneity.shape"]
    assert 3.0 / 1.35 <= shape <= 3.0 * 1.35, shape
    assert "ed_var" in fit["calibration"]["final_residual_ratios"]
    lo, hi = fit["ci95"]["sequencing.read_heterogeneity.shape"]
    assert lo <= hi
