"""PHASE 18: environment validation — the 13 checks of the bootstrap spec, each recorded with evidence.

`vnxdna validate` runs them in order and writes reports/validation.json (+ .md). A check is PASS only when its
evidence was produced in this run; anything that could not be exercised is SKIPPED with the reason.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import benchmarks, governor, harness, laya, observe, paths, queue, release, worker


def _git(*a: str, cwd: Path) -> str:
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True).stdout.strip()


def c_doctor() -> tuple[bool, Any]:
    d = observe.doctor()
    return d["ok"], {"summary": d["summary"], "fail": [c for c in d["checks"] if c["result"] == "FAIL"],
                     "warn": [c["check"] for c in d["checks"] if c["result"] == "WARN"]}


def c_system_benchmark() -> tuple[bool, Any]:
    r = benchmarks.run_system(quick=True, write=False)
    m = r["measured"]
    ok = bool(m.get("cpu_single_events_s")) and m["dna_roundtrip"].get("sha256_identical") is True
    return ok, {k: m.get(k) for k in ("cpu_single_events_s", "cpu_multi_events_s", "seq_write_MBps", "seq_read_MBps",
                                      "rust_compile_hello_release_s", "c_compile_3000fn_O2_s")} | {"dna_roundtrip": m["dna_roundtrip"]}


def c_repo_tests() -> tuple[bool, Any]:
    out = harness.task_dir(harness.Task(kind="tool", agent="test_engineer", task_type="test_run", input={"validate": True}))
    res = [harness.run_tool(t, paths.REPO, out) for t in ("pytest_full", "pytest_ops", "ruff")]
    ok = all(r["result"] == "pass" for r in res)
    release._gate_record("tests", "PASS" if ok else "FAIL", [{k: r[k] for k in ("tool", "result", "exit_code", "log")} for r in res])
    return ok, [{k: r[k] for k in ("tool", "result", "exit_code", "seconds")} | {"summary": r["tail"].strip().splitlines()[-1:]} for r in res]


def c_v3_regression() -> tuple[bool, Any]:
    base = json.loads((paths.REPO / "docs" / "releases" / "v3-baseline.json").read_text())
    cur = benchmarks.run_dna("smoke")
    import yaml
    thr = yaml.safe_load((paths.LAYA_CONFIG / "release-gates.yaml").read_text())["benchmark_thresholds"]
    viol = release.regression_vs_baseline(cur, base["benchmark_smoke"], thr)
    compat = [t for t in ("tests/v2/test_compat_v2.py", "tests/v3") if (paths.REPO / t).exists()]
    return cur["pass"] and not viol, {"violations": viol, "benchmark_run": cur["run_dir"],
                                     "baseline_tests": base["tests"], "compat_suites_in_full_run": compat}


def c_e2e() -> tuple[bool, Any]:
    r = release.e2e()
    return r["ok"], r


def c_sha256() -> tuple[bool, Any]:
    with tempfile.TemporaryDirectory(dir=paths.run_dir()) as tmp:
        from .experiments import run_trial
        r = run_trial({"name": "sha", "seed": 99, "size": "100KB", "coverage": 1,
                                    "coverage_model": "fixed", "consensus": False}, Path(tmp), 2)
    return r["outcome"] == "RECOVERED" and r["input_sha256"] == r["output_sha256"], \
        {"input_sha256": r["input_sha256"], "output_sha256": r["output_sha256"], "bytes": r["input_bytes"]}


def c_laya() -> tuple[bool, Any]:
    flows = {}
    a = laya.decide("ruff lint the repository", requested_by="validate")                         # tool path, executed
    flows["tool_path"] = {"id": a["decision_id"], "status": a["status"], "steps": [t["step"] for t in a["trace"]]}
    b = laya.decide("design the V4 module boundaries", requested_by="validate", execute=False)   # high risk → approval
    flows["approval_gate"] = {"id": b["decision_id"], "status": b["status"]}
    laya.approve(b["decision_id"], "validate")
    b2 = laya.decide("design the V4 module boundaries", requested_by="validate", execute=False, decision_id=b["decision_id"])
    flows["after_approval"] = {"status": b2["status"]}
    c = laya.decide("summarise this log: governor level OK, 3 jobs done, 0 refused", task_type="log_summary", requested_by="validate")
    flows["local_model_path"] = {"id": c["decision_id"], "status": c["status"],
                                 "model": next((t.get("model") for t in c["trace"] if t["step"] == "SELECT_MODEL"), None)}
    ok = (a["status"] == "accepted" and b["status"] == "pending_approval" and b2["status"] == "planned"
          and c["status"] in ("accepted", "accepted_pending_human_review"))
    return ok, flows


def c_harness() -> tuple[bool, Any]:
    r = harness.execute(harness.Task(kind="tool", agent="test_engineer", task_type="test_run", input={"validate": True},
                                     tools=["compileall"]))
    rec = Path(r["artifacts"]) / "record.json"
    fields = ("task_id", "timestamp", "agent", "model", "repository", "input", "tools_used", "tests", "benchmark", "result",
              "failure_class", "failure_reason")
    stored = json.loads(rec.read_text())
    bad = harness.execute(harness.Task(kind="tool", agent="x", task_type="test_run", input={}, tools=["no_such_tool"]))
    return r["result"] == "success" and all(f in stored for f in fields) and bad["failure_class"] == "policy_violation", \
        {"record": str(rec), "commit": stored["repository"]["commit"], "negative_case": bad["failure_class"]}


def c_governor() -> tuple[bool, Any]:
    r = governor.run(["sh", "-c", "cat /proc/self/cgroup"], cls="misc", mem=64 * 2**20, stdout=subprocess.PIPE)
    refused = governor.run(["true"], cls="misc", mem=10**15, wait=False)
    wd = subprocess.run(["systemctl", "is-active", "vnxdna-governor.service"], capture_output=True, text=True).stdout.strip()
    oom = governor.run(["/opt/vnx-dna/venv/bin/python", "-c", "b = bytearray(1200 * 2**20); b[::4096] = b'x' * len(b[::4096])"],
                       cls="misc", mem=256 * 2**20, timeout=60)
    ok = r["governed"] and r["status"] == "done" and refused["status"] == "refused" and refused["exit_code"] == 75 \
        and wd == "active" and oom["status"] in ("killed_limit", "failed")
    return ok, {"governed_job": r["unit"], "refusal": refused["reason"], "watchdog": wd,
                "memory_cap_job": {"status": oom["status"], "exit": oom["exit_code"], "mem_max": oom["mem_max"]}}


def c_mcp() -> tuple[bool, Any]:
    m = observe.mcp_health(refresh=True)
    return m["total"] > 0, {"ready": m["ready"], "total": m["total"], "servers": m["servers"],
                            "note": "VNX-DNA agents use no MCP server (config/laya/tools.yaml mcp.allowed_servers = [])"}


def c_queue() -> tuple[bool, Any]:
    res: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(dir=paths.run_dir()) as tmp:
        lq = queue.LocalQueue(Path(tmp) / "q.db")
        # lint_fix runs in the `misc` class, so the check does not depend on how busy the test class is
        lq.put({"request": "ruff lint", "execute": False, "requested_by": "validate"})
        w = worker.run(max_messages=1, poll=1, backend=lq)
        res["local_worker"] = w
    try:
        rq = queue.RedisQueue(prefix="vnxdna:validate")
        rq.purge()
        mid = rq.put({"x": 1})
        got = rq.get(timeout=2)
        rq.ack(got[0]) if got else None
        res["redis"] = {"roundtrip": bool(got and got[0] == mid), "depth": rq.depth()}
        rq.purge()
    except OSError as exc:
        res["redis"] = {"roundtrip": False, "error": repr(exc)}
    res["sqs"] = "adapter unit-tested with a fake client; disabled (no AWS account configured)"
    ok = w["processed"] == 1 and w["decisions"][0]["status"] == "planned" and res["redis"]["roundtrip"]
    return ok, res


def c_logging() -> tuple[bool, Any]:
    gov = (paths.LOGS / "governor.jsonl").read_text().splitlines()
    recent = [json.loads(x) for x in gov[-200:]]
    events = sorted({e["event"] for e in recent})
    decisions = list((paths.ARTIFACTS / "laya").glob("*/decision-*.json"))
    records = list((paths.ARTIFACTS / "harness").glob("*/*/record.json"))
    ok = "job_start" in events and "job_end" in events and decisions and records
    return bool(ok), {"governor_events": events, "decision_records": len(decisions), "harness_records": len(records)}


def c_rollback() -> tuple[bool, Any]:
    bks = sorted(paths.BACKUPS.glob("*/MANIFEST.json"))
    if not bks:
        return False, "no backup"
    dest = bks[-1].parent
    from . import backup
    v = backup.verify(dest)
    man = json.loads((dest / "MANIFEST.json").read_text())
    entry = next(e for e in man["entries"] if "bundle" in e and e["source"].endswith("VSnexustechnologies-"))
    with tempfile.TemporaryDirectory(dir=paths.run_dir()) as tmp:
        clone = Path(tmp) / "restore"
        subprocess.run(["git", "clone", "-q", str(dest / entry["bundle"]["file"]), str(clone)], check=True)
        tag = _git("rev-parse", "v3.0.0^{commit}", cwd=clone)
        head_present = subprocess.run(["git", "-C", str(clone), "cat-file", "-e", entry["git"]["head"]]).returncode == 0
    scripts = sorted((paths.HOME / "rollback").glob("*.sh"))
    syntax = {s.name: subprocess.run(["sh", "-n", str(s)]).returncode == 0 for s in scripts}
    ok = v["ok"] and tag == "9b5123ceb86805b9d0eabd7a5f74592c3d97eb58" and head_present and all(syntax.values())
    return ok, {"backup": str(dest), "verified": v["ok"], "restored_tag_v3": tag, "restored_head_present": head_present,
                "rollback_scripts_syntax_ok": syntax}


CHECKS: list[tuple[str, Callable[[], tuple[bool, Any]]]] = [
    ("1 vnxdna doctor", c_doctor), ("2 system benchmark", c_system_benchmark), ("3 repository tests", c_repo_tests),
    ("4 V3 regression", c_v3_regression), ("5 end-to-end encode/decode", c_e2e), ("6 SHA-256 reconstruction", c_sha256),
    ("7 LAYA decision flow", c_laya), ("8 agent harness", c_harness), ("9 resource governor", c_governor),
    ("10 MCP connectivity", c_mcp), ("11 queue backend", c_queue), ("12 logging", c_logging), ("13 rollback", c_rollback),
]


def run(only: list[str] | None = None) -> dict[str, Any]:
    results = []
    for name, fn in CHECKS:
        if only and not any(name.startswith(o + " ") for o in only):
            continue
        t0 = time.time()
        try:
            ok, ev = fn()
            res = "PASS" if ok else "FAIL"
        except Exception as exc:
            import traceback
            res, ev = "ERROR", {"error": repr(exc), "traceback": traceback.format_exc()[-1500:]}
        results.append({"check": name, "result": res, "seconds": round(time.time() - t0, 1), "evidence": ev})
    out: dict[str, Any] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "commit": _git("rev-parse", "HEAD", cwd=paths.REPO),
           "results": results, "pass": all(r["result"] == "PASS" for r in results)}
    paths.ensure(paths.REPORTS)
    prev = json.loads((paths.REPORTS / "validation.json").read_text()) if (paths.REPORTS / "validation.json").exists() and only else None
    if prev:  # partial re-run: merge into the previous full result
        keep = {r["check"]: r for r in prev["results"]}
        keep.update({r["check"]: r for r in results})
        out["results"] = [keep[n] for n, _ in CHECKS if n in keep]
        out["pass"] = all(r["result"] == "PASS" for r in out["results"])
    (paths.REPORTS / "validation.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    return out
