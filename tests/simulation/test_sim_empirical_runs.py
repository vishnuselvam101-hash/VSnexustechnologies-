"""``vnx.channel-model/2`` empirical insertion/deletion run lengths (V7 step 7.2, docs/V7_NANOPORE_MODEL_DESIGN.md
section 3): validation and typed refusals, canonical form and SHA-256 stability, /1 refusal, sampler distribution,
determinism, unchanged geometric draws, and the run lengths the engine produces (SIMULATED, software tests)."""
from __future__ import annotations

import copy
import json
import random

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.simulation import engine, model as cm, model2
from vnxdna.simulation.errormodels import DeletionModel, InsertionModel, draw_runs

# D3 run-15 FIT deletion runs 1:2.44 M, 2:1.18 M, 3:0.21 M, 4:0.07 M (shape only; not a fitted parameter)
PMF = [0.62, 0.25, 0.07, 0.03, 0.015, 0.008, 0.004, 0.003]


def _doc(seq_stage):
    return {"schema": cm.SCHEMA_V2, "name": "t", "version": "1.0.0", "stages": {"sequencing": seq_stage}}


def _model(seq_stage):
    return cm.from_doc(_doc(seq_stage))[0]


def _strands(path, n, length, seed=3):
    rnd = random.Random(seed)
    seqs = ["".join(rnd.choice("ACGT") for _ in range(length)) for _ in range(n)]
    path.write_text("".join(f">s{i}\n{s}\n" for i, s in enumerate(seqs)))
    return seqs


# -- validation ---------------------------------------------------------------------------------------------------------
def test_empirical_run_length_helper_computes_the_mean():
    rl = model2.empirical_run_length(PMF, 10.0)
    want = sum((i + 1) * p for i, p in enumerate(PMF[:-1])) + PMF[-1] * 10.0
    assert rl == {"distribution": "empirical", "mean": want, "pmf": PMF, "tail_mean": 10.0}
    assert model2.empirical_run_length([1.0] + [0.0] * 7)["mean"] == 1.0


@pytest.mark.parametrize("kind", ["insertion", "deletion"])
def test_empirical_runs_load_canonicalise_and_round_trip(kind):
    rl = model2.empirical_run_length(PMF, 9.5)
    m = _model({kind: {"rate": 0.01, "run_length": rl}})
    assert m.stages["sequencing"][kind]["run_length"] == rl
    again, _ = cm.from_doc(json.loads(m.dumps()))
    assert again.doc == m.doc and again.sha256 == m.sha256
    assert f"{kind}.run_length" in model2.active_effects(m.stages)
    with pytest.raises(VNXConfigurationError, match="cannot express"):
        m.to_v1()


def test_models_without_empirical_runs_keep_their_canonical_form():
    m = _model({"deletion": {"rate": 0.01, "run_length": {"distribution": "geometric", "mean": 1.4}},
                "insertion": {"rate": 0.01, "run_length": {"distribution": "geometric", "mean": 1.2}}})
    seq = m.stages["sequencing"]
    assert seq["deletion"]["run_length"] == {"distribution": "geometric", "mean": 1.4}
    assert seq["insertion"]["run_length"] == {"distribution": "geometric", "mean": 1.2}
    assert "deletion.run_length" not in model2.active_effects(m.stages)


def _bad(mut):
    rl = model2.empirical_run_length(PMF, 9.0)
    mut(rl)
    return rl


@pytest.mark.parametrize("rl, why", [
    (_bad(lambda r: r.update(pmf=PMF[:7])), "8 probabilities"),
    (_bad(lambda r: r.update(pmf=[0.5] + PMF[1:])), "sum to 1"),
    (_bad(lambda r: r.update(pmf=[1.1, -0.1] + [0.0] * 6)), "pmf"),
    (_bad(lambda r: r.update(pmf="x")), "8 probabilities"),
    (_bad(lambda r: r.update(mean=r["mean"] + 0.01)), "implied"),
    (_bad(lambda r: r.update(tail_mean=7.0)), "tail_mean"),
    (_bad(lambda r: r.update(tail_mean=None)), "tail_mean"),
    (_bad(lambda r: r.update(extra=1)), "extra"),
    (_bad(lambda r: r.update(pmf=[float("nan")] + PMF[1:])), "pmf"),
])
@pytest.mark.parametrize("kind", ["insertion", "deletion"])
def test_malformed_empirical_runs_are_refused_with_a_typed_error(rl, why, kind):
    with pytest.raises(VNXConfigurationError, match=why):
        _model({kind: {"rate": 0.01, "run_length": copy.deepcopy(rl)}})


def test_empirical_runs_are_refused_outside_the_v2_sequencing_stage():
    rl = model2.empirical_run_length(PMF)
    with pytest.raises(VNXConfigurationError):          # synthesis keeps the /1 deletion form
        cm.from_doc({"schema": cm.SCHEMA_V2, "name": "t", "version": "1.0.0",
                     "stages": {"synthesis": {"deletion": {"rate": 0.01, "run_length": rl}}}})
    with pytest.raises(VNXConfigurationError):          # a /1 document cannot carry it
        cm.from_doc({"schema": cm.SCHEMA_V1, "name": "t", "version": "1.0.0",
                     "stages": {"sequencing": {"deletion": {"rate": 0.01, "run_length": rl}}}})


# -- sampler ------------------------------------------------------------------------------------------------------------
def test_draw_runs_follows_the_pmf_and_the_tail_mean():
    m = DeletionModel(0.01, "empirical", model2.empirical_mean(PMF, 12.0), None, tuple(PMF), 12.0)
    n = 400_000
    runs = draw_runs(m, n, np.random.default_rng(7))
    assert runs.dtype == np.int64 and runs.min() == 1
    for k in range(1, 8):
        p = PMF[k - 1]
        assert abs((runs == k).mean() - p) < 5 * (p * (1 - p) / n) ** 0.5, k
    tail = runs[runs >= 8]
    assert abs(tail.size / n - PMF[7]) < 5 * (PMF[7] / n) ** 0.5
    assert abs(tail.mean() - 12.0) < 0.5                     # 7 + Geometric(mean 5): sd ~4.5, ~1200 tail draws
    assert abs(runs.mean() - m.run_mean) < 0.02


def test_draw_runs_is_deterministic_and_a_tail_mean_of_8_gives_exactly_8():
    m = InsertionModel(0.01, None, None, "empirical", model2.empirical_mean(PMF, 8.0), tuple(PMF), 8.0)
    a = draw_runs(m, 10_000, np.random.default_rng(1))
    b = draw_runs(m, 10_000, np.random.default_rng(1))
    assert np.array_equal(a, b) and a.max() == 8


def test_geometric_draws_are_the_v6_draws():
    m = DeletionModel(0.01, "geometric", 1.7)
    assert np.array_equal(draw_runs(m, 5000, np.random.default_rng(4)), np.random.default_rng(4).geometric(1 / 1.7, 5000))


def test_clustered_flag():
    one = (1.0,) + (0.0,) * 7
    assert not DeletionModel(0.01, "empirical", 1.0, None, one, 8.0).clustered
    assert DeletionModel(0.01, "empirical", 1.5, None, tuple(PMF), 8.0).clustered
    assert InsertionModel(0.01, None, None, "empirical", 1.5, tuple(PMF), 8.0).clustered


def test_parameters_report_the_empirical_distribution():
    rl = model2.empirical_run_length(PMF, 9.0)
    assert DeletionModel.from_json({"rate": 0.01, "run_length": rl}).parameters()["run_length"] == rl
    assert InsertionModel.from_json({"rate": 0.01, "base_weights": None, "run_length": rl}).parameters()["run_length"] == rl


# -- engine -------------------------------------------------------------------------------------------------------------
def _length_change_hist(tmp_path, seq_stage, seed, n=6000, length=60):
    p = tmp_path / "s.fasta"
    seqs = _strands(p, n, length)
    out = tmp_path / f"o{seed}-{len(list(tmp_path.glob('o*.fastq')))}.fastq"
    engine.simulate_file(p, out, _model({**seq_stage, "coverage": {"model": "fixed", "mean": 1}}), seed)
    reads = [x for x in out.read_text().split("\n")[1::4] if x]
    hist: dict[int, int] = {}
    for r, s in zip(reads, seqs):
        d = len(r) - len(s)
        if d:
            hist[d] = hist.get(d, 0) + 1                  # mostly one event per read at these rates
    return hist, out.read_bytes()


@pytest.mark.parametrize("kind, sign", [("deletion", -1), ("insertion", 1)])
def test_engine_produces_the_empirical_run_lengths(tmp_path, kind, sign):
    pmf = [0.5, 0.3, 0.1, 0.05, 0.03, 0.01, 0.005, 0.005]
    stage = {kind: {"rate": 0.0015, "run_length": model2.empirical_run_length(pmf, 8.0)}}
    hist, raw = _length_change_hist(tmp_path, stage, 11)
    total = sum(v for d, v in hist.items() if d * sign > 0)
    assert total > 300
    for k in (1, 2, 3):
        share = hist.get(sign * k, 0) / total
        assert abs(share - pmf[k - 1]) < 0.06, (k, share)
    assert sum(v for d, v in hist.items() if d * sign >= 4) / total > 0.05
    again, raw2 = _length_change_hist(tmp_path, stage, 11)
    assert raw2 == raw                                                 # same seed, same reads


def test_engine_empirical_shape_is_not_a_geometric_with_the_same_mean(tmp_path):
    rl = model2.empirical_run_length([0.5, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0])      # runs of 1 or 4, mean 2.5
    emp, _ = _length_change_hist(tmp_path, {"deletion": {"rate": 0.0015, "run_length": rl}}, 5)
    geo, _ = _length_change_hist(tmp_path, {"deletion": {"rate": 0.0015, "run_length": {"distribution": "geometric",
                                                                                        "mean": 2.5}}}, 5)
    share = {k: (h.get(-2, 0) + h.get(-3, 0)) / sum(v for d, v in h.items() if d < 0) for k, h in (("e", emp), ("g", geo))}
    assert share["e"] < 0.05 < 0.25 < share["g"], share       # geometric(2.5): P(2) + P(3) = 0.24 + 0.144
