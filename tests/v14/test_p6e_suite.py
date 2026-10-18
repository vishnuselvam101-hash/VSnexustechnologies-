"""Phase 6 workstream E harness logic: the preregistered manifest, its parser/validator, the statistics, the
correctness classification, the summary and the adversarial pool transforms (experiments/v14/bench/p6e-channels/suite.py).

SYNTHETIC SOFTWARE TEST data only; no decode is timed here and no channel number is produced.
"""
from __future__ import annotations

import copy
import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

from vnxdna.simulation.channel import ChannelConfig
from vnxdna.v6.loss import LossConfig

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "experiments" / "v14" / "bench" / "p6e-channels" / "suite.py"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


S = _load("p6e_suite", SUITE)


@pytest.fixture()
def manifest() -> dict:
    return S.load_manifest()


# ---------------------------------------------------------------------------------------------------- manifest
def test_committed_manifest_is_valid_and_preregisters_the_targets(manifest):
    names = [w["name"] for w in manifest["workloads"]]
    assert len(names) == len(set(names)) == 30
    a = S.workload(manifest, "T-A-indel-4m-w1")
    assert (a["kind"], a["size"], a["workers"], a["target"]) == ("decode", 4 << 20, 1, "A")
    assert S.channel_spec(manifest, a)["params"] == {"substitution_rate": 0.01, "insertion_rate": 0.002,
                                                     "deletion_rate": 0.002, "coverage": 5}
    assert S.seeds_of(a) == list(range(95500, 95505))            # the V13 report's indel-w1 seeds
    b = S.workload(manifest, "T-B-clean-4m-w4")
    assert (b["size"], b["workers"], b["target"]) == (4 << 20, 4, "B")
    assert S.channel_spec(manifest, b)["params"] == {"coverage": 3} and S.seeds_of(b) == list(range(95100, 95105))
    groups = {w["group"] for w in manifest["workloads"]}
    assert groups == set(S.GROUPS)
    assert manifest["label"] == "SIMULATED" and set(manifest["method"]["smoke"]["configurations"]) == set(S.CONFIGS)


def test_channel_fields_match_the_simulator_dataclass():
    defaults = {f.name: f.default for f in dataclasses.fields(ChannelConfig) if f.name != "seed"}
    assert defaults == S.CHANNEL_FIELDS


def test_every_decode_workload_resolves_to_a_valid_channel_and_loss(manifest):
    v6 = _load("v6_channel_compose", ROOT / "experiments" / "v6" / "channel" / "channel.py")
    for w in manifest["workloads"]:
        if w["kind"] != "decode":
            continue
        spec = S.channel_spec(manifest, w)
        ChannelConfig(seed=1, **spec["params"]).validate()
        LossConfig(**spec["loss"], seed=1).validate()
        if spec["simulator"] == "v6-compose":
            doc = S.model_document(w["name"], spec)
            assert v6.Model(doc, S.MANIFEST).channel == {**S.CHANNEL_FIELDS, **spec["params"]}
        if "model" in w["channel"]:
            assert spec["model"]["name"] == w["channel"]["model"] and len(spec["model"]["sha256"]) == 64
        else:
            assert spec["model"] is None
    assert S.channel_spec(manifest, S.workload(manifest, "T-A-indel-4m-w1"))["simulator"] == "v4"
    assert S.channel_spec(manifest, S.workload(manifest, "adv-loss-beyond-capacity-1m-w4"))["simulator"] == "v6-compose"


def _bad(manifest, mutate, match):
    m = copy.deepcopy(manifest)
    mutate(m)
    with pytest.raises(S.ManifestError, match=match):
        S.validate_manifest(m)


@pytest.mark.parametrize("mutate, match", [
    (lambda m: m.update(schema="x"), "schema"),
    (lambda m: m.update(label="MEASURED"), "SIMULATED"),
    (lambda m: m.update(workloads=[]), "non-empty"),
    (lambda m: m["workloads"].append(copy.deepcopy(m["workloads"][0])), "duplicate"),
    (lambda m: m["workloads"][0].update(kind="stream"), "kind"),
    (lambda m: m["workloads"][0].update(size=0), "size"),
    (lambda m: m["workloads"][0].update(size=True), "size"),
    (lambda m: m["workloads"][0].update(workers=0), "workers"),
    (lambda m: m["workloads"][0].update(payload="png"), "payload"),
    (lambda m: m["workloads"][0].update(expect="maybe"), "expect"),
    (lambda m: m["workloads"][0].update(group="misc"), "group"),
    (lambda m: m["workloads"][0].update(seeds={"start": 1, "n": 0, "extend_to": 0}), "n >= 1"),
    (lambda m: m["workloads"][0].update(seeds={"start": 1, "n": 5, "extend_to": 4}), "extend_to"),
    (lambda m: m["workloads"][0].update(seeds={"start": 1, "n": 5}), "seeds"),
    (lambda m: m["workloads"][0].update(channel={"ref": "NOPE"}), "unknown channel ref"),
    (lambda m: m["workloads"][0].update(channel={"params": {"coverag": 3}}), "unknown channel parameters"),
    (lambda m: m["workloads"][0].update(channel={"model": "no-such-model"}), "unknown channel model"),
    (lambda m: m["workloads"][0].update(channel={"model": "clean", "ref": "INDEL"}), "not both"),
    (lambda m: m["workloads"][0].update(channel={"ref": "INDEL", "overrides": {"coverage": 2}}), "overrides"),
    (lambda m: m["workloads"][0].update(channel={"ref": "INDEL", "loss": {"dropout": 1.5}}), "dropout"),
    (lambda m: m["workloads"][0].update(channel={"ref": "INDEL", "loss": {"drop": 0.1}}), "unknown loss"),
    (lambda m: m["workloads"][0].pop("channel"), "need a channel"),
    (lambda m: m["workloads"][0].update(archive_options={"key": "x"}), "archive_options"),
    (lambda m: m["workloads"][2].update(channel={"ref": "INDEL"}), "encode workloads"),
    (lambda m: m["workloads"][0].update(adversary={"type": "nuke"}), "adversary type"),
    (lambda m: m["workloads"][0].update(adversary={"type": "truncate"}), "takes exactly"),
    (lambda m: m["workloads"][0].update(adversary={"type": "truncate", "keep_fraction": 2}), "keep_fraction"),
    (lambda m: m["channels"].update(BAD={"rate": 1}), "channel ref BAD"),
])
def test_validator_rejects_malformed_manifests(manifest, mutate, match):
    _bad(manifest, mutate, match)


def test_load_manifest_rejects_unreadable_files(tmp_path):
    with pytest.raises(S.ManifestError, match="cannot read"):
        S.load_manifest(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(S.ManifestError, match="cannot read"):
        S.load_manifest(tmp_path / "bad.json")
    with pytest.raises(S.ManifestError, match="unknown workload"):
        S.workload(S.load_manifest(), "nope")


def test_seed_selection_smoke_extension_and_size(manifest):
    w = S.workload(manifest, "indel-16m-w4")
    smoke = manifest["method"]["smoke"]
    assert S.seeds_of(w) == [97300, 97301, 97302]
    assert S.seeds_of(w, extended=True) == list(range(97300, 97306))
    assert S.extension_seeds(w) == [97303, 97304, 97305]
    assert S.seeds_of(w, smoke=smoke) == [97300, 97301]
    assert S.effective_size(w, smoke) == 1 << 20 and S.effective_size(w, None) == 16 << 20
    tiny = {**w, "size": 1000}
    assert S.effective_size(tiny, smoke) == 1000


# ---------------------------------------------------------------------------------------------------- statistics
def test_boot_ci_is_the_v11_harness_statistic():
    v11 = _load("v11_bench", ROOT / "experiments" / "v11" / "bench" / "bench.py")
    for xs in ([1.0, 2.0, 3.0, 4.0, 5.0], [2.9, 2.7, 3.1, 2.8, 2.95, 3.3, 2.6], [1.0]):
        assert S.boot_ci(xs) == v11._boot_ci(xs)
    # n = 5: the percentile bootstrap of the median equals the min-max range (the V13 report's note)
    assert S.boot_ci([1.0, 2.0, 3.0, 4.0, 5.0]) == [1.0, 5.0]


def test_ratio_record_is_paired_and_reports_relative_width():
    r = S.ratio_record([10.0, 12.0, 8.0], [5.0, 4.0, 4.0])
    assert r["per_seed"] == [2.0, 3.0, 2.0] and r["median"] == 2.0 and r["n"] == 3
    assert r["ci95"] == [2.0, 3.0] and r["rel_ci_width"] == 0.5
    with pytest.raises(ValueError):
        S.ratio_record([1.0], [1.0, 2.0])
    with pytest.raises(ValueError):
        S.ratio_record([], [])


def test_noise_verdict_and_tiers():
    aa = {"ci95": [0.95, 1.05]}
    fast = {"ci95": [5.1, 5.6]}
    assert S.noise_verdict(fast, aa) == "faster beyond noise" and S.tier_of(fast, "faster beyond noise") == 2
    assert S.noise_verdict({"ci95": [1.0, 2.5]}, aa) == "within noise"
    assert S.noise_verdict({"ci95": [0.5, 0.9]}, aa) == "slower beyond noise"
    assert S.noise_verdict(fast, None) == "no A/A"
    assert S.tier_of({"ci95": [1.99, 2.31]}, "faster beyond noise") == 0   # lower bound below 2x: no tier
    assert S.tier_of({"ci95": [10.0, 12.0]}, "faster beyond noise") == 3
    assert S.tier_of({"ci95": [10.0, 12.0]}, "within noise") == 0
    assert S.overlaps([1, 2], [2, 3]) and not S.overlaps([1, 2], [2.1, 3])


def test_ci_extension_rule():
    ok = {"median": 2.0, "ci95": [1.9, 2.1], "rel_ci_width": 0.1}
    wide = {"median": 2.0, "ci95": [1.0, 3.0], "rel_ci_width": 1.0}
    assert not S.needs_extension({"v12/v14": ok, "v13/v14": ok, "v14/v14aa": ok})
    assert S.needs_extension({"v12/v14": ok, "v14/v14aa": wide})
    assert not S.needs_extension({"v12/v13": wide})          # v12/v13 is a re-measurement, not an extension trigger
    assert not S.needs_extension({})


@pytest.mark.parametrize("row, expect, cls", [
    ({"outcome": "SUCCESS", "matches_input": True}, "success", (False, True, "ok")),
    ({"outcome": "SUCCESS", "matches_input": False}, "no-false-success", (True, False, "false success")),
    ({"outcome": "SUCCESS", "matches_input": False}, "explicit-failure", (True, False, "false success")),
    ({"outcome": "SUCCESS", "matches_input": False, "output_is_foreign": True}, "no-false-success",
     (False, False, "decoded the foreign archive")),
    ({"outcome": "FAILED", "matches_input": False}, "success", (False, False, "failed")),
    ({"outcome": "FAILED", "matches_input": False}, "no-false-success", (False, True, "ok")),
    ({"outcome": "ERROR:VNXInputError", "matches_input": False}, "explicit-failure", (False, True, "ok")),
    ({"outcome": "SUCCESS", "matches_input": True}, "explicit-failure", (False, True, "unexpectedly recovered")),
    ({"outcome": "CRASH:-11", "matches_input": False}, "no-false-success", (False, False, "crash/timeout")),
    ({"outcome": "TIMEOUT", "matches_input": False}, "explicit-failure", (False, False, "crash/timeout")),
    ({"outcome": "ENCODED", "matches_input": None}, "success", (False, True, "ok")),
])
def test_classify(row, expect, cls):
    c = S.classify(row, expect)
    assert (c["false_success"], c["expectation_met"], c["note"]) == cls


def _row(w, seed, config, seconds, outcome="SUCCESS", matches=True, sha="a" * 64, rss=100 << 20):
    return {"workload": w, "seed": seed, "config": config, "seconds": seconds, "outcome": outcome,
            "matches_input": matches, "output_sha256": sha, "size": 1 << 20, "commit": "c" + config,
            "tree_rss_peak_bytes": rss, "load1_start": 1.0, "run_label": "measurement",
            "report": {"peak_rss_bytes": rss, "stage_seconds": {"pass1_reads": seconds / 2},
                       "engine": {"rung": "stream", "fallbacks": []}, "groups_failed": 0}}


def test_summarize_counts_false_success_and_uses_the_in_run_aa(manifest):
    m = {**manifest, "workloads": [S.workload(manifest, "indel-4m-w4"), S.workload(manifest, "lowcov2-4m-w4"),
                                   S.workload(manifest, "mixed-4m-w4")]}
    rows = []
    for i, seed in enumerate(range(95300, 95305)):
        rows += [_row("indel-4m-w4", seed, "v12", 3.0 + 0.01 * i), _row("indel-4m-w4", seed, "v13", 2.0),
                 _row("indel-4m-w4", seed, "v14", 1.0), _row("indel-4m-w4", seed, "v14aa", 1.0 + 0.01 * (i - 2))]
    for seed in (95400, 95401):
        rows += [_row("lowcov2-4m-w4", seed, c, 1.0) for c in ("v12", "v13", "v14aa")]
        rows.append(_row("lowcov2-4m-w4", seed, "v14", 1.0, matches=False, sha="b" * 64))
    rows.append(_row("lowcov2-4m-w4", 95402, "v12", 1.0))      # incomplete seed: not paired, not used
    s = S.summarize(rows, m)
    a = s["workloads"]["indel-4m-w4"]
    assert a["n"] == 5 and a["identical_outcome_and_output"] and a["false_success"] == {c: 0 for c in S.CONFIGS}
    assert a["ratios"]["v12/v14"]["median"] == 3.02 and a["ratios"]["v12/v14"]["verdict"] == "faster beyond noise"
    assert a["ratios"]["v12/v14"]["tier"] == 1 and "tier" not in a["ratios"]["v13/v14"]
    assert "verdict" not in a["ratios"]["v14/v14aa"] and a["stage_seconds_median"]["v14"] == {"pass1_reads": 0.5}
    assert a["engine_rungs"]["v14"] == ["stream"] and a["engine_fallbacks"]["v14"] == 0
    b = s["workloads"]["lowcov2-4m-w4"]
    assert b["n"] == 2 and b["incomplete_seeds"] == 1 and b["false_success"]["v14"] == 2
    assert b["expectation_failures"]["v14"] == [95400, 95401] and not b["identical_outcome_and_output"]
    assert s["workloads"]["mixed-4m-w4"] == {"status": "NOT RUN"}
    assert s["totals"]["false_success"] == 2 and s["totals"]["nonidentical_outputs"] == 1
    assert s["totals"]["runs"] == 5 * 4 + 2 * 4


# ---------------------------------------------------------------------------------------------------- inputs
def test_zero_heavy_payload_is_deterministic_and_mostly_zero():
    a, b = S.zero_heavy(1 << 20, 5), S.zero_heavy(1 << 20, 5)
    assert a == b and len(a) == 1 << 20 and a != S.zero_heavy(1 << 20, 6)
    assert a.count(0) / len(a) > 0.97 and a.count(0) < len(a)
    assert S.zero_heavy(0, 1) == b"" and len(S.zero_heavy(1000, 1)) == 1000


def _fastq(path: Path, n: int, length: int = 40) -> list:
    recs = [[b"@r%d" % i, (b"ACGT" * length)[i % 4:i % 4 + length], b"+", b"I" * length] for i in range(n)]
    S.write_fastq(path, recs)
    return recs


def test_fastq_round_trip(tmp_path):
    recs = _fastq(tmp_path / "r.fastq", 7)
    assert S.read_fastq(tmp_path / "r.fastq") == recs


@pytest.mark.parametrize("adv", [
    {"type": "junk-reads", "fraction": 0.5}, {"type": "malformed", "fraction": 0.2},
    {"type": "jackpot", "fraction": 0.1, "copies": 5}, {"type": "poison", "fraction": 0.5, "mutations": 3, "copies": 4},
    {"type": "foreign-mix", "fraction": 0.4}, {"type": "truncate", "keep_fraction": 0.5}, {"type": "empty"},
])
def test_adversary_transforms_are_deterministic(tmp_path, adv):
    out = []
    for k in range(2):
        d = tmp_path / str(k)
        d.mkdir()
        _fastq(d / "r.fastq", 100)
        _fastq(d / "f.fastq", 100, 41)
        (d / "s.fasta").write_bytes(b"".join(b">s%d\n%s\n" % (i, b"ACGT" * 10) for i in range(20)))
        info = S.adversary_transform(adv, d / "r.fastq", 9, foreign=d / "f.fastq", strands=d / "s.fasta")
        out.append(((d / "r.fastq").read_bytes(), info))
    assert out[0] == out[1]
    data, info = out[0]
    t = adv["type"]
    if t == "empty":
        assert data == b""
    elif t == "truncate":
        assert info["bytes_after"] == info["bytes_before"] // 2 and len(data) == info["bytes_after"]
    elif t == "junk-reads":
        assert info["junk_records"] == 50 and info["records_after"] == 150
        assert b"@junk_3\n\n+\n\n" in data                                          # an empty sequence record
    elif t == "malformed":
        assert info["malformed_records"] == 20 and b"X" in data and b"\x00" in data
    elif t == "jackpot":
        assert info["records_after"] == 100 + 10 * 4
    elif t == "poison":
        assert info["poisoned_strands"] == 10 and info["records_after"] == 100 + 10 * 4
        poisoned = [r[1] for r in S.read_fastq(Path(tmp_path / "0" / "r.fastq")) if r[0].startswith(b"@poison")]
        assert all(sum(x != y for x, y in zip(p, b"ACGT" * 10)) == 3 for p in poisoned)
    elif t == "foreign-mix":
        assert info["foreign_records"] == 67 and info["records_after"] == 167        # 40 % of the final pool


def test_report_excerpt_keeps_timings_and_counters():
    rep = {"stage_seconds": {"pass1_reads": 1.0}, "peak_rss_bytes": 5, "reads": {"reads": 9}, "engine": {"rung": "x"},
           "groups_failed": 1, "groups_decoded": 3, "superblock_conflicts": 0, "duplicate_conflicts": 2,
           "archive_tags_seen": ["a", "b"], "superblock": {"big": 1}, "by_pool_fallback": 4,
           "recovery_plan": {"budget_exhausted": [], "decisions": [{"stage": "FAST", "run": True},
                                                                  {"stage": "SLOW", "run": False}]}}
    ex = S.report_excerpt(rep)
    assert ex["stages_run"] == ["FAST"] and ex["archive_tags_seen"] == 2 and ex["by_pool_fallback"] == 4
    assert "superblock" not in ex and ex["stage_seconds"] == {"pass1_reads": 1.0} and ex["duplicate_conflicts"] == 2
    assert S.report_excerpt(None) == {}


def test_env_crash_detection_and_timing_status(manifest):
    assert S.env_crash("OpenBLAS blas_thread_init: pthread_create failed for thread 6 of 8: Resource temporarily")
    assert not S.env_crash("Segmentation fault") and not S.env_crash("") and not S.env_crash(None)
    m = {**manifest, "workloads": [S.workload(manifest, "adv-empty-pool")]}
    rows = [{**_row("adv-empty-pool", 98260, c, 0.01, outcome="ERROR:VNXFormatError", matches=False, sha=None),
             "run_label": S.SMOKE_LABEL} for c in S.CONFIGS]
    rows[0]["retried_env_crash"] = [{"returncode": -2}]
    s = S.summarize(rows, m)
    assert s["timings_are_results"] is False and "is a V14 result" in s["timing_status"]
    assert s["totals"]["env_crash_retries"] == 1 and s["totals"]["expectation_failures"] == 0
    rows = [{**r, "run_label": "measurement"} for r in rows]
    assert S.summarize(rows, m)["timings_are_results"] is True


def test_check_commits_pins_trees():
    commits = {"v12": "6bf7749aa", "v14": "423efa0bb", "v14aa": "423efa0bb+dirty"}
    S.check_commits(commits, {"v12": "6bf7749", "v14": "423efa0", "v13": ""})
    with pytest.raises(SystemExit, match="expected 58171b8"):
        S.check_commits(commits, {"v14": "58171b8"})
    with pytest.raises(SystemExit, match="v14aa"):
        S.check_commits(commits, {"v14aa": "423efa0"})
    with pytest.raises(SystemExit, match="unknown"):
        S.check_commits(commits, {"v13": "58171b8"})
