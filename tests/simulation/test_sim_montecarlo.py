"""Monte Carlo trials, parameter sweeps and the SDK channel calls (SIMULATED)."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from vnxdna import sdk
from vnxdna.core import schema
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.simulation import montecarlo, registry


@pytest.fixture(scope="module")
def strands(tmp_path_factory):
    p = tmp_path_factory.mktemp("mc") / "in.fasta"
    rng = np.random.default_rng(77)
    with open(p, "w") as f:
        for i, row in enumerate(rng.integers(0, 4, (400, 90))):
            f.write(f">s{i}\n{''.join('ACGT'[b] for b in row)}\n")
    return p


def test_monte_carlo_seeds_determinism_and_rates(strands, tmp_path):
    m = registry.load_model("substitution-heavy")
    a = montecarlo.monte_carlo(m, strands, tmp_path / "a", trials=4, base_seed=100)
    b = montecarlo.monte_carlo(m, strands, tmp_path / "b", trials=4, base_seed=100)
    assert a["seeds"] == [100, 101, 102, 103]
    assert [r["reads_sha256"] for r in a["runs"]] == [r["reads_sha256"] for r in b["runs"]]
    assert len({r["reads_sha256"] for r in a["runs"]}) == 4
    assert not any((tmp_path / "a").iterdir())                          # reads deleted unless keep_reads
    p = m.stages["sequencing"]["substitution"]["rate"]
    s = a["summary"]["substitution_rate"]
    n = sum(r["stats"]["reads"] for r in a["runs"]) * 90
    assert s["n"] == 4 and abs(s["mean"] - p) <= 5 * math.sqrt(p * (1 - p) / n) + 0.04 * p
    for r in a["runs"]:
        assert r["metadata"]["seed"] == r["seed"] and r["metadata"]["data_source"] == "SIMULATED"


def test_monte_carlo_evaluate_callback_and_keep_reads(strands, tmp_path):
    seen = []

    def evaluate(path, meta):
        seen.append(path.name)
        return {"bytes": path.stat().st_size, "sha": meta["output"]["sha256"]}
    res = montecarlo.monte_carlo(registry.load_model("clean"), strands, tmp_path, trials=2, keep_reads=True,
                                 evaluate=evaluate)
    assert seen == ["clean.s0.fastq", "clean.s1.fastq"] and (tmp_path / "clean.s1.fastq").is_file()
    assert res["runs"][0]["evaluation"]["sha"] == res["runs"][0]["reads_sha256"]


def test_sweep_grid_points_are_derived_models(strands, tmp_path):
    m = registry.load_model("clean")
    grid = {"sequencing.substitution.rate": [0.0, 0.02], "synthesis.dropout_rate": [0.0, 0.1]}
    doc = montecarlo.sweep(m, strands, tmp_path, grid, trials=2, base_seed=7)
    assert doc["schema"] == "vnx.channel-sweep/1" and doc["data_source"] == "SIMULATED" and len(doc["points"]) == 4
    assert doc["seed_rule"] == "trial seed = base_seed + trial index"
    pts = {(p["parameters"]["sequencing.substitution.rate"], p["parameters"]["synthesis.dropout_rate"]): p
           for p in doc["points"]}
    assert pts[(0.0, 0.0)]["summary"]["substitution_rate"]["max"] == 0.0
    assert pts[(0.02, 0.0)]["summary"]["substitution_rate"]["mean"] > 0.015
    assert pts[(0.0, 0.1)]["summary"]["strand_loss_fraction"]["mean"] > 0.05
    assert len({p["model_sha256"] for p in doc["points"]}) == 4
    meta = doc["points"][1]["runs"][0]["metadata"]
    assert meta["model"]["provenance"]["derived"][0]["from"] == "clean@1.0.0"
    json.dumps(doc)


@pytest.mark.parametrize("grid", [{"sequencing.bogus": [1]}, {"sequencing.substitution.rate": []},
                                  {"sequencing.substitution.rate": [0.9]}, {"x": 1}])
def test_bad_grids_fail_before_any_run(strands, tmp_path, grid):
    with pytest.raises(VNXConfigurationError):
        montecarlo.sweep(registry.load_model("clean"), strands, tmp_path / "o", grid, trials=1)
    assert not (tmp_path / "o").exists() or not any((tmp_path / "o").iterdir())


def test_trial_limits(strands, tmp_path):
    for bad in (0, -1, montecarlo.MAX_TRIALS + 1, True):
        with pytest.raises(VNXConfigurationError):
            montecarlo.monte_carlo(registry.load_model("clean"), strands, tmp_path, trials=bad)


def test_sdk_channel_calls(strands, tmp_path):
    res = sdk.simulate(strands, tmp_path / "r.fastq", model="illumina-like@1.0.0", seed=3, metadata=tmp_path / "r.json")
    doc = res.to_json()
    schema.validate(doc, "vnx.result/1")
    body = doc["result"]
    assert body["model"] == "illumina-like" and body["model_version"] == "1.0.0" and body["seed"] == 3
    assert body["model_schema"] == "vnx.channel-model/1" and body["evidence_class"] == "SIMULATED"
    assert doc["provenance"]["seeds"] == {"channel": 3}
    meta = json.loads((tmp_path / "r.json").read_text())
    schema.validate(meta, "vnx.simulation-metadata/1")
    assert {o["role"] for o in doc["outputs"]} == {"reads", "metadata"}
    assert meta["output"]["sha256"] == doc["outputs"][0]["sha256"]
    # named model + V4 override names + a dotted /1 path
    res = sdk.simulate(strands, tmp_path / "o.fastq", model="clean", seed=1, coverage=2,
                       overrides={"substitution_rate": 0.01, "sequencing.quality.sd": 1.0})
    assert res.body["metadata"]["overrides"] == {"sequencing.coverage.mean": 2, "sequencing.substitution.rate": 0.01,
                                                 "sequencing.quality.sd": 1.0}
    assert res.body["coverage"] == 2 and res.body["substitutions"] > 0
    with pytest.raises(VNXConfigurationError):
        sdk.simulate(strands, tmp_path / "x.fastq", model="clean", config=tmp_path / "r.json")
    lst = sdk.channel_models().to_json()
    schema.validate(lst, "vnx.result/1")
    assert lst["result"]["count"] == len(registry.available())
    one = sdk.channel_model("mixed-harsh").body["model"]
    schema.validate(one, "vnx.channel-model/1")
    v0 = sdk.channel_model("mixed-harsh", schema="vnx.channel-model/0").body["model"]
    assert v0["loss"]["dropout"] == 0.05
    (tmp_path / "v0.json").write_text(json.dumps(v0))
    sdk.channel_convert(tmp_path / "v0.json", tmp_path / "v1.json")
    assert json.loads((tmp_path / "v1.json").read_text())["stages"] == one["stages"]
    sw = sdk.channel_sweep(strands, tmp_path / "sw", model="clean", grid={"sequencing.n_rate": [0.0, 0.01]}, trials=1,
                           output=tmp_path / "sw.json")
    schema.validate(sw.to_json(), "vnx.result/1")
    assert json.loads((tmp_path / "sw.json").read_text())["points"][1]["summary"]["n_rate"]["mean"] > 0.005
