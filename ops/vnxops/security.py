"""PHASE 14: security checks — secret scanning, permissions, dependency audit, SBOM, safe paths.

The built-in secret scanner is a fast first pass (used by LAYA's review on every agent patch); gitleaks is the
authoritative scan for releases. Findings are redacted: only the rule, file and line are recorded, never the value.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from . import paths, system

SECRET_RULES = [
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("llm-provider-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("generic-assignment", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]")),
]


def scan_text(text: str, source: str = "<text>") -> list[dict[str, Any]]:
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for rule, rx in SECRET_RULES:
            if rx.search(line):
                out.append({"rule": rule, "source": source, "line": lineno})
    return out


def scan_patch(patch: str) -> list[dict[str, Any]]:
    """Scan only added lines of a unified diff."""
    findings, current = [], "<patch>"
    for line in patch.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+") and not line.startswith("+++"):
            for f in scan_text(line[1:], current):
                findings.append(f)
    return findings


def safe_join(base: Path, user_path: str) -> Path:
    """Resolve ``user_path`` under ``base``; raise ValueError on traversal (.., absolute paths, symlink escapes)."""
    base = base.resolve()
    target = (base / user_path).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"path escapes {base}: {user_path!r}")
    return target


def permission_checks() -> list[dict[str, Any]]:
    """Files that must not be group/world readable or writable."""
    checks = [(paths.HOME / "backups", 0o077), (paths.CONFIG, 0o022), (paths.RUN, 0o022), (Path("/root/.ssh"), 0o077),
              (Path("/root/.config/gh/hosts.yml"), 0o077)]
    agent_home = system.agent_home()
    if agent_home is not None:
        checks.append((agent_home / ".credentials.json", 0o077))
    out = []
    for p, forbidden in checks:
        if not p.exists():
            continue
        mode = stat.S_IMODE(p.stat().st_mode)
        out.append({"path": str(p), "mode": oct(mode), "ok": not (mode & forbidden), "forbidden_bits": oct(forbidden)})
    return out


def tracked_secret_files(repo: Path) -> list[str]:
    names = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True).stdout.splitlines()
    bad = re.compile(r"(^|/)(\.env(\..+)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*|.*credentials.*\.json|key\.txt)$")
    return [n for n in names if bad.search(n) and not n.endswith(".env.example")]


def gitleaks(repo: Path, report: Path) -> dict[str, Any]:
    p = subprocess.run(["gitleaks", "git", "--no-banner", "--redact", "--report-format", "json", "--report-path",
                        str(report), str(repo)], capture_output=True, text=True)
    findings = json.loads(report.read_text()) if report.exists() and report.stat().st_size else []
    return {"exit_code": p.returncode, "findings": len(findings),
            "rules": sorted({f.get("RuleID") for f in findings}), "report": str(report)}


def pip_audit(lock: Path, report: Path) -> dict[str, Any]:
    p = subprocess.run(["pip-audit", "-r", str(lock), "--require-hashes", "--progress-spinner", "off", "-f", "json",
                        "-o", str(report)], capture_output=True, text=True)
    data = json.loads(report.read_text()) if report.exists() and report.stat().st_size else {}
    vulns = [(d["name"], v["id"]) for d in data.get("dependencies", []) for v in d.get("vulns", [])]
    return {"exit_code": p.returncode, "vulnerabilities": len(vulns), "ids": vulns, "report": str(report),
            "stderr": p.stderr.strip()[-300:] if p.returncode not in (0, 1) else ""}


def sbom(venv_python: Path, out: Path) -> dict[str, Any]:
    p = subprocess.run(["cyclonedx-py", "environment", str(venv_python), "--of", "JSON", "-o", str(out)],
                       capture_output=True, text=True)
    comps = len(json.loads(out.read_text()).get("components", [])) if out.exists() else 0
    return {"exit_code": p.returncode, "components": comps, "sbom": str(out), "stderr": p.stderr.strip()[-300:]}


def subprocess_audit(root: Path) -> list[dict[str, Any]]:
    """Flag shell invocation, os-level system calls, dynamic code evaluation and unsafe deserialisation (review aid, not proof of safety)."""
    rx = re.compile(r"shell\s*=\s*True|os\.system\(|\beval\(|\bexec\(|pickle\.loads?\(|yaml\.load\((?!.*Loader)")
    hits = []
    for f in list(root.rglob("*.py")):
        if any(part in (".venv", "venv", "build", "legacy") for part in f.parts):
            continue
        for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            if rx.search(line) and "noqa: S" not in line:
                hits.append({"file": str(f.relative_to(root)), "line": i, "code": line.strip()[:120]})
    return hits


def run_all(repo: Path | None = None) -> dict[str, Any]:
    repo = repo or paths.REPO
    out = paths.REPORTS / "security"
    paths.ensure(out)
    res: dict[str, Any] = {
        "gitleaks": gitleaks(repo, out / "gitleaks.json"),
        "pip_audit": pip_audit(repo / "ops" / "requirements.lock", out / "pip-audit.json"),
        "sbom": sbom(paths.VENV / "bin" / "python", out / "sbom.cdx.json"),
        "tracked_secret_files": tracked_secret_files(repo),
        "permissions": permission_checks(),
        "subprocess_audit": subprocess_audit(repo / "src") + subprocess_audit(repo / "ops"),
    }
    res["ok"] = (res["gitleaks"]["findings"] == 0 and res["pip_audit"]["vulnerabilities"] == 0
                 and not res["tracked_secret_files"] and all(p["ok"] for p in res["permissions"]))
    (out / "summary.json").write_text(json.dumps(res, indent=2) + "\n")
    os.chmod(out, 0o750)
    return res
