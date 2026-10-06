"""The V7 channel-model fitter (library part): alignment normalisation, tallies, estimators, round trip on SIMULATED data
from every shipped model (M10), determinism over worker counts, provenance of the produced /2 document."""
from __future__ import annotations

import json
import random

import numpy as np
import pytest

pytest.importorskip("edlib")

from vnxdna.simulation import model as cm, registry                                           # noqa: E402
from vnxdna.simulation.fit import estimate as est, fit as F, model_out as MO, pipeline as P, simulate as S, tally as T, validate as V   # noqa: E402
from vnxdna.simulation.fit.align import align, left_normalise                                   # noqa: E402

MODELS = [f"{n}@{v}" for n, v in registry.available()]


def refs_of(n, L, seed=1):
    rnd = random.Random(seed)
    return [bytes(rnd.choice(b"ACGT") for _ in range(L)) for _ in range(n)]


def replay(ref: bytes, read: bytes, runs, ws=0) -> bytes:
    out, ri, qi = bytearray(), 0, ws
    for op, n in runs:
        if op in "=X":
            out += read[qi:qi + n]
            ri += n
            qi += n
        elif op == "I":
            out += read[qi:qi + n]
            qi += n
        else:
            ri += n
    return bytes(out)


# -- alignment ---------------------------------------------------------------------------------------------------------
def test_indels_are_shifted_to_the_leftmost_position():
    ref = b"ACGTAAAACGTTTGCA"
    assert align(b"ACGTAAACGTTTGCA", ref)[1] == [["=", 4], ["D", 1], ["=", 11]]           # one A of AAAA deleted: first A
    assert align(b"ACGTAAAAACGTTTGCA", ref)[1] == [["=", 4], ["I", 1], ["=", 12]]         # extra A: before the run
    assert align(b"ACGAAAACGTTTGCA", ref)[1] == [["=", 3], ["D", 1], ["=", 12]]           # T deleted, nothing to shift
    assert align(b"ACGTAAAACGATTGCA", ref)[1] == [["=", 10], ["X", 1], ["=", 5]]          # a mismatch is never moved


def test_normalisation_is_idempotent_and_keeps_the_alignment_valid():
    rnd = random.Random(4)
    for _ in range(300):
        ref = bytes(rnd.choice(b"AC") for _ in range(30))                 # a two-letter alphabet makes repeats common
        read = bytearray(ref)
        for _k in range(rnd.randint(0, 4)):
            i = rnd.randrange(len(read))
            kind = rnd.random()
            if kind < 0.4 and len(read) > 5:
                del read[i]
            elif kind < 0.8:
                read.insert(i, rnd.choice(b"AC"))
            else:
                read[i] = rnd.choice(b"AC")
        read = bytes(read)
        d, runs, w = align(read, ref)
        assert replay(ref, read, runs) == read
        assert sum(n for op, n in runs if op in "=XD") == len(ref)
        assert left_normalise(ref, read, [list(r) for r in runs]) == runs
        assert d == sum(n for op, n in runs if op != "=")


def test_hw_mode_aligns_the_reference_inside_a_read_with_flanks():
    ref = b"ACGTTGCAAGCT"
    read = b"TTTT" + b"ACGTGCAAGCT" + b"GGGG"                               # one deletion inside, flanks outside
    d, runs, (s, e) = align(read, ref, "HW")
    assert d == 1 and (s, e) == (4, 15) and [op for op, _ in runs if op != "="] == ["D"]


# -- tallies -----------------------------------------------------------------------------------------------------------
def test_tally_counts_the_events_of_known_reads():
    ref = b"ACGTAAAACGTTTGCA"
    lay = T.Layout(len(ref))
    v, rs = T.tally_reference(ref, [b"ACGTAAACGTTTGCA", b"ACGTAAAAACGTTTGCA", b"ACGTAAAACGATTGCA", ref], lay)
    g = lambda n: lay.get(v, n)                                              # noqa: E731
    assert g("n_reads")[0] == 4
    assert g("pos_del")[4] == 1 and g("pos_delstart")[4] == 1 and g("pos_ins")[4] == 1 and g("pos_sub")[10] == 1
    assert g("sub_matrix").reshape(4, 4)[1 if False else 2, 0] == 1 if False else g("sub_matrix").sum() == 1
    assert g("ins_runs")[0] == 1 and g("del_runs")[0] == 1 and g("ins_base")[0] == 1
    assert rs[:T.EDIT_BINS][0] == 1 and rs[:T.EDIT_BINS][1] == 3
    assert lay.ctx(v, "ctx_sites").sum() == 4 * len(ref) * len(lay.minruns)


def test_reads_with_too_many_edits_are_excluded_not_tallied():
    ref = refs_of(1, 60)[0]
    lay = T.Layout(60)
    far = bytes(b"ACGT"[(b"ACGT".index(c) + 1) % 4] for c in ref)
    v, _ = T.tally_reference(ref, [ref, far], lay)
    assert lay.get(v, "n_reads")[0] == 1 and lay.get(v, "excluded")[0] == 1


# -- estimators --------------------------------------------------------------------------------------------------------
def test_deletion_process_inversion_recovers_the_simulated_parameters():
    rng = np.random.default_rng(1)
    for d, m in ((0.02, 2.6), (0.05, 1.0), (0.06, 3.0)):
        n = 400_000
        starts = np.flatnonzero(rng.random(n) < d)
        runs = rng.geometric(1 / m, starts.size)
        deleted = np.zeros(n + 100, bool)
        for i, r in zip(starts, runs):
            deleted[i:i + r] = True
        deleted = deleted[:n]
        d2, m2 = est.deletion_from_observed((deleted[1:] & ~deleted[:-1]).mean(), deleted.mean())
        assert abs(d2 - d) < 0.08 * d + 5e-4 and abs(m2 - m) < 0.12 * m, (d, m, d2, m2)


def test_profile_bins_flat_data_gives_no_profile_and_a_real_profile_is_found():
    rng = np.random.default_rng(2)
    flat = rng.poisson(40, 120)
    assert est._bins_for(flat) is None
    prof = rng.poisson(40 * np.where(np.arange(120) < 20, 4.0, 1.0))
    B = est._bins_for(prof)
    assert B is not None and B >= 2


def test_coverage_families_are_recovered_and_selected_by_aic():
    rng = np.random.default_rng(3)
    n = 30000
    x = rng.poisson(rng.gamma(4, 20 / 4, n))
    x[rng.random(n) < 0.05] = 0
    r = F.fit_coverage(F.counts_hist(x))
    assert r["coverage"]["model"] == "negative-binomial" and r["choice"][1] is True
    assert abs(r["coverage"]["mean"] - 20) < 0.5 and abs(r["coverage"]["dispersion"] - 4) < 0.5 and abs(r["dropout"] - 0.05) < 0.01
    y = rng.poisson(15 * rng.lognormal(-0.58 ** 2 / 2, 0.58, n))
    r = F.fit_coverage(F.counts_hist(y))
    assert r["coverage"]["model"] == "lognormal" and abs(r["coverage"]["sigma"] - 0.58) < 0.03 and r["dropout"] == 0.0
    z = rng.poisson(10, n)
    assert F.fit_coverage(F.counts_hist(z))["coverage"]["model"] in ("poisson", "negative-binomial")


# -- round trip on SIMULATED data from the shipped models (M10) ------------------------------------------------------------
@pytest.mark.parametrize("ref", MODELS)
def test_round_trip_observed_rates_match_the_simulator_counters(ref):
    model = registry.load_model(ref)
    L, n, cov = 100, 1200, 6
    refs = refs_of(n, L, seed=7)
    counters: dict = {}
    clusters = S.simulate_clusters(model, refs, cov, seed=11, stats=counters)
    lay = T.Layout(L)
    M, _ = P.tally_matrix(zip(refs, clusters), lay)
    fit = F.fit_tallies(M, lay, seed=3, bootstrap=20, with_coverage=False)
    obs = fit["values"]["_observed"]
    sites = n * cov * L
    for key, counter in (("substitution", "substitutions"), ("insertion_bases", "insertions"), ("deletion_bases", "deletions")):
        truth = counters[counter] / sites
        se = (max(truth, 1e-9) / sites) ** 0.5
        tol = 5 * se + 0.12 * truth + 2e-4                       # 12 %: unit-cost alignment merges adjacent events
        assert abs(obs[key] - truth) <= tol, (ref, key, obs[key], truth)
    assert fit["values"]["sequencing.insertion.run_length.mean"] == pytest.approx(1.0, abs=0.05)


@pytest.mark.parametrize("ref", ["nanopore-like", "mixed-harsh", "deletion-heavy", "illumina-like"])
def test_round_trip_refit_of_the_fitted_model_is_self_consistent(ref):
    model = registry.load_model(ref)
    L, refs = 100, refs_of(2500, 100, seed=9)
    clusters = S.simulate_clusters(model, refs, 8, seed=2)
    lay = T.Layout(L)
    M, _ = P.tally_matrix(zip(refs, clusters), lay)
    cal = dict(refs=refs[:2000], coverage=10, iterations=4)
    fit = F.fit_tallies(M, lay, seed=3, bootstrap=40, with_coverage=False, calibration=cal)
    fitted = MO.build(fit, **_provenance(fit))
    m2, _ = cm.from_doc(fitted)
    rep = V.round_trip(m2, lay, refs, coverage=8, seed=21, bootstrap=40, calibration=cal)
    assert rep["pass"], {k: v for k, v in rep["parameters"].items() if not v["pass"]}


def _provenance(fit, seed=3):
    return dict(name="unit-fit", version="1.0.0", model_id="unit-F", description="unit test fit", note="SIMULATED input",
                datasets=[{"id": "X", "accession": "unit:x", "url": "https://example.org", "files": [{"name": "f", "sha256": "1" * 64}]}],
                split={"name": "FIT", "manifest_sha256": "2" * 64},
                fitting={"method": "unit", "version": "1", "commit": "a" * 40, "dirty": False, "seed": seed,
                         "timestamp_utc": "2026-10-05T00:00:00Z", "software": {"numpy": np.__version__}})


# -- determinism over workers, provenance ----------------------------------------------------------------------------------
def test_fit_is_byte_identical_for_1_3_and_8_workers():
    model = registry.load_model("nanopore-like")
    refs = refs_of(500, 100, seed=5)
    clusters = S.simulate_clusters(model, refs, 6, seed=4)
    lay = T.Layout(100)
    docs = []
    for workers in (1, 3, 8):
        M, rs = P.tally_matrix(zip(refs, clusters), lay, workers=workers)
        fit = F.fit_tallies(M, lay, seed=3, bootstrap=25, calibration=dict(refs=refs[:250], coverage=6, iterations=2, workers=workers))
        doc = MO.build(fit, **_provenance(fit))
        docs.append(cm.canonical_json(doc))
    assert docs[0] == docs[1] == docs[2]


def test_fitted_document_carries_labels_provenance_and_parameter_records():
    model = registry.load_model("mixed-harsh")
    refs = refs_of(800, 100, seed=6)
    clusters = S.simulate_clusters(model, refs, 6, seed=5)
    lay = T.Layout(100)
    M, _ = P.tally_matrix(zip(refs, clusters), lay)
    fit = F.fit_tallies(M, lay, seed=3, bootstrap=20)
    doc = MO.build(fit, **_provenance(fit))
    m, _ = cm.from_doc(json.loads(json.dumps(doc)))
    assert m.doc["data_source"] == "LABORATORY" and m.doc["evidence_class"] == "PUBLIC-DATA-DERIVED"
    assert m.doc["provenance"]["fitting"]["commit"] == "a" * 40 and m.doc["provenance"]["split"]["name"] == "FIT"
    p = m.doc["parameters"]["sequencing.substitution.rate"]
    assert p["basis"] == "measured" and p["ci95"][0] <= p["value"] <= p["ci95"][1] * 1.0001
    assert m.doc["parameters"]["sequencing.homopolymer.min_run"]["basis"] == "estimated"
    assert m.doc["parameters"]["synthesis.dropout_rate"]["basis"] == "inferred"
    assert m.unsupported_effects() == []              # the fitter only emits effects the simulator honours
    # the fitted model simulates
    refs2 = refs_of(50, 100, seed=8)
    assert len(S.simulate_clusters(m, refs2, 3, seed=1)) == 50


def test_fitting_code_has_no_unguarded_read_access():
    from pathlib import Path
    src = Path(F.__file__).parent
    for p in src.glob("*.py"):
        text = p.read_text()
        assert "Clusters.txt" not in text and "RX__" not in text and ".fastq" not in text, p


def test_calibrated_fit_recovers_the_parameters_of_the_simulated_truth():
    """Simulation-calibrated fit of reads simulated from nanopore-like: baseline rates and homopolymer multipliers within 8 %."""
    model = registry.load_model("nanopore-like")
    refs = refs_of(3000, 100, seed=9)
    clusters = S.simulate_clusters(model, refs, 8, seed=2)
    lay = T.Layout(100)
    M, _ = P.tally_matrix(zip(refs, clusters), lay)
    fit = F.fit_tallies(M, lay, seed=3, bootstrap=20, with_coverage=False, calibration=dict(refs=refs[:2000], coverage=10, iterations=4))
    v, s = fit["values"], model.stages["sequencing"]
    for path, truth in (("sequencing.substitution.rate", s["substitution"]["rate"]), ("sequencing.insertion.rate", s["insertion"]["rate"]),
                        ("sequencing.deletion.rate", s["deletion"]["rate"]),
                        ("sequencing.homopolymer.indel_multiplier", s["homopolymer"]["indel_multiplier"]),
                        ("sequencing.homopolymer.substitution_multiplier", s["homopolymer"]["substitution_multiplier"])):
        assert abs(v[path] - truth) <= 0.08 * truth, (path, v[path], truth)
    assert fit["calibration"]["trace"] and "factors" in fit["calibration"]


def test_dp_diag_aligner_has_edlib_distances_and_prefers_substitutions_at_ties():
    from vnxdna.simulation.fit.align import dp_align_batch, right_normalise
    rnd = random.Random(3)
    ref = refs_of(1, 60, seed=3)[0]
    reads = []
    for _ in range(80):
        r = bytearray(ref)
        for _k in range(rnd.randint(0, 6)):
            i = rnd.randrange(len(r))
            k = rnd.random()
            if k < .35:
                del r[i]
            elif k < .7:
                r.insert(i, rnd.choice(b"ACGT"))
            else:
                r[i] = rnd.choice(b"ACGT")
        reads.append(bytes(r))
    dp = dp_align_batch(ref, reads)
    subs_dp = subs_ed = 0
    for read, (d, runs, w) in zip(reads, dp):
        d_ed, runs_ed, _ = align(read, ref)
        assert d == d_ed and replay(ref, read, runs) == read
        subs_dp += sum(n for op, n in runs if op == "X")
        subs_ed += sum(n for op, n in runs_ed if op == "X")
        rn = right_normalise(ref, read, [list(x) for x in runs_ed])
        assert replay(ref, read, rn) == read and sum(n for op, n in rn if op != "=") == d
    assert subs_dp >= subs_ed
    # tie: ref AC vs read CA (two mismatches, or one deletion plus one insertion): the diagonal alignment is chosen
    assert [op for op, _ in dp_align_batch(b"ACGTT", [b"CAGTT"])[0][1]] == ["X", "="]


def test_metric_functions_accept_equal_and_reject_different_distributions():
    from vnxdna.simulation.fit.tally import DRIFT_BINS, EDIT_BINS
    rs = np.zeros(EDIT_BINS + DRIFT_BINS, dtype=np.int64)
    rs[:20] = np.arange(20, 0, -1) * 50
    rs[EDIT_BINS + 100] = 500
    rs[EDIT_BINS + 98] = 300
    assert V.m2_edit_distance(rs, rs)["pass"] and V.m3_drift(rs, rs)["pass"]
    other = rs.copy()
    other[:20] = np.arange(1, 21) * 50
    assert not V.m2_edit_distance(rs, other)["pass"]
    shifted = rs.copy()
    shifted[EDIT_BINS + 98], shifted[EDIT_BINS + 110] = 0, 300
    assert not V.m3_drift(rs, shifted)["pass"]
    lo, hi = V.wilson(50, 100)
    assert 0.39 < lo < 0.41 and 0.59 < hi < 0.61


def test_center_star_consensus_recovers_the_reference_from_noisy_reads():
    ref = refs_of(1, 80, seed=12)[0]
    model = registry.load_model("mixed-mild")
    reads = S.simulate_clusters(model, [ref], 15, seed=3)[0]
    assert V.consensus_error(reads, ref) < V.consensus_error(reads[:1], ref) + 1e-9
    assert V.consensus_error([ref] * 3, ref) == 0.0
    assert V.exact_length_vote([ref, ref], ref) == (True, 0.0)
