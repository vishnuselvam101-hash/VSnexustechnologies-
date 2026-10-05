"""Strand-level loss models for SIMULATED outer-code experiments (not fitted to any synthesis or storage platform).

The V4 channel (vnxdna.v4.channel) models i.i.d. strand dropout and per-read errors. Outer-code resilience also
depends on *correlated* loss, modelled here on the strand file's order (taken as synthesis / pool order):

* ``iid``     every strand is lost independently with probability ``dropout``
* ``bursts``  ``burst_count`` contiguous runs of ``burst_length`` strands are lost, starts uniform over the file
              (runs may overlap)

The surviving strands are written as a new strand file that can then be passed through the V4 channel for coverage
sampling and base errors. Deterministic: the mask is a function of (strand count, configuration, seed).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from vnxdna.dnaenc.constraints import iter_fasta
from vnxdna.core.errors import V6ConfigurationError


@dataclass(frozen=True)
class LossConfig:
    dropout: float = 0.0
    burst_count: int = 0
    burst_length: int = 0
    seed: int = 0

    def validate(self) -> "LossConfig":
        if not 0.0 <= self.dropout < 1.0:
            raise V6ConfigurationError("dropout must be in [0, 1)")
        if self.burst_count < 0 or self.burst_length < 0:
            raise V6ConfigurationError("burst_count and burst_length must be >= 0")
        return self

    def to_dict(self) -> dict:
        return asdict(self)


def loss_mask(n: int, cfg: LossConfig) -> np.ndarray:
    """(n,) bool: True = strand survives."""
    cfg.validate()
    rng = np.random.default_rng([cfg.seed, 0x56360001])
    keep = rng.random(n) >= cfg.dropout if cfg.dropout else np.ones(n, dtype=bool)
    if cfg.burst_count and cfg.burst_length and n:
        L = min(cfg.burst_length, n)
        starts = rng.integers(0, n - L + 1, size=cfg.burst_count)
        for st in starts.tolist():
            keep[st:st + L] = False
    return keep


def apply_loss(strands: str, output: str, cfg: LossConfig) -> dict:
    """Write the surviving strands of a FASTA strand file to ``output`` (FASTA). Returns counts."""
    recs = list(iter_fasta(strands))
    keep = loss_mask(len(recs), cfg)
    with open(output, "w") as f:
        for (name, seq), k in zip(recs, keep.tolist()):
            if k:
                f.write(f">{name}\n{seq}\n")
    return {"strands": len(recs), "kept": int(keep.sum()), "lost": int((~keep).sum()), "loss": cfg.to_dict()}
