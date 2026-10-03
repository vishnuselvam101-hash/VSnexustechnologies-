"""PHASE 26: self-improvement loop — OBSERVE → PROPOSE (→ IMPLEMENT → TEST → BENCHMARK → REVIEW → ACCEPT via LAYA).

`vnxdna improve` only observes and writes proposals (reports/proposals/YYYY-MM-DD.md + .json). It never edits code
and never merges. A human (or `vnxdna laya run --from-proposal ID`) turns a proposal into a LAYA request, which then
goes through the normal gates. All observations are deterministic (Tier 0): no model is called.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from . import harness, paths, security


def _slowest_tests(junit: Path, n: int = 10) -> list[dict[str, Any]]:
    if not junit.exists():
        return []
    import xml.etree.ElementTree as ET
    cases = [(float(c.get("time", 0)), f"{c.get('classname')}::{c.get('name')}") for c in ET.parse(junit).getroot().iter("testcase")]
    return [{"test": name, "seconds": round(t, 2)} for t, name in sorted(cases, reverse=True)[:n]]


def _failing(junit: Path) -> list[str]:
    if not junit.exists():
        return []
    import xml.etree.ElementTree as ET
    out = []
    for c in ET.parse(junit).getroot().iter("testcase"):
        if c.find("failure") is not None or c.find("error") is not None:
            out.append(f"{c.get('classname')}::{c.get('name')}")
    return out


def _doc_gaps(repo: Path) -> list[str]:
    """CLI commands not mentioned in docs/CLI.md; modules without a docstring."""
    gaps = []
    cli = (repo / "src" / "vnxdna" / "cli.py").read_text()
    doc = (repo / "docs" / "CLI.md").read_text()
    for cmd in re.findall(r'@app\.command\("([a-z-]+)"\)', cli):
        if f"`{cmd}" not in doc:
            gaps.append(f"docs/CLI.md does not document `{cmd}`")
    for f in sorted((repo / "src" / "vnxdna").rglob("*.py")):
        txt = f.read_text()
        if txt.strip() and not re.match(r'\s*(#.*\n)*\s*(from __future__.*\n)?\s*"""', txt) and "legacy" not in f.parts:
            gaps.append(f"{f.relative_to(repo)} has no module docstring")
    return gaps[:30]


def observe() -> dict[str, Any]:
    junit = paths.REPORTS / "v3-baseline" / "junit.xml"
    obs: dict[str, Any] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                           "repository": harness.git_state(paths.REPO)}
    obs["failing_tests"] = _failing(junit)
    obs["slow_tests"] = _slowest_tests(junit)
    bl = paths.REPO / "docs" / "releases" / "v3-baseline.json"
    if bl.exists():
        b = json.loads(bl.read_text()).get("benchmark_standard") or {}
        sizes = [s for s in b.get("sizes", []) if "throughput_MBps" in s]
        if sizes:
            big = max(sizes, key=lambda s: s["bytes"])
            obs["throughput_at_largest"] = {"size": big["size_label"], **big["throughput_MBps"]}
            slowest = min(big["throughput_MBps"].items(), key=lambda kv: kv[1] or 1e9)
            obs["slowest_stage"] = {"stage": slowest[0], "MBps": slowest[1], "size": big["size_label"]}
            obs["peak_rss_largest"] = max(big["peak_rss_bytes"].values())
            obs["net_bits_per_base"] = big.get("net_bits_per_base")
        if b.get("recovery"):
            obs["recovery"] = b["recovery"]
    obs["documentation_gaps"] = _doc_gaps(paths.REPO)
    obs["security"] = {"subprocess_audit": security.subprocess_audit(paths.REPO / "src")[:20]}
    r = subprocess.run([str(paths.VENV / "bin" / "ruff"), "check", "--statistics", "src"], capture_output=True, text=True, cwd=paths.REPO)
    obs["lint"] = r.stdout.strip().splitlines()[:20]
    return obs


def propose(obs: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic rules from observations to proposals. Each proposal names its evidence and its acceptance gate."""
    props: list[dict[str, Any]] = []
    for t in obs["failing_tests"]:
        props.append({"kind": "failing_test", "priority": 1, "title": f"Fix failing test {t}", "evidence": "baseline junit.xml",
                      "laya_request": f"debug the failing test {t}", "gate": "TEST"})
    if obs.get("slowest_stage") and obs["slowest_stage"]["MBps"] is not None and obs["slowest_stage"]["MBps"] < 5:
        s = obs["slowest_stage"]
        props.append({"kind": "performance", "priority": 2, "title": f"`{s['stage']}` is the slowest stage ({s['MBps']} MB/s at {s['size']})",
                      "evidence": "docs/releases/v3-baseline.json benchmark_standard", "gate": "PERFORMANCE",
                      "laya_request": f"profile the {s['stage']} stage and propose an optimisation; benchmark before/after with `vnxdna benchmark --dna --suite standard`",
                      "status": "HYPOTHESIS: a profile is needed before any change"})
    for t in obs["slow_tests"][:3]:
        if t["seconds"] > 30:
            props.append({"kind": "test_speed", "priority": 3, "title": f"Slow test {t['test']} ({t['seconds']} s)",
                          "evidence": "baseline junit.xml", "gate": "TEST",
                          "laya_request": f"check whether {t['test']} should be marked slow or made faster without weakening it"})
    for g in obs["documentation_gaps"][:10]:
        props.append({"kind": "documentation", "priority": 4, "title": g, "evidence": "static check", "gate": "DOCUMENTATION",
                      "laya_request": f"documentation: {g}"})
    for h in obs["security"]["subprocess_audit"][:5]:
        props.append({"kind": "security", "priority": 2, "title": f"Review {h['file']}:{h['line']} ({h['code'][:60]})",
                      "evidence": "subprocess/eval audit", "gate": "SECURITY", "laya_request": f"security review of {h['file']}:{h['line']}"})
    if obs["lint"]:
        props.append({"kind": "lint", "priority": 5, "title": f"{len(obs['lint'])} ruff rule groups report findings in src/",
                      "evidence": "ruff --statistics", "gate": "LINT", "laya_request": "lint fix src"})
    for i, p in enumerate(sorted(props, key=lambda p: int(p["priority"])), 1):  # type: ignore[call-overload]
        p["id"] = f"P{dt.date.today():%Y%m%d}-{i:02d}"
    return sorted(props, key=lambda p: int(p["priority"]))  # type: ignore[call-overload]


def run() -> dict[str, Any]:
    obs = observe()
    props = propose(obs)
    d = paths.REPORTS / "proposals"
    paths.ensure(d)
    stem = d / dt.date.today().isoformat()
    stem.with_suffix(".json").write_text(json.dumps({"observations": obs, "proposals": props}, indent=2, default=str) + "\n")
    md = ["# Improvement proposals " + dt.date.today().isoformat(), "",
          "*OBSERVE → PROPOSE only. Nothing here is implemented or merged automatically; each item becomes a LAYA request "
          "and must pass its gate.*", "", "| id | priority | kind | proposal | gate | evidence |", "|---|---|---|---|---|---|"]
    md += [f"| {p['id']} | {p['priority']} | {p['kind']} | {p['title']} | {p['gate']} | {p['evidence']} |" for p in props]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n")
    return {"proposals": len(props), "report": str(stem.with_suffix(".md"))}
