"""Shared helpers for the V7 read-clustering tests (SYNTHETIC test data; every channel here is SIMULATED)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.dnaenc.mapping import nt_to_bytes
from vnxdna.sync.template import strip_markers_exact
from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en

CODE = {c: i for i, c in enumerate("ACGT")}
RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)


def read_fasta_codes(path: Path) -> list[np.ndarray]:
    out = []
    for line in Path(path).read_text().splitlines():
        if line and not line.startswith(">"):
            out.append(np.array([CODE[c] for c in line.strip()], dtype=np.uint8))
    return out


def make_strands(work: Path, size: int, profile: str = "v4-balanced", seed: int = 7701, data: bytes | None = None,
                 **archive_kw) -> tuple[bytes, list[np.ndarray]]:
    """(container bytes, encoded strands as base codes) of one small uncompressed archive."""
    work.mkdir(parents=True, exist_ok=True)
    if data is None:
        datagen.generate(work / "in.bin", size, "random", seed)
    else:
        (work / "in.bin").write_bytes(data)
    ar.build_archive([work / "in.bin"], work / "a.vnx", ar.ArchiveOptions(compression="none"), **archive_kw)
    en.encode_container(work / "a.vnx", work / "s.fasta", en.DNAOptions(profile=profile))
    return (work / "a.vnx").read_bytes(), read_fasta_codes(work / "s.fasta")


def truth_frames(lay, strands: list[np.ndarray]) -> set:
    """The (kind, tag, group, symbol, payload bytes) of every strand, decoded from the exact strand."""
    fb, _ = strip_markers_exact(lay, np.stack(strands))
    P = decode_frames(lay, nt_to_bytes(fb))
    assert P.ok.all()
    return {(int(P.kind[i]), int(P.tag[i]), int(P.group[i]), int(P.symbol[i]), P.payload[i].tobytes())
            for i in range(len(strands))}


def frame_key(f: dict) -> tuple:
    return (f["kind"], f["tag"], f["group"], f["symbol"], np.asarray(f["payload"], dtype=np.uint8).tobytes())


def noisy(rng: np.random.Generator, s: np.ndarray, ps: float = 0.02, pi: float = 0.01, pd: float = 0.03) -> np.ndarray:
    """An i.i.d. substitution/insertion/deletion read of ``s`` (SIMULATED; test channel only)."""
    u = rng.random(s.size)
    keep = u >= pd
    ins = (u >= pd) & (u < pd + pi)
    sub = rng.random(s.size) < ps
    base = np.where(sub, rng.integers(0, 4, s.size), s).astype(np.uint8)
    out = []
    for b, k, i in zip(base.tolist(), keep.tolist(), ins.tolist()):
        if i:
            out.append(int(rng.integers(4)))
        if k:
            out.append(b)
    return np.array(out, dtype=np.uint8)


def revcomp(r: np.ndarray) -> np.ndarray:
    return RC[np.asarray(r)[::-1]]
