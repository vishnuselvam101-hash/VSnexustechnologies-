"""Shared harness code for the V5 Phase 3 experiments. All results are SIMULATED.

The harness owns the ground truth: it builds real V4 strands, injects controlled edits (or runs the V4 channel with a
truth-recording twin), and only *after* decoding compares what the decoder produced with the truth. The decoder
functions called here receive reads (and optionally qualities) and nothing else.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "benchmarks" / "v5"))
from provenance import provenance  # noqa: E402

from vnxdna.v4.constraints import ConstraintConfig  # noqa: E402
from vnxdna.v4.frame import KIND_DATA, PROFILES, Layout, build_strands, decode_frames, nt_to_bytes  # noqa: E402
from vnxdna.v4.sync import TemplateAligner, frame_erasures_to_bytes  # noqa: E402

TAG = 0x5A5A
Q_CORRECT, Q_ERROR = 35, 12          # the V4 channel's default quality scale (channel.py), not a platform model

LAYOUTS = {
    "v4-balanced (24/3)": PROFILES["v4-balanced"][0],     # 313 nt, the default
    "period 16/2": Layout(40, 16, 16, 2),
    "period 32/2 (V4 round 1)": Layout(40, 16, 32, 2),
    "v4-indel (24/3, r=20)": PROFILES["v4-indel"][0],
}


def sha(*arrays) -> str:
    h = hashlib.sha256()
    for a in arrays:
        h.update(np.ascontiguousarray(a).tobytes())
    return h.hexdigest()


def make_strands(layout: Layout, n: int, seed: int) -> dict:
    """Real V4 strands with random payloads. Returns strands and the (unscrambled) truth the harness compares to."""
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (n, layout.payload_bytes), dtype=np.uint8)
    groups = np.arange(n, dtype=np.int64) // 80
    symbols = np.arange(n, dtype=np.int64) % 80
    strands, _ = build_strands(layout, ConstraintConfig(), TAG, KIND_DATA, groups, symbols, payloads)
    return {"strands": strands, "payloads": payloads, "groups": groups, "symbols": symbols,
            "strands_sha256": sha(strands)}


def inject(strand: np.ndarray, events: list[tuple], informative_quality: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Apply edits given in *strand* (template) coordinates. events: ("del", p) | ("ins", p, base) inserted before p |
    ("sub", p, base). Positions refer to the original strand; edits are applied right to left so they do not move each
    other. Returns (read, Phred qualities): Q_CORRECT everywhere, Q_ERROR on inserted/substituted bases if informative."""
    seq = [int(b) for b in strand]
    err = [False] * len(seq)
    for ev in sorted(events, key=lambda e: (e[1], 0 if e[0] == "sub" else 1), reverse=True):
        kind, p = ev[0], ev[1]
        if kind == "del":
            del seq[p]
            del err[p]
        elif kind == "ins":
            seq.insert(p, int(ev[2]))
            err.insert(p, True)
        elif kind == "sub":
            seq[p] = int(ev[2])
            err[p] = True
        else:
            raise ValueError(kind)
    read = np.asarray(seq, dtype=np.uint8)
    q = np.full(read.size, Q_CORRECT, dtype=np.uint8)
    if informative_quality:
        q[np.asarray(err, dtype=bool)] = Q_ERROR
    return read, q


def v4_decode(layout: Layout, proj, rows) -> np.ndarray:
    """The V4 sync path for already-projected reads: inner RS with the V4 erasures + CRC (decoder._try)."""
    frames = nt_to_bytes(np.minimum(proj.bases[rows], 3))
    er = frame_erasures_to_bytes(proj.erased[rows])
    P = decode_frames(layout, frames, er, errors_only_retry=False)
    return P.ok & proj.ok[rows], P


def write_result(directory: Path, name: str, config: dict, results: dict, readme: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"experiment": name, "simulated": True, "config": config, "provenance": provenance(config), "results": results}
    (directory / "results.json").write_text(json.dumps(payload, indent=2, default=_json_default) + "\n")
    (directory / "config.json").write_text(json.dumps(config, indent=2, default=_json_default) + "\n")
    if readme is not None:
        (directory / "README.md").write_text(readme)
    return directory / "results.json"


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Layout):
        return o.to_dict()
    raise TypeError(type(o))


def dist(values) -> dict:
    a = np.asarray(values, dtype=np.float64)
    if a.size == 0:
        return {"n": 0}
    return {"n": int(a.size), "mean": round(float(a.mean()), 3), "median": float(np.median(a)),
            "p95": float(np.percentile(a, 95)), "max": float(a.max()), "min": float(a.min())}


def aligner(layout: Layout, band: int = 6) -> TemplateAligner:
    return TemplateAligner(layout, band)
