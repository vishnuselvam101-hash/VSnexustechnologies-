"""V6 recovery planner and budgets through the real pipeline.

The planner must (1) leave default decoding unchanged, (2) record every escalation with its reason and signals,
(3) stop admitting expensive work at a work budget and let the integrity chain alone decide SUCCESS, and (4) stop a
decode at a hard budget (wall time, memory) with a typed error and nothing published.
"""
from __future__ import annotations

import hashlib

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.constraints import iter_fasta
from vnxdna.v4.errors import VNXConfigurationError, VNXDecodeError
from vnxdna.v4.frame import KIND_DATA
from vnxdna.v6.recovery import RecoveryBudget, RecoveryPlanner, VNXBudgetExceeded

VOLATILE = ("stage_seconds", "seconds", "peak_rss_bytes", "output")


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("planner")
    datagen.generate(d / "in.bin", 40_000, "random", 7301)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    # indel-heavy reads at low coverage: the cheap paths leave groups incomplete, so deferred rounds A/B run
    ch.simulate_file(d / "s.fasta", d / "noisy.fastq",
                     ch.ChannelConfig(substitution_rate=0.004, insertion_rate=0.006, deletion_rate=0.006, coverage=2,
                                      seed=7302))
    return {"dir": d, "sha": hashlib.sha256((d / "a.vnx").read_bytes()).hexdigest()}


def _decode(arc, reads="noisy.fastq", name="out", **kw):
    out = arc["dir"] / f"{name}.vnx"
    out.unlink(missing_ok=True)
    res = de.decode_reads(arc["dir"] / reads, out, de.DecodeOptions(**kw), overwrite=True)
    exact = out.exists() and hashlib.sha256(out.read_bytes()).hexdigest() == arc["sha"]
    if res.status == "SUCCESS":
        assert exact, "SUCCESS must mean the exact container"
    return res, exact


def _stable(report):
    out = {k: v for k, v in report.items() if k not in VOLATILE}
    if "recovery_schedule" in out:                     # per-round wall time is volatile too
        sched = dict(out["recovery_schedule"])
        sched["rounds"] = {r: {k: v for k, v in d.items() if k != "seconds"} for r, d in sched.get("rounds", {}).items()}
        out["recovery_schedule"] = sched
    return out


def _stages(plan):
    return [(d["stage"], d["run"]) for d in plan["decisions"]]


# ------------------------------------------------------------------------------------------------ budget validation
@pytest.mark.parametrize("kw", [dict(max_reads_examined=-1), dict(max_round_b_reads=1.5), dict(max_outer_stripes=True),
                                dict(max_wall_seconds=0), dict(max_wall_seconds=float("nan")), dict(max_rss_bytes="1G")])
def test_invalid_budgets_are_rejected(kw):
    with pytest.raises(VNXConfigurationError):
        RecoveryBudget(**kw).validate()
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(recovery_budget=RecoveryBudget(**kw)).validate()


def test_budget_must_be_a_recovery_budget():
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(recovery_budget={"max_reads_examined": 3}).validate()


def test_default_budget_is_unlimited():
    assert RecoveryBudget().unlimited
    assert not RecoveryBudget(max_outer_stripes=0).unlimited


# ------------------------------------------------------------------------------------------------ provenance
def test_default_decode_records_the_plan(arc):
    res, exact = _decode(arc, reads="s.fasta")
    assert res.status == "SUCCESS" and exact
    plan = res.report["recovery_plan"]
    assert plan["budget_unlimited"] and plan["budget_exhausted"] == []
    assert _stages(plan) == [("FAST", True), ("SYNC", True), ("SMART", False), ("SOFT", False), ("OUTER", True)]
    fast = plan["decisions"][0]
    assert fast["signals"]["reads"] == res.report["reads"]["reads"]
    assert fast["result"]["reads_verified"] == res.report["reads"]["fast"]
    assert all(d["why"] for d in plan["decisions"])


def test_explicit_unlimited_budget_changes_nothing(arc):
    a, _ = _decode(arc, name="a", indel_recovery="smart")
    b, _ = _decode(arc, name="b", indel_recovery="smart", recovery_budget=RecoveryBudget())
    assert a.status == b.status
    assert _stable(a.report) == _stable(b.report)


def test_deferred_rounds_are_explained(arc):
    res, _ = _decode(arc, indel_recovery="smart", soft_decoding="erasure")
    plan = res.report["recovery_plan"]
    smart_soft = [d for d in plan["decisions"] if d["stage"] == "SMART+SOFT"]
    assert smart_soft and smart_soft[0]["why"].startswith("round S")
    assert any(d["why"].startswith("round A") for d in smart_soft)
    expensive = [d for d in plan["decisions"] if d["stage"] == "EXPENSIVE"]
    assert len(expensive) == 1 and "superblock_decoded" in expensive[0]["signals"]
    sched = res.report["recovery_schedule"]
    assert expensive[0]["run"] == sched["round_b"]
    rounds = sched["rounds"]
    assert plan["spent"]["reads_examined"] == sum(r["reads"] for r in rounds.values())


def test_plan_is_identical_across_worker_counts(arc):
    budget = RecoveryBudget(max_reads_examined=40)
    a, _ = _decode(arc, name="w1", indel_recovery="smart", recovery_budget=budget, workers=1)
    b, _ = _decode(arc, name="w3", indel_recovery="smart", recovery_budget=budget, workers=3)
    assert a.status == b.status
    assert a.report["recovery_plan"] == b.report["recovery_plan"]
    assert _stable(a.report) == _stable(b.report)


# ------------------------------------------------------------------------------------------------ work budgets
def test_zero_read_budget_examines_nothing_and_never_lies(arc):
    full, _ = _decode(arc, name="full", indel_recovery="smart")
    assert full.report["recovery_plan"]["spent"]["reads_examined"] > 0, "fixture must need recovery"
    res, exact = _decode(arc, name="zero", indel_recovery="smart", recovery_budget=RecoveryBudget(max_reads_examined=0))
    plan = res.report["recovery_plan"]
    assert plan["spent"]["reads_examined"] == 0
    assert plan["budget_exhausted"] and plan["budget_exhausted"][0]["limit"] == "max_reads_examined"
    assert all(r["reads"] == 0 for r in res.report["recovery_schedule"]["rounds"].values())
    assert res.status in ("SUCCESS", "PARTIAL", "FAILURE")      # SUCCESS only with the exact container (in _decode)


def test_read_budget_is_respected_exactly(arc):
    res, _ = _decode(arc, name="cap", indel_recovery="smart", recovery_budget=RecoveryBudget(max_reads_examined=25))
    plan = res.report["recovery_plan"]
    assert plan["spent"]["reads_examined"] <= 25
    assert sum(r["reads"] for r in res.report["recovery_schedule"]["rounds"].values()) == plan["spent"]["reads_examined"]


def test_round_b_budget(arc):
    res, _ = _decode(arc, name="b0", indel_recovery="smart", recovery_budget=RecoveryBudget(max_round_b_reads=0))
    rounds = res.report["recovery_schedule"]["rounds"]
    assert rounds.get("B", {"reads": 0})["reads"] == 0
    assert res.report["recovery_plan"]["spent"]["round_b_reads"] == 0


# ------------------------------------------------------------------------------------------------ hard budgets
def test_wall_time_budget_stops_the_decode_and_publishes_nothing(arc):
    out = arc["dir"] / "late.vnx"
    out.unlink(missing_ok=True)
    with pytest.raises(VNXBudgetExceeded) as e:
        de.decode_reads(arc["dir"] / "noisy.fastq", out,
                        de.DecodeOptions(recovery_budget=RecoveryBudget(max_wall_seconds=1e-9)))
    assert not out.exists()
    assert isinstance(e.value, VNXDecodeError) and e.value.stage == "budget"
    plan = e.value.details["recovery_plan"]
    assert plan["budget_exhausted"][-1]["limit"] == "max_wall_seconds"


def test_memory_budget_stops_the_decode(arc):
    out = arc["dir"] / "mem.vnx"
    out.unlink(missing_ok=True)
    with pytest.raises(VNXBudgetExceeded):
        de.decode_reads(arc["dir"] / "s.fasta", out, de.DecodeOptions(recovery_budget=RecoveryBudget(max_rss_bytes=1)))
    assert not out.exists()


def test_generous_hard_budgets_change_nothing(arc):
    a, _ = _decode(arc, reads="s.fasta", name="g1")
    b, _ = _decode(arc, reads="s.fasta", name="g2", recovery_budget=RecoveryBudget(max_wall_seconds=3600, max_rss_bytes=1 << 40))
    assert a.status == b.status == "SUCCESS"
    pa, pb = a.report.pop("recovery_plan"), b.report.pop("recovery_plan")
    assert _stable(a.report) == _stable(b.report)
    assert pa["decisions"] == pb["decisions"] and pb["checkpoints"] > 0


# ------------------------------------------------------------------------------------------------ outer budget (V6)
@pytest.fixture(scope="module")
def cols(arc):
    d = arc["dir"]
    en.encode_container(d / "a.vnx", d / "cols.fasta", en.DNAOptions(stripe_depth=4, column_parity=2))
    lost = {1, 2}                                     # two whole data rows of stripe 0: only the column code recovers them
    with open(d / "cols-lost.fasta", "w") as f:
        for i, (head, seq) in enumerate(iter_fasta(d / "cols.fasta")):
            _, _, kind, g, _ = head.split("|")
            if int(kind) == KIND_DATA and int(g) in lost:
                continue
            f.write(f">r{i}\n{seq}\n")
    return "cols-lost.fasta"


def test_outer_stripe_budget(arc, cols):
    ok, exact = _decode(arc, reads=cols, name="c1")
    assert ok.status == "SUCCESS" and exact
    assert ok.report["outer_v6"]["data_rows_recovered_by_columns"] == 2
    res, exact = _decode(arc, reads=cols, name="c0", recovery_budget=RecoveryBudget(max_outer_stripes=0))
    assert res.status != "SUCCESS" and not exact
    assert res.report["outer_v6"]["stripes_skipped_by_budget"] >= 1
    plan = res.report["recovery_plan"]
    assert any(e["limit"] == "max_outer_stripes" for e in plan["budget_exhausted"])
    assert plan["decisions"][-1]["stage"] == "REJECT"


# ------------------------------------------------------------------------------------------------ unit
def test_planner_unit_accounting():
    p = RecoveryPlanner(RecoveryBudget(max_reads_examined=10, max_round_b_reads=3))
    assert p.admit_reads("S", 4) == 4
    assert p.admit_reads("B", 5) == 3
    assert p.admit_reads("A", 9) == 3
    assert p.admit_reads("A", 1) == 0
    assert p.spent == {"reads_examined": 10, "round_b_reads": 3, "outer_stripes": 0}
    p.unexamined("B", 2)
    assert p.spent["reads_examined"] == 8 and p.spent["round_b_reads"] == 1
    assert [e["limit"] for e in p.exhausted] == ["max_round_b_reads", "max_reads_examined", "max_reads_examined"]
