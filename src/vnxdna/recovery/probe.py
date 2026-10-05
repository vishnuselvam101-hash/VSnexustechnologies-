"""Decode stage D1: sample the reads and choose the strand layout (spec §3.10). Formerly
``vnxdna.v4.decoder.detect_layout`` (V6 Phase 2, M4)."""
from __future__ import annotations

import os
from collections import Counter

import numpy as np

from vnxdna.core.errors import VNXConfigurationError, VNXFormatError
from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.dnaenc.layout import Layout, PROFILES
from vnxdna.dnaenc.mapping import nt_to_bytes
from vnxdna.native.reads import iter_reads
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.pass1 import _RC
from vnxdna.sync.template import strip_markers_exact


# ============================================================================ layout detection
def detect_layout(reads_path: str | os.PathLike, opt: DecodeOptions) -> Layout:
    if opt.layout is not None:
        return opt.layout.validate()
    if opt.profile is not None:
        if opt.profile not in PROFILES:
            raise VNXConfigurationError(f"unknown profile {opt.profile!r}")
        return PROFILES[opt.profile][0]
    counts: Counter = Counter()
    sample: list[np.ndarray] = []
    seen = 0
    for batch in iter_reads(reads_path, 4096):
        counts.update(batch.lengths.tolist())
        offs = np.concatenate([[0], np.cumsum(batch.lengths)])
        sample.extend(batch.codes[offs[i]:offs[i + 1]] for i in range(batch.count))
        seen += batch.count
        if seen >= 20000:
            break
    if not counts:
        raise VNXFormatError("no reads found", stage="input")
    # candidate layouts: distinct profile layouts whose strand length is near many reads; several layouts can share a
    # length, so each candidate is scored by how many sampled exact-length reads pass its frame check (both orientations)
    candidates = []
    for name, (lay, _, _) in PROFILES.items():
        near = sum(v for length, v in counts.items() if abs(length - lay.strand_nt) <= opt.band)
        if near >= max(1, seen // 10) and lay not in [c[1] for c in candidates]:
            candidates.append((name, lay))
    if not candidates:
        raise VNXFormatError(f"cannot detect the strand layout (modal read length {counts.most_common(1)[0][0]}); pass --profile",
                             stage="layout")
    if len(candidates) == 1:
        return candidates[0][1]
    best = None
    for name, lay in candidates:
        exact = [r for r in sample if r.size == lay.strand_nt][:4000]
        score = 0
        if exact:
            mat = np.stack(exact)
            for m in (mat, _RC[mat[:, ::-1]]):
                fb, _ = strip_markers_exact(lay, m)
                score += int(decode_frames(lay, nt_to_bytes(np.minimum(fb, 3)), errors_only_retry=False).ok.sum())
        if best is None or score > best[0]:
            best = (score, name, lay)
    if best[0] == 0:
        raise VNXFormatError("several layouts match the read lengths and none verifies on a sample; pass --profile", stage="layout")
    return best[2]


def probe_file(path: str | os.PathLike, *, deep: bool = False) -> dict:
    """``vnx.probe/1`` answer for a read or strand file: can this reader decode it, and with which layout?"""
    from vnxdna.core.errors import VNXError
    out = {"schema": "vnx.probe/1", "object": "reads", "readable": "yes", "reason": None, "frame": None,
           "strand_profile": None, "layout": None, "primers": None, "sample_reads": None, "superblock": None,
           "generated_by": None}
    try:
        lay = detect_layout(path, DecodeOptions())
    except VNXError as error:
        out.update(readable="unknown" if error.code == "LAYOUT_UNDETECTED" else "no", reason=error.to_dict())
        return out
    name = next((n for n, (lay_, _, _) in PROFILES.items() if lay_ == lay), None)
    out.update(frame={"version": 4, "domain": "VNX4 scrambler"}, strand_profile=name, layout=lay.to_dict(),
               generated_by={"answer": "VNX4 frame 4: VNX-DNA >= 4.0", "exact": False})
    return out
