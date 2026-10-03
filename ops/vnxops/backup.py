"""PHASE 1: non-destructive backups. Git history goes into bundles (all refs); working trees into tar.zst archives.

Nothing is deleted or modified at the source. Every archive gets a SHA-256 in MANIFEST.json, and `verify` re-checks
them (bundle integrity via `git bundle verify`).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from . import paths

EXCLUDES = [".venv", "venv", "__pycache__", ".pytest_cache", ".hypothesis", ".ruff_cache", "build", "node_modules",
            "*.egg-info"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False).stdout.strip()


def backup(sources: list[Path], label: str = "manual", dest_root: Path | None = None) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = (dest_root or paths.BACKUPS) / f"{stamp}-{label}"
    dest.mkdir(parents=True, exist_ok=False)
    os.chmod(dest, 0o700)
    entries: list[dict[str, Any]] = []
    bundled: set[str] = set()
    for src in sources:
        src = src.resolve()
        name = str(src).strip("/").replace("/", "_")
        entry: dict[str, Any] = {"source": str(src)}
        common = _git(src, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common and _git(src, "rev-parse", "--verify", "-q", "HEAD") and common not in bundled:
            bundle = dest / f"{name}.bundle"
            subprocess.run(["git", "-C", str(src), "bundle", "create", "-q", str(bundle), "--all"], check=True)
            bundled.add(common)
            entry["bundle"] = {"file": bundle.name, "sha256": sha256(bundle), "bytes": bundle.stat().st_size}
        if common:
            entry["git"] = {"head": _git(src, "rev-parse", "HEAD"), "branch": _git(src, "branch", "--show-current"),
                            "status": _git(src, "status", "--porcelain").splitlines()}
        tar = dest / f"{name}.worktree.tar.zst"
        excl = [f"--exclude={e}" for e in EXCLUDES]
        subprocess.run(["tar", "--zstd", "-cf", str(tar), *excl, "-C", str(src.parent), src.name], check=True)
        entry["worktree_tar"] = {"file": tar.name, "sha256": sha256(tar), "bytes": tar.stat().st_size,
                                 "excludes": EXCLUDES}
        entries.append(entry)
    manifest = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "label": label,
                "entries": entries,
                "restore": ["git clone <name>.bundle <dir>   # full history, every branch and tag",
                            "tar --zstd -xf <name>.worktree.tar.zst -C <parent>   # working tree incl. uncommitted files"]}
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return dest


def _bundle_ok(bundle: Path) -> bool:
    """`git bundle verify` needs a repository for context; use an empty throw-away one so the result never depends
    on the caller's working directory. A full (--all) bundle has no prerequisites, so an empty repo suffices."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="bundle-verify-") as tmp:
        subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
        return subprocess.run(["git", "-C", tmp, "bundle", "verify", "-q", str(bundle)], capture_output=True).returncode == 0


def verify(dest: Path) -> dict[str, Any]:
    manifest = json.loads((dest / "MANIFEST.json").read_text())
    results = []
    for e in manifest["entries"]:
        for kind in ("bundle", "worktree_tar"):
            if kind in e:
                f = dest / e[kind]["file"]
                ok = f.exists() and sha256(f) == e[kind]["sha256"]
                if ok and kind == "bundle":
                    ok = _bundle_ok(f)
                results.append({"file": f.name, "ok": ok})
    return {"backup": str(dest), "ok": all(r["ok"] for r in results), "files": results}
