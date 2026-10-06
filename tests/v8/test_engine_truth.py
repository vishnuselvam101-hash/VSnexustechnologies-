"""V8.7 simulator truth: ``Simulator.simulate_batch(truth=True)`` returns the source strand and reverse-complement flag of
every read without changing a single draw (reads byte-identical with and without truth), across coverage, duplicates,
missing reads, reverse complements and contamination. SIMULATED."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.simulation import model as cm, model2
from vnxdna.simulation.engine import Simulator
from vnxdna.simulation.errormodels import reverse_complement_flat


def _model(seq_extra=None, storage=None):
    seq = {"substitution": {"rate": 0.0}, "insertion": {"rate": 0.0}, "deletion": {"rate": 0.0},
           "coverage": {"model": "poisson", "mean": 4}, "reverse_complement_rate": 0.5}
    seq.update(seq_extra or {})
    stages = {"sequencing": seq, **({"storage": storage} if storage else {})}
    return cm.from_doc({"schema": cm.SCHEMA_V2, "name": "t", "version": "1.0.0", "stages": stages})[0]


@pytest.mark.parametrize("seq_extra, storage", [(None, None), ({"duplicate_rate": 0.3}, None),
                                                ({"missing_read_rate": 0.2}, None), (None, {"contamination_rate": 0.1})])
def test_truth_maps_every_read_and_changes_nothing(seq_extra, storage):
    m = _model(seq_extra, storage)
    rng = np.random.default_rng(3)
    codes = rng.integers(0, 4, (60, 40)).astype(np.uint8)
    sim = Simulator(m.stages)
    a = sim.simulate_batch(codes, 11, 0)
    b = sim.simulate_batch(codes, 11, 0, truth=True)
    assert np.array_equal(a["codes"], b["codes"]) and np.array_equal(a["lengths"], b["lengths"])
    assert "source" not in a and b["source"].size == b["lengths"].size == b["reverse_complement"].size
    offs = np.concatenate([[0], np.cumsum(b["lengths"])])
    for k, (s, rc) in enumerate(zip(b["source"], b["reverse_complement"])):
        read = b["codes"][offs[k]:offs[k + 1]].copy()
        if s < 0:
            continue
        if rc:
            q = np.zeros_like(read)
            reverse_complement_flat(read, q, np.array([read.size]), np.array([0]))
        assert np.array_equal(read, codes[s]), k                       # error-free channel: the read is its strand


def test_truth_with_errors_keeps_the_draws():
    seq = {"substitution": {"rate": 0.02}, "insertion": {"rate": 0.01, "run_length": model2.empirical_run_length([0.8, 0.2] + [0.0] * 6)},
           "deletion": {"rate": 0.01}, "coverage": {"model": "fixed", "mean": 3},
           "homopolymer": {"min_run": 3, "indel_multiplier": 1.0, "substitution_multiplier": 1.0,
                           "indel_by_length": [1.0, 1.2, 1.5, 2.0, 2.5, 3.0]}}
    m = cm.from_doc({"schema": cm.SCHEMA_V2, "name": "t", "version": "1.0.0", "stages": {"sequencing": seq}})[0]
    codes = np.random.default_rng(5).integers(0, 4, (30, 50)).astype(np.uint8)
    sim = Simulator(m.stages)
    a, b = sim.simulate_batch(codes, 2, 1), sim.simulate_batch(codes, 2, 1, truth=True)
    assert np.array_equal(a["codes"], b["codes"]) and np.array_equal(a["quals"], b["quals"])
    assert b["source"].tolist() == np.repeat(np.arange(30), 3).tolist()
