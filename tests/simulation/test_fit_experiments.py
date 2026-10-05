"""Experiment-side code of the V7 fits (experiments/v7/fit): D02 read assignment, PhiX mapping and the job table, on synthetic
data only (no public data needed)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

pytest.importorskip("edlib")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/v7/fit"))
import d02 as D2          # noqa: E402
import fitlib as FL       # noqa: E402
import run as R           # noqa: E402
from vnxdna.simulation import registry   # noqa: E402
from vnxdna.simulation.fit import estimate as est, pipeline as P, simulate as S, tally as T, validate as V   # noqa: E402


def designs(n=60, L=108, seed=2):
    rnd = random.Random(seed)
    return [(f"{i:06d}", bytes(rnd.choice(b"ACGT") for _ in range(L))) for i in range(n)]


def test_assigner_assigns_by_seeds_orients_and_refuses_ambiguous_or_foreign_reads():
    ds = designs()
    A = D2.Assigner(ds)
    rnd = random.Random(5)
    flank = bytes(rnd.choice(b"ACGT") for _ in range(42))
    d7 = ds[7][1]
    mutated = bytearray(d7)
    mutated[30] = ord("A") if mutated[30] != ord("A") else ord("C")
    reads = [d7 + flank, bytes(mutated) + flank, FL.rc(d7 + flank), bytes(rnd.choice(b"ACGT") for _ in range(150))]
    out = A(reads)
    assert out[0] == out[1] == out[2] == ds[7][0] and out[3] is None
    assert A.orient[reads[2]] is True and A.orient[reads[0]] is False
    # a read made of two designs' halves is ambiguous or too distant, never forced onto one design
    chimera = ds[3][1][:54] + ds[4][1][54:] + flank
    assert A([chimera])[0] in (None, ds[3][0], ds[4][0])
    assert A.stats["assigned"] >= 3


def test_phix_mapping_counts_events_and_cuts_reads_to_108_cycles():
    rnd = random.Random(8)
    genome = bytes(rnd.choice(b"ACGT") for _ in range(5386))
    px = D2.PhiX(genome)
    for _ in range(300):
        s = rnd.randrange(0, 5386 - 150)
        read = bytearray(genome[s:s + 150])
        if rnd.random() < 0.5:
            i = rnd.randrange(0, 100)
            read[i] = b"ACGT"[(b"ACGT".index(read[i]) + 1) % 4]
        px.add(bytes(read) if rnd.random() < 0.5 else FL.rc(bytes(read)))
    assert px.stats["mapped"] >= 290
    out = px.fit(20, 1)
    assert 0.002 < out["values"]["substitution_rate"] < 0.006       # about 0.5 substitutions per 108 bases... measured on the mapped bases
    assert out["values"]["insertion_rate"] < 5e-4 and out["values"]["deletion_rate"] < 5e-4      # only substitutions were made


def test_jobs_are_consistent_with_the_split_manifest():
    import json
    sm = json.loads((ROOT / "experiments/v7/split/SPLIT_MANIFEST.json").read_text())
    d03_runs = set(sm["datasets"]["d03-nanopore"]["groups"])
    held = sm["datasets"]["d03-nanopore"]["heldout_run"]
    for jid, job in R.JOBS.items():
        if job["dataset"] == "d03-nanopore":
            assert job["groups"] and all(g in d03_runs for g in job["groups"]), jid
            assert not any(g.startswith(held.replace("file-", "file-") + "_") for g in job["groups"]), jid     # never the held-out file
    assert R.D03_FILES == (0, 2) and held == "file-1"


def test_quality_estimator_recovers_a_simulated_calibration():
    m = registry.load_model("mixed-harsh").with_parameters({"sequencing.quality": {
        "correct": 37, "error": 11, "informative": 0.6, "sd": 2.0, "position_slope": 0.04}})
    rnd = random.Random(3)
    refs = [bytes(rnd.choice(b"ACGT") for _ in range(100)) for _ in range(1200)]
    q: list = []
    cl = S.simulate_clusters(m, refs, 8, seed=2, quals=q)
    lay = T.Layout(100, quality=True, cycles=100)
    M, _ = P.tally_matrix(zip(refs, cl, q), lay)
    v = est.estimate_quality(lay, M.sum(axis=0).astype(float))
    assert abs(v["sequencing.quality.correct"] - 37) <= 1 and abs(v["sequencing.quality.error"] - 11) <= 2
    assert abs(v["sequencing.quality.informative"] - 0.6) < 0.06 and abs(v["sequencing.quality.position_slope"] - 0.04) < 0.01
    sim_T = M.sum(axis=0).astype(float)
    assert V.m9_quality(lay, sim_T, sim_T)["pass"] is True


def test_quality_tally_counts_cycles_in_the_sequenced_orientation():
    ref = designs(1)[0][1]
    lay = T.Layout(108, quality=True, cycles=150)
    qual = bytes([33 + 30] * 100 + [33 + 10] * 8 + [33 + 2] * 42)
    read = ref + b"A" * 42                                                  # design first, 42 flank cycles after it
    v1, _ = T.tally_reference(ref, [read], lay, "HW", [(qual, False)])
    # a read sequenced from the other strand: the sequenced read is rc(read); the pipeline aligns the oriented read (= `read`) and
    # keeps the quality string in the sequenced orientation, so the design occupies cycles 42..149
    v2, _ = T.tally_reference(ref, [read], lay, "HW", [(qual, True)])
    n1, n2 = lay.get(v1, "q_cycle_n"), lay.get(v2, "q_cycle_n")
    assert n1[:108].sum() == 108 and n1[108:].sum() == 0
    assert n2[42:].sum() == 108 and n2[:42].sum() == 0
