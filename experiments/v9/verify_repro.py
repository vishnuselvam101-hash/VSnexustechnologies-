"""V9 reproduction check: compare regenerated V9 result rows with the committed ones, deterministic content only.

Rows are matched by their identity fields (candidate, level, mode, channel, coverage, profile, size, seed); every other
field must be equal after the volatile fields are removed. Not compared: timings (every ``*_seconds`` field), load
averages, peak RSS, timestamps and commit fields. A regenerated row with no committed counterpart is a failure (nothing is silently new).

    PYTHONPATH=src python experiments/v9/verify_repro.py FRESH.jsonl COMMITTED.jsonl [FRESH COMMITTED ...]
    PYTHONPATH=src python experiments/v9/verify_repro.py --head PATH [PATH ...]   # working tree vs git HEAD

Exit 1 on any difference.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KEY = ("candidate", "level", "mode", "channel", "coverage", "profile", "size", "seed")
VOLATILE = {"time", "seconds", "decode_seconds", "decode_seconds_total", "median_decode_seconds", "median_seconds",
            "p95_seconds", "runtime_ratio_vs_baseline", "load1", "peak_rss_bytes", "commit", "environment", "wall_s",
            "cpu_s", "peak_rss_mib", "timestamp_utc"}


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in VOLATILE and not k.endswith("_seconds")}
    if isinstance(o, list):
        return [strip(v) for v in o]
    if isinstance(o, float):
        return round(o, 9)
    return o


def rows(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if line.strip():
            r = json.loads(line)
            out[tuple(r.get(k) for k in KEY)] = strip(r)
    return out


def committed(rel: str) -> str | None:
    p = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=REPO, capture_output=True, text=True, check=False)
    return p.stdout if p.returncode == 0 else None


def compare(fresh_text: str, old_text: str | None, label: str) -> list[str]:
    if old_text is None:
        return [f"{label}: no committed file"]
    if label.endswith(".json"):
        a, b = strip(json.loads(fresh_text)), strip(json.loads(old_text))
        return [] if a == b else [f"{label}: content differs"]
    fresh, old = rows(fresh_text), rows(old_text)
    bad = []
    for k, r in fresh.items():
        if k not in old:
            bad.append(f"{label}: row {k} not in the committed file")
        elif r != old[k]:
            diff = sorted(f for f in set(r) | set(old[k]) if r.get(f) != old[k].get(f))
            bad.append(f"{label}: row {k} differs in {diff}")
    print(f"{label}: {len(fresh)} rows compared, {len(bad)} differences")
    return bad


def main(argv: list[str]) -> int:
    bad: list[str] = []
    if argv[:1] == ["--head"]:
        for rel in argv[1:]:
            bad += compare((REPO / rel).read_text(), committed(rel), rel)
    else:
        if not argv or len(argv) % 2:
            raise SystemExit(__doc__)
        for fresh, old in zip(argv[::2], argv[1::2]):
            bad += compare(Path(fresh).read_text(), Path(old).read_text(), fresh)
    for b in bad:
        print("DIFF", b)
    print("REPRODUCED" if not bad else f"NOT REPRODUCED ({len(bad)} differences)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
