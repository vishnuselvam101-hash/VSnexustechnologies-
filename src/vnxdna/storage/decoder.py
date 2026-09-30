"""DNA reads → validated shards → stored chunks (the recovery side).

Pipeline:

1. **Geometry discovery** (only when no container is at hand): try every
   mapping and even inner-parity size until clean reads pass the frame CRC.
2. **Scan.** Every read is validated independently: forward CRC, then reverse
   complement, then inner-RS correction in both orientations, then (opt-in,
   experimental) indel realignment. Reads whose CRC never verifies are discarded,
   so a corrupt copy can never shadow a valid one.
3. **Duplicate resolution.** Validated copies of one address are merged. When
   distinct payloads disagree, the strict majority wins, and a tie is treated
   as an erasure.
4. **Manifest.** Read from the metadata strands (kind 1, Cauchy 8+8) and then
   checked like any manifest (schema, digest/HMAC).
5. **Outer decoding** per chunk, only for the stripes the caller asks for. That
   is the basis of random access.
"""
from __future__ import annotations

import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..container import manifest as mf
from ..dna.mapping import INVALID, _ASCII_TO_CODE, get_mapping, reverse_complement_codes
from ..dna.strand import FRAME_FORMAT, KEYSTREAMS, KIND_DATA, KIND_META, StrandGeometry, _parse_plain, parse_frame
from ..ecc.cauchy import CauchyErasureCode
from ..errors import ConfigurationError, InsufficientRedundancyError, MetadataError, UnrecoverableCorruptionError
from .encoder import META_DATA_SHARDS, META_MAGIC, META_PARITY_SHARDS, used_data_shards


@dataclass(frozen=True)
class DecodeOptions:
    reverse_complement: bool = True
    indel_repair: bool = False  # EXPERIMENTAL: realign reads whose length differs by 1..max_indel
    max_indel: int = 1
    max_indel_candidates: int = 4096

    def __post_init__(self) -> None:
        if isinstance(self.max_indel, bool) or not isinstance(self.max_indel, int) or not 0 <= self.max_indel <= 3:
            raise ConfigurationError("max_indel must be an integer in 0..3")
        if not isinstance(self.max_indel_candidates, int) or not 1 <= self.max_indel_candidates <= 1 << 20:
            raise ConfigurationError("max_indel_candidates must be in 1..2^20")


@dataclass
class ScanResult:
    geometry: StrandGeometry
    candidates: dict[tuple[int, int, int, int], Counter] = field(default_factory=lambda: defaultdict(Counter))
    stats: Counter = field(default_factory=Counter)

    def tags(self) -> Counter:
        counts: Counter = Counter()
        for (tag, kind, _, _), c in self.candidates.items():
            counts[(tag, kind)] += sum(c.values())
        return counts


def codes_matrix(sequences: list[str], length: int) -> tuple[np.ndarray, np.ndarray]:
    """Equal-length reads → codes. ``N`` becomes an erasure; other symbols invalidate the read."""
    if not sequences:
        return np.zeros((0, length), dtype=np.uint8), np.zeros(0, dtype=bool)
    raw = np.frombuffer("".join(sequences).encode("ascii", errors="replace"), dtype=np.uint8).reshape(len(sequences), length)
    codes = _ASCII_TO_CODE[raw]
    valid = ~(codes == 255).any(axis=1)
    return np.where(codes == 255, INVALID, codes).astype(np.uint8), valid


def _fast_parse(geometry: StrandGeometry, frames: np.ndarray, erasures: np.ndarray) -> list[tuple | None]:
    s = geometry.systematic_bytes
    p = geometry.payload_bytes
    plain = frames[:, 1:s] ^ KEYSTREAMS[frames[:, 0], : s - 1]
    clean = ~erasures.any(axis=1)
    out: list[tuple | None] = []
    for row, ok in zip(plain, clean):
        if not ok:
            out.append(None)
            continue
        body = row[: 8 + p].tobytes()
        if zlib.crc32(body) != int.from_bytes(row[8 + p:].tobytes(), "big") or body[0] >> 4 != FRAME_FORMAT or (body[0] & 0x0F) > KIND_META:
            out.append(None)
            continue
        out.append((body[0] & 0x0F, int.from_bytes(body[1:4], "big"), int.from_bytes(body[4:7], "big"), body[7], body[8:]))
    return out


def scan_reads(sequences: list[str], geometry: StrandGeometry, options: DecodeOptions = DecodeOptions()) -> ScanResult:
    """Validate every read and collect candidate payloads keyed by (tag, kind, stripe, shard)."""
    result = ScanResult(geometry)
    stats = result.stats
    mapping = get_mapping(geometry.mapping)
    length = geometry.strand_nt
    stats["reads_total"] = len(sequences)
    exact = [s for s in sequences if len(s) == length]
    other = [s for s in sequences if len(s) != length]
    codes, valid = codes_matrix(exact, length)
    stats["reads_invalid_symbols"] += int((~valid).sum())
    codes = codes[valid]

    def accept(parsed: tuple, corrected: int, orientation: str, repair: str = "none") -> None:
        kind, tag, stripe, shard, payload = parsed
        result.candidates[(tag, kind, stripe, shard)][bytes(payload)] += 1
        stats["reads_valid"] += 1
        if corrected:
            stats["reads_inner_corrected"] += 1
            stats["inner_symbols_corrected"] += corrected
        if orientation == "reverse_complement":
            stats["reads_reverse_complement"] += 1
        if repair != "none":
            stats["reads_indel_repaired"] += 1

    if codes.shape[0]:
        frames, erasures = mapping.decode(codes)
        first = _fast_parse(geometry, frames, erasures)
        pending = []
        for i, r in enumerate(first):
            if r is None:
                pending.append(i)
            else:
                accept(r, 0, "forward")
        rc_frames = rc_erasures = None
        if pending and options.reverse_complement:
            rc_frames, rc_erasures = mapping.decode(reverse_complement_codes(codes[pending]))
            still = []
            for j, (i, r) in enumerate(zip(pending, _fast_parse(geometry, rc_frames, rc_erasures))):
                if r is not None:
                    accept(r, 0, "reverse_complement")
                else:
                    still.append((i, j))
        else:
            still = [(i, None) for i in pending]
        for i, j in still:
            parsed = parse_frame(geometry, frames[i], erasures[i])
            if parsed is not None:
                accept(parsed[0], parsed[1], "forward")
                continue
            if j is not None:
                parsed = parse_frame(geometry, rc_frames[j], rc_erasures[j])
                if parsed is not None:
                    accept(parsed[0], parsed[1], "reverse_complement")
                    continue
            stats["reads_rejected"] += 1
    for seq in other:
        if options.indel_repair and 0 < abs(len(seq) - length) <= options.max_indel:
            from ..sync.indel import repair_read
            repaired = repair_read(seq, geometry, options)
            if repaired is not None:
                accept(*repaired)
                continue
        stats["reads_length_mismatch"] += 1
    return result


def resolve_candidates(counter: Counter) -> tuple[bytes | None, str]:
    """Choose one payload among *validated* copies of one shard address.

    Identical copies merge. Among disagreeing copies the strictly most frequent
    wins ("conflict_majority"). A tie returns None ("conflict_tie"), so the shard
    is treated as an erasure and the outer code, not a guess, supplies it.
    """
    if not counter:
        return None, "missing"
    ranked = counter.most_common(2)
    if len(ranked) == 1:
        return ranked[0][0], "unique" if ranked[0][1] == 1 else "duplicates_consistent"
    if ranked[0][1] > ranked[1][1]:
        return ranked[0][0], "conflict_majority"
    return None, "conflict_tie"


def discover_geometry(sequences: list[str], sample: int = 400) -> StrandGeometry:
    """Infer mapping, payload size and inner parity from the DNA alone.

    For the most common read lengths, every mapping and even inner parity size
    is tried. A hypothesis scores only when clean reads pass the frame CRC and
    version check (false-positive probability ≈ 2^-32 per read). Needs only a
    few error-free reads.
    """
    votes: Counter = Counter()
    by_length: Counter = Counter(len(s) for s in sequences)
    for length, _ in by_length.most_common(4):
        reads = [s for s in sequences if len(s) == length][:sample]
        for name in ("2bit", "rotation3", "codebook8"):
            mapping = get_mapping(name)
            if length % mapping.nt_per_byte:
                continue
            frame_len = length // mapping.nt_per_byte
            codes, valid = codes_matrix(reads, length)
            codes = codes[valid]
            if not codes.shape[0]:
                continue
            for oriented in (codes, reverse_complement_codes(codes)):
                frames, erasures = mapping.decode(oriented)
                clean = frames[~erasures.any(axis=1)]
                for nsym in range(0, min(64, frame_len - 14) + 1, 2):
                    payload = frame_len - 13 - nsym
                    if payload < 1:
                        continue
                    geometry = StrandGeometry(name, payload, nsym)
                    hits = sum(1 for row in clean[:64] if _parse_plain(geometry, row[: geometry.systematic_bytes].tobytes()))
                    if hits:
                        votes[(name, payload, nsym)] += hits
        if votes:
            break
    if not votes:
        raise UnrecoverableCorruptionError("no VNX-DNA format-4 strands could be identified in the reads "
                                           "(no read passed a frame CRC under any supported geometry)")
    (name, payload, nsym), _ = votes.most_common(1)[0]
    return StrandGeometry(name, payload, nsym)


def recover_manifest_from_dna(scan: ScanResult, tag: int | None = None) -> bytes:
    """Reassemble the canonical manifest from metadata strands (kind 1)."""
    meta_tags = sorted({t for (t, kind, _, _) in scan.candidates if kind == KIND_META})
    if tag is None:
        if not meta_tags:
            raise UnrecoverableCorruptionError("no metadata strands survived; the manifest cannot be recovered from DNA")
        if len(meta_tags) > 1:
            raise MetadataError(f"reads contain metadata for several archives (tags {[f'{t:06x}' for t in meta_tags]}); "
                                "separate the pools first", details={"archive_tags": [f"{t:06x}" for t in meta_tags]})
        tag = meta_tags[0]
    code = CauchyErasureCode(META_DATA_SHARDS, META_PARITY_SHARDS)
    p = scan.geometry.payload_bytes

    def stripes(indices: list[int]) -> bytes:
        shards = np.zeros((len(indices), code.total_shards, p), dtype=np.uint8)
        present = np.zeros((len(indices), code.total_shards), dtype=bool)
        for row, stripe in enumerate(indices):
            for shard in range(code.total_shards):
                payload, _ = resolve_candidates(scan.candidates.get((tag, KIND_META, stripe, shard), Counter()))
                if payload is not None:
                    shards[row, shard] = np.frombuffer(payload, dtype=np.uint8)
                    present[row, shard] = True
        try:
            return code.decode(shards, present).tobytes()
        except InsufficientRedundancyError as error:
            raise InsufficientRedundancyError("the DNA copy of the manifest is unrecoverable: " + str(error), details=error.details) from None

    head = stripes([0])
    if head[:4] != META_MAGIC:
        raise MetadataError("DNA manifest copy has an invalid header")
    length = int.from_bytes(head[4:8], "big")
    if length > mf.MAX_MANIFEST_BYTES:
        raise MetadataError("DNA manifest copy declares an implausible length")
    total = -(-(8 + length) // (code.data_shards * p))
    stream = head + (stripes(list(range(1, total))) if total > 1 else b"")
    return stream[8:8 + length]


class ReadsSource:
    """Chunk source that rebuilds stored chunks from scanned reads (outer decoding on demand)."""

    def __init__(self, scan: ScanResult, manifest: mf.Manifest):
        self.scan = scan
        self.manifest = manifest
        self.tag = manifest.archive_tag
        self.code = CauchyErasureCode(manifest.erasure_code.data_shards, manifest.erasure_code.parity_shards)
        self.stripe_details: list[dict[str, Any]] = []

    def __call__(self, c: int, stats: Counter) -> bytes:
        record = self.manifest.chunks[c]
        code, p = self.code, self.manifest.strand.payload_bytes
        k, n = code.data_shards, code.total_shards
        if record.stripe_count == 0:
            return b""
        shards = np.zeros((record.stripe_count, n, p), dtype=np.uint8)
        present = np.zeros((record.stripe_count, n), dtype=bool)
        emitted = np.ones((record.stripe_count, n), dtype=bool)
        for row in range(record.stripe_count):
            stripe = record.first_stripe + row
            used = used_data_shards(record.stored_size, row, record.stripe_count, k, p)
            for shard in range(n):
                if used <= shard < k:  # shortened: implicit zero shard, never emitted
                    present[row, shard] = True
                    emitted[row, shard] = False
                    continue
                payload, how = resolve_candidates(self.scan.candidates.get((self.tag, KIND_DATA, stripe, shard), Counter()))
                stats["shards_" + how] += 1
                if payload is not None:
                    shards[row, shard] = np.frombuffer(payload, dtype=np.uint8)
                    present[row, shard] = True
        lost = (~present).sum(axis=1)
        stats["stripes_decoded"] += record.stripe_count
        stats["shards_erased"] += int(lost.sum())
        stats["stripes_outer_recovered"] += int((~present[:, :k]).any(axis=1).sum())
        stats["max_erasures_in_a_stripe"] = max(stats.get("max_erasures_in_a_stripe", 0), int(lost.max()))
        try:
            data = code.decode(shards, present)
        except InsufficientRedundancyError as error:
            bad = [record.first_stripe + s for s in error.details["unrecoverable_stripes"]]
            stats["stripes_unrecoverable"] += len(bad)
            worst = int(lost.max())
            raise InsufficientRedundancyError(
                f"chunk {c}: {len(bad)} stripe(s) lost more than {code.parity_shards} of their strands "
                f"(worst stripe lost {worst}); the outer code guarantees recovery of at most {code.parity_shards} per stripe",
                details={"chunk": c, "stripes": bad[:50], "parity_shards": code.parity_shards, "worst_stripe_erasures": worst}) from None
        return data.reshape(-1)[: record.stored_size].tobytes()


def read_statistics(scan: ScanResult, manifest: mf.Manifest) -> dict[str, Any]:
    tags = scan.tags()
    tag = manifest.archive_tag
    return {**dict(scan.stats), "reads_foreign_archive": sum(c for (t, _), c in tags.items() if t != tag)}
