"""V9 G1 channel-model effect: latent read classes (``read_heterogeneity.distribution = "latent-states"``)."""
from __future__ import annotations

import copy

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.simulation import engine, model as cm
from vnxdna.simulation.fit import simulate as S

from simulation.test_fit_heterogeneity import fitted, refs_of

TWO = {"distribution": "latent-states", "states": [{"weight": 0.8, "sub": 0.5, "ins": 0.5, "del": 0.5},
                                                   {"weight": 0.2, "sub": 3.0, "ins": 1.0, "del": 3.0}]}


def test_latent_states_are_validated_and_honoured():
    m, _ = cm.from_doc(fitted(copy.deepcopy(TWO)))
    assert m.doc["stages"]["sequencing"]["read_heterogeneity"] == TWO
    assert m.unsupported_effects() == []
    engine.Simulator(m.stages)
    with pytest.raises(VNXConfigurationError, match="cannot express"):
        m.to_v1()
    bad_states = [[], [{"weight": 1.0, "sub": 1, "ins": 1}], [{"weight": 0.5, "sub": 1, "ins": 1, "del": 1}],
                  [{"weight": 1.0, "sub": -1, "ins": 1, "del": 1}], [{"weight": 1.0, "sub": 51, "ins": 1, "del": 1}],
                  [{"weight": 0.2, "sub": 1, "ins": 1, "del": 1}] * 5, [{"weight": 1.0, "sub": 1, "ins": 1, "del": 1, "x": 0}]]
    for st in bad_states:
        with pytest.raises(VNXConfigurationError):
            cm.from_doc(fitted({"distribution": "latent-states", "states": st}))
    for bad in ({"distribution": "latent-states", "shape": 2.0, "states": TWO["states"]}, {"distribution": "gamma", "shape": 2.0,
                                                                                            "states": TWO["states"]}):
        with pytest.raises(VNXConfigurationError):
            cm.from_doc(fitted(copy.deepcopy(bad)))


def test_one_unit_class_simulates_exactly_like_no_effect():
    base, _ = cm.from_doc(fitted())
    one, _ = cm.from_doc(fitted({"distribution": "latent-states", "states": [{"weight": 1.0, "sub": 1.0, "ins": 1.0, "del": 1.0}]}))
    refs = refs_of(300, 80)
    assert S.simulate_clusters(base, refs, 2, 7) == S.simulate_clusters(one, refs, 2, 7)


def test_classes_mix_per_read_rates():
    edlib = pytest.importorskip("edlib")
    zero, _ = cm.from_doc(fitted({"distribution": "latent-states", "states": [{"weight": 0.5, "sub": 0.0, "ins": 0.0, "del": 0.0},
                                                                              {"weight": 0.5, "sub": 6.0, "ins": 6.0, "del": 6.0}]}))
    refs = refs_of(3000, 100)
    cl = S.simulate_clusters(zero, refs, 1, 11)
    d = np.array([edlib.align(r[0], ref)["editDistance"] for ref, r in zip(refs, cl)])
    clean = (d == 0).mean()
    assert 0.45 <= clean <= 0.55                       # the zero class is exactly error-free; the other almost never is
    assert d[d > 0].mean() > 10                        # 6x of 3 % per base over 100 bases
    assert np.array_equal(d, np.array([edlib.align(r[0], ref)["editDistance"]
                                       for ref, r in zip(refs, S.simulate_clusters(zero, refs, 1, 11))]))


def test_site_cap():
    w = np.array([1.0])
    m = np.array([[50.0, 50.0, 50.0]])
    r = tuple(np.full((2, 5), 0.1) for _ in range(3))
    s, i, d = engine.latent_states(r, w, m, np.random.default_rng(1))
    assert np.allclose(s + i + d, engine.HETEROGENEITY_SITE_CAP)


def test_fuzz_latent_states_schema():
    """Property: any read_heterogeneity value is either refused with VNXConfigurationError or accepted, simulates, and
    round-trips through the canonical document."""
    hyp = pytest.importorskip("hypothesis")
    st = hyp.strategies
    num = st.one_of(st.floats(allow_nan=True, allow_infinity=True), st.integers(-5, 100), st.booleans(), st.none(), st.text(max_size=3))
    state = st.dictionaries(st.sampled_from(["weight", "sub", "ins", "del", "x"]), num, max_size=5)
    het = st.one_of(st.none(), st.fixed_dictionaries({"distribution": st.sampled_from(["latent-states", "gamma", "beta"])},
                                                    optional={"states": st.lists(state, max_size=6), "shape": num}))

    @hyp.settings(max_examples=400, deadline=None)
    @hyp.given(het)
    def run(h):
        try:
            m, _ = cm.from_doc(fitted(copy.deepcopy(h)))
        except VNXConfigurationError:
            return
        again, _ = cm.from_doc(__import__("json").loads(m.dumps()))
        assert again.sha256 == m.sha256
        S.simulate_clusters(m, refs_of(4, 30), 1, 3)

    run()
