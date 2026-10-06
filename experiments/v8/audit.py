"""V8.17 independent audit: mechanical checks of the V8 release criteria (docs/V8_PLAN.md, V8 specification).

    PYTHONPATH=src python experiments/v8/audit.py        # writes experiments/v8/results/audit.json, exit 1 on a failed check

Checks:
* held-out: no granted HELD-OUT request in the V8 ledger; no V8 code path opened run 13; the V7 held-out access log is
  unchanged since V7 closed;
* DEV: the fitting, pre-check, extraction and F2-rule scripts never requested DEV; every DEV *evaluation* (DEV tables,
  comparison) comes after the D verdict commit. DEV preprocessing (the segmentation pass, which writes FIT and DEV caches
  and records DEV segment counts and read-level histograms) happened before the verdict and is listed as a disclosure;
* pre-registration before data: the pre-registration commit precedes the first ledger entry; amendment A1 precedes the
  pre-check it governs;
* seeds: every pre-registered matrix cell has exactly the seeds 83000-83009 (no cherry-picking), the oracle 83000-83004;
* failures: every non-EXACT matrix decode has a taxonomy category (classified == total); 0 false success everywhere;
* provenance: models carry parameter_sha256 matching their stages; matrix rows carry model, container and read hashes;
* staleness: every committed result file was last changed at or after the last change of the code that produces it;
* hygiene: no AI attribution trailer or AI-tool name in V8 commit messages; the commit author is vishnuselvam101-hash;
  no private key, token or key-file content in the V8 diff.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V7_CLOSE = "0fd7c54d1b35178abb38520913797873d1cb22bc"
sys.path.insert(0, str(REPO / "src"))


def git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()


def commit_time(rev: str) -> int:
    return int(git("show", "-s", "--format=%ct", rev) or 0)


def first_commit_touching(path: str) -> str:
    out = git("log", "--format=%H", "--diff-filter=A", "--", path).splitlines()
    return out[-1] if out else ""


def last_commit_touching(path: str) -> str:
    return git("log", "-1", "--format=%H", "--", path)


def ledger_time(ts: str) -> int:
    import datetime as dt
    return int(dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp())


def main() -> int:
    checks: dict = {}
    led = [json.loads(x) for x in (REPO / "experiments/v8/datasets/ACCESS_LEDGER.jsonl").read_text().splitlines()]
    checks["no_heldout_granted"] = not any(e["split"] == "HELD-OUT" and e["granted"] for e in led)
    checks["run13_never_granted"] = not any(e["run"] == "run13" and e["granted"] for e in led)
    checks["v7_heldout_log_unchanged_since_v7_close"] = git("diff", "--stat", V7_CLOSE, "HEAD", "--",
                                                          "experiments/v7/datasets/ACCESS_LOG.jsonl") == ""
    verdict_c = first_commit_touching("experiments/v8/d13/results/VERDICT.json")
    t_verdict = commit_time(verdict_c)
    dev = [e for e in led if e["split"] == "DEV" and e["granted"]]
    prep = [e for e in dev if " segment " in e["script"] + " "]
    evals = [e for e in dev if e not in prep]
    checks["dev_evaluations_only_after_verdict"] = bool(verdict_c) and all(ledger_time(e["timestamp_utc"]) >= t_verdict for e in evals)
    checks["model_scripts_never_requested_dev"] = not any(any(k in e["script"] for k in ("fit.py", "extract.py", "f2_rule.py"))
                                                         for e in dev)
    checks["dev_evaluation_requests"] = sorted({e["script"] for e in evals})
    checks["disclosure_dev_preprocessing_before_verdict"] = sorted({e["script"] for e in prep
                                                                   if ledger_time(e["timestamp_utc"]) < t_verdict})
    pre_c = first_commit_touching("docs/V8_PREREGISTRATION.md")
    checks["prereg_before_first_data_access"] = commit_time(pre_c) <= min(ledger_time(e["timestamp_utc"]) for e in led)
    a1_c = first_commit_touching("docs/V8_PREREGISTRATION-A1.md")
    pre_a1 = first_commit_touching("experiments/v8/d13/results/precheck-f1-a1.json")
    checks["a1_before_its_precheck"] = commit_time(a1_c) <= commit_time(pre_a1)
    rows = [json.loads(x) for x in (REPO / "experiments/v8/matrix/results/matrix.jsonl").read_text().splitlines()]
    cells: dict = {}
    for r in rows:
        cells.setdefault((r["channel"], r["coverage"], r["profile"]), []).append(r["seed"])
    checks["matrix_cells"] = len(cells)
    checks["matrix_seeds_complete"] = len(cells) == 36 and all(sorted(v) == list(range(83000, 83010)) for v in cells.values())
    orc = [json.loads(x) for x in (REPO / "experiments/v8/oracle/results/oracle.jsonl").read_text().splitlines()]
    ocells: dict = {}
    for r in orc:
        ocells.setdefault((r["coverage"], r["profile"]), []).append(r["seed"])
    checks["oracle_seeds_complete"] = len(ocells) == 6 and all(sorted(v) == list(range(83000, 83005)) for v in ocells.values())
    fails = [r for r in rows if r["outcome"] != "EXACT"]
    checks["failures_total"] = len(fails)
    checks["failures_classified_equals_total"] = all(r["taxonomy"].get("primary") for r in fails)
    checks["false_success_matrix"] = sum(r["false_success"] for r in rows)
    checks["false_success_oracle"] = sum(lv["outcome"] == "FALSE_SUCCESS" for r in orc for lv in r["levels"].values())
    arch = json.loads((REPO / "experiments/v8/archive/results/check.json").read_text())
    checks["false_success_archive"] = arch["false_success"]
    checks["zero_false_success"] = checks["false_success_matrix"] == checks["false_success_oracle"] == checks["false_success_archive"] == 0
    from vnxdna.simulation import model as cm, model2
    m = json.loads((REPO / "experiments/v8/d13/models/d13-nanopore-f1.json").read_text())
    mm = cm.from_doc(m)[0]
    checks["model_parameter_hash_matches"] = m["provenance"]["fitting"].get("parameter_sha256") == model2.parameter_sha256(mm.doc["stages"])
    checks["matrix_rows_have_hashes"] = all(r["model"]["sha256"] and r["container_sha256"] and r["reads_sha256"] for r in rows)
    producers = {"experiments/v8/matrix/results/matrix.jsonl": ["experiments/v8/matrix/run.py", "experiments/v8/matrix/taxonomy.py"],
                 "experiments/v8/oracle/results/oracle.jsonl": ["experiments/v8/oracle/run.py"],
                 "experiments/v8/coverage/results/envelope.json": ["experiments/v8/coverage/envelope.py"],
                 "experiments/v8/d13/results/precheck-f1-a1.json": ["experiments/v8/d13/fit.py", "src/vnxdna/simulation/fit/adequacy.py"],
                 "experiments/v8/d13/results/comparison.json": ["experiments/v8/d13/compare.py"],
                 "experiments/v8/archive/results/check.json": ["experiments/v8/archive/check.py"],
                 "experiments/v8/scale/results/scale.json": ["experiments/v8/scale/bench.py"]}
    stale = []
    for res, codes in producers.items():
        tr = commit_time(last_commit_touching(res))
        for c in codes:
            if commit_time(last_commit_touching(c)) > tr:
                stale.append({"result": res, "newer_code": c})
    checks["stale_results"] = stale
    msgs = git("log", "--format=%an|%s%n%b", f"{V7_CLOSE}..HEAD")
    checks["author_ok"] = all(ln.split("|")[0] == "vishnuselvam101-hash" for ln in msgs.splitlines() if "|" in ln)
    checks["no_ai_attribution"] = not re.search(r"(?i)co-authored-by|generated with|claude|anthropic|chatgpt|copilot", msgs)
    diff = git("diff", V7_CLOSE, "HEAD")
    checks["no_secrets_in_diff"] = not re.search(r"(BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|github_pat_|sk-ant-|xox[bp]-|AKIA[0-9A-Z]{16})", diff)
    gate = ["no_heldout_granted", "run13_never_granted", "v7_heldout_log_unchanged_since_v7_close", "dev_evaluations_only_after_verdict",
            "model_scripts_never_requested_dev",
            "prereg_before_first_data_access", "a1_before_its_precheck", "matrix_seeds_complete", "oracle_seeds_complete",
            "failures_classified_equals_total", "zero_false_success", "model_parameter_hash_matches", "matrix_rows_have_hashes",
            "author_ok", "no_ai_attribution", "no_secrets_in_diff"]
    failed = [g for g in gate if checks.get(g) is not True] + (["stale_results"] if stale else [])
    out = {"audit": "V8.17", "head": git("rev-parse", "HEAD"), "checks": checks, "failed": failed, "pass": not failed}
    (REPO / "experiments/v8/results").mkdir(parents=True, exist_ok=True)
    (REPO / "experiments/v8/results/audit.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"pass": out["pass"], "failed": failed}, indent=1))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
