"""Container → DNA strands: redundancy, error correction and DNA mapping.

Layers, in order:

1. **Striping.** Each stored chunk is split into ``P``-byte shards and grouped
   into stripes of ``K`` data shards (the last stripe of a chunk is zero-padded).
2. **Outer erasure code.** Cauchy Reed–Solomon over GF(256) adds ``M`` parity
   shards per stripe. Any ``M`` lost shards of a stripe are recoverable.
3. **Shortening.** Data shards of a chunk's last stripe that lie *entirely*
   in the zero padding are not emitted. The decoder knows from the
   authenticated manifest that they are zero. This is the standard
   shortened-code construction and keeps the MDS property: any ``M``
   emitted strands of such a stripe may still be lost.
4. **Framing.** Every shard becomes a strand frame with in-band address,
   CRC-32 and inner RS parity (see :mod:`vnxdna.dna.strand`).
5. **Mapping and screening** to A/C/G/T under the manifest's constraints.
6. **Metadata strands.** The canonical manifest is also written as strands
   (Cauchy 8+8, frame kind 1), so a FASTA file alone is a complete archive.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..container import manifest as mf
from ..dna.constraints import ConstraintSpec
from ..dna.mapping import to_string
from ..dna.strand import CRC_BYTES, HEADER_BYTES, KIND_DATA, KIND_META, StrandGeometry, build_strands
from ..ecc.cauchy import CauchyErasureCode

META_DATA_SHARDS = 8
META_PARITY_SHARDS = 8
META_MAGIC = b"VNXM"


@dataclass
class EncodedStrands:
    sequences: list[str]
    labels: list[str]
    stats: dict[str, Any]


def geometry_of(m: mf.Manifest) -> StrandGeometry:
    return StrandGeometry(m.strand.mapping, m.strand.payload_bytes, m.strand.inner_parity_bytes)


def constraints_of(m: mf.Manifest) -> ConstraintSpec:
    c = m.constraints
    return ConstraintSpec(gc_min_percent=c.gc_min_percent, gc_max_percent=c.gc_max_percent, max_homopolymer=c.max_homopolymer,
                          gc_window_nt=c.gc_window_nt, forbidden_motifs=tuple(c.forbidden_motifs),
                          check_reverse_complement=c.check_reverse_complement)


def used_data_shards(stored_size: int, stripe_index_in_chunk: int, stripe_count: int, data_shards: int, payload: int) -> int:
    """Number of data shards of a chunk stripe that carry stored bytes (the rest are implicit zeros)."""
    if stripe_index_in_chunk < stripe_count - 1:
        return data_shards
    tail = stored_size - (stripe_count - 1) * data_shards * payload
    return -(-tail // payload)


def _stripe_rows(stored: bytes, code: CauchyErasureCode, payload: int, first_stripe: int, *, shorten: bool):
    stripe_bytes = code.data_shards * payload
    stripes = -(-len(stored) // stripe_bytes)
    if stripes == 0:
        empty = np.zeros(0, dtype=np.int64)
        return np.zeros((0, payload), dtype=np.uint8), empty, empty
    buffer = np.zeros(stripes * stripe_bytes, dtype=np.uint8)
    buffer[: len(stored)] = np.frombuffer(stored, dtype=np.uint8)
    data = buffer.reshape(stripes, code.data_shards, payload)
    full = np.concatenate([data, code.encode(data)], axis=1)
    n = code.total_shards
    keep = np.ones((stripes, n), dtype=bool)
    if shorten:
        used = used_data_shards(len(stored), stripes - 1, stripes, code.data_shards, payload)
        keep[-1, used:code.data_shards] = False
    rows = full[keep]
    stripe_index = np.repeat(np.arange(first_stripe, first_stripe + stripes), n).reshape(stripes, n)[keep]
    shard_index = np.tile(np.arange(n), (stripes, 1))[keep]
    return rows, stripe_index, shard_index


def metadata_stream(manifest_bytes: bytes) -> bytes:
    return META_MAGIC + len(manifest_bytes).to_bytes(4, "big") + manifest_bytes


def encode_container(manifest: mf.Manifest, manifest_bytes: bytes, stored_chunks: list[bytes]) -> EncodedStrands:
    """Produce the complete strand pool (data + parity + metadata) for a container."""
    geometry = geometry_of(manifest)
    spec = constraints_of(manifest)
    code = CauchyErasureCode(manifest.erasure_code.data_shards, manifest.erasure_code.parity_shards)
    p = geometry.payload_bytes
    rows, stripes, shards, kinds = [], [], [], []
    for record, stored in zip(manifest.chunks, stored_chunks):
        r, s, i = _stripe_rows(stored, code, p, record.first_stripe, shorten=True)
        rows.append(r); stripes.append(s); shards.append(i); kinds.append(np.full(r.shape[0], KIND_DATA, dtype=np.uint8))
    data_strands = int(sum(r.shape[0] for r in rows))
    meta_code = CauchyErasureCode(META_DATA_SHARDS, META_PARITY_SHARDS)
    r, s, i = _stripe_rows(metadata_stream(manifest_bytes), meta_code, p, 0, shorten=False)
    rows.append(r); stripes.append(s); shards.append(i); kinds.append(np.full(r.shape[0], KIND_META, dtype=np.uint8))
    meta_strands = int(r.shape[0])
    tag = manifest.archive_tag
    all_kinds, all_stripes, all_shards = np.concatenate(kinds), np.concatenate(stripes), np.concatenate(shards)
    codes, variants = build_strands(geometry, spec, tag, all_kinds, all_stripes, all_shards, np.concatenate(rows))
    sequences = [to_string(row) for row in codes]
    labels = [f"vnx4:{tag:06x}:{'d' if k == KIND_DATA else 'm'}:{st}:{sh}"
              for k, st, sh in zip(all_kinds.tolist(), all_stripes.tolist(), all_shards.tolist())]
    stats = efficiency(manifest, data_strands, meta_strands)
    stats.update({"screening_mean_variant": float(variants.mean()) if variants.size else 0.0,
                  "screening_max_variant": int(variants.max()) if variants.size else 0})
    return EncodedStrands(sequences, labels, stats)


def efficiency(m: mf.Manifest, data_strands: int, meta_strands: int) -> dict[str, Any]:
    """Storage accounting that separates compression, encryption, ECC and DNA expansion."""
    k, mpar = m.erasure_code.data_shards, m.erasure_code.parity_shards
    p = m.strand.payload_bytes
    parity_strands = m.erasure_code.stripe_count * mpar
    data_shard_strands = data_strands - parity_strands
    nt = m.strand.strand_nt
    total_strands = data_strands + meta_strands
    total_nt = total_strands * nt
    original = m.content.size if m.content is not None else None
    encrypted_overhead = 16 * len(m.chunks) if m.encrypted else 0  # one AES-GCM tag per chunk
    stored = m.stored_size
    frame_overhead_bytes = total_strands * (HEADER_BYTES + CRC_BYTES + m.strand.inner_parity_bytes)
    out: dict[str, Any] = {
        "original_bytes": original,
        "stored_bytes": stored,
        "compressed_bytes": stored - encrypted_overhead,
        "encryption_overhead_bytes": encrypted_overhead,
        "chunks": len(m.chunks),
        "stripes": m.erasure_code.stripe_count,
        "outer_code": f"{k}+{mpar} Cauchy RS",
        "data_shard_strands": data_shard_strands,
        "padding_bytes": data_shard_strands * p - stored,
        "parity_strands": parity_strands,
        "outer_parity_bytes": parity_strands * p,
        "metadata_strands": meta_strands,
        "strands_total": total_strands,
        "strand_nt": nt,
        "frame_overhead_bytes": frame_overhead_bytes,
        "inner_parity_bytes_total": total_strands * m.strand.inner_parity_bytes,
        "dna_bases_total": total_nt,
        "bits_per_base_raw_mapping": {"2bit": 2.0, "rotation3": 8 / 6, "codebook8": 1.0}[m.strand.mapping],
    }
    if original:
        out["bases_per_original_byte"] = total_nt / original
        out["net_bits_per_base"] = 8 * original / total_nt
        out["compression_ratio"] = (stored - encrypted_overhead) / original
    else:
        out["bases_per_original_byte"] = None
        out["net_bits_per_base"] = None
        out["compression_ratio"] = None
    if stored:
        out["redundancy_overhead_percent"] = 100.0 * parity_strands * p / stored
        out["total_expansion_bases_per_stored_byte"] = total_nt / stored
    return out
