"""PHASE 8: VNX-DNA LAYA — the decision and orchestration layer.

    REQUEST → CLASSIFY → PLAN → SELECT AGENT → SELECT MODEL → CHECK RESOURCES → (APPROVAL) → EXECUTE → TEST →
    BENCHMARK → REVIEW → ACCEPT / REJECT → LOG

LAYA does not execute suggestions blindly: each step is driven by config/laya/*.yaml, deterministic where possible,
and every request ends in exactly one decision record under artifacts/laya/YYYY-MM-DD/decision-<id>.json — including
rejections, deferrals and requests waiting for approval. Accepted agent work stays on its experiment/* branch; a
human merges it.
"""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from . import governor, harness, models, paths, security

TOOL_TASKS = {"test_run": ["pytest_fast"], "benchmark_run": ["bench_smoke"], "lint_fix": ["ruff"]}
MODEL_TASKS = {"log_summary", "classification"}
STATUSES = ("accepted", "accepted_pending_human_review", "rejected", "deferred", "pending_approval", "failed", "planned")


def cfg(name: str) -> dict[str, Any]:
    return yaml.safe_load((paths.LAYA_CONFIG / f"{name}.yaml").read_text())


def decisions_dir(day: dt.date | None = None) -> Path:
    d = paths.ARTIFACTS / "laya" / (day or dt.date.today()).isoformat()
    paths.ensure(d)
    return d


def _write(rec: dict[str, Any]) -> Path:
    p = decisions_dir() / f"decision-{rec['decision_id']}.json"
    rec["logged_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    p.write_text(json.dumps(rec, indent=2, default=str) + "\n")
    return p


def find_decision(decision_id: str) -> tuple[Path, dict[str, Any]] | None:
    for p in (paths.ARTIFACTS / "laya").glob(f"*/decision-{decision_id}.json"):
        return p, json.loads(p.read_text())
    return None


# ------------------------------------------------------------------------------------------------ steps


def classify(text: str, policy: dict[str, Any], use_model: bool = True) -> dict[str, Any]:
    for rule in policy["classification"]["rules"]:
        if re.search(rule["match"], text, re.I):
            return {"task_type": rule["type"], "method": "rule", "rule": rule["match"]}
    types = sorted(policy["task_types"])
    if use_model and policy["classification"].get("fallback_model_classification") and models.ollama_ready():
        choice = models.select("classification")
        if choice["model"] and choice["backend"] == "ollama":
            try:
                r = models.generate(choice["model"], f"Classify this engineering request into exactly one of: {', '.join(types)}.\n"
                                    f"Request: {text}\nAnswer with the single category name only.", max_tokens=8)
                ans = r["message"]["content"].strip().lower().strip(".` ")
                if ans in types:
                    return {"task_type": ans, "method": "model", "model": choice["model"]}
                return {"task_type": policy["classification"]["default"], "method": "default",
                        "note": f"model answer {ans!r} not a valid type"}
            except OSError:
                pass
    return {"task_type": policy["classification"]["default"], "method": "default"}


def plan(task_type: str, policy: dict[str, Any], text: str) -> dict[str, Any]:
    spec = policy["task_types"][task_type]
    req = spec.get("requirements", {})
    kind = "tool" if task_type in TOOL_TASKS else "model" if task_type in MODEL_TASKS else "agent"
    tools = list(TOOL_TASKS.get(task_type, []))
    if task_type == "test_run" and re.search(r"\b(full|all|regression)\b", text, re.I):
        tools = ["pytest_full"]
    tests = ["pytest_fast", "ruff"] if req.get("tests_required") and kind == "agent" else []
    bench = ["bench_smoke"] if req.get("benchmark_required") and kind == "agent" else []
    return {"kind": kind, "steps": spec["steps"], "requirements": req, "risk": spec["risk"], "job_class": spec["job_class"],
            "tools": tools, "tests": tests, "benchmark": bench}


def select_agent(task_type: str, policy: dict[str, Any], agents: dict[str, Any]) -> dict[str, Any]:
    spec = policy["task_types"][task_type]
    for name in (spec["agent"], spec.get("fallback")):
        if name and name in agents["agents"]:
            return {"agent": name, "fallback_used": name != spec["agent"], "tiers": agents["agents"][name]["tiers"]}
    return {"agent": None}


def select_model(task_type: str, kind: str, agent_tiers: list[int]) -> dict[str, Any]:
    if kind == "tool":
        return {"model": "deterministic", "tier": 0, "backend": "none", "reasons": ["tier-0 tool task"], "rejected": []}
    choice = models.select(task_type)
    reg = models.registry()
    if kind == "agent" and choice.get("backend") != "agent-cli":
        # Only the agent CLI can edit a worktree under permission control; local models never get write access.
        cands = [n for n, c in reg["models"].items() if c["backend"] == "agent-cli" and c.get("configured", True) and c["tier"] in agent_tiers]
        if cands:
            n = sorted(cands, key=lambda x: reg["models"][x]["tier"])[0]
            choice = {"model": n, "tier": reg["models"][n]["tier"], "backend": "agent-cli",
                      "reasons": choice.get("reasons", []) + [f"agent task needs edit tools → {n}"], "rejected": choice.get("rejected", [])}
    if choice.get("tier") is not None and choice["tier"] not in agent_tiers and kind != "agent":
        allowed = [n for n, c in reg["models"].items() if c["tier"] in agent_tiers and c["tier"] >= choice["tier"]]
        if allowed:
            n = sorted(allowed, key=lambda x: reg["models"][x]["tier"])[0]
            choice = {**choice, "model": n, "tier": reg["models"][n]["tier"], "backend": reg["models"][n]["backend"],
                      "reasons": choice["reasons"] + [f"agent restricted to tiers {agent_tiers} → {n}"]}
    return choice


def remote_spend_today() -> float:
    total = 0.0
    for p in decisions_dir().glob("decision-*.json"):
        try:
            total += float(json.loads(p.read_text()).get("cost_usd") or 0)
        except (ValueError, json.JSONDecodeError):
            continue
    return total


def check_resources(job_class: str, model_choice: dict[str, Any]) -> dict[str, Any]:
    pol = governor.Policy.load()
    mem = pol.class_mem(job_class)
    if model_choice.get("backend") == "ollama":
        b = json.loads(models.BENCH_FILE.read_text()).get(model_choice["model"], {}) if models.BENCH_FILE.exists() else {}
        mem += b.get("ram_rss_bytes") or governor.parse_size(models.registry()["models"][model_choice["model"]].get("ram_estimate", "2GiB"))
    d = governor.admit(job_class, mem, pol, governor.State())
    return {"admitted": d.admitted, "reason": d.reason, "level": d.level, "mem_estimate": mem}


def approval_state(decision_id: str | None, risk: str, policy: dict[str, Any], approvals: dict[str, Any]) -> dict[str, Any]:
    need = policy["risk_levels"][risk]["approval"]
    if need == "none":
        return {"required": False}
    if decision_id:
        found = find_decision(decision_id)
        if found and found[1].get("approval"):
            ap = found[1]["approval"]
            age_h = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ap["at"])).total_seconds() / 3600
            if age_h <= approvals["approval_expiry_hours"] and not ap.get("used"):
                return {"required": True, "granted": True, "by": ap["by"], "at": ap["at"], "mode": need}
            return {"required": True, "granted": False, "why": "approval expired or already used", "mode": need}
    return {"required": True, "granted": False, "mode": need}


def review(task_type: str, plan_: dict[str, Any], record: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    rv = policy["review"]
    checks: list[dict[str, Any]] = []
    escalate = False
    if plan_["kind"] == "agent":
        att = record["attempts"][-1] if record["attempts"] else {}
        patch_path = att.get("patch")
        patch = Path(patch_path).read_text() if patch_path and Path(patch_path).exists() else ""
        files = re.findall(r"^\+\+\+ b/(.+)$", patch, re.M)
        lines = att.get("changed_lines", 0)
        checks.append({"check": "diff_size", "ok": lines <= rv["max_changed_lines"], "detail": f"{lines} changed lines"})
        forbidden = [f for f in files if any(fnmatch.fnmatch(f, g) for g in rv["forbidden_paths"])]
        checks.append({"check": "forbidden_paths", "ok": not forbidden, "detail": forbidden})
        appr = [f for f in files if any(fnmatch.fnmatch(f, g) for g in rv["approval_paths"])]
        if appr:
            escalate = True
        checks.append({"check": "format_surface", "ok": True, "detail": appr, "escalates_to_approval": bool(appr)})
        secrets = security.scan_patch(patch) if rv.get("secret_scan") else []
        checks.append({"check": "secret_scan", "ok": not secrets, "detail": secrets})
        if plan_["requirements"].get("docs_required"):
            src = [f for f in files if f.startswith("src/")]
            docs = [f for f in files if any(fnmatch.fnmatch(f, g) for g in rv["docs_paths"])]
            checks.append({"check": "docs_updated", "ok": not src or bool(docs), "detail": {"src": len(src), "docs": len(docs)}})
        if plan_["requirements"].get("tests_required"):
            checks.append({"check": "tests_changed_or_added", "ok": any(f.startswith(("tests/", "ops/tests/")) and not f.startswith("tests/fixtures/")
                                      for f in files) or not files,
                           "detail": "behaviour changes must ship with tests"})
        if plan_["requirements"].get("evidence_labels_required"):
            text = att.get("summary", "")
            labels = re.findall(r"ESTABLISHED FACT|PUBLISHED RESULT|EXPERIMENTAL OBSERVATION|ENGINEERING ASSUMPTION|HYPOTHESIS|UNVERIFIED CLAIM|FUTURE WORK", text)
            checks.append({"check": "evidence_labels", "ok": bool(labels), "detail": sorted(set(labels))})
        if not files and task_type in ("implementation", "debugging", "refactoring", "test_generation"):
            checks.append({"check": "produced_change", "ok": False, "detail": "agent finished without changing any file"})
    for t in record.get("tests", []) + record.get("benchmark", []):
        checks.append({"check": f"gate:{t['tool']}", "ok": t["result"] == "pass", "detail": t.get("status")})
    if plan_["kind"] == "model" and record["attempts"]:
        att = record["attempts"][-1]
        if att.get("untrusted_output"):
            checks.append({"check": "untrusted_model_output", "ok": True, "detail": "output is advisory text only"})
    return {"checks": checks, "ok": all(c["ok"] for c in checks), "escalate_to_approval": escalate}


# ------------------------------------------------------------------------------------------------ main flow


def decide(text: str, *, task_type: str | None = None, requested_by: str = "owner", execute: bool = True,
           decision_id: str | None = None, use_model_classifier: bool = True, base: str = "HEAD",
           timeout: float | None = None) -> dict[str, Any]:
    policy, agents, approvals = cfg("policy"), cfg("agents"), cfg("approvals")
    did = decision_id or dt.datetime.now().strftime("%H%M%S-") + uuid.uuid4().hex[:8]
    rec: dict[str, Any] = {"decision_id": did, "schema": "vnxdna.laya-decision/1",
                           "request": {"text": text, "requested_by": requested_by, "task_type_hint": task_type},
                           "trace": [], "status": None, "repository": harness.git_state(paths.REPO)}
    prior = find_decision(did) if decision_id else None

    def step(name: str, **data: Any) -> None:
        rec["trace"].append({"step": name, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **data})

    def finish(status: str, reason: str | None = None) -> dict[str, Any]:
        rec["status"], rec["reason"] = status, reason
        step("LOG", path=str(decisions_dir() / f"decision-{did}.json"))
        if prior and prior[1].get("approval"):
            rec["approval"] = {**prior[1]["approval"], "used": status not in ("pending_approval", "deferred")}
        _write(rec)
        return rec

    step("REQUEST")
    if task_type and task_type not in policy["task_types"]:
        return finish("rejected", f"unknown task type {task_type}")
    cl = {"task_type": task_type, "method": "explicit"} if task_type else classify(text, policy, use_model_classifier)
    step("CLASSIFY", **cl)
    tt = cl["task_type"]
    pl = plan(tt, policy, text)
    step("PLAN", **pl)
    ag = select_agent(tt, policy, agents)
    step("SELECT_AGENT", **ag)
    if not ag["agent"]:
        return finish("rejected", "no agent for task type")
    mc = select_model(tt, pl["kind"], ag["tiers"])
    step("SELECT_MODEL", **mc)
    if not mc.get("model"):
        return finish("deferred", "no model satisfies requirements under current resources")
    if mc.get("backend") == "agent-cli":
        spent = remote_spend_today()
        if spent >= approvals["remote_budget"]["daily_usd_cap"]:
            return finish("pending_approval", f"remote budget: ${spent:.2f} spent today ≥ daily cap")
    res = check_resources(pl["job_class"], mc)
    step("CHECK_RESOURCES", **res)
    if not res["admitted"]:
        return finish("deferred", res["reason"])
    ap = approval_state(decision_id, pl["risk"], policy, approvals)
    step("APPROVAL", **ap)
    if ap["required"] and not ap.get("granted"):
        return finish("pending_approval", f"risk {pl['risk']} requires approval: `vnxdna laya approve {did}`")
    if not execute:
        return finish("planned", "dry run (execute=False)")
    task = harness.Task(kind=pl["kind"], agent=ag["agent"], task_type=tt, input={"request": text}, model=mc["model"],
                        tools=pl["tools"], tests=pl["tests"], benchmark=pl["benchmark"], timeout=timeout, decision_id=did)
    xr = harness.execute(task, base=base)
    rec["execution"] = {"task_id": xr["task_id"], "result": xr["result"], "failure_class": xr["failure_class"],
                        "failure_reason": xr["failure_reason"], "record": f"{xr['artifacts']}/record.json",
                        "branch": xr.get("branch"), "workspace": xr.get("workspace")}
    rec["cost_usd"] = sum(float(a.get("cost_usd") or 0) for a in xr["attempts"])
    step("EXECUTE", **rec["execution"])
    step("TEST", results=[{"tool": t["tool"], "result": t["result"]} for t in xr.get("tests", [])]
         + [{"tool": s["tool"], "result": s["result"]} for a in xr["attempts"] for s in a.get("steps", []) if s["tool"].startswith(("pytest", "ruff"))])
    step("BENCHMARK", results=[{"tool": t["tool"], "result": t["result"]} for t in xr.get("benchmark", [])]
         + [{"tool": s["tool"], "result": s["result"]} for a in xr["attempts"] for s in a.get("steps", []) if s["tool"].startswith("bench")])
    rv = review(tt, pl, xr, policy)
    step("REVIEW", **rv)
    if xr["result"] != "success":
        return finish("failed", f"{xr['failure_class']}: {xr['failure_reason']}")
    if not rv["ok"]:
        return finish("rejected", "review: " + ", ".join(c["check"] for c in rv["checks"] if not c["ok"]))
    if rv["escalate_to_approval"] and not ap.get("granted"):
        return finish("pending_approval", "change touches format/compatibility surface")
    if pl["requirements"].get("human_review") or pl["kind"] == "agent":
        return finish("accepted_pending_human_review", f"branch {xr.get('branch')} ready for human review" if xr.get("branch") else "output ready for review")
    return finish("accepted")


def approve(decision_id: str, by: str) -> dict[str, Any]:
    found = find_decision(decision_id)
    if not found:
        raise SystemExit(f"no decision {decision_id}")
    p, rec = found
    if rec.get("status") != "pending_approval":
        raise SystemExit(f"decision {decision_id} is {rec.get('status')}, not pending_approval")
    rec["approval"] = {"by": by, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "used": False}
    p.write_text(json.dumps(rec, indent=2, default=str) + "\n")
    return rec


def recent(limit: int = 20) -> list[dict[str, Any]]:
    files = sorted((paths.ARTIFACTS / "laya").glob("*/decision-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in files[:limit]:
        r = json.loads(f.read_text())
        out.append({"decision_id": r["decision_id"], "status": r["status"], "task_type": next(
            (t.get("task_type") for t in r["trace"] if t["step"] == "CLASSIFY"), None), "reason": r.get("reason"),
            "request": r["request"]["text"][:80]})
    return out
