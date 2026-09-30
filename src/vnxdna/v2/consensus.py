"""Consensus of clustered reads (multi-read synchronization), exposing ambiguity as ``N``.

For every cluster (reads of one designed strand, forward-oriented):

1. **Draft.** Quality-weighted per-position vote over the reads that have the
   strand length L. If there are none, the read whose length is closest to L
   (trimmed or ``N``-padded to L) is the draft.
2. **Synchronization.** Every read of another length is aligned to its
   cluster's draft with banded edit-distance alignment (:mod:`.align`,
   batched across many clusters). The alignment projects the read onto the L
   strand positions: a base, or a gap where the read has a deletion. Bases the
   read inserted have no strand position and are dropped. Reads whose
   alignment costs more than ``max_edit_fraction × L`` edits are excluded
   from the vote (for example a tentative member with a wrong address).
3. **Vote.** At each position the weights of A, C, G, T and "deleted" are
   summed. A base's weight is its Phred quality (a log-likelihood-style
   weight); ``N`` contributes nothing. The winner must carry at least
   ``min_winner_share`` of the two leading weights. Otherwise the position
   is written as ``N``. A winning "deleted" removes the position.
4. **Check.** The consensus is test-parsed as a frame (CRC-32, then inner RS).
   If it does not verify and the cluster has a read that verified on its own,
   that read is written instead, and the fallback is counted.

``N`` is not a guess. The decoder treats it as an erasure, and the inner RS
code corrects ``e`` errors plus ``f`` erasures when ``2e + f ≤ r``, so an honest
``N`` costs half as much as a wrong base. Consensus never decides correctness:
the frame CRC, the chunk SHA-256 and the object SHA-256 do.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from ..dna.mapping import _ASCII_TO_CODE, _CODE_TO_ASCII, get_mapping
from ..errors import InvalidInputError, OutputError
from .align import GAP, align_batch
from .cluster import iter_clusters
from .frame import FrameGeometry, parse_batch, parse_one_corrected
from .strandio import StrandWriter

BLOCK_READS = 16384
BLOCK_CLUSTERS = 2048


def _codes(text: str) -> np.ndarray:
    codes = _ASCII_TO_CODE[np.frombuffer(text.encode("ascii"), dtype=np.uint8)]
    return np.where(codes == 255, 4, codes).astype(np.uint8)


def _quals(text: str) -> np.ndarray:
    return (np.frombuffer(text.encode("ascii"), dtype=np.uint8).astype(np.int16) - 33).clip(0, 93).astype(np.uint8)


def _vote(block: list[dict], length: int, band: int, max_edit_fraction: float, min_winner_share: float,
          single_read_min_quality: int, stats: Counter) -> list[np.ndarray]:
    """Consensus for a block of clusters. Returns one code array per cluster (4 = N)."""
    reads: list[np.ndarray] = []
    quals: list[np.ndarray] = []
    owner: list[int] = []
    for ci, cluster in enumerate(block):
        qs = cluster.get("quals") or [None] * len(cluster["reads"])
        for text, qtext in zip(cluster["reads"], qs):
            codes = _codes(text)
            reads.append(codes)
            quals.append(_quals(qtext) if qtext is not None else np.full(codes.size, 30, dtype=np.uint8))
            owner.append(ci)
    owner_arr = np.asarray(owner, dtype=np.int64)
    n_clusters = len(block)
    exact = np.array([r.size == length for r in reads], dtype=bool)
    weights = np.zeros((n_clusters, length, 6), dtype=np.float64)  # A C G T N(unused) GAP

    def add_votes(owners: np.ndarray, proj: np.ndarray, q: np.ndarray) -> None:
        sym = proj.astype(np.int64)
        w = np.where(sym == 4, 0.0, np.maximum(q.astype(np.float64), 1.0))
        w = np.where(sym == GAP, 20.0, w)
        flat_index = (owners[:, None] * length + np.arange(length)[None, :]) * 6 + np.minimum(sym, 5)
        weights.reshape(-1)[:] += np.bincount(flat_index.reshape(-1), weights=w.reshape(-1), minlength=weights.size)

    # 1. draft from exact-length reads
    ex = np.flatnonzero(exact)
    if ex.size:
        add_votes(owner_arr[ex], np.stack([reads[i] for i in ex]), np.stack([quals[i] for i in ex]))
    has_exact = np.zeros(n_clusters, dtype=bool)
    has_exact[owner_arr[ex]] = True
    draft = np.argmax(weights[:, :, :4], axis=2).astype(np.uint8)
    for ci in np.flatnonzero(~has_exact).tolist():
        members = [i for i in range(len(reads)) if owner[i] == ci]
        best = min(members, key=lambda i: abs(reads[i].size - length))
        r = reads[best]
        draft[ci] = r[:length] if r.size >= length else np.concatenate([r, np.full(length - r.size, 4, np.uint8)])
        stats["clusters_without_full_length_read"] += 1
    # 2. align the other reads to their draft
    other = np.flatnonzero(~exact)
    if other.size:
        proj, cost, inserted, ok = align_batch([reads[i] for i in other], draft[owner_arr[other]], band)
        limit = 2 * max_edit_fraction * length
        use = ok & (cost <= limit)
        stats["reads_aligned"] += int(use.sum())
        stats["reads_excluded_unalignable"] += int((~use).sum())
        stats["inserted_bases_dropped"] += int(inserted[use].sum())
        if use.any():
            q_proj = np.full((int(use.sum()), length), 30, dtype=np.uint8)
            for row, i in enumerate(other[use].tolist()):
                q = quals[i]
                mean_q = int(q.mean()) if q.size else 30
                q_proj[row] = mean_q
            add_votes(owner_arr[other[use]], proj[use], q_proj)
    stats["reads_voted_full_length"] += int(ex.size)
    # 3. call (vectorised over the block)
    support = np.bincount(owner_arr, minlength=n_clusters)
    w = weights[:, :, [0, 1, 2, 3, 5]]
    order = np.argsort(-w, axis=2, kind="stable")
    top = np.take_along_axis(w, order[:, :, :1], axis=2)[:, :, 0]
    second = np.take_along_axis(w, order[:, :, 1:2], axis=2)[:, :, 0]
    winner = order[:, :, 0]
    total = top + second
    ambiguous = (top <= 0) | (top < min_winner_share * np.where(total > 0, total, 1))
    ambiguous |= (support[:, None] == 1) & (top < single_read_min_quality)
    symbols = np.where(winner == 4, GAP, winner).astype(np.uint8)
    symbols[ambiguous] = 4
    stats["ambiguous_positions"] += int(ambiguous.sum())
    stats["deleted_positions"] += int((symbols == GAP).sum())
    return [row[row != GAP] for row in symbols]


def consensus_file(clusters_path: str | os.PathLike, output_path: str | os.PathLike, *, overwrite: bool = False, band: int = 12,
                   max_edit_fraction: float = 0.15, min_winner_share: float = 0.6, single_read_min_quality: int = 10,
                   fallback_to_verified: bool = True) -> dict[str, Any]:
    """Cluster file → one consensus sequence per cluster (FASTA). Ambiguous positions are ``N``."""
    started = time.perf_counter()
    out = Path(output_path)
    if out.exists() and not overwrite:
        raise OutputError(f"output already exists: {out} (use --force to overwrite)")
    items = iter_clusters(clusters_path)
    header = next(items)
    g = header["geometry"]
    geometry = FrameGeometry(g["mapping"], g["payload_bytes"], g["inner_parity_bytes"])
    length = geometry.strand_nt
    mapping = get_mapping(geometry.mapping)
    stats: Counter = Counter()
    with StrandWriter(out, "fasta", overwrite=overwrite) as writer:
        block: list[dict] = []
        block_reads = 0

        def flush() -> None:
            nonlocal block, block_reads
            if not block:
                return
            seqs = _vote(block, length, band, max_edit_fraction, min_winner_share, single_read_min_quality, stats)
            lines = []
            for cluster, seq in zip(block, seqs):
                verdict = "consensus"
                if seq.size == length:
                    frames, erasures = mapping.decode(seq[None, :])
                    if parse_batch(geometry, frames, erasures).ok[0]:
                        stats["consensus_crc_valid"] += 1
                    elif parse_one_corrected(geometry, frames[0], erasures[0]) is not None:
                        stats["consensus_valid_after_inner_rs"] += 1
                    else:
                        verdict = "invalid"
                else:
                    verdict = "invalid"
                    stats["consensus_length_mismatch"] += 1
                if verdict == "invalid" and fallback_to_verified and cluster.get("verified", 0):
                    seq = _best_verified(cluster, geometry)
                    if seq is not None:
                        verdict = "fallback"
                        stats["fallback_to_verified_read"] += 1
                if verdict == "invalid":
                    stats["consensus_unverified_written"] += 1
                stats["clusters"] += 1
                n_amb = int((seq == 4).sum())
                label = f"c{cluster['id']};n={len(cluster['reads'])};v={cluster.get('verified', 0)};amb={n_amb};{verdict}"
                lines.append(b">" + label.encode("ascii") + b"\n" + _CODE_TO_ASCII[seq].tobytes() + b"\n")
            data = b"".join(lines)
            writer.write_bytes(data, len(lines), 0)
            block, block_reads = [], 0

        for cluster in items:
            block.append(cluster)
            block_reads += len(cluster["reads"])
            if block_reads >= BLOCK_READS or len(block) >= BLOCK_CLUSTERS:
                flush()
        flush()
        written = writer.commit()
    return {"status": "SUCCESS", "operation": "consensus", "input": str(clusters_path), "output": str(out),
            "consensus_sequences": written["records"], "stats": dict(stats),
            "parameters": {"band": band, "max_edit_fraction": max_edit_fraction, "min_winner_share": min_winner_share,
                           "single_read_min_quality": single_read_min_quality, "fallback_to_verified": fallback_to_verified},
            "elapsed_s": time.perf_counter() - started}


def _best_verified(cluster: dict, geometry: FrameGeometry) -> np.ndarray | None:
    """A member read that verifies on its own (forward-oriented by the clusterer), highest mean quality first."""
    mapping = get_mapping(geometry.mapping)
    qs = cluster.get("quals") or [None] * len(cluster["reads"])
    candidates = sorted(zip(cluster["reads"], qs), key=lambda rq: -(float(_quals(rq[1]).mean()) if rq[1] else 0.0))
    for text, _ in candidates:
        codes = _codes(text)
        if codes.size != geometry.strand_nt:
            continue
        frames, erasures = mapping.decode(codes[None, :])
        if parse_batch(geometry, frames, erasures).ok[0] or parse_one_corrected(geometry, frames[0], erasures[0]) is not None:
            return codes
    return None
