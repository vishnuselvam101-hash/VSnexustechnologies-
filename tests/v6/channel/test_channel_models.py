"""Channel-model layer tests (SIMULATED channel; no biological claims): determinism, realised-vs-configured rates,
sidecar completeness, CLI, and a small end-to-end evaluate run."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

CHAN_DIR = Path(__file__).resolve().parents[3] / "experiments" / "v6" / "channel"
sys.path.insert(0, str(CHAN_DIR))
import channel as chn  # noqa: E402
import evaluate as ev  # noqa: E402

from vnxdna.v4.errors import VNXConfigurationError  # noqa: E402

N_STRANDS, L = 2000, 120          # 2 simulator batches of 1024
ALL = chn.model_names()


@pytest.fixture(scope="module")
def strands(tmp_path_factory) -> Path:
    rng = np.random.default_rng(20260601)
    p = tmp_path_factory.mktemp("strands") / "in.fasta"
    with open(p, "w") as f:
        for i, row in enumerate(rng.integers(0, 4, (N_STRANDS, L))):
            f.write(f">s{i}\n{''.join('ACGT'[b] for b in row)}\n")
    return p


@pytest.fixture(scope="module")
def runs(strands, tmp_path_factory):
    """Cache: model name → (sidecar, fastq path); seed fixed."""
    out = tmp_path_factory.mktemp("reads")
    cache: dict = {}

    def get(name: str):
        if name not in cache:
            fq = out / f"{name}.fastq"
            cache[name] = (chn.simulate_with_sidecar(chn.load_model(name), strands, fq, seed=77), fq)
        return cache[name]
    return get


def _tol(n: float, p: float, rel: float = 0.04) -> float:
    return 5 * math.sqrt(max(p * (1 - p), 1e-12) / n) + rel * p + 1e-9


def test_expected_models_exist():
    for name in ("clean", "substitution-heavy", "insertion-heavy", "deletion-heavy", "mixed-mild", "mixed-harsh", "dropout-5",
                 "dropout-10", "dropout-20", "burst-loss", "uneven-coverage", "quality-degradation", "illumina-like",
                 "nanopore-like"):
        assert name in ALL


@pytest.mark.parametrize("name", ALL)
def test_model_files_are_explicit_and_labelled(name):
    m = chn.load_model(name)
    doc = json.loads(m.path.read_text())
    assert doc["classification"] == "SIMULATED" and doc["name"] == name and doc["version"]
    assert set(doc["channel"]) == set(chn._CH_FIELDS) and set(doc["loss"]) == set(chn._LOSS_FIELDS)
    assert m.sha256 == chn.sha256_file(m.path)


def test_model_missing_parameter_rejected(tmp_path):
    doc = json.loads((CHAN_DIR / "models" / "clean.json").read_text())
    del doc["channel"]["n_rate"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc))
    with pytest.raises(VNXConfigurationError):
        chn.load_model(bad)
    with pytest.raises(VNXConfigurationError):
        chn.load_model("no-such-model")


@pytest.mark.parametrize("name", ALL)
def test_realised_rates_match_configured(name, runs, strands):
    sc, fq = runs(name)
    m = chn.load_model(name)
    codes = chn.read_strand_codes(strands)
    exp = chn.expected_rates(m, codes)
    real = sc["realised_rates"]
    st = sc["composition"]["channel_stats"]
    reads0 = st["reads"] - st["duplicates"]
    pos = reads0 * L
    bases0 = pos + st["insertions"] - st["deletions"]
    for key, n in (("substitution_rate", pos), ("insertion_rate", pos), ("deletion_rate", pos), ("n_rate", bases0),
                   ("burst_rate", reads0), ("duplication_rate", reads0), ("reverse_complement_rate", st["reads"])):
        assert abs(real[key] - exp[key]) <= _tol(n, exp[key], 0.08 if m.channel["homopolymer_indel_multiplier"] != 1 else 0.04), \
            (name, key, real[key], exp[key])
    # strand loss: i.i.d. dropout (binomial) plus at most burst_count * burst_length extra strands
    lo = exp["strand_loss_fraction"]
    hi = lo + m.loss["burst_count"] * m.loss["burst_length"] / N_STRANDS
    assert lo - _tol(N_STRANDS, lo) <= real["strand_loss_fraction"] <= hi + _tol(N_STRANDS, hi)
    if m.loss["burst_count"]:
        assert real["strand_loss_fraction"] > lo        # bursts remove strands beyond the i.i.d. loss
    # coverage: mean reads per surviving strand
    c = m.channel
    w = (np.exp(-c["gc_bias_strength"] * ((((codes == 1) | (codes == 2)).mean(axis=1) - c["gc_bias_optimum"]) / 0.1) ** 2)
         if c["gc_bias_strength"] else np.ones(N_STRANDS))
    mu = c["coverage"] * w.mean() * (1 + c["duplication_rate"])
    var = {"fixed": 0.0, "poisson": mu, "negative-binomial": mu + mu * mu / c["coverage_dispersion"]}[c["coverage_model"]]
    if c["gc_bias_strength"] and c["coverage_model"] == "fixed":
        var = mu
    kept = N_STRANDS - sc["composition"]["loss"]["lost"]
    assert abs(real["reads_per_surviving_strand"] - mu) <= 5 * math.sqrt(var * 1.5 / kept) + 0.03 * mu + 1e-9, name


@pytest.mark.parametrize("name", ALL)
def test_realised_rates_independent_of_simulator_counters(name, runs, strands):
    """Checks that use only the written FASTQ (not the simulator's own event counts): mean read length and quality mix."""
    sc, fq = runs(name)
    m = chn.load_model(name)
    c = m.channel
    lines = fq.read_text().split("\n")
    seqs = [s for s in lines[1::4] if s]
    quals = [q for q in lines[3::4] if q]
    assert len(seqs) == sc["output"]["reads"] == len(quals)
    assert all(len(s) == len(q) for s, q in zip(seqs, quals))
    lens = np.array([len(s) for s in seqs])
    exp = chn.expected_rates(m, chn.read_strand_codes(strands))
    exp_shift = exp["insertion_rate"] - exp["deletion_rate"]       # includes homopolymer weighting and per-read bursts
    expected_len = L * (1 + exp_shift)
    se = lens.std() / math.sqrt(lens.size)
    assert abs(lens.mean() - expected_len) <= 6 * se + 0.05 * L * (exp["insertion_rate"] + exp["deletion_rate"]) + 0.02, name
    allq = np.frombuffer("".join(quals).encode(), dtype=np.uint8) - 33
    assert set(np.unique(allq)) <= {c["quality_correct"], c["quality_error"]}
    if c["quality_informative"] and c["quality_error"] != c["quality_correct"]:
        err_frac = (c["substitution_rate"] + c["insertion_rate"] + c["n_rate"]) / (1 + exp_shift)
        exp_low = c["quality_informative"] * err_frac
        obs_low = float((allq == c["quality_error"]).mean())
        assert abs(obs_low - exp_low) <= _tol(allq.size, exp_low, 0.15), (name, obs_low, exp_low)
    elif not c["quality_informative"]:
        assert (allq == c["quality_correct"]).all()


def test_clean_model_reads_are_exact(runs, strands):
    sc, fq = runs("clean")
    ref = [s for _, s in __import__("vnxdna.v4.constraints", fromlist=["iter_fasta"]).iter_fasta(strands)]
    seqs = [s for s in fq.read_text().split("\n")[1::4] if s]
    assert len(seqs) == 3 * N_STRANDS
    assert all(seqs[3 * i + j] == ref[i] for i in range(N_STRANDS) for j in range(3))


@pytest.mark.parametrize("name", ["mixed-harsh", "nanopore-like", "burst-loss"])
def test_deterministic_across_workers_and_repeats(name, strands, tmp_path):
    m = chn.load_model(name)
    digests, sidecars = [], []
    for workers in (1, 2, 3, 1):
        fq = tmp_path / f"w{workers}-{len(digests)}.fastq"
        sc = chn.simulate_with_sidecar(m, strands, fq, seed=5, workers=workers)
        digests.append(fq.read_bytes())
        sidecars.append(chn.deterministic_part(sc) | {"input": None, "output": {k: v for k, v in sc["output"].items() if k != "file"}})
    assert all(d == digests[0] for d in digests)
    assert all(s == sidecars[0] for s in sidecars)
    other = tmp_path / "other.fastq"
    chn.simulate_with_sidecar(m, strands, other, seed=6)
    assert other.read_bytes() != digests[0]


def test_sidecar_complete(runs, strands):
    sc, fq = runs("mixed-harsh")
    on_disk = json.loads(chn.sidecar_path(fq).read_text())
    for key in chn.REQUIRED_SIDECAR_KEYS:
        assert key in on_disk, key
    assert on_disk["classification"] == "SIMULATED" and "SIMULATED" in on_disk["statement"]
    assert on_disk["seed"] == 77
    m = chn.load_model("mixed-harsh")
    assert on_disk["model"]["name"] == "mixed-harsh" and on_disk["model"]["sha256"] == m.sha256
    assert on_disk["model"]["parameters"] == m.parameters()
    assert on_disk["coverage"]["model"] == m.channel["coverage_model"] and on_disk["coverage"]["mean"] == m.channel["coverage"]
    assert on_disk["quality_model"] == m.quality_model()
    assert on_disk["input"]["sha256"] == chn.sha256_file(strands)
    assert on_disk["output"]["sha256"] == chn.sha256_file(fq)
    assert re.fullmatch(r"[0-9a-f]{40}", on_disk["git"]["commit"]) and isinstance(on_disk["git"]["dirty"], bool)
    assert on_disk["decoder"] is None and on_disk["decode_result"] is None     # filled by evaluate.py only


def test_cli_roundtrip(strands, tmp_path):
    env = {**os.environ, "PYTHONPATH": str(CHAN_DIR.parents[2] / "src")}
    out = tmp_path / "cli.fastq"
    r = subprocess.run([sys.executable, str(CHAN_DIR / "simulate.py"), "--model", "dropout-10", "--strands", str(strands), "--seed", "3",
                        "--out", str(out)], capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr
    printed = json.loads(r.stdout)
    assert printed["classification"] == "SIMULATED"
    sc = json.loads(Path(str(out) + ".json").read_text())
    assert sc["output"]["sha256"] == chn.sha256_file(out) == printed["output_sha256"]
    # a model given as a path behaves like the named one
    out2 = tmp_path / "cli2.fastq"
    r2 = subprocess.run([sys.executable, str(CHAN_DIR / "simulate.py"), "--model", str(CHAN_DIR / "models" / "dropout-10.json"),
                         "--strands", str(strands), "--seed", "3", "--out", str(out2)], capture_output=True, text=True, env=env, timeout=300)
    assert r2.returncode == 0 and out2.read_bytes() == out.read_bytes()


def test_evaluate_end_to_end(tmp_path):
    out = tmp_path / "res.jsonl"
    rc = ev.main(["--models", "clean,mixed-mild", "--seeds", "2", "--size", "3000", "--out", str(out)])
    assert rc == 0
    recs = [json.loads(line) for line in out.read_text().splitlines()]
    assert all(r["classification"] == "SIMULATED" for r in recs)
    header, trials, summary = recs[0], [r for r in recs if r["record"] == "trial"], recs[-1]
    assert header["record"] == "header" and "SIMULATED" in header["statement"] and summary["record"] == "summary"
    assert len(trials) == 4
    for t in trials:
        assert t["status"] == "SUCCESS" and t["outcome"] == "exact" and t["sha_match"] and not t["false_success"]
        assert t["container_sha256_decoded"] == t["container_sha256_expected"]
        assert t["decoder"]["workers"] == 1 and t["model_sha256"] and "groups_failed" in t["recovery"]
    assert summary["false_success_total"] == 0
    for model, s in summary["per_model"].items():
        assert s["trials"] == 2 and s["exact"] == 2 and 0 < s["wilson95_low"] < s["wilson95_high"] <= 1


def test_summary_counts_false_success_and_wilson():
    rows = [{"model": "m", "outcome": "exact", "false_success": False, "decode_seconds": 1.0}] * 3 + \
           [{"model": "m", "outcome": "FALSE_SUCCESS", "false_success": True, "decode_seconds": 1.0},
            {"model": "m", "outcome": "failed-detected", "false_success": False, "decode_seconds": 2.0}]
    s = ev.summarize(rows)["m"]
    assert s["false_success"] == 1 and s["exact"] == 3 and s["failed_detected"] == 1
    assert s["wilson95_low"] < 0.6 < s["wilson95_high"]
