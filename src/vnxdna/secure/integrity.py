"""Security integrity snapshot (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §8).

A baseline is a map {path: SHA-256} over chosen roots (by default the installed ``vnxdna`` package's code files and
the VNX-Secure home's policy), authenticated with HMAC-SHA256 under the integrity key. A check recomputes the hashes:

* VERIFIED - the file's hash equals the baseline;
* MODIFIED - the file exists with a different hash, or a baseline file is missing;
* UNKNOWN  - the file is not in the baseline (new file);
* FAILED   - the file could not be read, or the baseline itself does not authenticate (then every item is FAILED).

What this verifies: that the listed files have the bytes they had when the baseline was taken, as seen by this
process. What it does NOT verify: the interpreter, shared libraries, the kernel, memory, firmware, or that the
process doing the check is itself uncompromised. A host-level attacker who controls the checking code can lie.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

from .events import canonical

CODE_SUFFIXES = (".py", ".c", ".h", ".so", ".json")
MAX_FILES = 100_000


def _walk(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for f in sorted(filenames):
            if f.endswith(CODE_SUFFIXES):
                out.append(Path(dirpath) / f)
        if len(out) > MAX_FILES:
            raise ValueError("too many files under the integrity roots")
    return out


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def hash_roots(roots: list[Path]) -> dict:
    files = {}
    for r in roots:
        for p in _walk(Path(r)):
            try:
                files[str(p)] = _sha(p)
            except OSError:
                files[str(p)] = None
    return files


def make_baseline(roots: list[Path], key: bytes, ts: float) -> dict:
    body = {"version": 1, "roots": [str(r) for r in roots], "created": ts, "files": hash_roots(roots)}
    return {**body, "mac": hmac.new(key, canonical(body), hashlib.sha256).hexdigest()}


def baseline_ok(baseline: dict, key: bytes) -> bool:
    try:
        body = {k: baseline[k] for k in ("version", "roots", "created", "files")}
        return hmac.compare_digest(hmac.new(key, canonical(body), hashlib.sha256).hexdigest(), baseline["mac"])
    except (KeyError, TypeError, ValueError):
        return False


def check(baseline: dict | None, key: bytes) -> dict:
    """Returns {"status", "items": {path: status}, "counts", "scope"}; status is the worst item status."""
    if baseline is None:
        return {"status": "UNKNOWN", "items": {}, "counts": {}, "reason": "no baseline", "scope": SCOPE}
    if not baseline_ok(baseline, key):
        return {"status": "FAILED", "items": {p: "FAILED" for p in baseline.get("files", {}) if isinstance(p, str)},
                "counts": {}, "reason": "baseline does not authenticate (tampered or wrong key)", "scope": SCOPE}
    now = hash_roots([Path(r) for r in baseline["roots"]])
    items = {}
    for p, h in baseline["files"].items():
        if p not in now:
            items[p] = "MODIFIED"          # missing
        elif now[p] is None or h is None:
            items[p] = "FAILED"
        else:
            items[p] = "VERIFIED" if hmac.compare_digest(now[p], h) else "MODIFIED"
    for p in now:
        if p not in baseline["files"]:
            items[p] = "UNKNOWN"
    counts = {s: sum(1 for v in items.values() if v == s) for s in ("VERIFIED", "MODIFIED", "UNKNOWN", "FAILED")}
    status = next((s for s in ("FAILED", "MODIFIED", "UNKNOWN") if counts[s]), "VERIFIED")
    return {"status": status, "items": {p: s for p, s in sorted(items.items()) if s != "VERIFIED"}, "counts": counts,
            "reason": f"{counts['VERIFIED']} verified, {counts['MODIFIED']} modified, {counts['UNKNOWN']} unknown, "
                      f"{counts['FAILED']} failed", "scope": SCOPE}


SCOPE = ("Verifies only that the files under the baseline roots have the bytes recorded in the authenticated baseline. "
         "Does not verify the interpreter, libraries, kernel, firmware, memory, or the checking process itself; a hash "
         "match does not prove the host is uncompromised.")


def dumps(baseline: dict) -> str:
    return json.dumps(baseline, indent=1, sort_keys=True) + "\n"
