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

import math
import os
import time
from collections import Counter
from typing import Any

import numpy as np

from ..dna.mapping import _ASCII_TO_CODE, _CODE_TO_ASCII, get_mapping
from ..errors import ConfigurationError, InvalidInputError
from .align import GAP, align_reads
from .cluster import iter_clusters
from .frame import FrameGeometry, correct_frames, parse_batch, parse_one_corrected
from .paths import check_output_file
from .strandio import StrandWriter

BLOCK_READS = 16384
BLOCK_CLUSTERS = 2048


def _codes(text: str) -> np.ndarray:
    codes = _ASCII_TO_CODE[np.frombuffer(text.encode("ascii"), dtype=np.uint8)]
    return np.where(codes == 255, 4, codes).astype(np.uint8)


def _quals(text: str) -> np.ndarray:
    return (np.frombuffer(text.encode("ascii"), dtype=np.uint8).astype(np.int16) - 33).clip(0, 93).astype(np.uint8)


def _correct_read(codes: np.ndarray, geometry: FrameGeometry) -> np.ndarray | None:
    """The corrected strand for a full-length read that the inner RS can fix (verified by the CRC), else None."""
    mapping = get_mapping(geometry.mapping)
    frames, erasures = mapping.decode(codes[None, :])
    corrected, accepted, _ = correct_frames(geometry, frames, erasures)
    return mapping.encode(corrected)[0] if accepted[0] else None


def _vote(block: list[dict], geometry: FrameGeometry, band: int, max_edit_fraction: float, min_winner_share: float,
          single_read_min_quality: int, stats: Counter, rounds: int = 2) -> list[np.ndarray]:
    """Consensus for a block of clusters. Returns one code array per cluster (4 = N).

    1. A read that passes the frame CRC is the strand (a wrong one needs a CRC collision): it is used as is.
    2. Otherwise a full-length read that the inner RS corrects (and the CRC then verifies) gives the strand.
    3. Otherwise iterative alignment: all reads are aligned to a draft (first a seed read), positions and
       insertion slots are voted, and the vote becomes the next draft; ambiguous positions become N.
    """
    length = geometry.strand_nt
    mapping = get_mapping(geometry.mapping)
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
    n_clusters = len(block)
    out: list[np.ndarray | None] = [None] * n_clusters
    exact = np.flatnonzero(np.array([r.size == length for r in reads], dtype=bool))
    if exact.size:
        frames, erasures = mapping.decode(np.stack([reads[i] for i in exact]))
        verified = exact[parse_batch(geometry, frames, erasures).ok]
        for i in sorted(verified.tolist(), key=lambda i: reads[i].tobytes()):  # CRC-valid reads of one strand are identical
            if out[owner[i]] is None:
                out[owner[i]] = reads[i]
                stats["consensus_from_verified_read"] += 1
    by_cluster: dict[int, list[int]] = {}
    for i, ci in enumerate(owner):
        by_cluster.setdefault(ci, []).append(i)
    for ci in range(n_clusters):
        if out[ci] is not None:
            continue
        candidates = sorted((i for i in by_cluster.get(ci, []) if reads[i].size == length and not (reads[i] == 4).all()),
                            key=lambda i: (-float(quals[i].mean()) if quals[i].size else 0.0, reads[i].tobytes()))[:3]
        for i in candidates:
            fixed = _correct_read(reads[i], geometry)
            if fixed is not None:
                out[ci] = fixed
                stats["consensus_from_corrected_read"] += 1
                break
    pending = [ci for ci in range(n_clusters) if out[ci] is None]
    if pending:
        consensus = _align_consensus(pending, by_cluster, reads, quals, length, band, max_edit_fraction, min_winner_share,
                                     single_read_min_quality, stats, rounds)
        for ci, seq in zip(pending, consensus):
            out[ci] = seq
            stats["consensus_by_alignment"] += 1
    return out  # type: ignore[return-value]


def _align_consensus(pending: list[int], by_cluster: dict[int, list[int]], reads: list[np.ndarray], quals: list[np.ndarray],
                     length: int, band: int, max_edit_fraction: float, min_winner_share: float, single_read_min_quality: int,
                     stats: Counter, rounds: int) -> list[np.ndarray]:
    members = [by_cluster.get(ci, []) for ci in pending]
    row_reads = [reads[i] for m in members for i in m]
    row_quals = [quals[i] for m in members for i in m]
    row_owner = np.array([k for k, m in enumerate(members) for _ in m], dtype=np.int64)
    drafts = []
    for m in members:  # seed: the read closest to the strand length, best mean quality first
        best = min(m, key=lambda i: (abs(reads[i].size - length), -float(quals[i].mean()) if quals[i].size else 0.0,
                                     reads[i].tobytes()))
        drafts.append(reads[best].copy())
    k = len(pending)
    final: list[np.ndarray] = drafts
    for round_index in range(rounds):
        last = round_index == rounds - 1
        a = align_reads(row_reads, [drafts[o] for o in row_owner], band, row_quals)
        limit = 2 * max_edit_fraction * length
        use = a.ok & (a.cost <= limit)
        if last:
            stats["reads_aligned"] += int(use.sum())
            stats["reads_excluded_unalignable"] += int((~use).sum())
        width = a.projection.shape[1]
        sym = a.projection.astype(np.int64)
        w = np.where(sym == 4, 0.0, np.maximum(a.proj_quality.astype(np.float64), 1.0))
        w = np.where(sym == GAP, 20.0, w)
        w[~use] = 0.0
        index = (row_owner[:, None] * width + np.arange(width)[None, :]) * 6 + np.minimum(sym, 5)
        votes = np.bincount(index.reshape(-1), weights=w.reshape(-1), minlength=k * width * 6).reshape(k, width, 6)
        used_reads = np.bincount(row_owner[use], minlength=k)
        has_ins = (a.ins_count > 0) & use[:, None]
        ins_n = np.zeros((k, width + 1), dtype=np.int64)
        np.add.at(ins_n, row_owner, has_ins.astype(np.int64))
        ins_votes = np.zeros((k, width + 1, 4), dtype=np.int64)
        rr, jj = np.nonzero(has_ins & (a.ins_base < 4))
        np.add.at(ins_votes, (row_owner[rr], jj, a.ins_base[rr, jj].astype(np.int64)), 1)
        new_drafts = []
        for c in range(k):
            dlen = drafts[c].size
            v = votes[c, :dlen][:, [0, 1, 2, 3, 5]]
            order = np.argsort(-v, axis=1, kind="stable")
            top = v[np.arange(dlen), order[:, 0]]
            second = v[np.arange(dlen), order[:, 1]]
            total = top + second
            winner = order[:, 0]
            ambiguous = (top <= 0) | (top < min_winner_share * np.where(total > 0, total, 1))
            if used_reads[c] <= 1:
                ambiguous |= top < single_read_min_quality
            symbols: list[int] = []
            for j in range(dlen + 1):
                if used_reads[c] and ins_n[c, j] * 2 > used_reads[c]:
                    symbols.append(int(np.argmax(ins_votes[c, j])))
                    if last:
                        stats["inserted_positions"] += 1
                if j == dlen:
                    break
                if winner[j] == 4 and not ambiguous[j]:
                    if last:
                        stats["deleted_positions"] += 1
                    continue
                if last and ambiguous[j]:
                    symbols.append(4)
                    stats["ambiguous_positions"] += 1
                else:
                    best_base = int(np.argmax(v[j, :4])) if winner[j] == 4 else int(winner[j])
                    symbols.append(best_base)
            new_drafts.append(np.array(symbols, dtype=np.uint8) if symbols else drafts[c])
        drafts = new_drafts
        final = drafts
    return final


def consensus_file(clusters_path: str | os.PathLike, output_path: str | os.PathLike, *, overwrite: bool = False, band: int = 12,
                   max_edit_fraction: float = 0.15, min_winner_share: float = 0.6, single_read_min_quality: int = 10,
                   fallback_to_verified: bool = True) -> dict[str, Any]:
    """Cluster file → one consensus sequence per cluster (FASTA). Ambiguous positions are ``N``."""
    started = time.perf_counter()
    # parameter ranges (VNX-DNA 2.0 accepted e.g. a negative edit fraction or a share of 5 and silently degraded)
    if isinstance(band, bool) or not isinstance(band, int) or not 1 <= band <= 64:
        raise ConfigurationError("band must be an integer in 1..64")
    for name, value in (("max_edit_fraction", max_edit_fraction), ("min_winner_share", min_winner_share)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ConfigurationError(f"{name} must be a number in [0, 1]")
    if isinstance(single_read_min_quality, bool) or not isinstance(single_read_min_quality, int) or not 0 <= single_read_min_quality <= 93:
        raise ConfigurationError("single_read_min_quality must be an integer in 0..93")
    out = check_output_file(output_path, overwrite=overwrite)
    items = iter_clusters(clusters_path)
    header = next(items)
    g = header["geometry"]
    try:
        geometry = FrameGeometry(g["mapping"], g["payload_bytes"], g["inner_parity_bytes"])
    except ConfigurationError as error:
        raise InvalidInputError(f"{clusters_path}: cluster file header has an invalid geometry ({error})") from None
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
            seqs = _vote(block, geometry, band, max_edit_fraction, min_winner_share, single_read_min_quality, stats)
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
