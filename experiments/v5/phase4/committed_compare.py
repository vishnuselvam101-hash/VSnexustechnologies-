"""Gate A: compare the clean-commit re-runs (c5b68d6) with the published Phase 4 results (dirty working tree).

Every leaf of results + config is classified:

  A  deterministic difference   an integer, string, boolean, hash or list length differs
  B  float noise                a float differs by at most 1e-9 relative (summation order)
  C  runtime / memory           seconds, CPU time, peak RSS (machine- and moment-dependent; summarised, not compared)
  D  genuine behaviour change   = A, unless explained (none are explained away automatically)

Integrity counters (false, ambiguous, wrong-but-verified, false SUCCESS) are summed over the re-run and must be 0.

usage: python experiments/v5/phase4/committed_compare.py [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEW = HERE / "committed-c5b68d6"
EXPS = ["P4-EXP-01-substitution-sweep", "P4-EXP-03-indel-plus-substitution", "P4-EXP-04-rs-boundary",
        "P4-EXP-06-adversarial", "P4-EXP-07-identical-reads", "P4-EXP-08-coverage"]
VOLATILE = ("seconds", "rss", "cpu", "wall", "provenance", "timestamp", "per_second")
INTEGRITY = ("false", "false_success", "ambiguous", "wrong_but_verified_frames", "accepted_wrong_address")


def volatile(key: str) -> bool:
    return any(t in key for t in VOLATILE)


def walk(a, b, path, out, runtime):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out["A"].append(f"{path}.{k}: present in only one")
            elif volatile(k):
                runtime.append((f"{path}.{k}", a[k], b[k]))
            else:
                walk(a[k], b[k], f"{path}.{k}", out, runtime)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out["A"].append(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", out, runtime)
    elif isinstance(a, float) and isinstance(b, float) and not isinstance(a, bool):
        if a != b:
            rel = abs(a - b) / max(abs(a), abs(b), 1e-300)
            out["B" if rel <= 1e-9 else "A"].append(f"{path}: {a!r} != {b!r} (rel {rel:.1e})")
    elif a != b:
        out["A"].append(f"{path}: {str(a)[:80]} != {str(b)[:80]}")


def numbers(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        yield float(v)
    elif isinstance(v, list):
        for x in v:
            yield from numbers(x)


def integrity(obj, acc):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in INTEGRITY and isinstance(v, (int, float)) and not isinstance(v, bool):
                acc[k] = acc.get(k, 0) + v
            elif k == "false_success" and isinstance(v, bool):
                acc[k] = acc.get(k, 0) + int(v)
            else:
                integrity(v, acc)
    elif isinstance(obj, list):
        for x in obj:
            integrity(x, acc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=NEW / "comparison.json")
    a = ap.parse_args()
    report = {}
    for e in EXPS:
        if not (NEW / e / "results.json").exists():
            print(f"{e:36s} MISSING in {NEW.name}")
            report[e] = {"missing": True}
            continue
        old = json.loads((HERE / e / "results.json").read_text())
        new = json.loads((NEW / e / "results.json").read_text())
        out = {"A": [], "B": []}
        runtime: list = []
        walk(old["results"], new["results"], "results", out, runtime)
        walk(old["config"], new["config"], "config", out, runtime)
        ratios = []
        for p, x, y in runtime:
            xs, ys = list(numbers(x)), list(numbers(y))
            if "seconds" in p and len(xs) == len(ys):
                ratios += [b / a for a, b in zip(xs, ys) if a and a > 0.05]
        rss = [(p, x, y) for p, x, y in runtime if "rss" in p]
        rss_r = []
        for p, x, y in rss:
            xs, ys = list(numbers(x)), list(numbers(y))
            rss_r += [b / a for a, b in zip(xs, ys) if a]
        integ: dict = {}
        integrity(new["results"], integ)
        prov, oprov = new["provenance"], old["provenance"]
        report[e] = {
            "A_deterministic_differences": len(out["A"]), "A_examples": out["A"][:20],
            "B_float_noise": len(out["B"]), "B_examples": out["B"][:5],
            "C_runtime": {"timed_values": len(ratios),
                          "seconds_ratio_new_over_old_median": round(statistics.median(ratios), 3) if ratios else None,
                          "seconds_ratio_min": round(min(ratios), 3) if ratios else None,
                          "seconds_ratio_max": round(max(ratios), 3) if ratios else None,
                          "rss_ratio_median": round(statistics.median(rss_r), 3) if rss_r else None},
            "integrity_rerun": integ,
            "config_sha256": [oprov.get("config_sha256"), prov.get("config_sha256")],
            "rerun": {"commit_under_test": prov.get("commit_under_test"), "worktree_dirty": prov.get("worktree_dirty")},
            "published": {"commit_under_test": oprov.get("commit_under_test"), "worktree_dirty": oprov.get("worktree_dirty")}}
        r = report[e]
        print(f"{e:36s} A={r['A_deterministic_differences']:<4d} B={r['B_float_noise']:<4d} "
              f"time x{r['C_runtime']['seconds_ratio_new_over_old_median']}  rss x{r['C_runtime']['rss_ratio_median']}  "
              f"integrity {integ}  rerun {prov.get('commit_under_test', '')[:7]} dirty={prov.get('worktree_dirty')}")
        for d in out["A"][:5]:
            print("    A", d)
    a.out.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
