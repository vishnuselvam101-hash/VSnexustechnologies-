import json

import pytest

from vnxops import cli, experiments, release


def test_matrix_expansion_is_deterministic():
    suite = {"defaults": {"size": "1KB"}, "matrix": [{"name": "s", "substitution_rate": [0.1, 0.2], "coverage": 5, "seeds": [1, 2]}]}
    t = experiments.expand(suite)
    assert len(t) == 4 and t == experiments.expand(suite)
    assert {x["seed"] for x in t} == {1, 2} and all(x["coverage"] == 5 and x["size"] == "1KB" for x in t)


def test_flags():
    assert experiments._flags({"gc_min": 40, "forbid_motif": ["AA", "CC"], "x": 1, "experimental_indel_repair": True},
                              ["gc_min", "forbid_motif", "experimental_indel_repair"]) == \
        ["--gc-min", "40", "--forbid-motif", "AA", "--forbid-motif", "CC", "--experimental-indel-repair"]


def test_all_suites_parse():
    for name in ("smoke", "error-models", "constraints"):
        assert experiments.expand(experiments.load_suite(name))


def test_real_trial_roundtrip_sha256(tmp_path):
    r = experiments.run_trial({"name": "t", "seed": 3, "size": "8KB", "coverage": 5, "substitution_rate": 0.003}, tmp_path, workers=1)
    assert r["outcome"] == "RECOVERED" and r["input_sha256"] == r["output_sha256"] and r["failed_bytes"] == 0
    for k in ("seed", "input_bytes", "error_model", "reads", "recovered_bytes", "runtime_s", "peak_rss_bytes", "cpu_seconds"):
        assert k in r


def _bench(tp, bits=1.9, rss=100 * 2**20, rec=1.0, wrong=0):
    return {"sizes": [{"size_label": "100KB", "bytes": 100_000, "throughput_MBps": {"encode": tp}, "net_bits_per_base": bits,
                       "peak_rss_bytes": {"encode": rss}}],
            "recovery": {"recovery_rate": rec, "undetected_corruption": wrong}}


THR = {"throughput_max_regression": 0.15, "peak_rss_max_regression": 0.25, "dna_density_max_regression": 0.0,
       "recovery_rate_min": 1.0, "undetected_corruption_max": 0}


def test_regression_gate():
    base = _bench(1.0)
    assert release.regression_vs_baseline(_bench(0.9), base, THR) == []
    assert release.regression_vs_baseline(_bench(0.8), base, THR)[0]["metric"] == "encode MB/s"
    assert release.regression_vs_baseline(_bench(1.0, bits=1.8), base, THR)[0]["metric"] == "net bits/base"
    assert release.regression_vs_baseline(_bench(1.0, rss=200 * 2**20), base, THR)[0]["metric"] == "peak RSS"
    assert release.regression_vs_baseline(_bench(1.0, wrong=1), base, THR)[0]["metric"] == "undetected corruption"
    assert release.regression_vs_baseline(_bench(1.0, rec=0.5), base, THR)[0]["metric"] == "recovery rate"


@pytest.mark.parametrize("argv,route", [
    (["status"], "ops"), (["doctor", "--deep"], "ops"), (["encode", "a", "-o", "b"], "library"),
    (["inspect", "a"], "library"), (["benchmark", "--system"], "ops"), (["benchmark", "scale"], "library"),
    (["experiment", "smoke"], "ops"), (["experiment", "run", "--input", "x"], "library"), (["--help"], "ops"),
    (["verify", "x"], "library"), (["simulate", "x"], "library"), (["release", "check", "--version", "v4"], "ops"),
])
def test_cli_routing(argv, route):
    assert cli.route(argv) == route


def test_cli_parser_has_all_spec_commands():
    p = cli.build_parser()
    choices = p._subparsers._group_actions[0].choices
    for c in ("doctor", "status", "benchmark", "experiment", "report", "agent", "release", "test", "laya", "queue", "governor"):
        assert c in choices


def test_experiment_list_json(capsys):
    assert cli.main(["experiment", "--list"]) == 0
    assert "smoke" in json.loads(capsys.readouterr().out)


def test_text_after_options_is_accepted(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "c_laya", lambda a: seen.setdefault("text", a.text) and 0)
    parser_fn = cli.build_parser

    def patched():
        p = parser_fn()
        p._subparsers._group_actions[0].choices["laya"].set_defaults(fn=cli.c_laya)
        return p
    monkeypatch.setattr(cli, "build_parser", patched)
    cli.main(["laya", "plan", "--type", "test_run", "--by", "x", "run the tests"])
    assert seen["text"] == "run the tests"
    with pytest.raises(SystemExit):
        cli.main(["laya", "plan", "--type", "test_run", "text", "--bogus"])
