"""EXPERIMENTAL synchronization: RS-assisted realignment of reads with indels.

Status: research feature, **off by default** (``--experimental-indel-repair``).
Reed–Solomon codes correct substitutions and erasures, not insertions or
deletions. An indel shifts every later symbol. Without resynchronization, a
read whose length differs from the strand length is discarded and becomes an
erasure for the outer code (which is always safe).

Method (for a read that is ``d = len(read) − L`` bases off, 1 ≤ |d| ≤ max_indel):

* Hypothesize that the |d| indels fall in frame bytes ``j₁ ≤ … ≤ j_|d|``.
  For a deletion, insert an unreadable placeholder at the first nucleotide of
  byte j. For an insertion, remove the first nucleotide of byte j. Then mark
  every hypothesized byte as an **erasure**.
* If the true indel lies inside byte j, only that byte is misaligned, and it is
  flagged. RS decoding with those erasures (``2e + f ≤ nsym``) then restores
  the frame, and it is accepted **only if the frame CRC-32 verifies**.
* Hypotheses are enumerated in byte order, in both orientations, up to
  ``max_indel_candidates`` per read.

Guarantee and limits (see docs/CHANNEL_MODEL.md for measurements):

* A read with exactly one indel and at most ``(nsym − 1) // 2`` substitutions
  is recovered, because one hypothesis matches it exactly.
* For |d| = 2 all byte pairs are tried, which costs O(F²) decodes per read.
* Net-zero indel pairs (an insertion plus a deletion) keep the read length and
  are handled only as ordinary byte errors by the inner code.
* A false acceptance needs an RS miscorrection *and* a CRC-32 collision,
  roughly 2^-32 per hypothesis. The chunk and object SHA-256 checks catch it
  downstream in any case.
"""
from __future__ import annotations

from itertools import combinations_with_replacement

import numpy as np

from ..dna.mapping import INVALID, _ASCII_TO_CODE, get_mapping, reverse_complement_codes
from ..dna.strand import StrandGeometry, parse_frame


def _to_codes(sequence: str) -> np.ndarray | None:
    raw = np.frombuffer(sequence.encode("ascii", errors="replace"), dtype=np.uint8)
    codes = _ASCII_TO_CODE[raw]
    if (codes == 255).any():
        return None
    return codes.astype(np.uint8)


def realign(codes: np.ndarray, byte_positions: tuple[int, ...], delta: int, nt_per_byte: int) -> np.ndarray:
    """Apply one hypothesis: fix the length by inserting/deleting at the start of each listed byte."""
    out = codes
    for j in byte_positions:  # ascending; coordinates are final (strand-length) coordinates
        at = j * nt_per_byte
        if delta < 0:
            out = np.insert(out, at, INVALID)
        else:
            out = np.delete(out, at)
    return out


def repair_read(sequence: str, geometry: StrandGeometry, options) -> tuple[tuple, int, str, str] | None:
    """Try to resynchronize one read. Returns (parsed, symbols_corrected, orientation, "indel") or None."""
    delta = len(sequence) - geometry.strand_nt
    if delta == 0 or abs(delta) > options.max_indel:
        return None
    codes = _to_codes(sequence)
    if codes is None:
        return None
    mapping = get_mapping(geometry.mapping)
    b = mapping.nt_per_byte
    nsym = geometry.inner_parity_bytes
    if abs(delta) > nsym:
        return None
    orientations = [("forward", codes)]
    if options.reverse_complement:
        orientations.append(("reverse_complement", reverse_complement_codes(codes)))
    for orientation, oriented in orientations:
        budget = options.max_indel_candidates
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
            parsed = parse_frame(geometry, frames[0], erasures[0])
            if parsed is not None:
                return parsed[0], max(1, parsed[1]), orientation, "indel"
    return None
