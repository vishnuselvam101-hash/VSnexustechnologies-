"""V2 sequence constraints: the V1 rules plus a tandem-repeat (local complexity) limit.

The V1 :class:`~vnxdna.dna.constraints.ConstraintSpec` covers GC content
(whole strand and sliding window), homopolymer runs and forbidden motifs on
both strands. V2 adds ``max_tandem_repeat_nt``: no run of a period-2 or
period-3 repeat (``ACACAC…``, ``AGTAGTAGT…``) may be longer than this many
nucleotides. Short-period repeats are a common low-complexity pattern that
synthesis and sequencing handle poorly. 0 disables the check.

Guarantees provided *by construction* rather than by screening:

* **Uniqueness.** Every strand of an archive carries a distinct address
  (stripe, shard, kind) inside its frame. Mappings are injective, so no two
  strands of one archive have the same sequence.
* **Minimum distance.** Every frame is a codeword of the inner Reed–Solomon
  code with ``r`` parity bytes, so two different frames differ in at least
  ``r + 1`` bytes. With the ``2bit`` mapping that is at least ``r + 1``
  nucleotide positions.
* **Address robustness.** The address lies inside the CRC-32 and the inner RS
  codeword, so address bytes are corrected like any other byte and a wrong
  address is accepted only after a CRC collision (about 2^-32).

The encoder either satisfies every requested constraint or raises
:class:`~vnxdna.errors.ConstraintError`. It never emits a strand that
violates the recorded rules.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..dna.constraints import ConstraintSpec, analyze
from ..dna.mapping import to_codes
from ..errors import ConfigurationError

TANDEM_PERIODS = (2, 3)


@dataclass(frozen=True)
class ConstraintSpecV2(ConstraintSpec):
    max_tandem_repeat_nt: int = 0  # 0 disables; otherwise the longest allowed period-2/3 repeat run

    def __post_init__(self) -> None:
        super().__post_init__()
        value = self.max_tandem_repeat_nt
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigurationError("max_tandem_repeat_nt must be a non-negative integer")
        if value and value < 2 * max(TANDEM_PERIODS):
            raise ConfigurationError(f"max_tandem_repeat_nt must be 0 or at least {2 * max(TANDEM_PERIODS)}")

    @classmethod
    def from_dict(cls, data: dict) -> "ConstraintSpecV2":
        return cls(**{**data, "forbidden_motifs": tuple(data.get("forbidden_motifs", ()))})


def longest_tandem_repeat(sequence: str, period: int) -> int:
    """Scalar reference: length of the longest run with ``s[i] == s[i + period]`` throughout (plus the period)."""
    best = 0
    run = 0
    for i in range(len(sequence) - period):
        run = run + 1 if sequence[i] == sequence[i + period] else 0
        best = max(best, run)
    return best + period if best else 0


def analyze_v2(sequence: str, spec: ConstraintSpecV2) -> dict:
    report = analyze(sequence, spec)
    if spec.max_tandem_repeat_nt:
        longest = max(longest_tandem_repeat(sequence.upper(), p) for p in TANDEM_PERIODS)
        report["longest_tandem_repeat"] = longest
        if longest > spec.max_tandem_repeat_nt:
            report["violations"].append("tandem_repeat")
            report["valid"] = False
    return report


def _runs(equal: np.ndarray, need: int) -> np.ndarray:
    """True for rows of a boolean (N, W) array that contain ``need`` consecutive True values."""
    width = equal.shape[1]
    if need <= 0:
        return np.ones(equal.shape[0], dtype=bool)
    if width < need:
        return np.zeros(equal.shape[0], dtype=bool)
    acc = equal[:, : width - need + 1].copy()
    for i in range(1, need):
        acc &= equal[:, i: width - need + 1 + i]
    return acc.any(axis=1)


def satisfied_v2(codes: np.ndarray, spec: ConstraintSpec) -> np.ndarray:
    """Vectorised check over (N, L) codes → bool (N,).

    Gives the same answer as the V1 :func:`vnxdna.dna.constraints.satisfied`
    for the V1 rules (tests compare them) and adds the tandem-repeat rule. Runs
    are found with shifted boolean ANDs rather than integer cumulative sums,
    which is several times faster on large batches.
    """
    n, length = codes.shape
    ok = np.ones(n, dtype=bool)
    if length == 0:
        return ok
    is_gc = (codes == 1) | (codes == 2)
    gc = is_gc.sum(axis=1, dtype=np.int32)
    ok &= (spec.gc_min_percent * length <= 100 * gc) & (100 * gc <= spec.gc_max_percent * length)
    if spec.gc_window_nt and length >= spec.gc_window_nt:
        w = spec.gc_window_nt
        csum = np.concatenate([np.zeros((n, 1), dtype=np.int32), np.cumsum(is_gc, axis=1, dtype=np.int32)], axis=1)
        window = csum[:, w:] - csum[:, :-w]
        ok &= ((spec.gc_min_percent * w <= 100 * window) & (100 * window <= spec.gc_max_percent * w)).all(axis=1)
    h = spec.max_homopolymer
    if h and length > h:
        ok &= ~_runs(codes[:, 1:] == codes[:, :-1], h)  # a run of h+1 equal bases is h consecutive equalities
    limit = getattr(spec, "max_tandem_repeat_nt", 0)
    if limit:
        for p in TANDEM_PERIODS:
            if length > p:
                ok &= ~_runs(codes[:, p:] == codes[:, :-p], limit - p + 1)
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
