import json

import pytest

from vnxops import governor, laya, models

POLICY = laya.cfg("policy")
AGENTS = laya.cfg("agents")


@pytest.mark.parametrize("text,expected", [
    ("Design the module boundaries for the V4 ECC engine", "architecture"),
    ("Survey published literature on fountain codes", "research"),
    ("Run the full regression suite", "test_run"),
    ("benchmark encode throughput at 10 MB", "benchmark_run"),
    ("The decoder fails on truncated reads with a traceback", "debugging"),
    ("Implement streaming random access for V5", "implementation"),
    ("Update the README limitations section", "documentation"),
    ("audit dependencies for known CVEs", "security_review"),
    ("ruff lint", "lint_fix"),
])
def test_rule_classification(text, expected):
    assert laya.classify(text, POLICY, use_model=False)["task_type"] == expected


def test_unmatched_request_falls_back_to_default_without_model():
    r = laya.classify("zzz qqq", POLICY, use_model=False)
    assert r == {"task_type": POLICY["classification"]["default"], "method": "default"}


def test_plan_kinds_and_gates():
    assert laya.plan("test_run", POLICY, "run the tests")["kind"] == "tool"
    assert laya.plan("test_run", POLICY, "run the full suite")["tools"] == ["pytest_full"]
    p = laya.plan("implementation", POLICY, "implement x")
    assert p["kind"] == "agent" and p["tests"] and p["benchmark"] and p["risk"] == "medium"
    assert laya.plan("log_summary", POLICY, "summarise the log")["kind"] == "model"


def test_every_task_type_has_an_agent_and_model_requirement():
    reqs = models.registry()["task_requirements"]
    for tt, spec in POLICY["task_types"].items():
        assert spec["agent"] in AGENTS["agents"], tt
        assert tt in reqs, tt


def test_agent_tasks_always_get_the_agent_cli():
    m = laya.select_model("implementation", "agent", [3, 2])
    assert m["backend"] == "agent-cli"


def test_approval_required_for_high_risk_and_granted_once(home):
    approvals = laya.cfg("approvals")
    assert laya.approval_state(None, "low", POLICY, approvals) == {"required": False}
    st = laya.approval_state(None, "high", POLICY, approvals)
    assert st["required"] and not st["granted"]


def test_review_rejects_forbidden_paths_secrets_and_big_diffs(tmp_path):
    patch = tmp_path / "p.patch"
    patch.write_text("+++ b/tests/fixtures/x.bin\n+data\n+++ b/src/vnxdna/a.py\n+token = 'abcdefghijklmnopqrstuvwxyz'\n")
    record = {"attempts": [{"patch": str(patch), "changed_lines": 5000}], "tests": [], "benchmark": []}
    rv = laya.review("implementation", laya.plan("implementation", POLICY, "x"), record, POLICY)
    failed = {c["check"] for c in rv["checks"] if not c["ok"]}
    assert {"diff_size", "forbidden_paths", "secret_scan", "docs_updated", "tests_changed_or_added"} <= failed
    assert not rv["ok"]


def test_review_escalates_format_changes(tmp_path):
    patch = tmp_path / "p.patch"
    patch.write_text("+++ b/src/vnxdna/container/vxdna.py\n+x = 1\n+++ b/tests/unit/test_x.py\n+def test(): pass\n+++ b/docs/ECC.md\n+doc\n")
    record = {"attempts": [{"patch": str(patch), "changed_lines": 3}], "tests": [], "benchmark": []}
    rv = laya.review("implementation", laya.plan("implementation", POLICY, "x"), record, POLICY)
    assert rv["ok"] and rv["escalate_to_approval"]


def test_dry_run_decision_is_logged(home, monkeypatch):
    monkeypatch.setattr(governor, "snapshot", lambda: governor.Snapshot(20 * 2**30, 32 * 2**30, 0, 80 * 2**30, 0, 0, 0, 0))
    rec = laya.decide("run the tests", execute=False, use_model_classifier=False)
    assert rec["status"] == "planned"
    steps = [t["step"] for t in rec["trace"]]
    assert steps[:7] == ["REQUEST", "CLASSIFY", "PLAN", "SELECT_AGENT", "SELECT_MODEL", "CHECK_RESOURCES", "APPROVAL"]
    path, stored = laya.find_decision(rec["decision_id"])
    assert stored["status"] == "planned" and json.loads(path.read_text())["decision_id"] == rec["decision_id"]


def test_high_risk_waits_for_approval_then_runs_dry(home, monkeypatch):
    monkeypatch.setattr(governor, "snapshot", lambda: governor.Snapshot(20 * 2**30, 32 * 2**30, 0, 80 * 2**30, 0, 0, 0, 0))
    rec = laya.decide("design the V5 architecture", execute=False, use_model_classifier=False)
    assert rec["status"] == "pending_approval"
    laya.approve(rec["decision_id"], "tester")
    again = laya.decide("design the V5 architecture", execute=False, decision_id=rec["decision_id"], use_model_classifier=False)
    assert again["status"] == "planned"


def test_deferred_when_resources_short(home, monkeypatch):
    monkeypatch.setattr(governor, "snapshot", lambda: governor.Snapshot(1 * 2**30, 32 * 2**30, 0, 80 * 2**30, 0, 0, 0, 0))
    rec = laya.decide("run the tests", execute=False, use_model_classifier=False)
    assert rec["status"] == "deferred"
