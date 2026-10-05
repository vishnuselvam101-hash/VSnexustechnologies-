"""Error models and stage mechanisms (SIMULATED): each one alone, combined, and realised against configured parameters.

Tolerances are 5-sigma binomial/Poisson intervals plus a small relative allowance, as in ``tests/v6/channel``.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core import schema
from vnxdna.simulation import engine
from vnxdna.simulation import errormodels as em
from vnxdna.simulation import model as cm
from vnxdna.simulation import registry

N, L = 2000, 100


def tol(n: float, p: float, rel: float = 0.04) -> float:
    return 5 * math.sqrt(max(p * (1 - p), 1e-12) / max(n, 1)) + rel * p + 1e-9


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def design():
    return np.random.default_rng(4040).integers(0, 4, (N, L)).astype(np.uint8)


@pytest.fixture(scope="module")
def strands(design, tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("mech") / "in.fasta"
    with open(p, "w") as f:
        for i, row in enumerate(design):
            f.write(f">s{i}\n{''.join('ACGT'[b] for b in row)}\n")
    return p


def model(**paths) -> cm.ChannelModel:
    """The clean identity model with dotted-path changes (``seq__coverage__mean=...`` style keys use '.')."""
    base, _ = cm.from_doc({"schema": cm.SCHEMA_V1, "name": "mech", "version": "1.0.0"})
    return base.with_parameters({k.replace("__", "."): v for k, v in paths.items()}) if paths else base


def run(m, strands, tmp_path, seed=3, **kw):
    out = tmp_path / f"r{seed}.fastq"
    body = engine.simulate_file(strands, out, m, seed, overwrite=True, **kw)
    lines = out.read_text().split("\n")
    return body, [s for s in lines[1::4]], [q for q in lines[3::4]], out


def codes_of(s: str) -> np.ndarray:
    return np.frombuffer(s.encode(), dtype=np.uint8)


# ================================================================================================================ standalone
def test_standalone_substitution_with_matrix_and_from_multipliers(design):
    pool = em.SequencePool.from_matrix(design)
    matrix = ((0, 0, 1, 0), (0, 0, 0, 1), (1, 0, 0, 0), (0, 1, 0, 0))      # transitions only
    sub = em.SubstitutionModel(0.05, matrix=matrix, from_multipliers=(1.0, 2.0, 0.0, 1.0))
    out = sub.apply(pool, np.random.default_rng(1))
    assert (out.lengths == L).all()
    diff = out.codes != design
    frm, to = design[diff], out.codes[diff]
    assert set(zip(frm.tolist(), to.tolist())) <= {(0, 2), (1, 3), (3, 1)}     # G never mutates (multiplier 0)
    for b, mult in ((0, 1.0), (1, 2.0), (3, 1.0)):
        n_b = int((design == b).sum())
        p = 0.05 * mult
        assert abs(int((frm == b).sum()) / n_b - p) <= tol(n_b, p)
    assert out.stats["substitutions"] == int(diff.sum())


def test_standalone_insertion_base_weights_and_deletion_runs(design):
    pool = em.SequencePool.from_matrix(design)
    out = em.InsertionModel(0.02, base_weights=(1.0, 0.0, 0.0, 0.0)).apply(pool, np.random.default_rng(2))
    added = int(out.lengths.sum() - pool.lengths.sum())
    assert added == out.stats["insertions"] and abs(added / design.size - 0.02) <= tol(design.size, 0.02)
    flat, _ = out.flat()
    assert int((flat == 0).sum()) - int((design == 0).sum()) == added            # every inserted base is an A
    dele = em.DeletionModel(0.004, "geometric", 3.0)
    out = dele.apply(pool, np.random.default_rng(3))
    deleted = int(pool.lengths.sum() - out.lengths.sum())
    events = 0.004 * design.size
    assert abs(deleted / events - 3.0) <= 0.15 * 3.0                              # mean run length ~3 (edge-truncated)
    single = em.DeletionModel(0.004).apply(pool, np.random.default_rng(3))
    assert abs((pool.lengths.sum() - single.lengths.sum()) / design.size - 0.004) <= tol(design.size, 0.004)


def test_composite_is_joint_and_reproducible(design):
    pool = em.SequencePool.from_matrix(design)
    comp = em.CompositeModel((em.SubstitutionModel(0.01), em.InsertionModel(0.01), em.DeletionModel(0.01),
                              em.QualityModel(30, 10, 0.0, 2.0, 0.0)))
    a = comp.apply(pool, np.random.default_rng(5))
    b = comp.apply(pool, np.random.default_rng(5))
    assert a.strings() == b.strings() and (a.quals == b.quals).all()
    joint = em.apply_per_base(pool, np.random.default_rng(5), em.SubstitutionModel(0.01), em.InsertionModel(0.01),
                              em.DeletionModel(0.01))
    assert a.strings() == joint.strings()
    assert comp.describe()["kind"] == "composite" and len(comp.parameters()["models"]) == 4


def test_dropout_and_coverage_models():
    rng = np.random.default_rng(6)
    pool = em.SequencePool.from_strings(["ACGT"] * 20000)
    kept = em.DropoutModel(0.1).apply(pool, rng)
    assert abs(kept.stats["dropped"] / 20000 - 0.1) <= tol(20000, 0.1)
    mu = np.full(20000, 8.0)
    for cov, var in ((em.CoverageModel("poisson", 8.0), 8.0),
                     (em.CoverageModel("negative-binomial", 8.0, dispersion=2.0), 8.0 + 64 / 2.0),
                     (em.CoverageModel("lognormal", 8.0, sigma=0.58), 8.0 + 64 * (math.exp(0.58 ** 2) - 1))):
        r = cov.sample(mu, rng, weighted=False)
        assert abs(r.mean() - 8.0) <= 5 * math.sqrt(var / r.size), cov.model
        assert abs(r.var() / var - 1) <= 0.12, (cov.model, r.var(), var)
    assert (em.CoverageModel("fixed", 3).sample(mu, rng, weighted=False) == 3).all()
    out = em.CoverageModel("fixed", 3).apply(pool.take(np.arange(10)), rng)
    assert out.lengths.size == 30


def test_quality_variation():
    q = em.QualityModel(30, 10, 0.0, sd=3.0, position_slope=0.1)
    lengths = np.full(2000, 100)
    base = q.base_scores(np.zeros(200000, dtype=bool), np.random.default_rng(7))
    out = q.vary(base, lengths, np.random.default_rng(8)).astype(float).reshape(2000, 100)
    assert abs(out[:, 0].mean() - 30) < 0.5 and abs(out[:, 99].mean() - (30 - 9.9)) < 0.5
    assert abs((out - (30 - 0.1 * np.arange(100))).std() - 3.0) < 0.15


# ================================================================================================================ stages
def test_synthesis_errors_are_shared_by_all_reads_of_a_molecule(strands, design, tmp_path):
    m = model(synthesis__substitution={"rate": 0.02, "matrix": None, "from_multipliers": None},
              sequencing__coverage={"model": "fixed", "mean": 4, "dispersion": 5.0, "sigma": 0.0})
    body, seqs, _, _ = run(m, strands, tmp_path)
    assert len(seqs) == 4 * N and body["synthesis_substitutions"] > 0 and body["substitutions"] == 0
    by_strand = [seqs[4 * i:4 * i + 4] for i in range(N)]
    assert all(len(set(g)) == 1 for g in by_strand)                     # one molecule per strand: identical reads
    diffs = sum(int((codes_of(g[0]) != np.frombuffer(b"ACGT", np.uint8)[design[i]]).sum()) for i, g in enumerate(by_strand))
    assert abs(diffs / design.size - 0.02) <= tol(design.size, 0.02)
    # several molecule variants: reads of a strand come from at most M distinct molecules
    m4 = m.with_parameters({"synthesis.molecules_per_strand": 3})
    _, seqs4, _, _ = run(m4, strands, tmp_path)
    groups = [set(seqs4[4 * i:4 * i + 4]) for i in range(N)]
    assert max(len(g) for g in groups) <= 3 and sum(len(g) > 1 for g in groups) > N // 4


def test_synthesis_truncation_keeps_a_3prime_suffix(strands, design, tmp_path):
    m = model(synthesis__truncation={"rate": 0.3, "min_fraction": 0.5})
    body, seqs, _, _ = run(m, strands, tmp_path)
    text = ["".join("ACGT"[b] for b in row) for row in design]
    short = [(s, t) for s, t in zip(seqs, text) if len(s) < L]
    assert abs(len(short) / N - 0.3) <= tol(N, 0.3) and body["truncated_molecules"] == len(short)
    assert all(t.endswith(s) and len(s) >= L // 2 for s, t in short)


def test_storage_damage_breakage_retention_contamination(strands, design, tmp_path):
    deam = [[0, 0, 1, 0], [0, 0, 0, 1], [1, 0, 0, 0], [0, 1, 0, 0]]
    m = model(storage__damage={"rate": 0.01, "matrix": deam, "from_multipliers": [0.0, 1.0, 1.0, 0.0]},
              sequencing__coverage={"model": "fixed", "mean": 1, "dispersion": 5.0, "sigma": 0.0})
    body, seqs, _, _ = run(m, strands, tmp_path)
    pairs = set()
    for s, row in zip(seqs, design):
        d = codes_of(s) != np.frombuffer(b"ACGT", np.uint8)[row]
        pairs |= set(zip(row[d].tolist(), codes_of(s)[d].tolist()))
    assert pairs <= {(1, ord("T")), (2, ord("A"))}                       # C>T and G>A only
    n_cg = int(((design == 1) | (design == 2)).sum())
    assert abs(body["damaged_bases"] / n_cg - 0.01) <= tol(n_cg, 0.01)
    b = 0.002
    body, _, _, _ = run(model(storage__breakage_rate=b), strands, tmp_path)
    p_broken = 1 - (1 - b) ** L
    assert abs(body["broken_molecules"] / N - p_broken) <= tol(N, p_broken)
    assert body["broken_strands"] == body["broken_molecules"] and body["reads"] == N - body["broken_strands"]
    cov = {"model": "poisson", "mean": 10.0, "dispersion": 5.0, "sigma": 0.0}
    body, _, _, _ = run(model(storage__retention=0.5, sequencing__coverage=cov), strands, tmp_path)
    assert abs(body["reads"] / N - 5.0) <= 5 * math.sqrt(5.0 / N)
    body, seqs, _, _ = run(model(storage__contamination_rate=0.1, sequencing__coverage=cov), strands, tmp_path)
    frac = body["contaminant_reads"] / body["reads"]
    assert abs(frac - 0.1) <= tol(body["reads"], 0.1) and all(len(s) == L for s in seqs)


def test_amplification_bias_pcr_errors_and_duplicates(strands, tmp_path):
    cov = {"model": "poisson", "mean": 10.0, "dispersion": 5.0, "sigma": 0.0}
    plain, _, _, _ = run(model(sequencing__coverage=cov), strands, tmp_path)
    m = model(sequencing__coverage=cov, amplification__efficiency_sigma=0.8)
    counts = []
    for seed in range(3):
        _, seqs, _, _ = run(m, strands, tmp_path, seed=seed)
        counts.append(len(seqs))
    assert abs(np.mean(counts) / N - 10.0) < 0.6                    # mean preserved (weights have mean 1)
    m = model(amplification__cycles=10, amplification__substitution_per_cycle={"rate": 0.001, "matrix": None,
                                                                              "from_multipliers": None},
              sequencing__coverage=cov)
    body, _, _, _ = run(m, strands, tmp_path)
    assert abs(body["pcr_substitutions"] / body["bases"] - 0.01) <= tol(body["bases"], 0.01)
    body, _, _, _ = run(model(amplification__duplicate_rate=0.2, sequencing__coverage=cov), strands, tmp_path)
    reads0 = body["reads"] - body["amplification_duplicates"]
    assert abs(body["amplification_duplicates"] / reads0 - 0.2) <= tol(reads0, 0.2)
    assert body["duplicates"] == 0


def test_lognormal_and_uneven_coverage(strands, tmp_path):
    m = model(sequencing__coverage={"model": "lognormal", "mean": 20.0, "dispersion": 5.0, "sigma": 0.58})
    _, seqs, _, out = run(m, strands, tmp_path)
    names = [ln[1:] for ln in out.read_text().split("\n")[0::4] if ln]
    assert len(names) == len(seqs)
    # reads are written strand by strand; count reads per strand by matching the design rows
    # (no errors in this model, so each read equals its strand)
    from collections import Counter
    c = Counter(seqs)
    per = np.array(list(c.values()) + [0] * (N - len(c)))
    var = 20 + 400 * (math.exp(0.58 ** 2) - 1)
    assert abs(per.mean() - 20) <= 5 * math.sqrt(var / N) and abs(per.var() / var - 1) <= 0.2


def test_sequencing_matrix_runs_profile_read_length_missing_quality(strands, design, tmp_path):
    to_g = [[0, 0, 1, 0], [0, 0, 1, 0], [1, 0, 0, 0], [0, 0, 1, 0]]          # everything goes to G, G goes to A
    profile = {"basis": "absolute", "substitution": [0.0] * 50 + [2.0]}
    m = model(sequencing__substitution={"rate": 0.02, "matrix": to_g, "from_multipliers": None},
              sequencing__position_profile=profile)
    body, seqs, _, _ = run(m, strands, tmp_path)
    ref = np.frombuffer(b"ACGT", np.uint8)
    first, second, wrong = 0, 0, 0
    for s, row in zip(seqs, design):
        d = codes_of(s) != ref[row]
        first += int(d[:50].sum())
        second += int(d[50:].sum())
        wrong += int(((codes_of(s)[d] != ord("G")) & (row[d] != 2)).sum()) + int(((codes_of(s)[d] != ord("A")) &
                                                                                  (row[d] == 2)).sum())
    assert first == 0 and wrong == 0
    n2 = N * 50
    assert abs(second / n2 - 0.04) <= tol(n2, 0.04)
    m = model(sequencing__deletion={"rate": 0.003, "run_length": {"distribution": "geometric", "mean": 2.6}})
    body, seqs, _, _ = run(m, strands, tmp_path)
    deleted = N * L - sum(len(s) for s in seqs)
    assert abs(deleted / (0.003 * N * L) - 2.6) <= 0.2 * 2.6 and body["deletions"] == deleted
    m = model(sequencing__read_length={"max_length": 80, "truncation_rate": 0.25, "min_fraction": 0.5},
              sequencing__missing_read_rate=0.1,
              sequencing__quality={"correct": 38, "error": 10, "informative": 0.0, "sd": 0.0, "position_slope": 0.2})
    body, seqs, quals, _ = run(m, strands, tmp_path)
    assert max(len(s) for s in seqs) == 80 and min(len(s) for s in seqs) >= 40
    assert abs(body["missing_reads"] / N - 0.1) <= tol(N, 0.1) and len(seqs) == N - body["missing_reads"]
    short = sum(len(s) < 80 for s in seqs) / len(seqs)
    assert abs(short - 0.25) <= tol(len(seqs), 0.25)
    q0 = [ord(q[0]) - 33 for q in quals]
    q79 = [ord(q[79]) - 33 for q in quals if len(q) == 80]
    assert set(q0) == {38} and set(q79) == {round(38 - 0.2 * 79)}


def test_stage_streams_do_not_rerandomise_other_mechanisms(strands, tmp_path):
    """Common random numbers: switching on one extension leaves the V4 draws, and so the other events, unchanged."""
    base = registry.load_model("mixed-mild")
    ref, seqs_ref, _, _ = run(base, strands, tmp_path)
    con, seqs_con, _, _ = run(base.with_parameters({"storage.contamination_rate": 0.05}), strands, tmp_path)
    assert len(seqs_con) - len(seqs_ref) == con["contaminant_reads"] > 0
    it = iter(seqs_con)
    assert all(any(s == t for t in it) for s in seqs_ref)            # the reference reads, in order, plus contaminants
    mat = base.with_parameters({"sequencing.substitution.matrix": [[0, 1, 0, 0], [1, 0, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]]})
    body, seqs_mat, _, _ = run(mat, strands, tmp_path)
    assert [len(s) for s in seqs_mat] == [len(s) for s in seqs_ref]       # same indels, reads, duplicates, N
    for k in engine.V4_STATS:
        assert body[k] == ref[k], k


EVERYTHING = {
    "synthesis.substitution.rate": 0.002, "synthesis.deletion.rate": 0.002,
    "synthesis.deletion.run_length": {"distribution": "geometric", "mean": 2.6}, "synthesis.insertion.rate": 0.0003,
    "synthesis.truncation.rate": 0.05, "synthesis.yield_sigma": 0.5, "synthesis.molecules_per_strand": 4,
    "synthesis.position_profile": {"basis": "relative", "deletion": [3.0, 1.0, 1.0, 1.0]},
    "storage.retention": 0.8, "storage.damage.rate": 0.001, "storage.breakage_rate": 0.0005,
    "storage.contamination_rate": 0.01, "storage.strand_loss": {"rate": 0.05, "burst_count": 1, "burst_length": 30},
    "amplification.efficiency_sigma": 0.3, "amplification.cycles": 20,
    "amplification.substitution_per_cycle.rate": 1e-4, "amplification.duplicate_rate": 0.05,
    "sequencing.coverage": {"model": "lognormal", "mean": 6.0, "dispersion": 5.0, "sigma": 0.58},
    "sequencing.substitution.matrix": [[0, 0.2, 0.6, 0.2], [0.3, 0, 0.3, 0.4], [0.5, 0.25, 0, 0.25], [0.2, 0.6, 0.2, 0]],
    "sequencing.insertion.base_weights": [0.4, 0.1, 0.1, 0.4], "sequencing.insertion.rate": 0.001,
    "sequencing.deletion.run_length": {"distribution": "geometric", "mean": 2.0}, "sequencing.deletion.rate": 0.001,
    "sequencing.substitution.rate": 0.004, "sequencing.quality.sd": 2.0, "sequencing.quality.position_slope": 0.05,
    "sequencing.read_length": {"max_length": 90, "truncation_rate": 0.1, "min_fraction": 0.5},
    "sequencing.missing_read_rate": 0.02, "sequencing.homopolymer.indel_multiplier": 2.0,
    "sequencing.bursts": {"rate": 0.01, "max_length": 5}, "sequencing.n_rate": 0.001,
    "sequencing.reverse_complement_rate": 0.5, "sequencing.duplicate_rate": 0.02, "sequencing.shuffle_window": 500}


def test_combined_model_is_deterministic_across_workers_and_seed_sensitive(strands, tmp_path):
    m = registry.load_model("illumina-like").with_parameters(EVERYTHING, label="all mechanisms")
    a = engine.simulate_file(strands, tmp_path / "a.fastq", m, 11)
    b = engine.simulate_file(strands, tmp_path / "b.fastq", m, 11, workers=3)
    c = engine.simulate_file(strands, tmp_path / "c.fastq", m, 12)
    assert sha(tmp_path / "a.fastq") == sha(tmp_path / "b.fastq") != sha(tmp_path / "c.fastq")
    assert a["metadata"]["stats"] == b["metadata"]["stats"]
    for k in ("synthesis_deletions", "truncated_molecules", "damaged_bases", "broken_molecules", "pcr_substitutions",
              "amplification_duplicates", "truncated_reads", "missing_reads", "contaminant_reads", "storage_lost",
              "bursts", "n_calls", "duplicates", "reverse_complement"):
        assert a[k] > 0, k
    assert c["reads"] != a["reads"] or c["metadata"]["output"]["sha256"] != a["metadata"]["output"]["sha256"]


def test_metadata_is_complete_and_labelled(strands, tmp_path):
    m = registry.load_model("nanopore-like")
    body = engine.simulate_file(strands, tmp_path / "r.fasta", m, 5, overrides={"sequencing.coverage.mean": 15.0})
    meta = body["metadata"]
    schema.validate(meta, "vnx.simulation-metadata/1")
    assert meta["data_source"] == "SIMULATED" and meta["evidence_class"] == "SIMULATED" and "No DNA" in meta["statement"]
    assert meta["seed"] == 5 and meta["model"]["name"] == "nanopore-like" and meta["model"]["version"] == "1.0.0"
    assert meta["model"]["sha256"] == m.sha256 and meta["parameters"] == m.parameters()
    assert meta["versions"]["simulator_version"] == engine.SIMULATOR_VERSION and meta["versions"]["model_schema"] == cm.SCHEMA_V1
    assert meta["input"]["sha256"] == sha(strands) and meta["input"]["strands"] == N and meta["input"]["strand_length"] == L
    assert meta["output"]["sha256"] == sha(tmp_path / "r.fasta") and meta["output"]["format"] == "fasta"
    assert meta["overrides"] == {"sequencing.coverage.mean": 15.0}
    json.dumps(meta)                                                      # machine-readable as is


def test_seed_must_be_valid(strands, tmp_path):
    from vnxdna.core.errors import VNXConfigurationError
    for bad in (-1, 2 ** 63, True, 1.5):
        with pytest.raises(VNXConfigurationError):
            engine.simulate_file(strands, tmp_path / "x.fastq", model(), bad)
    assert not (tmp_path / "x.fastq").exists()
