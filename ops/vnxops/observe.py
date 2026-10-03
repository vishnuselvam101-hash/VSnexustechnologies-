"""PHASE 10: observability — `vnxdna status` (one-screen summary) and `vnxdna doctor` (checks with fixes).

Both print JSON with --json. Slow probes (MCP health via the agent CLI's `mcp list`) are cached in run/mcp-health.json for
10 minutes so `status` stays fast.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from . import governor, models, paths, profile, system
from .sysinfo import cpu_percent, disk, meminfo, run

OK, WARN, FAIL = "PASS", "WARN", "FAIL"


def mcp_health(max_age: float = 600, refresh: bool = False) -> dict[str, Any]:
    cache = paths.RUN / "mcp-health.json"
    if cache.exists() and not refresh and time.time() - cache.stat().st_mtime < max_age:
        return json.loads(cache.read_text())
    from .audit import _mcp
    res = _mcp()
    servers = res.get("servers", [])
    data = {"checked_at": time.time(), "servers": servers, "ready": sum("Connected" in s["status"] for s in servers),
            "total": len(servers)}
    paths.ensure(paths.RUN)
    cache.write_text(json.dumps(data))
    return data


def _git(repo: Path) -> dict[str, Any]:
    code, out = run(["git", "-C", str(repo), "status", "--porcelain"])
    _, head = run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"])
    _, br = run(["git", "-C", str(repo), "branch", "--show-current"])
    dirty = [x for x in out.splitlines() if x.strip()] if code == 0 else None
    return {"branch": br, "head": head, "dirty_files": None if dirty is None else len(dirty)}


def _last_gate(name: str) -> dict[str, Any] | None:
    p = paths.REPORTS / "gates" / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def status() -> dict[str, Any]:
    m = meminfo()
    d = disk(str(paths.HOME))
    st = governor.State()
    q: dict[str, Any]
    try:
        from . import queue
        q = queue.from_config().health()
    except Exception as exc:
        q = {"ok": False, "error": repr(exc)}
    watchdog = subprocess.run(["systemctl", "is-active", "vnxdna-governor.service"], capture_output=True, text=True).stdout.strip()
    laya_ok = all((paths.LAYA_CONFIG / f).exists() for f in ("policy.yaml", "agents.yaml", "models.yaml", "resources.yaml",
                                                             "tools.yaml", "approvals.yaml", "release-gates.yaml"))
    mcp = mcp_health()
    tests, build = _last_gate("tests"), _last_gate("build")
    return {
        "cpu_percent": cpu_percent(0.3),
        "ram_percent": round(100 * (1 - m["MemAvailable"] / m["MemTotal"]), 1),
        "swap_percent": round(100 * (m["SwapTotal"] - m["SwapFree"]) / m["SwapTotal"], 1) if m.get("SwapTotal") else 0.0,
        "disk_percent": round(100 * d["used"] / d["total"], 1),
        "ollama": {"ready": models.ollama_ready(), "loaded": [x["name"] for x in models.loaded()]},
        "laya": {"ready": laya_ok},
        "harness": {"ready": paths.ARTIFACTS.exists(), "records": len(list((paths.ARTIFACTS / "harness").glob("*/*/record.json")))},
        "governor": {"watchdog": watchdog, "level": st.get("level", "OK"), "active_jobs": len(st.active())},
        "mcp": {"ready": mcp["ready"], "total": mcp["total"]},
        "queue": q,
        "git": _git(paths.REPO),
        "tests": tests.get("result") if tests else "UNKNOWN",
        "build": build.get("result") if build else "UNKNOWN",
    }


def render_status(s: dict[str, Any]) -> str:
    g = s["git"]
    rows = [("CPU", f"{s['cpu_percent']:.0f}%"), ("RAM", f"{s['ram_percent']:.0f}%"), ("SWAP", f"{s['swap_percent']:.0f}%"),
            ("DISK", f"{s['disk_percent']:.0f}%"),
            ("OLLAMA", ("READY" if s["ollama"]["ready"] else "DOWN") + (f" ({', '.join(s['ollama']['loaded'])} loaded)" if s["ollama"]["loaded"] else "")),
            ("LAYA", "READY" if s["laya"]["ready"] else "MISSING CONFIG"),
            ("HARNESS", f"READY ({s['harness']['records']} records)" if s["harness"]["ready"] else "NOT READY"),
            ("GOVERNOR", f"{s['governor']['watchdog'].upper()} level={s['governor']['level']} jobs={s['governor']['active_jobs']}"),
            ("MCP", f"{s['mcp']['ready']}/{s['mcp']['total']} READY"),
            ("QUEUE", f"READY {s['queue'].get('backend')} {s['queue'].get('depth')}" if s["queue"].get("ok") else f"DOWN {s['queue'].get('error')}"),
            ("GIT", ("CLEAN" if g["dirty_files"] == 0 else f"DIRTY ({g['dirty_files']} files)") + f" {g['branch']}@{g['head']}"),
            ("TESTS", s["tests"]), ("BUILD", s["build"])]
    return "VNX-DNA SYSTEM STATUS\n\n" + "\n".join(f"{k:<9} {v}" for k, v in rows) + "\n"


# ------------------------------------------------------------------------------------------------ doctor


def _check(name: str, ok: bool | None, detail: str, fix: str = "", warn_only: bool = False) -> dict[str, Any]:
    level = OK if ok else (WARN if warn_only or ok is None else FAIL)
    return {"check": name, "result": level, "detail": detail, "fix": "" if ok else fix}


def doctor(deep: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    sysc = system.load()
    checks.append(_check("system.yaml", system.path().exists(), str(system.path()), "vnxdna system init"))
    prof = profile.load()
    age_days = (time.time() - (paths.CONFIG / "hardware-capability.yaml").stat().st_mtime) / 86400 if prof else None
    checks.append(_check("hardware profile", age_days is not None and age_days < 30, f"age {age_days:.1f} d" if prof else "missing",
                         "vnxdna profile", warn_only=bool(prof)))
    tool_cfg = yaml.safe_load((paths.REPO / "config" / "toolchain.yaml").read_text())
    missing = [t for t in tool_cfg["required"] if not shutil.which(t)]
    checks.append(_check("toolchain (required)", not missing, f"missing: {missing}" if missing else f"{len(tool_cfg['required'])} tools present",
                         "see config/toolchain.yaml install hints"))
    opt_missing = [t for t in tool_cfg.get("optional", []) if not shutil.which(t)]
    checks.append(_check("toolchain (optional)", not opt_missing, f"missing: {opt_missing}" if opt_missing else "all present", warn_only=True))
    code, out = run([str(paths.VENV / "bin" / "python"), "-c", "import vnxdna, yaml, numpy, cryptography; print(vnxdna.__file__)"])
    checks.append(_check("ops venv", code == 0, out.splitlines()[-1] if out else str(code), "uv pip sync --require-hashes ops/requirements.lock"))
    code, out = run(["/root/.local/bin/uv", "pip", "check", "--python", str(paths.VENV / "bin" / "python")], timeout=60)
    checks.append(_check("venv dependency consistency", code == 0, out.splitlines()[-1] if out else "", "uv pip sync"))
    slice_ok = Path("/etc/systemd/system/vnxdna.slice").exists()
    checks.append(_check("governor slice", slice_ok, "/etc/systemd/system/vnxdna.slice", "vnxdna governor install"))
    wd = subprocess.run(["systemctl", "is-active", "vnxdna-governor.service"], capture_output=True, text=True).stdout.strip()
    checks.append(_check("governor watchdog", wd == "active", wd, "systemctl enable --now vnxdna-governor.service"))
    gs = governor.status()
    checks.append(_check("pressure level", gs["instant_level"] == "OK", f"{gs['instant_level']} {gs['triggers']}", "see vnxdna governor", warn_only=True))
    checks.append(_check("RAM above floor", gs["snapshot"]["mem_available"] > gs["floors"]["min_free_ram"],
                         f"{gs['snapshot']['mem_available'] >> 20} MiB available, floor {gs['floors']['min_free_ram'] >> 20} MiB"))
    checks.append(_check("disk above floor", gs["snapshot"]["disk_free"] > gs["floors"]["disk_floor"],
                         f"{gs['snapshot']['disk_free'] >> 30} GiB free, floor {gs['floors']['disk_floor'] >> 30} GiB",
                         "free space; nothing is deleted automatically"))
    checks += laya_config_checks()
    checks.append(_check("ollama", models.ollama_ready(), f"installed: {models.installed()}", "snap start ollama"))
    mb = paths.REPORTS / "model-benchmarks.json"
    checks.append(_check("model benchmarks", mb.exists(), str(mb), "vnxdna models bench", warn_only=True))
    cli = system.agent_cli()
    found = shutil.which(cli) if cli else None
    checks.append(_check("agent CLI", found is not None, str(found or cli or "not configured"),
                         "install the agent CLI and set agent.cli in system.yaml", warn_only=cli is None))
    mcp = mcp_health()
    checks.append(_check("MCP servers", mcp["ready"] == mcp["total"], f"{mcp['ready']}/{mcp['total']} connected: "
                         + ", ".join(f"{s['name']}={s['status']}" for s in mcp["servers"] if "Connected" not in s["status"]),
                         "see docs/MCP_REGISTRY.md (agents do not depend on any MCP server)", warn_only=True))
    from . import queue
    lq = queue.LocalQueue().health()
    checks.append(_check("queue: local", lq["ok"], json.dumps(lq.get("depth"))))
    try:
        rq = queue.RedisQueue(**{k: v for k, v in sysc["queue"]["redis"].items() if k in ("host", "port", "db")}).health()
    except Exception as exc:
        rq = {"ok": False, "error": repr(exc)}
    checks.append(_check("queue: redis", rq["ok"], json.dumps(rq.get("depth") or rq.get("error")), "systemctl start redis-server", warn_only=True))
    checks.append(_check("queue: sqs", None if not sysc["queue"]["sqs"]["enabled"] else True,
                         "disabled (optional cloud adapter)" if not sysc["queue"]["sqs"]["enabled"] else "enabled", warn_only=True))
    g = _git(paths.REPO)
    checks.append(_check("repository", g["dirty_files"] == 0, f"{g['branch']}@{g['head']} dirty={g['dirty_files']}",
                         "commit or stash work on a feature branch", warn_only=True))
    bl = paths.REPO / "docs" / "releases" / "V3_BASELINE.md"
    checks.append(_check("V3 baseline manifest", bl.exists(), str(bl), "vnxdna baseline"))
    bks = sorted(paths.BACKUPS.glob("*/MANIFEST.json"))
    age = (time.time() - bks[-1].stat().st_mtime) / 86400 if bks else None
    checks.append(_check("backup", age is not None and age < 7, f"latest {bks[-1].parent.name} ({age:.1f} d)" if bks else "none",
                         "vnxdna backup <repo paths>", warn_only=bool(bks)))
    from . import security
    perms = security.permission_checks()
    bad = [p["path"] for p in perms if not p["ok"]]
    checks.append(_check("secret file permissions", not bad, f"too open: {bad}" if bad else f"{len(perms)} paths ok", "chmod go-rwx <path>"))
    tracked = security.tracked_secret_files(paths.REPO)
    checks.append(_check("no secret files tracked", not tracked, str(tracked) if tracked else "none"))
    if deep:
        from . import harness
        for t in ("compileall", "pytest_ops", "dna_e2e", "bench_smoke"):
            r = harness.run_tool(t, paths.REPO, _doctor_dir())
            checks.append(_check(f"deep: {t}", r["result"] == "pass", f"{r['status']} exit={r['exit_code']} {r.get('seconds')} s; log {r['log']}"))
    summary = {k: sum(c["result"] == k for c in checks) for k in (OK, WARN, FAIL)}
    return {"summary": summary, "ok": summary[FAIL] == 0, "checks": checks}


def _doctor_dir() -> Path:
    d = paths.ARTIFACTS / "doctor" / time.strftime("%Y%m%d-%H%M%S")
    paths.ensure(d)
    return d


def laya_config_checks() -> list[dict[str, Any]]:
    """Cross-reference the LAYA YAML files: every referenced agent, model, tool, job class and prompt must exist."""
    out = []
    try:
        cfgs = {n: yaml.safe_load((paths.LAYA_CONFIG / f"{n}.yaml").read_text())
                for n in ("policy", "agents", "models", "resources", "tools", "approvals", "release-gates")}
    except Exception as exc:
        return [_check("LAYA config parse", False, repr(exc), "fix YAML syntax")]
    out.append(_check("LAYA config parse", True, "7 files"))
    errors = []
    agents = cfgs["agents"]["agents"]
    classes = cfgs["resources"]["job_classes"]
    tools = cfgs["tools"]["tools"]
    for tt, spec in cfgs["policy"]["task_types"].items():
        for a in (spec["agent"], spec.get("fallback")):
            if a and a not in agents:
                errors.append(f"task {tt}: unknown agent {a}")
        if spec["job_class"] not in classes:
            errors.append(f"task {tt}: unknown job class {spec['job_class']}")
        if spec["risk"] not in cfgs["policy"]["risk_levels"]:
            errors.append(f"task {tt}: unknown risk {spec['risk']}")
        if tt not in cfgs["models"]["task_requirements"]:
            errors.append(f"task {tt}: no model requirement")
    for r in cfgs["policy"]["classification"]["rules"]:
        if r["type"] not in cfgs["policy"]["task_types"]:
            errors.append(f"rule {r['match']}: unknown type {r['type']}")
    for a in agents:
        if not (paths.REPO / "ops" / "prompts" / f"{a}.md").exists():
            errors.append(f"agent {a}: prompt missing")
    for g, spec in cfgs["release-gates"]["gates"].items():
        for t in spec.get("tools", []):
            if t not in tools:
                errors.append(f"gate {g}: unknown tool {t}")
    out.append(_check("LAYA config cross-references", not errors, "; ".join(errors) if errors else "agents, models, tools, classes, prompts consistent"))
    return out


def render_doctor(d: dict[str, Any]) -> str:
    lines = ["VNX-DNA DOCTOR", ""]
    for c in d["checks"]:
        lines.append(f"[{c['result']}] {c['check']}: {c['detail']}" + (f"\n        fix: {c['fix']}" if c["fix"] and c["result"] != OK else ""))
    s = d["summary"]
    lines += ["", f"{s[OK]} passed, {s[WARN]} warnings, {s[FAIL]} failures"]
    return "\n".join(lines) + "\n"
