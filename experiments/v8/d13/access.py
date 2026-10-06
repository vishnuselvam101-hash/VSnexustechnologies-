"""V8 access control for D13 (docs/V8_PREREGISTRATION.md §1, §4).

* FIT and DEV: allowed for runs 15, 16, 18 and 20. Each request is appended to ``experiments/v8/datasets/ACCESS_LEDGER.jsonl``
  (timestamp, script, run, split, purpose, granted).
* HELD-OUT (reference buckets 8-9 of those runs, and run 13 as a whole): refused unless a PREREG commit SHA is given that
  exists, is an ancestor of HEAD, and contains a file ``experiments/v8/**/PREREG-HELDOUT*.md``. Refusals are logged too.
* Run 13 is never served for FIT or DEV.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LEDGER = REPO / "experiments" / "v8" / "datasets" / "ACCESS_LEDGER.jsonl"
FIT, DEV, HELDOUT = "FIT", "DEV", "HELD-OUT"
RUNS = {"run15": "apollo", "run16": "365-dishes", "run18": "apollo", "run20": "vitruvian"}
HELDOUT_RUN = "run13"
REFERENCE_FILES = {"apollo": "d13/seqs_apollo.txt", "365-dishes": "d13/seqs_365-dishes.txt",
                   "vitruvian": "d13/seqs_Vitruvian.txt", "space_shuttle": "d13/seqs_space_shuttle.txt"}


class AccessRefused(PermissionError):
    pass


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def prereg_ok(sha: str | None, repo: Path = REPO) -> bool:
    """True if ``sha`` is a commit, an ancestor of HEAD, and contains experiments/v8/**/PREREG-HELDOUT*.md."""
    if not sha or _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode:
        return False
    if _git(repo, "merge-base", "--is-ancestor", sha, "HEAD").returncode:
        return False
    names = _git(repo, "ls-tree", "-r", "--name-only", sha, "experiments/v8").stdout.split()
    return any(Path(n).name.startswith("PREREG-HELDOUT") and n.endswith(".md") for n in names)


class V8Guard:
    def __init__(self, script: str, *, prereg_sha: str | None = None, ledger: Path = LEDGER, repo: Path = REPO):
        self.script, self.prereg, self.ledger, self.repo = script, prereg_sha, Path(ledger), Path(repo)

    def _log(self, run: str, split: str, purpose: str, granted: bool, why: str = "") -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "script": self.script,
                 "dataset": "d13-lopez-nanopore", "run": run, "split": split, "purpose": purpose, "granted": granted}
        if why:
            entry["refused_because"] = why
        if self.prereg:
            entry["prereg_sha"] = self.prereg
        with self.ledger.open("a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def authorize(self, run: str, split: str, purpose: str) -> None:
        """Raise AccessRefused unless ``run``/``split`` may be read now; log the request either way."""
        why = ""
        if split not in (FIT, DEV, HELDOUT):
            why = f"unknown split {split!r}"
        elif run == HELDOUT_RUN and split != HELDOUT:
            why = "run 13 is held out as a whole"
        elif run not in RUNS and run != HELDOUT_RUN:
            why = f"unknown run {run!r}"
        elif split == HELDOUT and not prereg_ok(self.prereg, self.repo):
            why = "held-out access needs a PREREG commit (experiments/v8/**/PREREG-HELDOUT*.md) that is an ancestor of HEAD"
        self._log(run, split, purpose, not why, why)
        if why:
            raise AccessRefused(why)
