"""Single-read synchronization for frame format 5 (RS-assisted realignment).

Reed–Solomon codes correct substitutions and erasures, not insertions or
deletions: one indel shifts every later symbol, and the read no longer has the
strand length. VNX-DNA handles indels in two separate layers, and neither of
them is the Reed–Solomon code itself:

1. **Multi-read synchronization** (:mod:`vnxdna.v2.consensus`): reads of one
   strand are aligned to each other (banded edit-distance alignment) and
   voted into one consensus of the strand length. This is the main indel
   defence when coverage > 1.
2. **Single-read realignment** (this module, opt-in for raw reads): for a read
   that is ``d`` bases off (1 ≤ |d| ≤ max_indel), hypothesise that the indels
   fall in frame bytes ``j1 ≤ … ≤ j|d|``, restore the length at those bytes, mark
   them as erasures and let the inner RS decoder (``2e + f ≤ r``) fix them. A
   hypothesis is accepted only if the frame CRC-32 verifies.

Guarantee: a read with exactly one indel and at most ``(r − 1) // 2`` other
byte errors is recovered, because one hypothesis matches it exactly. Two
indels cost O(F²) hypotheses (capped by ``max_candidates``). A false acceptance
needs an RS miscorrection *and* a CRC-32 collision (about 2^-32 per
hypothesis), and the chunk SHA-256 checks catch it downstream in any case.
"""
from __future__ import annotations

from itertools import combinations_with_replacement

import numpy as np

from ..dna.mapping import INVALID, get_mapping, reverse_complement_codes
from .frame import FrameGeometry, parse_one_corrected


def realign(codes: np.ndarray, byte_positions: tuple[int, ...], delta: int, nt_per_byte: int) -> np.ndarray:
    out = codes
    for j in byte_positions:
        at = j * nt_per_byte
        out = np.insert(out, at, INVALID) if delta < 0 else np.delete(out, at)
    return out


def repair_read(codes: np.ndarray, geometry: FrameGeometry, *, max_indel: int = 1, max_candidates: int = 4096,
                reverse_complement: bool = True) -> tuple[tuple, int, str] | None:
    """Try to resynchronise one read. Returns (parsed, symbols corrected, orientation) or None."""
    delta = int(codes.size) - geometry.strand_nt
    if delta == 0 or abs(delta) > max_indel or abs(delta) > geometry.inner_parity_bytes:
        return None
    if (codes == INVALID).any():
        return None
    mapping = get_mapping(geometry.mapping)
    b = mapping.nt_per_byte
    nsym = geometry.inner_parity_bytes
    orientations = [("forward", codes)]
    if reverse_complement:
        orientations.append(("reverse_complement", reverse_complement_codes(codes)))
    for orientation, oriented in orientations:
        budget = max_candidates
        for positions in combinations_with_replacement(range(geometry.frame_bytes), abs(delta)):
            if budget <= 0:
                break
            budget -= 1
            candidate = realign(oriented, positions, delta, b)
            if candidate.size != geometry.strand_nt:
                continue
            frames, erasures = mapping.decode(candidate[None, :])
            erasures = erasures.copy()
            erasures[0, list(positions)] = True
            if int(erasures.sum()) > nsym:
                continue
            parsed = parse_one_corrected(geometry, frames[0], erasures[0])
            if parsed is not None:
                return parsed[0], max(1, parsed[1]), orientation
    return None
