"""Compare a Phase 3 reproduction directory with the committed Phase 3 results (deterministic fields only).

Timings, memory, CPU time and provenance are reported but not compared (they depend on the machine and the moment);
everything else — outcomes, counts, hashes, erasures, classes, false acceptances — must be identical.

usage: python experiments/v5/phase3/repro_compare.py REPRO_DIR [--out FILE]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPS = ["P3-EXP-01-single-indel", "P3-EXP-02-multi-indel", "P3-EXP-03-consensus", "P3-EXP-04-accounting", "P3-EXP-05-v4-vs-v5"]
VOLATILE = ("seconds", "per_second", "rss", "provenance", "wall", "_s", "cpu", "timestamp", "stage_seconds", "bases_per_second")


def strip(obj, path=""):
    if isinstance(obj, dict):
        return {k: strip(v, f"{path}.{k}") for k, v in obj.items() if not any(t in k for t in VOLATILE)}
    if isinstance(obj, list):
        return [strip(v, path) for v in obj]
    return obj


def diff(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: present in only one")
            else:
                diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: {str(a)[:80]} != {str(b)[:80]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repro", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    report = {}
    for e in EXPS:
        old = json.loads((HERE / e / "results.json").read_text())
        new = json.loads((a.repro / e / "results.json").read_text())
        out: list = []
        diff(strip(old["results"]), strip(new["results"]), "results", out)
        diff(strip(old["config"]), strip(new["config"]), "config", out)
        prov = new["provenance"]
        report[e] = {"identical_deterministic_fields": not out, "differences": out[:50], "n_differences": len(out),
                     "repro_commit": prov.get("commit_under_test"), "repro_worktree_dirty": prov.get("worktree_dirty"),
                     "original_commit": old["provenance"].get("commit_under_test"),
                     "original_worktree_dirty": old["provenance"].get("worktree_dirty")}
        print(f"{e:28s} identical={not out} ({len(out)} differences)  repro {prov.get('commit_under_test', '')[:7]} "
              f"dirty={prov.get('worktree_dirty')}  original {old['provenance'].get('commit_under_test', '')[:7]} "
              f"dirty={old['provenance'].get('worktree_dirty')}")
        for d in out[:5]:
            print("   ", d)
    if a.out:
        a.out.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
