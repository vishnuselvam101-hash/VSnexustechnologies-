"""V8.16: compare regenerated V8 outputs (working tree) with the committed ones (git HEAD), deterministic content only.

Compared: segment/observation cache hashes and counts per run; FIT/DEV table content hashes; extraction statistics; F1
parameter hash; pre-check decision and stability classes; F2-rule ratio; comparison failures; decoder-matrix rows
(container and read-file hashes, outcomes, taxonomy); envelope classes; oracle outcomes; archive check. Not compared:
timings, timestamps, load averages, commit fields, and the canonical model SHA-256 (it includes provenance such as the
fitting commit and time; the parameter hash identifies the fitted values).

    PYTHONPATH=src python experiments/v8/verify_repro.py      # exit 1 on any difference
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VOLATILE = {"seconds", "timestamp_utc", "environment", "code", "code_at_start", "load_average", "load_average_start",
            "load_average_end", "decode_seconds", "peak_rss_bytes", "wall_s", "cpu_s", "peak_rss_mib", "children_maxrss_mib",
            "cpu_utilisation", "path", "sha256", "model_sha256", "model_file", "commit", "fitting", "workers",
            "peak_rss_mb_self_plus_children", "median_decode_seconds", "max_peak_rss_mib", "content_sha256_note"}


def committed(rel: str) -> str | None:
    p = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=REPO, capture_output=True, text=True, check=False)
    return p.stdout if p.returncode == 0 else None


def strip(o, keep_sha: bool = False):
    if isinstance(o, dict):
        return {k: strip(v, keep_sha) for k, v in o.items() if k not in VOLATILE or (keep_sha and k == "sha256")}
    if isinstance(o, list):
        return [strip(v, keep_sha) for v in o]
    if isinstance(o, float):
        return round(o, 9)
    return o


def compare_json(rel: str, keep_sha: bool = False) -> list[str]:
    old = committed(rel)
    p = REPO / rel
    if old is None:
        return [f"{rel}: not committed"]
    if not p.exists():
        return [f"{rel}: not regenerated"]
    a, b = strip(json.loads(old), keep_sha), strip(json.loads(p.read_text()), keep_sha)
    return [] if a == b else [f"{rel}: differs"]


def compare_jsonl(rel: str, key) -> list[str]:
    old = committed(rel)
    p = REPO / rel
    if old is None or not p.exists():
        return [f"{rel}: missing"]
    a = {key(r): strip(r) for r in map(json.loads, old.splitlines())}
    b = {key(r): strip(r) for r in map(json.loads, p.read_text().splitlines())}
    bad = [k for k in a if a[k] != b.get(k)]
    return [f"{rel}: {len(bad)} of {len(a)} rows differ (first {bad[:3]})"] if bad else []


def main() -> int:
    errs: list[str] = []
    for run in ("run15", "run16", "run18", "run20"):
        errs += compare_json(f"experiments/v8/d13/results/pipeline-{run}.json", keep_sha=True)
    tabs = json.loads((REPO / "experiments/v8/d13/results/tables.json").read_text())["splits"]
    old_tabs = json.loads(committed("experiments/v8/d13/results/tables.json") or "{}").get("splits", {})
    for s in old_tabs:
        if old_tabs[s].get("content_sha256") != tabs.get(s, {}).get("content_sha256"):
            errs.append(f"tables {s}: content hash differs")
    for rel in ("experiments/v8/d13/results/extraction.json", "experiments/v8/d13/results/f2-rule.json",
                "experiments/v8/d13/results/comparison.json", "experiments/v8/coverage/results/envelope.json",
                "experiments/v8/oracle/results/summary.json", "experiments/v8/matrix/results/summary.json"):
        errs += compare_json(rel)
    pre_old = json.loads(committed("experiments/v8/d13/results/precheck-f1-a1.json") or "{}")
    pre_new = json.loads((REPO / "experiments/v8/d13/results/precheck-f1-a1.json").read_text())
    for k in ("parameter_sha256",):
        if pre_old.get(k) != pre_new.get(k):
            errs.append(f"precheck {k} differs")
    if pre_old.get("decision", {}).get("decision") != pre_new.get("decision", {}).get("decision") or \
            {m: v["class"] for m, v in pre_old.get("stability", {}).items()} != {m: v["class"] for m, v in pre_new.get("stability", {}).items()}:
        errs.append("precheck decision or stability classes differ")
    errs += compare_jsonl("experiments/v8/matrix/results/matrix.jsonl", lambda r: (r["channel"], r["coverage"], r["profile"], r["seed"]))
    errs += compare_jsonl("experiments/v8/oracle/results/oracle.jsonl", lambda r: (r["coverage"], r["profile"], r["seed"]))
    a = json.loads(committed("experiments/v8/archive/results/check.json") or "{}")
    b = json.loads((REPO / "experiments/v8/archive/results/check.json").read_text())
    if strip(a.get("runs")) != strip(b.get("runs")) or a.get("negative") != b.get("negative"):
        errs.append("archive check differs")
    print("\n".join(errs) if errs else "REPRODUCED: every compared V8 output equals the committed one")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
