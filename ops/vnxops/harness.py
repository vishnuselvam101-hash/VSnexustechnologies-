"""PHASE 6: agent/tool execution harness.

The harness runs one task, under the governor, and writes one machine-readable record:

  task_id, timestamp, agent, model, repository state (path, branch, commit, dirty files), input, output (artifact
  paths), tools used, tests, benchmark, result, failure class + reason, attempts, timings.

Three task kinds:
  * ``tool``  — a Tier-0 tool from config/laya/tools.yaml (argv only, never a shell string).
  * ``model`` — a text-only model call (local Ollama or the remote agent CLI); output is a proposal artifact.
  * ``agent`` — the external agent CLI editing an isolated git worktree on ``experiment/<task-id>``; never pushes, never merges.

Failures are classified; only retryable classes are retried, each time with the next *different* strategy from
policy.yaml, up to max_attempts. Logs, workspace and traceback are always preserved.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import governor, models, paths, system

FAILURE_CLASSES = ("timeout", "resource_refused", "killed_limit", "model_error", "tool_error", "test_failure",
                   "policy_violation", "approval_required", "secret_detected", "transient", "internal")


@dataclass
class Task:
    kind: str                      # tool | model | agent
    agent: str
    task_type: str
    input: dict[str, Any]
    model: str | None = None
    tools: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    benchmark: list[str] = field(default_factory=list)
    timeout: float | None = None
    decision_id: str | None = None
    task_id: str = field(default_factory=lambda: dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((paths.LAYA_CONFIG / name).read_text())


def git_state(repo: Path) -> dict[str, Any]:
    def g(*a: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True).stdout.strip()
    status = g("status", "--porcelain")
    return {"path": str(repo), "branch": g("branch", "--show-current"), "commit": g("rev-parse", "HEAD"),
            "dirty_files": len([x for x in status.splitlines() if x.strip()]), "describe": g("describe", "--tags", "--always")}


def task_dir(task: Task) -> Path:
    d = paths.ARTIFACTS / "harness" / task.task_id[:8] / task.task_id
    paths.ensure(d)
    return d


def _subst(v: str, repo: Path) -> str:
    return v.replace("{repo}", str(repo)).replace("{venv}", str(paths.VENV)).replace("{workers}", "2")


def run_tool(name: str, repo: Path, out: Path, strategy: str | None = None, timeout: float | None = None) -> dict[str, Any]:
    spec = _load("tools.yaml")["tools"].get(name)
    if spec is None:
        return {"tool": name, "result": "error", "failure": "policy_violation", "reason": f"unknown tool {name}"}
    argv = [_subst(a, repo) for a in spec["argv"]]
    if strategy == "reduce_parallelism":
        argv = [a.replace("--workers=4", "--workers=2") for a in argv]
    log = out / f"{name}.log"
    with open(log, "wb") as fh:
        rec = governor.run(argv, cls=spec.get("job_class", "misc"), mem=governor.parse_size(spec.get("mem", "1GiB")),
                           timeout=timeout or spec.get("timeout"), cwd=_subst(spec.get("cwd", "{repo}"), repo),
                           stdout=fh, stderr=subprocess.STDOUT,
                           env={**os.environ, "PYTHONHASHSEED": "0",
                                # tests that shell out (README examples) need the installed CLI on PATH
                                "PATH": f"{paths.VENV / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"})
    tail = log.read_text(errors="replace")[-2000:]
    failure = None
    if rec["status"] == "refused":
        failure = "resource_refused"
    elif rec["status"] == "timeout":
        failure = "timeout"
    elif rec["status"] == "killed_limit":
        failure = "killed_limit"
    elif rec["exit_code"] != 0:
        failure = "test_failure" if name.startswith("pytest") else ("secret_detected" if name == "gitleaks" else "tool_error")
    return {"tool": name, "argv": argv, "exit_code": rec["exit_code"], "status": rec["status"], "seconds": rec.get("seconds"),
            "log": str(log), "tail": tail, "result": "pass" if failure is None else "fail", "failure": failure}


def _agent_prompt(agent: str, request: str) -> str:
    agents = _load("agents.yaml")
    common = (paths.REPO / agents["common_rules"]).read_text()
    role_file = paths.REPO / "ops" / "prompts" / f"{agent}.md"
    role = role_file.read_text() if role_file.exists() else ""
    return f"{common}\n\n{role}\n\n# Task\n\n{request}\n"


def create_workspace(task: Task, base: str) -> Path:
    ws = Path(_load("tools.yaml")["repository"]["workspaces"]) / task.task_id
    paths.ensure(ws.parent)
    branch = f"experiment/{task.task_id}"
    subprocess.run(["git", "-C", str(paths.REPO), "worktree", "add", "-q", "-b", branch, str(ws), base], check=True,
                   capture_output=True)
    return ws


def run_agent(task: Task, out: Path, model_cfg: dict[str, Any], workspace: Path) -> dict[str, Any]:
    role = _load("agents.yaml")["agents"][task.agent]
    allowed = ",".join(role["tools"]) or "Read"
    cli = system.agent_cli()
    if not cli:
        raise RuntimeError("no agent CLI configured: set agent.cli in system.yaml or VNXDNA_AGENT_CLI")
    argv = [cli, "-p", "--output-format", "json", "--permission-mode", "dontAsk", "--max-turns", "40",
            "--allowedTools", allowed, "--disallowedTools", "WebFetch,WebSearch" if "WebSearch" not in role["tools"] else "Bash"]
    prompt = _agent_prompt(task.agent, task.input.get("request", ""))
    (out / "prompt.md").write_text(prompt)
    log = out / "agent.json"
    with open(log, "w") as fh:
        rec = governor.run(argv, cls="agent", mem=governor.parse_size(model_cfg.get("ram_estimate", "512MiB")),
                           timeout=task.timeout or 3600, cwd=str(workspace), stdout=fh, stderr=subprocess.STDOUT,
                           env={k: v for k, v in os.environ.items() if not k.endswith(("_TOKEN", "_KEY", "_SECRET"))},
                           stdin_text=prompt)
    try:
        res = json.loads(log.read_text())
    except json.JSONDecodeError:
        res = {"is_error": True, "result": log.read_text()[-1000:]}
    diff = subprocess.run(["git", "-C", str(workspace), "diff", "--stat", "HEAD"], capture_output=True, text=True).stdout
    subprocess.run(["git", "-C", str(workspace), "add", "-A"], capture_output=True)
    patch = subprocess.run(["git", "-C", str(workspace), "diff", "--cached", "HEAD"], capture_output=True, text=True).stdout
    (out / "changes.patch").write_text(patch)
    failure = None
    if rec["status"] == "refused":
        failure = "resource_refused"
    elif rec["status"] == "timeout":
        failure = "timeout"
    elif res.get("is_error"):
        failure = "model_error"
    return {"exit_code": rec["exit_code"], "status": rec["status"], "seconds": rec.get("seconds"),
            "summary": str(res.get("result", ""))[:4000], "cost_usd": res.get("total_cost_usd"),
            "diffstat": diff, "patch": str(out / "changes.patch"), "changed_lines": sum(
                1 for line in patch.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---"))),
            "tools_allowed": role["tools"], "result": "pass" if failure is None else "fail", "failure": failure}


def run_model(task: Task, out: Path, model_name: str, model_cfg: dict[str, Any]) -> dict[str, Any]:
    prompt = _agent_prompt(task.agent, task.input.get("request", ""))
    cost: float | None
    if model_cfg["backend"] == "ollama":
        st = governor.State()
        st.set("llm_loaded_by_laya", [model_name])
        try:
            r = models.generate(model_name, prompt, max_tokens=int(task.input.get("max_tokens", 800)))
            text, cost, err = r["message"]["content"], 0.0, None
        except OSError as exc:
            text, cost, err = "", 0.0, repr(exc)
    else:
        r = models.remote(prompt, model_cfg, timeout=task.timeout or 900)
        text, cost, err = r.get("text", ""), r.get("cost_usd"), r.get("error")
    (out / "output.md").write_text(text)
    return {"output": str(out / "output.md"), "chars": len(text), "cost_usd": cost, "error": err,
            "untrusted_output": model_cfg.get("trust") == "untrusted",
            "result": "fail" if err else "pass", "failure": "model_error" if err else None}


def execute(task: Task, *, base: str = "HEAD", strategy_override: str | None = None) -> dict[str, Any]:
    """Run a task with retries; returns and writes the execution record."""
    pol = _load("policy.yaml")["retries"]
    reg = models.registry()
    out = task_dir(task)
    record: dict[str, Any] = {"task_id": task.task_id, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                              "decision_id": task.decision_id, "agent": task.agent, "task_type": task.task_type,
                              "kind": task.kind, "model": task.model, "repository": git_state(paths.REPO),
                              "input": task.input, "attempts": [], "tools_used": [], "tests": [], "benchmark": [],
                              "result": None, "failure_class": None, "failure_reason": None, "artifacts": str(out)}
    strategies = [None] + list(pol["strategies"])
    model_name = task.model
    workspace: Path | None = None
    for attempt in range(1, int(pol["max_attempts"]) + 1):
        strategy = strategy_override if attempt == 1 and strategy_override else strategies[min(attempt - 1, len(strategies) - 1)]
        if attempt > 1 and strategy == record["attempts"][-1].get("strategy"):
            break  # never retry with the same strategy
        if strategy == "escalate_model" and model_name:
            tier = reg["models"].get(model_name, {}).get("tier", 0)
            higher = [n for n, c in reg["models"].items() if c["tier"] > tier and c["backend"] != "none"]
            if not higher:
                break
            model_name = sorted(higher, key=lambda n: reg["models"][n]["tier"])[0]
        att: dict[str, Any] = {"attempt": attempt, "strategy": strategy, "model": model_name}
        try:
            if task.kind == "tool":
                steps = [run_tool(t, paths.REPO, out, strategy, task.timeout) for t in task.tools]
                record["tools_used"] = task.tools
                att["steps"] = steps
                fail = next((s for s in steps if s["result"] != "pass"), None)
                att["failure"] = fail["failure"] if fail else None
            elif task.kind == "model":
                if model_name is None or model_name not in reg["models"]:
                    raise KeyError(f"unknown model {model_name!r}")
                att.update(run_model(task, out, model_name, reg["models"][model_name]))
            elif task.kind == "agent":
                workspace = workspace or create_workspace(task, base)
                record["workspace"] = str(workspace)
                record["branch"] = f"experiment/{task.task_id}"
                att.update(run_agent(task, out, reg["models"][model_name], workspace))
                if att["failure"] is None:
                    record["tests"] = [run_tool(t, workspace, out, strategy) for t in task.tests]
                    record["benchmark"] = [run_tool(t, workspace, out, strategy) for t in task.benchmark]
                    bad = next((s for s in record["tests"] + record["benchmark"] if s["result"] != "pass"), None)
                    if bad:
                        att["failure"] = "test_failure"
            else:
                att["failure"] = "policy_violation"
                att["reason"] = f"unknown task kind {task.kind}"
        except Exception as exc:
            att["failure"] = "internal"
            att["traceback"] = traceback.format_exc()
            (out / f"traceback-{attempt}.txt").write_text(att["traceback"])
            att["reason"] = repr(exc)
        record["attempts"].append(att)
        if not att.get("failure"):
            record["result"] = "success"
            break
        record["failure_class"] = att["failure"]
        record["failure_reason"] = att.get("reason") or att.get("error") or _first_fail_tail(att)
        if att["failure"] not in pol["retryable"] or att["failure"] in pol["never_retry"]:
            break
    if record["result"] != "success":
        record["result"] = "failure"
    record["model"] = model_name
    record["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (out / "record.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
    return record


def _first_fail_tail(att: dict[str, Any]) -> str | None:
    for s in att.get("steps", []):
        if s.get("result") != "pass":
            return f"{s['tool']}: exit {s.get('exit_code')} ({s.get('status')}); log {s.get('log')}"
    return None


def records(limit: int = 20) -> list[dict[str, Any]]:
    files = sorted((paths.ARTIFACTS / "harness").glob("*/*/record.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [json.loads(f.read_text()) for f in files[:limit]]


__all__ = ["Task", "execute", "run_tool", "git_state", "records", "FAILURE_CLASSES", "asdict"]
