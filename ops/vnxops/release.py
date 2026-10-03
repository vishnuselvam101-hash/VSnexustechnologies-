"""PHASES 11, 18: V3 baseline, end-to-end checks and version acceptance gates.

* ``e2e()`` — small store→encode→sequence→consensus→decode→restore run with SHA-256 reconstruction check, plus a
  reproducibility check (same seed twice ⇒ identical strand-pool SHA-256).
* ``baseline()`` — records the protected V3 baseline (commit, tag, test result, benchmark smoke result, known
  limitations) to docs/releases/V3_BASELINE.md + reports/v3-baseline/manifest.json. The baseline is measured from
  the pristine v3.0.0 worktree/venv, never from a development checkout.
* ``check(version)`` — runs every applicable gate in config/laya/release-gates.yaml and writes
  release/<VERSION>_ACCEPTANCE_REPORT.md. Human-only gates are reported as REQUIRES_HUMAN_REVIEW, never PASS.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from . import benchmarks, experiments, harness, paths, security
from .experiments import _sha, _stage, cli_argv

BASELINE_DIR = paths.HOME / "baseline" / "v3.0.0"
BASELINE_VENV = paths.HOME / "baseline" / "v3.0.0-venv"
BASELINE_REPORTS = paths.REPORTS / "v3-baseline"


def e2e(size: str = "64KB", seed: int = 7) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="e2e-", dir=paths.run_dir()) as tmp:
        t = Path(tmp)
        trial = {"name": "e2e", "seed": seed, "size": size, "coverage": 6, "substitution_rate": 0.004,
                 "insertion_rate": 0.0005, "deletion_rate": 0.0005, "dropout_rate": 0.01}
        r = experiments.run_trial(trial, t / "trial", workers=2)
        hashes = []
        for i in range(2):
            w = t / f"repro{i}"
            w.mkdir()
            log = w / "log"
            for argv in (["benchmark", "generate", "--size", "16KB", "--seed", "3", "-o", "in.dat"],
                         ["store", "in.dat", "-o", "a.vxdna", "--no-encrypt", "--workers", "1"],
                         ["encode", "a.vxdna", "-o", "s.fasta", "--workers", "1"],
                         ["sequence", "s.fasta", "-o", "r.fastq", "--seed", "11", "--coverage", "4", "--substitution-rate", "0.01"]):
                _stage([*cli_argv(), *argv], w, log)
            hashes.append({f: _sha(w / f) if (w / f).exists() else None for f in ("a.vxdna", "s.fasta", "r.fastq")})
    repro = hashes[0] == hashes[1] and all(hashes[0].values())
    return {"roundtrip": {k: r[k] for k in ("outcome", "input_sha256", "output_sha256", "reads", "runtime_s", "peak_rss_bytes",
                                             "failed_stage", "error_model")},
            "sha256_identical": r["outcome"] == "RECOVERED" and r["input_sha256"] == r["output_sha256"],
            "reproducible": repro, "repro_hashes": hashes[0], "ok": r["outcome"] == "RECOVERED" and repro}


def _git(*args: str, repo: Path = paths.REPO) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True).stdout.strip()


def _junit_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    tot: dict[str, Any] = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    tot["time_s"] = round(sum(float(s.get("time", 0)) for s in suites), 1)
    tot["passed"] = tot["tests"] - tot["failures"] - tot["errors"] - tot["skipped"]
    return tot


def baseline() -> dict[str, Any]:
    """Assemble the V3 baseline manifest from measurements taken on the pristine v3.0.0 worktree."""
    paths.ensure(BASELINE_REPORTS)
    commit = _git("rev-parse", "HEAD", repo=BASELINE_DIR)
    tag_commit = _git("rev-parse", "v3.0.0^{commit}", repo=BASELINE_DIR)
    junit = _junit_summary(BASELINE_REPORTS / "junit.xml")
    log = (BASELINE_REPORTS / "pytest.log").read_text(errors="replace") if (BASELINE_REPORTS / "pytest.log").exists() else ""
    bench_file = BASELINE_REPORTS / "bench-smoke.json"
    std_file = BASELINE_REPORTS / "bench-standard.json"
    bench = json.loads(bench_file.read_text()) if bench_file.exists() else None
    std = json.loads(std_file.read_text()) if std_file.exists() else None
    e2e_file = BASELINE_REPORTS / "e2e.json"
    e2e_res = json.loads(e2e_file.read_text()) if e2e_file.exists() else None
    clean = _git("status", "--porcelain", repo=BASELINE_DIR) == ""
    lim = (BASELINE_DIR / "docs" / "LIMITATIONS.md").read_text()
    lim_items = [line.strip()[2:] for line in lim.splitlines() if line.strip().startswith(("- ", "* "))][:40]
    manifest = {"schema": "vnxdna.baseline/1", "version": "3.0.0", "tag": "v3.0.0", "commit": commit,
                "tag_commit": tag_commit, "tag_matches": commit == tag_commit, "worktree_clean": clean,
                "worktree": str(BASELINE_DIR), "venv": str(BASELINE_VENV),
                "measured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "tests": junit, "test_exit": (re.findall(r"exit=(\d+)", log) or [None])[-1],
                "benchmark_smoke": bench, "benchmark_standard": std, "e2e": e2e_res,
                "known_limitations_source": "docs/LIMITATIONS.md @ v3.0.0", "known_limitations": lim_items}
    (BASELINE_REPORTS / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rel = paths.REPO / "docs" / "releases"
    paths.ensure(rel)
    (rel / "v3-baseline.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (rel / "V3_BASELINE.md").write_text(render_baseline(manifest))
    return manifest


def render_baseline(m: dict[str, Any]) -> str:
    t = m["tests"] or {}
    lines = ["# V3 baseline (protected)", "",
             "*Generated by `vnxdna baseline` from measurements on the pristine `v3.0.0` worktree "
             f"(`{m['worktree']}`, its own non-editable venv). Never edit by hand; re-run the command.*", "",
             "| Item | Value |", "|---|---|",
             f"| Version | {m['version']} |", f"| Tag | `{m['tag']}` → `{m['tag_commit']}` |",
             f"| Measured commit | `{m['commit']}` (matches tag: {m['tag_matches']}, worktree clean: {m['worktree_clean']}) |",
             f"| Measured at | {m['measured_at']} |",
             f"| Test suite | {t.get('passed', '?')} passed, {t.get('failures', '?')} failed, {t.get('errors', '?')} errors, "
             f"{t.get('skipped', '?')} skipped of {t.get('tests', '?')} in {t.get('time_s', '?')} s (exit {m['test_exit']}) |"]
    if m.get("e2e"):
        e = m["e2e"]
        lines.append(f"| End-to-end | {e['roundtrip']['outcome']}, SHA-256 identical: {e['sha256_identical']}, reproducible: {e['reproducible']} |")
    lines.append("")
    for key, title in (("benchmark_smoke", "Benchmark: smoke"), ("benchmark_standard", "Benchmark: standard")):
        if m.get(key):
            lines += [f"## {title}", "", benchmarks.render(m[key]).split("\n", 2)[2]]
    lines += ["## Rules", "",
              "- `v3.0.0` is never moved and `main` is not modified by environment work.",
              "- V4+ work happens on `development` / `feature/*` / `experiment/*` branches.",
              "- REGRESSION and PERFORMANCE gates compare against `docs/releases/v3-baseline.json` with the thresholds in",
              "  `config/laya/release-gates.yaml`.", "",
              "## Known limitations at V3 (from docs/LIMITATIONS.md @ v3.0.0)", ""]
    lines += [f"- {x}" for x in m["known_limitations"]] or ["- (none listed)"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ gates


def _gate_record(name: str, result: str, detail: Any) -> dict[str, Any]:
    rec = {"gate": name, "result": result, "detail": detail, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "commit": _git("rev-parse", "HEAD")}
    d = paths.REPORTS / "gates"
    paths.ensure(d)
    (d / f"{name.lower()}.json").write_text(json.dumps(rec, indent=2, default=str) + "\n")
    return rec


def _tools(names: list[str], out: Path) -> tuple[bool, list[dict[str, Any]]]:
    res = [harness.run_tool(n, paths.REPO, out) for n in names]
    return all(r["result"] == "pass" for r in res), [{k: r.get(k) for k in ("tool", "result", "exit_code", "status", "seconds", "log")} for r in res]


def regression_vs_baseline(current: dict[str, Any], base: dict[str, Any], thr: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare two benchmark results size by size. Returns violations (empty list = pass)."""
    v = []
    bsz = {s["size_label"]: s for s in base.get("sizes", []) if "throughput_MBps" in s}
    for s in current.get("sizes", []):
        b = bsz.get(s["size_label"])
        if not b or "throughput_MBps" not in s:
            continue
        for stage, val in s["throughput_MBps"].items():
            bv = b["throughput_MBps"].get(stage)
            if bv and val is not None and s["bytes"] >= 100_000 and val < bv * (1 - thr["throughput_max_regression"]):
                v.append({"size": s["size_label"], "metric": f"{stage} MB/s", "baseline": bv, "current": val})
        if b.get("net_bits_per_base") and s.get("net_bits_per_base") is not None and \
                s["net_bits_per_base"] < b["net_bits_per_base"] * (1 - thr["dna_density_max_regression"]) - 1e-9:
            v.append({"size": s["size_label"], "metric": "net bits/base", "baseline": b["net_bits_per_base"], "current": s["net_bits_per_base"]})
        brss, crss = max(b["peak_rss_bytes"].values()), max(s["peak_rss_bytes"].values())
        if crss > brss * (1 + thr["peak_rss_max_regression"]) and crss - brss > 32 * 2**20:
            v.append({"size": s["size_label"], "metric": "peak RSS", "baseline": brss, "current": crss})
    rc, rb = current.get("recovery"), base.get("recovery")
    if rc and rc["undetected_corruption"] > thr["undetected_corruption_max"]:
        v.append({"metric": "undetected corruption", "current": rc["undetected_corruption"]})
    if rc and rb and rc["recovery_rate"] < min(rb["recovery_rate"], thr["recovery_rate_min"]):
        v.append({"metric": "recovery rate", "baseline": rb["recovery_rate"], "current": rc["recovery_rate"]})
    return v


def check(version: str, classes: list[str] | None = None) -> dict[str, Any]:
    gates_cfg = yaml.safe_load((paths.LAYA_CONFIG / "release-gates.yaml").read_text())
    vclasses = set(classes or [])
    vkey = version.upper().split(".")[0]
    for cls, members in gates_cfg["version_classes"].items():
        if vkey in members:
            vclasses.add(cls)
    out = paths.ARTIFACTS / "release" / f"{version}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    paths.ensure(out)
    results = []
    base_file = paths.REPO / "docs" / "releases" / "v3-baseline.json"
    base = json.loads(base_file.read_text()) if base_file.exists() else None
    for name, g in gates_cfg["gates"].items():
        if "required_for" in g and g["required_for"] not in vclasses:
            continue
        if name == "BUILD":
            ok, det = _tools(["compileall"], out)
            h = subprocess.run([*cli_argv(), "--help"], capture_output=True, env=experiments.cli_env())
            ok = ok and h.returncode == 0
            det.append({"check": "vnx-dna --help", "exit": h.returncode})
            results.append(_gate_record(name, "PASS" if ok else "FAIL", det))
            _gate_record("build", "PASS" if ok else "FAIL", det)
        elif name == "TEST":
            ok, det = _tools(["pytest_full", "pytest_ops"], out)
            results.append(_gate_record(name, "PASS" if ok else "FAIL", det))
            _gate_record("tests", "PASS" if ok else "FAIL", det)
        elif name == "INTEGRATION":
            r = e2e()
            results.append(_gate_record(name, "PASS" if r["sha256_identical"] else "FAIL", r["roundtrip"]))
        elif name == "REPRODUCIBILITY":
            r = e2e(size="16KB")
            results.append(_gate_record(name, "PASS" if r["reproducible"] else "FAIL", r["repro_hashes"]))
        elif name in ("PERFORMANCE", "REGRESSION"):
            cur = benchmarks.run_dna("smoke")
            if base is None or not base.get("benchmark_smoke"):
                results.append(_gate_record(name, "FAIL", "no V3 baseline benchmark to compare against (run `vnxdna baseline`)"))
                continue
            viol = regression_vs_baseline(cur, base["benchmark_smoke"], gates_cfg["benchmark_thresholds"])
            ok = cur["pass"] and not viol
            results.append(_gate_record(name, "PASS" if ok else "FAIL", {"violations": viol, "run": cur["run_dir"]}))
        elif name == "SECURITY":
            s = security.run_all()
            ok = s["gitleaks"]["findings"] == 0 and s["pip_audit"]["vulnerabilities"] == 0 and not s["tracked_secret_files"]
            results.append(_gate_record(name, "PASS" if ok else "FAIL", {"gitleaks": s["gitleaks"]["findings"],
                                        "vulns": s["pip_audit"]["ids"], "tracked_secret_files": s["tracked_secret_files"]}))
        elif name == "LINT":
            ok, det = _tools(["ruff"], out)
            results.append(_gate_record(name, "PASS" if ok else "FAIL", det))
        elif name == "DOCUMENTATION":
            chlog = (paths.REPO / "CHANGELOG.md").read_text()
            ver = version.lstrip("vV")
            doc = {"changelog_mentions_version": ver in chlog, "limitations_doc": (paths.REPO / "docs" / "LIMITATIONS.md").exists()}
            results.append(_gate_record(name, "PASS" if all(doc.values()) else "FAIL", doc))
        else:  # SCIENTIFIC_VALIDATION, COMMERCIAL_READINESS_REVIEW
            results.append(_gate_record(name, "REQUIRES_HUMAN_REVIEW", g.get("check")))
    accepted = all(r["result"] == "PASS" for r in results)
    report = render_acceptance(version, results, accepted, vclasses)
    rel = paths.REPO / "release"
    paths.ensure(rel)
    p = rel / f"{version.upper()}_ACCEPTANCE_REPORT.md"
    p.write_text(report)
    return {"version": version, "accepted": accepted, "report": str(p), "gates": [{k: r[k] for k in ("gate", "result")} for r in results]}


def render_acceptance(version: str, results: list[dict[str, Any]], accepted: bool, classes: set[str]) -> str:
    lines = [f"# {version.upper()} acceptance report", "",
             f"*Generated by `vnxdna release check --version {version}` at {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M UTC} "
             f"on commit `{_git('rev-parse', 'HEAD')}`. Version classes: {sorted(classes) or ['standard']}.*", "",
             f"**Decision: {'ACCEPTED' if accepted else 'NOT ACCEPTED'}** — a version is accepted only when every applicable gate is PASS.", "",
             "| Gate | Result |", "|---|---|"]
    lines += [f"| {r['gate']} | {r['result']} |" for r in results]
    lines += ["", "## Details", ""]
    for r in results:
        lines += [f"### {r['gate']}: {r['result']}", "", "```json", json.dumps(r["detail"], indent=2, default=str)[:3000], "```", ""]
    return "\n".join(lines)


def run_baseline_measurements() -> dict[str, Any]:
    """Benchmarks + e2e against the pristine baseline install (VNXDNA_CLI → baseline venv)."""
    env_cli = str(BASELINE_VENV / "bin" / "vnx-dna")
    if not Path(env_cli).exists():
        raise SystemExit("baseline venv missing: see docs/releases/V3_BASELINE.md for how it is created")
    old = os.environ.get("VNXDNA_CLI")
    experiments.CLI_ENV = env_cli
    try:
        paths.ensure(BASELINE_REPORTS)
        smoke = benchmarks.run_dna("smoke")
        (BASELINE_REPORTS / "bench-smoke.json").write_text(json.dumps(smoke, indent=2) + "\n")
        std = benchmarks.run_dna("standard")
        (BASELINE_REPORTS / "bench-standard.json").write_text(json.dumps(std, indent=2) + "\n")
        e = e2e()
        (BASELINE_REPORTS / "e2e.json").write_text(json.dumps(e, indent=2) + "\n")
    finally:
        experiments.CLI_ENV = old
    return {"smoke": smoke["pass"], "standard": std["pass"], "e2e": e["ok"]}


__all__ = ["e2e", "baseline", "check", "run_baseline_measurements", "regression_vs_baseline", "shutil"]
