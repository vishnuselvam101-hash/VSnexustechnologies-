"""Access guard for V7 read data (docs/V7_PROTOCOL.md section 4.2).

This module is the only place in the V7 code that opens a read file (D03 RX files, CNR Clusters.txt, DT4DDS FASTQ). Fitting
and validation code obtains reads only through :class:`Guard`, which yields only the reads of references that belong to the
requested split (reference lists in ``lists/``, integrity-checked against SPLIT_MANIFEST.json):

* FIT and DEV: allowed. Reads of references outside the split are skipped before they reach the caller (for D02 raw reads
  the caller's assigner sees every read of the file to decide the reference; reads not assigned to the split are dropped
  and only counted).
* HELDOUT: refused unless a PREREG commit SHA is given that (a) exists, (b) is an ancestor of HEAD and (c) contains a file
  named ``PREREG*`` under ``experiments/v7``. Every held-out request, granted or refused, is appended to
  ``experiments/v7/datasets/ACCESS_LOG.jsonl`` (commit SHA, script, timestamp, purpose).
* Every FIT and DEV request (granted or refused) is appended to the access ledger
  ``experiments/v7/datasets/SPLIT_ACCESS_LEDGER.jsonl`` (same fields), so the looks at DEV that protocol 5.5 counts are
  recorded where they happen. Entries marked ``reconstructed`` were added from committed validation results for the runs
  made before the ledger existed.
* Whole held-out runs (D03 file-1) are not in the FIT or DEV run lists: requesting one of their groups for FIT or DEV is
  refused whatever SHA is given.

Reference and design files may be read freely (protocol 4.1: the split is a function of the references).
Python cannot make a file unreadable to code that bypasses this module; ``tests/v7/test_split_guard.py`` checks that no
other V7 module names a read file.
"""
from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import refsplit as rs  # noqa: E402

REPO = HERE.parents[2]
DEFAULT_LOG = HERE.parent / "datasets" / "ACCESS_LOG.jsonl"
LEDGER_NAME = "SPLIT_ACCESS_LEDGER.jsonl"
_SHA = re.compile(r"^[0-9a-f]{40}$")
OLIGOS = "d03/oligos.fasta"


class AccessRefused(PermissionError):
    """Read access refused by the split guard."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def head_sha(repo: Path = REPO) -> str:
    r = _git(repo, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def check_prereg(sha: str | None, repo: Path = REPO) -> str:
    """Return the verified full PREREG commit SHA or raise AccessRefused."""
    if not sha or not _SHA.match(sha):
        raise AccessRefused("held-out reads need the 40-hex commit SHA of the PREREG commit")
    if _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        raise AccessRefused(f"PREREG SHA {sha} is not a commit of this repository")
    if _git(repo, "merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
        raise AccessRefused(f"PREREG commit {sha} is not an ancestor of HEAD")
    names = _git(repo, "ls-tree", "-r", "--name-only", sha, "--", "experiments/v7").stdout.split("\n")
    if not any(Path(n).name.startswith("PREREG") for n in names if n):
        raise AccessRefused(f"commit {sha} contains no experiments/v7/**/PREREG* file")
    return sha


def clusters(fh):
    """(index, [read, ...]) per cluster of an RX / Clusters.txt stream. A cluster starts after a line of '=' (the files begin
    with one); an empty cluster has no reads."""
    cur, started, idx = [], False, 0
    for ln in fh:
        s = ln.strip()
        if s.startswith(b"="):
            if started:
                yield idx, cur
                idx += 1
            cur, started = [], True
        elif s:
            cur.append(s)
            started = True
    if started:
        yield idx, cur


def fasta_records(path: Path):
    name, parts = None, []
    with open(path, "rb") as fh:
        for line in fh:
            line = line.rstrip(b"\r\n")
            if line.startswith(b">"):
                if name is not None:
                    yield name.decode(), b"".join(parts)
                name, parts = line[1:], []
            elif line:
                parts.append(line)
    if name is not None:
        yield name.decode(), b"".join(parts)


class Guard:
    def __init__(self, data_dir, *, prereg_sha: str | None = None, script: str = "unknown", split_dir: Path = HERE,
                 log_path: Path | None = None, repo: Path = REPO, ledger_path: Path | None = None):
        self.data = Path(data_dir)
        self.split_dir = Path(split_dir)
        self.manifest = json.loads((self.split_dir / "SPLIT_MANIFEST.json").read_text())
        self.prereg = prereg_sha
        self.script = script
        self.log_path = Path(log_path) if log_path else DEFAULT_LOG
        # FIT/DEV ledger: next to the held-out log unless given (a test's log path keeps its ledger out of the repository)
        self.ledger_path = Path(ledger_path) if ledger_path else self.log_path.with_name(LEDGER_NAME)
        self.repo = Path(repo)
        self._ids: dict = {}
        self._runs: dict = {}
        self.dropped: dict = {}

    # -- lists --------------------------------------------------------------------------------------------------------
    def _load(self, dataset: str, split: str, kind: str) -> list[str]:
        meta = self.manifest["datasets"][dataset]["lists"][split][kind]
        text = (self.split_dir / meta["path"]).read_text()
        lines = [ln for ln in text.split("\n") if ln]
        if rs.list_sha256(lines) != meta["sha256"]:
            raise AccessRefused(f"list {meta['path']} does not match SPLIT_MANIFEST.json (altered?)")
        return lines

    def ids(self, dataset: str, split: str) -> frozenset:
        k = (dataset, split)
        if k not in self._ids:
            self._ids[k] = frozenset(self._load(dataset, split, "refs"))
        return self._ids[k]

    def runs(self, dataset: str, split: str) -> dict[str, str]:
        k = (dataset, split)
        if k not in self._runs:
            self._runs[k] = dict(ln.split("\t") for ln in self._load(dataset, split, "runs"))
        return self._runs[k]

    # -- authorisation ------------------------------------------------------------------------------------------------
    def _log(self, entry: dict, path: Path | None = None) -> None:
        entry = {"timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "commit": head_sha(self.repo),
                 "script": self.script, **entry}
        path = path or self.log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def authorize(self, dataset: str, target: str, split: str, purpose: str) -> str:
        """Check access to the reads of run/group `target` for `split`; return the scope of the run list entry."""
        if split not in rs.SPLITS:
            raise ValueError(f"unknown split {split!r}")
        if not purpose:
            raise AccessRefused("a purpose is required")
        scope = self.runs(dataset, split).get(target)
        if scope is None:
            self._log({"dataset": dataset, "target": target, "split": split, "purpose": purpose, "granted": False,
                       "reason": "run not in the split's run list"}, None if split == rs.HELDOUT else self.ledger_path)
            raise AccessRefused(f"{dataset}/{target} is not in the {split} run list")
        if split != rs.HELDOUT:
            self._log({"dataset": dataset, "target": target, "split": split, "purpose": purpose, "granted": True},
                      self.ledger_path)
        if split == rs.HELDOUT:
            try:
                sha = check_prereg(self.prereg, self.repo)
            except AccessRefused as e:
                self._log({"dataset": dataset, "target": target, "split": split, "purpose": purpose, "granted": False,
                           "reason": str(e)})
                raise
            self._log({"dataset": dataset, "target": target, "split": split, "purpose": purpose, "granted": True,
                       "prereg_sha": sha})
        return scope

    def _keep(self, dataset: str, target: str, split: str, scope: str):
        ids = self.ids(dataset, split)
        return (lambda i: True) if scope == "all-references" else (lambda i: i in ids)

    # -- references (free) --------------------------------------------------------------------------------------------
    def references(self, dataset: str) -> list[tuple[str, bytes]]:
        """All designed references (id, sequence) of a dataset, from reference/design files only."""
        if dataset == "cnr":
            cs = [ln.strip() for ln in (self.data / "cnr/Centers.txt").read_bytes().split(b"\n") if ln.strip()]
            return [(str(i), c) for i, c in enumerate(cs)]
        if dataset == "dt4dds-twist":
            return list(fasta_records(self.data / "dt4dds/design_files_Twist_GCfix_0a.fasta"))
        raise ValueError("D03 references are per group: use d03_references(group)")

    def d03_references(self, group: str) -> list[tuple[str, bytes]]:
        """(oligo ID, reference as listed in the group's TX file) in cluster order. TX is a reference file."""
        canon = {rs.canonical(s): n for n, s in fasta_records(self.data / OLIGOS)}
        tx = self._d03_path(group, "TX")
        return [(canon[rs.canonical(t)], t) for t in (ln.strip() for ln in tx.read_bytes().split(b"\n")) if t]

    def _d03_path(self, group: str, kind: str) -> Path:
        inner, direction = group.split("/")
        d = self.data / "d03" / "ex" / inner / direction
        found = sorted(d.glob(f"{kind}__*"))
        if len(found) != 1:
            raise FileNotFoundError(f"{d}/{kind}__*")
        return found[0]

    # -- reads (guarded) ----------------------------------------------------------------------------------------------
    def iter_cnr(self, split: str, purpose: str):
        """(reference ID, centre, reads) of the clusters of `split`."""
        scope = self.authorize("cnr", "cnr", split, purpose)
        keep = self._keep("cnr", "cnr", split, scope)
        refs = dict(self.references("cnr"))
        with open(self.data / "cnr/Clusters.txt", "rb") as fh:
            for idx, reads in clusters(fh):
                i = str(idx)
                if keep(i):
                    yield i, refs[i], reads

    def iter_d03(self, group: str, split: str, purpose: str):
        """(oligo ID, reference as listed in TX, reads) of the clusters of `split` in group `<file-archive>/<direction>`."""
        scope = self.authorize("d03-nanopore", group, split, purpose)
        keep = self._keep("d03-nanopore", group, split, scope)
        refs = self.d03_references(group)
        with open(self._d03_path(group, "RX"), "rb") as fh:
            for idx, reads in clusters(fh):
                i, seq = refs[idx]
                if keep(i):
                    yield i, seq, reads

    def iter_fastq_batches(self, run: str, split: str, assign_batch, purpose: str, batch: int = 50000):
        """Batches of (reference ID, name, sequence, quality-string) for the reads that `assign_batch(list of sequences)`
        assigns (to a reference ID or None) to a reference of `split`. Other reads are dropped and counted in
        ``self.dropped[run]`` (assignment is a pure function of the read and the design; nothing else is computed)."""
        scope = self.authorize("dt4dds-twist", run, split, purpose)
        if scope != "refs-in-split":
            raise AccessRefused(f"{run} has no designed references; use iter_fastq_control")
        ids = self.ids("dt4dds-twist", split)
        for recs in self._fastq(run, batch):
            assigned = assign_batch([r[1] for r in recs])
            out = [(a, *r) for a, r in zip(assigned, recs) if a is not None and a in ids]
            self.dropped[run] = self.dropped.get(run, 0) + len(recs) - len(out)
            if out:
                yield out

    def iter_fastq_control(self, run: str, purpose: str, batch: int = 50000):
        """Batches of (name, sequence, quality) of a sequencing-only control run (PhiX); FIT only."""
        scope = self.authorize("dt4dds-twist", run, rs.FIT, purpose)
        if scope != "control-no-references":
            raise AccessRefused(f"{run} is not a control run")
        yield from self._fastq(run, batch)

    def _fastq(self, run: str, batch: int):
        recs = []
        with gzip.open(self.data / f"dt4dds/{run}_1.fastq.gz", "rb") as fh:
            while True:
                head = fh.readline()
                if not head:
                    break
                seq, _plus, qual = fh.readline().strip(), fh.readline(), fh.readline().strip()
                recs.append((head[1:].split()[0].decode(), seq, qual))
                if len(recs) >= batch:
                    yield recs
                    recs = []
        if recs:
            yield recs
