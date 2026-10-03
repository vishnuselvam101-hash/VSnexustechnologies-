"""`vnxdna` — the VNX-DNA engineering-environment CLI.

Operations commands are implemented here. Every other command (encode, decode, verify, store, restore, simulate,
sequence, …) is forwarded unchanged to the VNX-DNA library CLI (`vnx-dna`), so `vnxdna` is the single entry point.
`inspect` is an alias of the library's `info`. Machine-readable output: `--json` on every ops command.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

OPS = {"audit", "backup", "profile", "governor", "run", "models", "laya", "agent", "harness", "queue", "worker",
       "status", "doctor", "test", "e2e", "baseline", "release", "security", "report", "improve", "system", "experiment", "validate",
       "benchmark"}
ALIASES = {"inspect": "info"}


def _out(obj: Any, as_json: bool, text: str | None = None) -> None:
    print(json.dumps(obj, indent=2, default=str) if as_json or text is None else text)


def forward(argv: list[str]) -> int:
    """Run the library CLI from this checkout's src/ with the same interpreter."""
    from . import experiments
    argv = [ALIASES.get(argv[0], argv[0]), *argv[1:]] if argv else argv
    os.execve(sys.executable, [sys.executable, "-m", "vnxdna", *argv], experiments.cli_env())
    return 0  # unreachable


# ------------------------------------------------------------------------------------------------ handlers


def c_audit(a: argparse.Namespace) -> int:
    from . import audit
    out = audit.write(audit.collect(include_mcp=not a.no_mcp))
    _out({"written": str(out), "files": sorted(p.name for p in out.iterdir())}, a.json, f"audit written to {out}")
    return 0


def c_backup(a: argparse.Namespace) -> int:
    from . import backup
    r = backup.verify(Path(a.verify)) if a.verify else backup.verify(backup.backup([Path(x) for x in a.sources], a.label))
    _out(r, True)
    return 0 if r["ok"] else 1


def c_profile(a: argparse.Namespace) -> int:
    from . import profile
    prof = profile.build_profile(quick=a.quick)
    path = profile.write(prof)
    _out(prof, a.json, f"profile written to {path}\n" + "\n".join(f"  {k:24} {v['value']}" for k, v in prof["limits"].items()))
    return 0


def c_governor(a: argparse.Namespace) -> int:
    from . import governor
    if a.action == "install":
        _out(governor.install(enable_watchdog=not a.no_watchdog), True)
    elif a.action == "watch":
        governor.Watchdog().loop()
    elif a.action == "tick":
        _out(governor.Watchdog().tick(), True)
    else:
        _out(governor.status(), True)
    return 0


def c_run(a: argparse.Namespace) -> int:
    from . import governor
    cmd = a.command[1:] if a.command and a.command[0] == "--" else a.command
    if not cmd:
        print("usage: vnxdna run [--class C] [--mem SIZE] [--timeout S] -- COMMAND...", file=sys.stderr)
        return 2
    rec = governor.run(cmd, cls=a.cls, mem=governor.parse_size(a.mem) if a.mem else None, timeout=a.timeout, wait=not a.no_wait)
    if a.json:
        print(json.dumps(rec), file=sys.stderr)
    code = rec["exit_code"]
    return code if isinstance(code, int) and code >= 0 else 128 + abs(code or 1)


def c_models(a: argparse.Namespace) -> int:
    from . import models, paths
    if a.action == "bench":
        res = models.bench_all(include_remote=not a.no_remote, only=a.only or None)
        (paths.REPORTS / "model-benchmarks.md").write_text("# Model benchmarks (measured on this host)\n\n" + models.render_report(res))
        _out(res, a.json, models.render_report(res))
    elif a.action == "select":
        _out(models.select(a.task, allow_remote=not a.no_remote), True)
    else:
        _out({"ollama_ready": models.ollama_ready(), "installed": models.installed(), "loaded": [m["name"] for m in models.loaded()]}, True)
    return 0


def c_laya(a: argparse.Namespace) -> int:
    from . import laya
    if a.action == "approve":
        _out(laya.approve(a.text, a.by), True)
        return 0
    if a.action == "list":
        rows = laya.recent(a.limit)
        _out(rows, a.json, "\n".join(f"{r['decision_id']}  {r['status']:<30} {r['task_type'] or '':<16} {r['request']}" for r in rows))
        return 0
    if not a.text:
        print("usage: vnxdna laya run|plan \"request text\"", file=sys.stderr)
        return 2
    rec = laya.decide(a.text, task_type=a.type, requested_by=a.by, execute=a.action == "run", decision_id=a.decision_id,
                      use_model_classifier=not a.no_model, timeout=a.timeout)
    summary = f"decision {rec['decision_id']}: {rec['status']} — {rec.get('reason') or ''}\n" + \
              "\n".join(f"  {t['step']:<16} " + ", ".join(f"{k}={v}" for k, v in t.items() if k not in ("step", "at") and not isinstance(v, (list, dict)))[:160]
                        for t in rec["trace"])
    _out(rec, a.json, summary)
    return 0 if rec["status"] in ("accepted", "accepted_pending_human_review", "planned", "pending_approval") else 1


def c_agent(a: argparse.Namespace) -> int:
    from . import laya
    agents = laya.cfg("agents")["agents"]
    if a.action == "list" or not a.text:
        _out(agents, a.json, "\n".join(f"{n:<24} tiers={c['tiers']} tools={len(c['tools'])}  {', '.join(c['responsibilities'])}" for n, c in agents.items()))
        return 0
    policy = laya.cfg("policy")
    types = [t for t, s in policy["task_types"].items() if s["agent"] == a.action]
    if not types:
        print(f"no task type is owned by agent {a.action}", file=sys.stderr)
        return 2
    rec = laya.decide(a.text, task_type=a.type or types[0], requested_by=a.by, execute=not a.plan)
    _out(rec, a.json, f"decision {rec['decision_id']}: {rec['status']} — {rec.get('reason') or ''}")
    return 0


def c_harness(a: argparse.Namespace) -> int:
    from . import harness, paths
    if a.action == "tool":
        out = harness.task_dir(harness.Task(kind="tool", agent="cli", task_type="tool", input={"tool": a.name}))
        r = harness.run_tool(a.name, paths.REPO, out)
        _out(r, a.json, f"{r['tool']}: {r['result']} ({r['status']}, exit {r['exit_code']}) log {r['log']}")
        return 0 if r["result"] == "pass" else 1
    rows = harness.records(a.limit)
    _out(rows, a.json, "\n".join(f"{r['task_id']}  {r['result']:<8} {r['task_type']:<16} {r['agent']:<20} {r.get('failure_class') or ''}" for r in rows))
    return 0


def c_queue(a: argparse.Namespace) -> int:
    from . import queue
    q = queue.from_config({"backend": a.backend}) if a.backend else queue.from_config()
    if a.action == "put":
        _out({"id": q.put({"request": a.text, "task_type": a.type, "requested_by": "cli"}), "backend": q.name}, True)
    elif a.action == "selftest":
        mid = q.put({"selftest": True})
        got = q.get(timeout=5)
        ok = got is not None and got[1].get("selftest") is True
        if got:
            q.ack(got[0])
        _out({"backend": q.name, "put": mid, "roundtrip": ok, "depth": q.depth()}, True)
        return 0 if ok else 1
    else:
        _out(q.health(), True)
    return 0


def c_worker(a: argparse.Namespace) -> int:
    from . import worker
    _out(worker.run(max_messages=a.max), True)
    return 0


def c_status(a: argparse.Namespace) -> int:
    from . import observe
    s = observe.status()
    _out(s, a.json, observe.render_status(s))
    return 0


def c_doctor(a: argparse.Namespace) -> int:
    from . import observe
    d = observe.doctor(deep=a.deep)
    _out(d, a.json, observe.render_doctor(d))
    return 0 if d["ok"] else 1


def c_test(a: argparse.Namespace) -> int:
    from . import harness, paths, release
    tools = {"fast": ["pytest_fast"], "full": ["pytest_full"], "ops": ["pytest_ops"], "all": ["pytest_full", "pytest_ops"]}[a.suite]
    out = harness.task_dir(harness.Task(kind="tool", agent="test_engineer", task_type="test_run", input={"suite": a.suite}))
    res = [harness.run_tool(t, paths.REPO, out) for t in tools]
    ok = all(r["result"] == "pass" for r in res)
    release._gate_record("tests", "PASS" if ok else "FAIL", [{k: r[k] for k in ("tool", "result", "exit_code", "log")} for r in res])
    _out(res, a.json, "\n".join(f"{r['tool']}: {r['result'].upper()} (exit {r['exit_code']}, {r['seconds']} s)\n{r['tail'][-600:]}" for r in res))
    return 0 if ok else 1


def c_e2e(a: argparse.Namespace) -> int:
    from . import release
    r = release.e2e(size=a.size)
    _out(r, a.json, f"end-to-end {r['roundtrip']['outcome']}: SHA-256 identical={r['sha256_identical']}, reproducible={r['reproducible']}")
    return 0 if r["ok"] else 1


def c_baseline(a: argparse.Namespace) -> int:
    from . import release
    if a.measure:
        _out(release.run_baseline_measurements(), True)
    m = release.baseline()
    _out(m, a.json, f"V3 baseline manifest written: tests {m['tests']}, tag matches {m['tag_matches']}")
    return 0


def c_release(a: argparse.Namespace) -> int:
    from . import release
    r = release.check(a.version, a.cls or None)
    _out(r, a.json, f"{a.version}: {'ACCEPTED' if r['accepted'] else 'NOT ACCEPTED'}\n" + "\n".join(f"  {g['gate']:<28} {g['result']}" for g in r["gates"]) + f"\nreport: {r['report']}")
    return 0 if r["accepted"] else 1


def c_security(a: argparse.Namespace) -> int:
    from . import security
    r = security.run_all()
    _out(r, a.json, f"security: {'OK' if r['ok'] else 'FINDINGS'} — gitleaks {r['gitleaks']['findings']}, "
                    f"vulnerable deps {r['pip_audit']['vulnerabilities']}, SBOM components {r['sbom']['components']}, "
                    f"tracked secret files {len(r['tracked_secret_files'])}, permission issues {sum(not p['ok'] for p in r['permissions'])}, "
                    f"subprocess/eval sites {len(r['subprocess_audit'])}")
    return 0 if r["ok"] else 1


def c_report(a: argparse.Namespace) -> int:
    from . import report
    p = report.environment_ready()
    _out({"report": str(p)}, a.json, f"report written to {p}")
    return 0


def c_validate(a: argparse.Namespace) -> int:
    from . import validate
    r = validate.run(only=a.only)
    _out(r, a.json, "\n".join(f"[{x['result']}] {x['check']} ({x['seconds']} s)" for x in r["results"]) + f"\noverall: {'PASS' if r['pass'] else 'FAIL'}")
    return 0 if r["pass"] else 1


def c_improve(a: argparse.Namespace) -> int:
    from . import improve
    _out(improve.run(), True)
    return 0


def c_system(a: argparse.Namespace) -> int:
    from . import system
    if a.action == "init":
        _out({"system_yaml": str(system.init(force=a.force))}, True)
    else:
        _out(system.load(), True)
    return 0


def c_experiment(a: argparse.Namespace) -> int:
    from . import experiments
    if a.list or not a.suite:
        from . import paths
        _out(sorted(p.stem for p in (paths.REPO / "experiments").glob("*.yaml")), True)
        return 0
    r = experiments.run_suite(a.suite, workers=a.workers, keep=a.keep, limit=a.limit)
    _out(r, a.json, f"{a.suite}: {r['outcomes']} undetected corruption={r['undetected_corruption']} pass={r['pass']}\n{r['run_dir']}/summary.md")
    return 0 if r["pass"] else 1


def c_benchmark(a: argparse.Namespace) -> int:
    from . import benchmarks
    if a.system:
        r = benchmarks.run_system(quick=not a.full)
        _out(r, a.json, "\n".join(f"{k:24} {v}" for k, v in r["limits"].items()))
        return 0
    r = benchmarks.run_dna(a.suite, workers=a.workers)
    _out(r, a.json, benchmarks.render(r) + f"\n{r['run_dir']}")
    return 0 if r["pass"] else 1


# ------------------------------------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vnxdna", description="VNX-DNA engineering environment. Library commands "
                                "(store, encode, decode, verify, restore, inspect, simulate, sequence, …) are forwarded to vnx-dna.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name: str, fn: Any, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        sp.set_defaults(fn=fn)
        return sp

    s = cmd("audit", c_audit, "read-only environment audit → /opt/vnx-dna/environment-audit")
    s.add_argument("--no-mcp", action="store_true")
    s = cmd("backup", c_backup, "git bundle + tar backups with SHA-256 manifest")
    s.add_argument("sources", nargs="*")
    s.add_argument("--label", default="manual")
    s.add_argument("--verify", metavar="BACKUP_DIR")
    s = cmd("profile", c_profile, "measure hardware → config/hardware-capability.yaml")
    s.add_argument("--quick", action="store_true")
    s = cmd("governor", c_governor, "resource governor: status | install | watch | tick")
    s.add_argument("action", nargs="?", default="status", choices=["status", "install", "watch", "tick"])
    s.add_argument("--no-watchdog", action="store_true")
    s = cmd("run", c_run, "run a command under the governor (admission + vnxdna.slice scope)")
    s.add_argument("--class", dest="cls", default="misc")
    s.add_argument("--mem")
    s.add_argument("--timeout", type=float)
    s.add_argument("--no-wait", action="store_true")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s = cmd("models", c_models, "model registry: list | bench | select TASK")
    s.add_argument("action", nargs="?", default="list", choices=["list", "bench", "select"])
    s.add_argument("task", nargs="?", default="classification")
    s.add_argument("--only", action="append")
    s.add_argument("--no-remote", action="store_true")
    s = cmd("laya", c_laya, "LAYA decisions: run | plan TEXT, approve ID, list")
    s.add_argument("action", choices=["run", "plan", "approve", "list"])
    s.add_argument("text", nargs="?", help="request text (run/plan) or decision id (approve)")
    s.add_argument("--type", help="explicit task type (skips classification)")
    s.add_argument("--by", default=os.environ.get("USER", "owner"))
    s.add_argument("--decision-id", help="re-run a decision that has been approved")
    s.add_argument("--no-model", action="store_true", help="rules-only classification")
    s.add_argument("--timeout", type=float)
    s.add_argument("--limit", type=int, default=20)
    s = cmd("agent", c_agent, "list agents, or send TEXT to an agent role through LAYA")
    s.add_argument("action", nargs="?", default="list")
    s.add_argument("text", nargs="?")
    s.add_argument("--type")
    s.add_argument("--by", default=os.environ.get("USER", "owner"))
    s.add_argument("--plan", action="store_true", help="decide only, do not execute")
    s = cmd("harness", c_harness, "execution records, or run one Tier-0 tool: harness records | harness tool NAME")
    s.add_argument("action", nargs="?", default="records", choices=["records", "tool"])
    s.add_argument("name", nargs="?")
    s.add_argument("--limit", type=int, default=20)
    s = cmd("queue", c_queue, "task queue: health | put TEXT | selftest")
    s.add_argument("action", nargs="?", default="health", choices=["health", "put", "selftest"])
    s.add_argument("text", nargs="?")
    s.add_argument("--type")
    s.add_argument("--backend", choices=["local", "redis", "sqs"])
    s = cmd("worker", c_worker, "process queued LAYA requests")
    s.add_argument("--max", type=int)
    cmd("status", c_status, "one-screen system status")
    s = cmd("doctor", c_doctor, "environment checks with fixes (--deep runs e2e, ops tests, bench smoke)")
    s.add_argument("--deep", action="store_true")
    s = cmd("test", c_test, "run the repository tests under the governor")
    s.add_argument("suite", nargs="?", default="fast", choices=["fast", "full", "ops", "all"])
    s = cmd("e2e", c_e2e, "small end-to-end encode/decode with SHA-256 check + reproducibility")
    s.add_argument("--size", default="64KB")
    s = cmd("baseline", c_baseline, "write the V3 baseline manifest (docs/releases/V3_BASELINE.md)")
    s.add_argument("--measure", action="store_true", help="first run benchmarks + e2e against the pristine v3.0.0 install")
    s = cmd("release", c_release, "run acceptance gates: release check --version V4")
    s.add_argument("action", choices=["check"])
    s.add_argument("--version", required=True)
    s.add_argument("--class", dest="cls", action="append", choices=["research", "commercial"])
    cmd("security", c_security, "gitleaks, pip-audit, SBOM, permissions, subprocess audit")
    cmd("report", c_report, "write /opt/vnx-dna/reports/ENVIRONMENT_READY.md")
    s = cmd("validate", c_validate, "the 13 environment validation checks → reports/validation.json")
    s.add_argument("--only", action="append", help="check number(s), e.g. --only 7 --only 11")
    cmd("improve", c_improve, "observe → propose (writes reports/proposals; never edits code)")
    s = cmd("system", c_system, "central config: system show | system init")
    s.add_argument("action", nargs="?", default="show", choices=["show", "init"])
    s.add_argument("--force", action="store_true")
    s = cmd("experiment", c_experiment, "run an experiment suite (experiments/*.yaml); `experiment run …` is the library command")
    s.add_argument("suite", nargs="?")
    s.add_argument("--list", action="store_true")
    s.add_argument("--workers", type=int)
    s.add_argument("--keep", action="store_true")
    s.add_argument("--limit", type=int)
    s = cmd("benchmark", c_benchmark, "--system (hardware) or --dna --suite smoke|standard|large; other `benchmark …` → library")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--system", action="store_true")
    g.add_argument("--dna", action="store_true")
    s.add_argument("--suite", default="standard")
    s.add_argument("--workers", type=int)
    s.add_argument("--full", action="store_true", help="full (not quick) system profile")
    return p


def route(argv: list[str]) -> str:
    """'ops' or 'library' for a command line."""
    if not argv or argv[0].startswith("-"):
        return "ops"
    head = argv[0]
    if head == "benchmark":
        return "ops" if {"--system", "--dna"} & set(argv) else "library"
    if head == "experiment":
        return "library" if len(argv) > 1 and argv[1] == "run" else "ops"
    return "ops" if head in OPS else "library"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if route(argv) == "library":
        return forward(argv)
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    # argparse leaves a positional that follows options unparsed (`laya run --type X "text"`); accept it as the text.
    if extra:
        if getattr(args, "text", "missing") is None and not any(e.startswith("-") for e in extra):
            args.text = " ".join(extra)
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
