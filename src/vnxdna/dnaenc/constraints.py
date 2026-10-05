"""Biological sequence-constraint engine (configurable screening rules + machine-readable diagnostics).

Rules (each can be disabled; none is presented as a universal biological truth —
thresholds depend on the synthesis and sequencing platform):

* ``GC_CONTENT``      whole-sequence GC fraction within [gc_min_percent, gc_max_percent]
* ``GC_WINDOW``       every window of ``gc_window_nt`` within [gc_window_min_percent, gc_window_max_percent]
* ``HOMOPOLYMER``     no run of the same base longer than ``max_homopolymer``
* ``TANDEM_REPEAT``   no period-2/3 repeat run longer than ``max_tandem_repeat_nt``
* ``FORBIDDEN_MOTIF`` none of ``forbidden_motifs`` (and their reverse complements if enabled)
* ``LENGTH``          length within [min_length, max_length]
* ``INVALID_BASE``    only A, C, G, T

Codes: A=0 C=1 G=2 T=3 (same as V3).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from vnxdna.core.errors import VNXConfigurationError, VNXFormatError

BASES = "ACGT"
_ASCII = np.full(256, 255, dtype=np.uint8)
for _i, _b in enumerate(BASES):
    _ASCII[ord(_b)] = _i
    _ASCII[ord(_b.lower())] = _i
RULES = ("GC_CONTENT", "GC_WINDOW", "HOMOPOLYMER", "TANDEM_REPEAT", "FORBIDDEN_MOTIF", "LENGTH", "INVALID_BASE")


def to_codes(seq: str | bytes) -> np.ndarray:
    raw = seq.encode("ascii", errors="replace") if isinstance(seq, str) else seq
    return _ASCII[np.frombuffer(raw, dtype=np.uint8)]


def reverse_complement(seq: str) -> str:
    return seq.upper()[::-1].translate(str.maketrans("ACGT", "TGCA"))


@dataclass
class ConstraintConfig:
    gc_min_percent: int = 40
    gc_max_percent: int = 60
    gc_window_nt: int = 0
    gc_window_min_percent: int = 25
    gc_window_max_percent: int = 75
    max_homopolymer: int = 4
    max_tandem_repeat_nt: int = 0
    forbidden_motifs: list[str] = field(default_factory=list)
    check_reverse_complement: bool = True
    min_length: int = 0
    max_length: int = 0

    def validate(self) -> "ConstraintConfig":
        ints = ("gc_min_percent", "gc_max_percent", "gc_window_nt", "gc_window_min_percent", "gc_window_max_percent",
                "max_homopolymer", "max_tandem_repeat_nt", "min_length", "max_length")
        for name in ints:
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise VNXConfigurationError(f"constraint {name} must be a non-negative integer")
        if not self.gc_min_percent <= self.gc_max_percent <= 100:
            raise VNXConfigurationError("invalid GC constraint: require 0 <= gc_min_percent <= gc_max_percent <= 100",
                                        details={"gc_min_percent": self.gc_min_percent, "gc_max_percent": self.gc_max_percent})
        if not self.gc_window_min_percent <= self.gc_window_max_percent <= 100:
            raise VNXConfigurationError("invalid windowed GC constraint")
        if self.max_tandem_repeat_nt and self.max_tandem_repeat_nt < 6:
            raise VNXConfigurationError("max_tandem_repeat_nt must be 0 or >= 6")
        if self.max_length and self.min_length > self.max_length:
            raise VNXConfigurationError("min_length > max_length")
        clean = []
        for m in self.forbidden_motifs:
            if not isinstance(m, str) or not m or set(m.upper()) - set(BASES) or len(m) > 64:
                raise VNXConfigurationError(f"forbidden motif {m!r} must be a non-empty ACGT string of at most 64 nt")
            clean.append(m.upper())
        self.forbidden_motifs = sorted(set(clean))
        return self

    def motifs(self) -> list[str]:
        out = set(self.forbidden_motifs)
        if self.check_reverse_complement:
            out |= {reverse_complement(m) for m in self.forbidden_motifs}
        return sorted(out)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ConstraintConfig":
        if not isinstance(data, dict):
            raise VNXConfigurationError("configuration must be a JSON object")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise VNXConfigurationError(f"unknown constraint keys: {sorted(unknown)}")
        return cls(**data).validate()

    @classmethod
    def load(cls, path: str | os.PathLike) -> "ConstraintConfig":
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError) as error:
            raise VNXConfigurationError(f"cannot read constraint config {path}: {error}") from None
        if not isinstance(data, dict):
            raise VNXConfigurationError("constraint config must be a JSON object")
        return cls.from_dict(data)


UNCONSTRAINED = ConstraintConfig(gc_min_percent=0, gc_max_percent=100, max_homopolymer=0)


def _runs(equal: np.ndarray, need: int) -> np.ndarray:
    """Rows of a boolean (N, W) matrix that contain ``need`` consecutive True values."""
    width = equal.shape[1]
    if need <= 0:
        return np.ones(equal.shape[0], dtype=bool)
    if width < need:
        return np.zeros(equal.shape[0], dtype=bool)
    acc = equal[:, : width - need + 1].copy()
    for i in range(1, need):
        acc &= equal[:, i: width - need + 1 + i]
    return acc.any(axis=1)


def violations_batch(codes: np.ndarray, cfg: ConstraintConfig) -> dict[str, np.ndarray]:
    """Vectorised rule check of equal-length sequences (N, L) → {rule: (N,) bool violated}."""
    n, length = codes.shape
    out = {r: np.zeros(n, dtype=bool) for r in RULES}
    if n == 0:
        return out
    out["INVALID_BASE"] = (codes > 3).any(axis=1)
    if cfg.max_length and length > cfg.max_length or length < cfg.min_length:
        out["LENGTH"][:] = True
    if length == 0:
        return out
    is_gc = (codes == 1) | (codes == 2)
    gc = is_gc.sum(axis=1, dtype=np.int32)
    out["GC_CONTENT"] = ~((cfg.gc_min_percent * length <= 100 * gc) & (100 * gc <= cfg.gc_max_percent * length))
    w = cfg.gc_window_nt
    if w and length >= w:
        csum = np.concatenate([np.zeros((n, 1), dtype=np.int32), np.cumsum(is_gc, axis=1, dtype=np.int32)], axis=1)
        win = csum[:, w:] - csum[:, :-w]
        out["GC_WINDOW"] = ~((cfg.gc_window_min_percent * w <= 100 * win) & (100 * win <= cfg.gc_window_max_percent * w)).all(axis=1)
    h = cfg.max_homopolymer
    if h and length > h:
        out["HOMOPOLYMER"] = _runs(codes[:, 1:] == codes[:, :-1], h)
    t = cfg.max_tandem_repeat_nt
    if t:
        bad = np.zeros(n, dtype=bool)
        for p in (2, 3):
            if length > p:
                bad |= _runs(codes[:, p:] == codes[:, :-p], t - p + 1)
        out["TANDEM_REPEAT"] = bad
    for motif in cfg.motifs():
        pat = to_codes(motif)
        m = len(pat)
        if m > length:
            continue
        match = np.ones((n, length - m + 1), dtype=bool)
        for i, c in enumerate(pat):
            match &= codes[:, i:length - m + 1 + i] == c
        out["FORBIDDEN_MOTIF"] |= match.any(axis=1)
    return out


def satisfied_batch(codes: np.ndarray, cfg: ConstraintConfig) -> np.ndarray:
    v = violations_batch(codes, cfg)
    bad = np.zeros(codes.shape[0], dtype=bool)
    for mask in v.values():
        bad |= mask
    return ~bad


def longest_homopolymer(codes: np.ndarray) -> int:
    if codes.size == 0:
        return 0
    change = np.flatnonzero(np.diff(codes.astype(np.int16)) != 0)
    bounds = np.concatenate([[-1], change, [codes.size - 1]])
    return int(np.diff(bounds).max())


def diagnose(sequence: str, cfg: ConstraintConfig) -> dict:
    """Machine-readable diagnostics for one sequence."""
    codes = to_codes(sequence)
    v = violations_batch(codes[None, :], cfg)
    violations = [r for r in RULES if bool(v[r][0])]
    valid_codes = codes[codes < 4]
    gc = float(((valid_codes == 1) | (valid_codes == 2)).mean()) if valid_codes.size else 0.0
    motifs = [m for m in cfg.motifs() if m in sequence.upper()]
    return {"valid": not violations, "length": int(codes.size), "gc_percent": round(100 * gc, 3),
            "gc_fraction": round(gc, 5), "max_homopolymer": longest_homopolymer(codes), "violations": violations,
            "forbidden_motifs_found": motifs}


def iter_fasta(path: str | os.PathLike, max_records: int = 50_000_000, max_len: int = 100_000):
    """Stream (name, sequence) from FASTA (or plain one-sequence-per-line) with bounded record length."""
    name = None
    parts: list[str] = []
    count = 0
    try:
        handle = open(path, "r", encoding="ascii", errors="replace")
    except OSError as error:
        raise VNXFormatError(f"cannot read {path}: {error.strerror or error}") from None
    with handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(parts)
                    count += 1
                    if count >= max_records:
                        raise VNXFormatError(f"more than {max_records} records")
                name, parts = line[1:].strip() or f"record{count}", []
            elif line.startswith("@") and name is None and not parts:
                raise VNXFormatError(f"{path} looks like FASTQ; vnx validate expects FASTA strands")
            else:
                if name is None:
                    name = f"line{lineno}"
                    parts = [line]
                    yield name, line
                    name, parts = None, []
                    count += 1
                    continue
                parts.append(line)
                if sum(len(p) for p in parts) > max_len:
                    raise VNXFormatError(f"record {name!r} is longer than {max_len} nt")
        if name is not None:
            yield name, "".join(parts)


def validate_file(path: str | os.PathLike, cfg: ConstraintConfig, *, max_reported: int = 100) -> dict:
    """Validate every sequence of a FASTA file; returns a summary plus the first ``max_reported`` failures."""
    total = valid = 0
    counts = {r: 0 for r in RULES}
    failures = []
    gc_min, gc_max, hp_max = 101.0, -1.0, 0
    for name, seq in iter_fasta(path):
        d = diagnose(seq, cfg)
        total += 1
        gc_min, gc_max = min(gc_min, d["gc_percent"]), max(gc_max, d["gc_percent"])
        hp_max = max(hp_max, d["max_homopolymer"])
        if d["valid"]:
            valid += 1
        else:
            for r in d["violations"]:
                counts[r] += 1
            if len(failures) < max_reported:
                failures.append({"name": name, **d})
    return {"valid": total > 0 and valid == total, "sequences": total, "valid_sequences": valid,
            "invalid_sequences": total - valid, "violation_counts": {k: v for k, v in counts.items() if v},
            "gc_percent_range": [gc_min if total else None, gc_max if total else None], "max_homopolymer": hp_max,
            "constraints": cfg.to_dict(), "failures": failures}
