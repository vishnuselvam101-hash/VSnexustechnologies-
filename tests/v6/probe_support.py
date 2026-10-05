"""Read pools for the probe and dispatch tests (spec §3.10; EXP-PROBE-1). SYNTHETIC SOFTWARE TEST data.

* ``frame4_pool``: a normal VNX4 frame-4 pool (any profile, V6 outer options optional);
* ``nibble_pool``: the same strands with the frame-version nibble changed (default 7), i.e. what a future encoder could
  write: every frame is valid (CRC, inner RS) except for its version; built exactly like ``build_strands``;
* ``v3_pool``: V2/V3 frame-format-5 strands written by the legacy ``vnx-dna`` encoder;
* ``v1_pool``: V1 frame-format-4 strands (``VNX-DNA/4 scrambler`` domain);
* ``random_pool``: uniformly random sequences.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from vnxdna.codec.codecs import InnerRS
from vnxdna.core.crc import crc32_bytes_be, crc32_rows
from vnxdna.dnaenc.constraints import ConstraintConfig, satisfied_batch
from vnxdna.dnaenc.frame4 import plain_rows
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.dnaenc.mapping import bytes_to_nt
from vnxdna.dnaenc.markers import insert_markers
from vnxdna.dnaenc.scrambler import VARIANTS, keystreams
from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en

ASCII = np.frombuffer(b"ACGT", dtype=np.uint8)


def write_fasta(path: Path, seqs: list[str]) -> Path:
    path.write_text("".join(f">r{i}\n{s}\n" for i, s in enumerate(seqs)))
    return path


def read_fasta(path: Path) -> list[str]:
    lines = path.read_text().split()
    return [s for s in lines if not s.startswith(">")]


def frame4_pool(d: Path, profile: str = "v4-balanced", size: int = 6000, seed: int = 1, **v6) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    datagen.generate(d / "in.bin", size, "random", seed)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions(profile=profile, **v6))
    return d / "s.fasta"


def nibble_strands(profile: str, n: int, nibble: int = 7, seed: int = 0) -> list[str]:
    """``n`` frame-4-shaped strands whose version nibble is ``nibble`` (screened like build_strands)."""
    lay = PROFILES[profile][0]
    rng = np.random.default_rng(seed)
    payloads = rng.integers(0, 256, (n, lay.payload_bytes), dtype=np.uint8)
    plain = plain_rows(0x3A5C, 0, np.arange(n) // 80, np.arange(n) % 80, payloads)
    plain[:, 0] = (nibble << 4) | (plain[:, 0] & 15)
    p = lay.payload_bytes
    plain[:, 9 + p:] = crc32_bytes_be(crc32_rows(plain[:, : 9 + p]))
    span = plain.shape[1]
    ks, inner, cfg = keystreams(span), InnerRS(lay.inner_parity), ConstraintConfig()
    out = np.empty((n, lay.strand_nt), dtype=np.uint8)
    todo = np.arange(n)
    for v in range(VARIANTS):
        if not todo.size:
            break
        msg = np.empty((todo.size, 1 + span), dtype=np.uint8)
        msg[:, 0] = v
        msg[:, 1:] = plain[todo] ^ ks[v]
        strands = insert_markers(lay, bytes_to_nt(np.concatenate([msg, inner.parity(msg)], axis=1)))
        ok = satisfied_batch(strands, cfg)
        out[todo[ok]] = strands[ok]
        todo = todo[~ok]
    return [ASCII[r].tobytes().decode() for r in out]


def nibble_pool(d: Path, profile: str = "v4-balanced", n: int = 400, nibble: int = 7, seed: int = 0) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    return write_fasta(d / f"nibble{nibble}.fasta", nibble_strands(profile, n, nibble, seed))


def v3_pool(d: Path, size: int = 6000, seed: int = 2) -> Path:
    from vnxdna.v2 import api as v2api
    d.mkdir(parents=True, exist_ok=True)
    datagen.generate(d / "in3.bin", size, "random", seed)
    v2api.store(d / "in3.bin", d / "a.vxdna", workers=1)
    v2api.encode(d / "a.vxdna", d / "v3.fasta", workers=1, write_index=False)
    return d / "v3.fasta"


def v1_pool(d: Path, size: int = 6000, seed: int = 3) -> Path:
    from vnxdna import api as v1api
    d.mkdir(parents=True, exist_ok=True)
    datagen.generate(d / "in1.bin", size, "random", seed)
    (d / "c1.vxdna").write_bytes(v1api.store_bytes((d / "in1.bin").read_bytes(), name="in1.bin"))
    v1api.encode(d / "c1.vxdna", d / "v1.fasta")
    return d / "v1.fasta"


def random_pool(d: Path, n: int = 400, seed: int = 4, lengths=(120, 260)) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    seqs = [ASCII[rng.integers(0, 4, int(rng.integers(*lengths)))].tobytes().decode() for _ in range(n)]
    return write_fasta(d / "random.fasta", seqs)
