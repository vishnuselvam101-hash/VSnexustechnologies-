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
byte errors is recovered, because one hypothesis matches it exactly. Two or
three indels need O(F²) or O(F³) hypotheses; the search stops after
``max_candidates`` hypotheses per orientation, so it is exhaustive only while
the hypothesis count fits the budget. A false acceptance needs an RS
miscorrection *and* a CRC-32 collision (about 2^-32 per hypothesis), and the
chunk SHA-256 checks catch it downstream in any case.

V3 changes: all hypotheses of a read are decoded in vectorised batches
(:func:`vnxdna.v2.frame.correct_frames`, one NumPy RS pass per block) instead
of one ``reedsolo`` call each, which is what makes the two- and three-indel
searches practical; reads that contain ``N`` (or other erasure symbols) are
repaired too (VNX-DNA 2.0 skipped them); the first accepted hypothesis in the
same enumeration order as V2 is returned, so results are deterministic.
"""
from __future__ import annotations

from itertools import combinations_with_replacement, islice

import numpy as np

from ..dna.mapping import INVALID, get_mapping, reverse_complement_codes
from .frame import FrameGeometry, parse_many_corrected


def realign(codes: np.ndarray, byte_positions: tuple[int, ...], delta: int, nt_per_byte: int) -> np.ndarray:
    out = codes
    for j in byte_positions:
        at = j * nt_per_byte
        out = np.insert(out, at, INVALID) if delta < 0 else np.delete(out, at)
    return out


HYPOTHESIS_BLOCK = 2048


def burst_span(geometry: FrameGeometry, delta: int) -> int:
    """Bytes flagged as erasures for a burst hypothesis (see :func:`burst_candidates`)."""
    b = get_mapping(geometry.mapping).nt_per_byte
    span = -(-(abs(delta) + b - 1) // b) if delta < 0 else 1
    return span + (1 if geometry.mapping == "rotation3" else 0)


def burst_candidates(codes: np.ndarray, geometry: FrameGeometry, delta: int) -> tuple[np.ndarray, np.ndarray]:
    """V3 burst hypotheses: one contiguous run of ``|delta|`` lost (delta < 0) or extra (delta > 0) bases.

    For every byte boundary ``j`` the run is assumed to start inside byte ``j``:
    ``|delta|`` erasure symbols are inserted (or ``|delta|`` bases removed) at
    nucleotide ``j · b``, which restores the alignment of everything after the
    run. The bytes that can still be wrong are flagged as erasures for the
    inner RS code: for a deletion the ``⌈(|delta| + b − 1) / b⌉`` bytes starting
    at ``j``; for an insertion only byte ``j`` (removing ``|delta|`` bases at
    ``j · b`` leaves at most ``b − 1`` wrong bases, all inside byte ``j``). The
    ``rotation3`` mapping decodes each digit relative to the previous base, so
    one more byte is flagged. That is F hypotheses per orientation for any burst
    length, instead of O(F^|delta|) for independent indels.
    Returns (codes (F, strand_nt), erasure byte masks (F, frame_bytes)).
    """
    b = get_mapping(geometry.mapping).nt_per_byte
    length = abs(delta)
    span = burst_span(geometry, delta)
    f = geometry.frame_bytes
    rows = []
    masks = np.zeros((f, f), dtype=bool)
    for j in range(f):
        at = j * b
        if delta < 0:
            row = np.concatenate([codes[:at], np.full(length, INVALID, dtype=codes.dtype), codes[at:]])
        else:
            row = np.concatenate([codes[:at], codes[at + length:]])
        rows.append(row[: geometry.strand_nt] if row.size >= geometry.strand_nt else
                    np.concatenate([row, np.full(geometry.strand_nt - row.size, INVALID, dtype=codes.dtype)]))
        masks[j, j:j + span] = True
    return np.stack(rows), masks


def repair_burst(codes: np.ndarray, geometry: FrameGeometry, *, max_burst: int, reverse_complement: bool = True
                 ) -> tuple[tuple, int, str] | None:
    """Resynchronise a read that lost or gained one contiguous run of 1..``max_burst`` bases (V3).

    Accepted only if the frame CRC-32 verifies after inner-RS decoding with the
    run's bytes as erasures. Guarantee: a single burst that leaves ``s + 2e ≤ r``
    is recovered, where e is the number of other byte errors and s the flagged
    span: ``⌈(L + b − 1) / b⌉`` bytes for L lost bases, 1 byte for L extra
    bases, plus 1 with the ``rotation3`` mapping.
    """
    delta = int(codes.size) - geometry.strand_nt
    if delta == 0 or abs(delta) > max_burst:
        return None
    if burst_span(geometry, delta) > geometry.inner_parity_bytes:
        return None
    mapping = get_mapping(geometry.mapping)
    orientations = [("forward", codes)]
    if reverse_complement:
        orientations.append(("reverse_complement", reverse_complement_codes(codes)))
    for orientation, oriented in orientations:
        candidates, masks = burst_candidates(oriented, geometry, delta)
        frames, erasures = mapping.decode(candidates)
        erasures = erasures | masks
        usable = np.flatnonzero(erasures.sum(axis=1) <= geometry.inner_parity_bytes)
        if usable.size == 0:
            continue
        parsed, counts = parse_many_corrected(geometry, frames[usable], erasures[usable])
        hits = np.flatnonzero(parsed.ok)
        if hits.size:
            i = int(hits[0])
            return ((int(parsed.kind[i]), int(parsed.tag[i]), int(parsed.stripe[i]), int(parsed.shard[i]), parsed.payload[i].tobytes()),
                    max(1, int(counts[i])), orientation)
    return None


def repair_read(codes: np.ndarray, geometry: FrameGeometry, *, max_indel: int = 1, max_candidates: int = 4096,
                reverse_complement: bool = True) -> tuple[tuple, int, str] | None:
    """Try to resynchronise one read. Returns (parsed, symbols corrected, orientation) or None."""
    delta = int(codes.size) - geometry.strand_nt
    if delta == 0 or abs(delta) > max_indel or abs(delta) > geometry.inner_parity_bytes:
        return None
    mapping = get_mapping(geometry.mapping)
    b = mapping.nt_per_byte
    nsym = geometry.inner_parity_bytes
    orientations = [("forward", codes)]
    if reverse_complement:
        orientations.append(("reverse_complement", reverse_complement_codes(codes)))
    for orientation, oriented in orientations:
        hypotheses = islice(combinations_with_replacement(range(geometry.frame_bytes), abs(delta)), max_candidates)
        while True:
            block = list(islice(hypotheses, HYPOTHESIS_BLOCK))
            if not block:
                break
            candidates = np.stack([realign(oriented, positions, delta, b) for positions in block])
            frames, erasures = mapping.decode(candidates)
            erasures = erasures.copy()
            for row, positions in enumerate(block):
                erasures[row, list(positions)] = True
            usable = np.flatnonzero(erasures.sum(axis=1) <= nsym)
            if usable.size == 0:
                continue
            parsed, counts = parse_many_corrected(geometry, frames[usable], erasures[usable])
            hits = np.flatnonzero(parsed.ok)
            if hits.size:
                i = int(hits[0])  # first accepted hypothesis in enumeration order (deterministic, as in V2)
                result = (int(parsed.kind[i]), int(parsed.tag[i]), int(parsed.stripe[i]), int(parsed.shard[i]),
                          parsed.payload[i].tobytes())
                return result, max(1, int(counts[i])), orientation
    return None
