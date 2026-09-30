"""DNA sequence constraint model (physical-layer rules, separate from mapping).

A :class:`ConstraintSpec` expresses computational synthesis/sequencing
heuristics commonly used in the DNA-storage literature: GC-content bounds
over the whole strand and optionally over sliding windows, a maximum
homopolymer run, and forbidden motifs (checked on both strands if
``check_reverse_complement``). Satisfying these is **not** evidence of
synthesis or sequencing compatibility. They are configurable screening rules.

All checks have a scalar reference (:func:`analyze`) and a vectorized batch
form (:func:`satisfied`) used during strand screening. Tests keep the two in
agreement.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..errors import ConfigurationError
from .mapping import BASES, reverse_complement, to_codes


@dataclass(frozen=True)
class ConstraintSpec:
    gc_min_percent: int = 40
    gc_max_percent: int = 60
    max_homopolymer: int = 4          # 0 disables the check
    gc_window_nt: int = 0             # 0 disables windowed GC; otherwise window length
    forbidden_motifs: tuple[str, ...] = ()
    check_reverse_complement: bool = True

    def __post_init__(self) -> None:
        for name in ("gc_min_percent", "gc_max_percent", "max_homopolymer", "gc_window_nt"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ConfigurationError(f"{name} must be a non-negative integer")
        if not self.gc_min_percent <= self.gc_max_percent <= 100:
            raise ConfigurationError("require 0 <= gc_min_percent <= gc_max_percent <= 100")
        motifs = tuple(self.forbidden_motifs)
        for motif in motifs:
            if not isinstance(motif, str) or not motif or set(motif.upper()) - set(BASES):
                raise ConfigurationError(f"forbidden motif {motif!r} must be a non-empty ACGT string")
        object.__setattr__(self, "forbidden_motifs", tuple(sorted({m.upper() for m in motifs})))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["forbidden_motifs"] = list(self.forbidden_motifs)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ConstraintSpec":
        return cls(**{**data, "forbidden_motifs": tuple(data.get("forbidden_motifs", ()))})

    def all_motifs(self) -> tuple[str, ...]:
        motifs = set(self.forbidden_motifs)
        if self.check_reverse_complement:
            motifs |= {reverse_complement(m) for m in self.forbidden_motifs}
        return tuple(sorted(motifs))


UNCONSTRAINED = ConstraintSpec(gc_min_percent=0, gc_max_percent=100, max_homopolymer=0)


def longest_homopolymer(sequence: str) -> int:
    best = run = 0
    previous = ""
    for base in sequence:
        run = run + 1 if base == previous else 1
        best = max(best, run)
        previous = base
    return best


def analyze(sequence: str, spec: ConstraintSpec) -> dict:
    """Scalar reference analysis of one sequence (upper-case ACGT expected)."""
    seq = sequence.upper()
    length = len(seq)
    gc = seq.count("G") + seq.count("C")
    gc_percent = 100.0 * gc / length if length else 0.0
    violations: list[str] = []
    invalid = sorted(set(seq) - set(BASES))
    if invalid:
        violations.append("invalid_symbols:" + "".join(invalid))
    if length and not spec.gc_min_percent * length <= 100 * gc <= spec.gc_max_percent * length:
        violations.append("gc_content")
    if spec.gc_window_nt and length >= spec.gc_window_nt:
        w = spec.gc_window_nt
        for start in range(length - w + 1):
            window = seq[start:start + w]
            count = window.count("G") + window.count("C")
            if not spec.gc_min_percent * w <= 100 * count <= spec.gc_max_percent * w:
                violations.append("gc_window")
                break
    run = longest_homopolymer(seq)
    if spec.max_homopolymer and run > spec.max_homopolymer:
        violations.append("homopolymer")
    violations += [f"motif:{m}" for m in spec.all_motifs() if m in seq]
    counts = {b: seq.count(b) for b in BASES}
    return {"length": length, "gc_percent": gc_percent, "longest_homopolymer": run, "base_counts": counts,
            "violations": violations, "valid": not violations}


def satisfied(codes: np.ndarray, spec: ConstraintSpec) -> np.ndarray:
    """Vectorized check over (N, L) nucleotide codes → bool (N,)."""
    n, length = codes.shape
    ok = np.ones(n, dtype=bool)
    if length == 0:
        return ok
    is_gc = (codes == 1) | (codes == 2)
    gc = is_gc.sum(axis=1)
    ok &= (spec.gc_min_percent * length <= 100 * gc) & (100 * gc <= spec.gc_max_percent * length)
    if spec.gc_window_nt and length >= spec.gc_window_nt:
        w = spec.gc_window_nt
        csum = np.concatenate([np.zeros((n, 1), dtype=np.int32), np.cumsum(is_gc, axis=1, dtype=np.int32)], axis=1)
        window = csum[:, w:] - csum[:, :-w]
        ok &= ((spec.gc_min_percent * w <= 100 * window) & (100 * window <= spec.gc_max_percent * w)).all(axis=1)
    h = spec.max_homopolymer
    if h and length > h:
        equal = (codes[:, 1:] == codes[:, :-1]).astype(np.int32)  # run of h+1 equal bases == h consecutive equalities
        csum = np.concatenate([np.zeros((n, 1), dtype=np.int32), np.cumsum(equal, axis=1, dtype=np.int32)], axis=1)
        ok &= ~((csum[:, h:] - csum[:, :-h]) == h).any(axis=1)
    for motif in spec.all_motifs():
        pattern = to_codes(motif)
        m = len(pattern)
        if m > length:
            continue
        match = np.ones((n, length - m + 1), dtype=bool)
        for i, c in enumerate(pattern):
            match &= codes[:, i:length - m + 1 + i] == c
        ok &= ~match.any(axis=1)
    return ok
