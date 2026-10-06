"""Shared code of the V7 fitting experiments (experiments/v7/fit): reads come only through the split guard
(experiments/v7/split/guard.py); everything numerical is in vnxdna.simulation.fit.

Evidence classes: fitted parameters PUBLIC-DATA-DERIVED; reads simulated from a fitted model SIMULATED."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "experiments/v7/split"))
import guard as G  # noqa: E402, F401

from vnxdna import _version  # noqa: E402
from vnxdna.core.provenance import git_state  # noqa: E402
from vnxdna.simulation import model as cm  # noqa: E402, F401
from vnxdna.simulation.fit import fit as F  # noqa: E402, F401
from vnxdna.simulation.fit import model_out as MO  # noqa: E402, F401
from vnxdna.simulation.fit import pipeline as P  # noqa: E402, F401
from vnxdna.simulation.fit import validate as V  # noqa: E402, F401
from vnxdna.simulation.fit.tally import Layout  # noqa: E402

DATA_DIR = os.environ.get("VNX_DATA_DIR", "/root/vnx-dna-lab/data/public")   # as experiments/v7/split/split.py
MANIFEST = json.loads((REPO / "experiments/v7/datasets/MANIFEST.json").read_text())
SPLIT_MANIFEST_BYTES = (REPO / "experiments/v7/split/SPLIT_MANIFEST.json").read_bytes()
SPLIT_SHA = hashlib.sha256(SPLIT_MANIFEST_BYTES).hexdigest()
METHOD = "vnx-channel-fit/1 (edlib unit-cost global alignment, leftmost indel normalisation, count estimators, bootstrap over references, simulation calibration)"
_COMP = bytes.maketrans(b"ACGTN", b"TGCAN")


def rc(b: bytes) -> bytes:
    return b.translate(_COMP)[::-1]


def software() -> dict:
    out = {"python": sys.version.split()[0], "numpy": np.__version__, "vnxdna": _version.__version__}
    try:
        out["edlib"] = importlib.metadata.version("edlib")
    except importlib.metadata.PackageNotFoundError:
        out["edlib"] = "unknown"
    return out


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fitting_block(seed: int) -> dict:
    gs = git_state(REPO)
    return {"method": METHOD, "version": _version.__version__, "commit": gs["commit"], "dirty": bool(gs["dirty"]), "seed": seed,
            "timestamp_utc": utc_now(), "software": software()}


def dataset_entry(key: str, files: list[tuple[str, str]], role: str | None = None) -> dict:
    """Provenance entry of a dataset from the manifest: ``files`` = (display name, sha256)."""
    d = MANIFEST["datasets"][key]
    e = {"id": {"cnr": "D04", "d03-nanopore": "D03", "dt4dds-twist": "D02"}[key], "accession": d["accession"] or d["source"],
         "url": d["url"], "files": [{"name": n, "sha256": h} for n, h in files]}
    if role:
        e["role"] = role
    return e


def manifest_sha(dataset: str, rel: str) -> str:
    for f in MANIFEST["datasets"][dataset]["files"]:
        if f["path"] == rel:
            return f["sha256"]
    raise KeyError(rel)


def counts_of(layout: Layout, M: np.ndarray) -> np.ndarray:
    return layout.get(M, "n_reads")[:, 0] + layout.get(M, "excluded")[:, 0]


class Collector:
    """Iterates (ref, reads) pairs for the pipeline while recording references and (optionally) the first clusters."""

    def __init__(self, source, orient_backward: bool = False, keep_clusters: int = 0):
        self.source, self.back, self.keep = source, orient_backward, keep_clusters
        self.refs: list = []
        self.ids: list = []
        self.clusters: list = []
        self.empty = 0

    def __iter__(self):
        for rid, ref, reads in self.source:
            if self.back:
                ref, reads = rc(ref), [rc(r) for r in reads]
            self.refs.append(ref)
            self.ids.append(rid)
            if not reads:
                self.empty += 1
            if len(self.clusters) < self.keep and reads:
                self.clusters.append((ref, reads))
            yield ref, reads


def percent(x: float) -> str:
    return f"{100 * x:.3f}"
