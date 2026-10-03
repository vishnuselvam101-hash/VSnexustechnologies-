import yaml

from vnxops import harness, paths


def _tools(tmp_path, monkeypatch, tools):
    cfg = tmp_path / "laya"
    cfg.mkdir()
    for f in paths.LAYA_CONFIG.glob("*.yaml"):
        (cfg / f.name).write_text(f.read_text())
    t = yaml.safe_load((cfg / "tools.yaml").read_text())
    t["tools"].update(tools)
    (cfg / "tools.yaml").write_text(yaml.safe_dump(t))
    monkeypatch.setattr(paths, "LAYA_CONFIG", cfg)


def test_tool_task_success_record(tmp_path, monkeypatch, no_systemd, home):
    _tools(tmp_path, monkeypatch, {"ok_tool": {"argv": ["true"], "cwd": "{repo}", "job_class": "misc", "mem": "64MiB", "timeout": 10}})
    r = harness.execute(harness.Task(kind="tool", agent="test_engineer", task_type="test_run", input={}, tools=["ok_tool"]))
    assert r["result"] == "success" and r["repository"]["commit"] and len(r["attempts"]) == 1
    for key in ("task_id", "timestamp", "agent", "model", "repository", "input", "tools_used", "tests", "benchmark",
                "result", "failure_class", "failure_reason", "attempts"):
        assert key in r


def test_test_failure_is_not_retried(tmp_path, monkeypatch, no_systemd, home):
    _tools(tmp_path, monkeypatch, {"pytest_bad": {"argv": ["false"], "cwd": "{repo}", "job_class": "test", "mem": "64MiB", "timeout": 10}})
    r = harness.execute(harness.Task(kind="tool", agent="test_engineer", task_type="test_run", input={}, tools=["pytest_bad"]))
    assert r["result"] == "failure" and r["failure_class"] == "test_failure" and len(r["attempts"]) == 1


def test_timeout_is_retried_with_different_strategies_then_stops(tmp_path, monkeypatch, no_systemd, home):
    _tools(tmp_path, monkeypatch, {"slow": {"argv": ["sleep", "5"], "cwd": "{repo}", "job_class": "misc", "mem": "64MiB", "timeout": 0.2}})
    r = harness.execute(harness.Task(kind="tool", agent="test_engineer", task_type="test_run", input={}, tools=["slow"]))
    strategies = [a["strategy"] for a in r["attempts"]]
    assert r["failure_class"] == "timeout" and len(strategies) == len(set(strategies)) <= 3


def test_unknown_tool_is_policy_violation(no_systemd, home):
    r = harness.execute(harness.Task(kind="tool", agent="x", task_type="test_run", input={}, tools=["no_such_tool"]))
    assert r["failure_class"] == "policy_violation"


def test_internal_errors_keep_traceback(no_systemd, home):
    r = harness.execute(harness.Task(kind="model", agent="coder", task_type="classification", input={}, model="not-a-model"))
    assert r["failure_class"] == "internal" and "traceback" in r["attempts"][0]
