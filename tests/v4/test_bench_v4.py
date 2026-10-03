"""Benchmark, sweep and experiment machinery (small sizes)."""
import json

from vnxdna.v4 import bench, datagen, experiment, sweep


def test_datagen_deterministic(tmp_path):
    for pattern in datagen.PATTERNS:
        a = datagen.generate(tmp_path / "a", 300_000, pattern, 5)
        b = datagen.generate(tmp_path / "b", 300_000, pattern, 5)
        assert a == b and (tmp_path / "a").stat().st_size == 300_000
    assert datagen.parse_size("2MB") == 2 << 20 and datagen.parse_size("1.5KB") == 1536


def test_end_to_end_isolated_reports_recoverable_rate():
    r = bench.isolated("end_to_end", input_size=50_000, channel={"substitution_rate": 0.002, "coverage": 2, "seed": 1})
    assert r["status"] == "SUCCESS" and r["input_sha256"] == r["output_sha256"]
    assert r["recoverable_mb_s"] > 0 and r["peak_rss_mb"] > 0


def test_failure_is_measured_not_hidden():
    r = bench.isolated("end_to_end", input_size=50_000, channel={"dropout_rate": 0.9, "coverage": 1, "seed": 1})
    assert r["status"] in ("PARTIAL", "FAILURE") and r["recoverable_mb_s"] == 0.0


def test_sweep_counts_every_trial():
    res = sweep.run_sweep({"input": {"size": 40_000, "pattern": "text", "seed": 2}, "channel": {"coverage": 1, "seed": 4},
                           "sweep": {"parameter": "dropout_rate", "values": [0.0, 0.6]}, "trials": 2, "workers": 1})
    assert [p["trials"] for p in res["points"]] == [2, 2]
    assert res["points"][0]["success_rate"] == 1.0 and res["points"][1]["success_rate"] == 0.0
    assert len(res["trials"]) == 4


def test_experiment_run_and_reproduce(tmp_path):
    d = tmp_path / "EXP-T001"
    d.mkdir()
    (d / "config.json").write_text(json.dumps({"id": "EXP-T001", "type": "sweep", "input": {"size": 30_000, "pattern": "mixed", "seed": 1},
                                               "channel": {"substitution_rate": 0.003, "coverage": 2, "seed": 77},
                                               "sweep": {"parameter": "deletion_rate", "values": [0.0, 0.003]}, "trials": 2}))
    experiment.run(d / "config.json")
    for name in ("environment.json", "seed.json", "results.json", "input.sha256"):
        assert (d / name).exists()
    rep = experiment.reproduce(d)
    assert rep["reproduced"], rep["differences"]


def test_codec_compare_runs_small():
    r = bench.codec_compare(k=64, trials=3, loss_rates=(0.0, 0.1), rs_groups=((32, 8),))
    assert r["schemes"]["cauchy-rs(32+8)"]["success_rate"]["0.0"] == 1.0
