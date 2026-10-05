"""Decode stage D8: superblock candidates per archive tag and the selection of one archive. Formerly
``vnxdna.v4.decoder._decode_superblock`` (V6 Phase 2, M4)."""
from __future__ import annotations

from collections import Counter

import numpy as np

from vnxdna.codec.codecs import CauchyRSCodec
from vnxdna.core.util import archive_mismatch
from vnxdna.core.errors import (VNXAddressError, VNXDecodeError, VNXFormatError, VNXResourceError,
                               VNXUnsupportedVersionError)
from vnxdna.dnaenc.layout import KIND_SUPER, Layout
from vnxdna.recovery.consensus import _consensus_symbols, resolve_duplicates
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.spill import Spill
from vnxdna.dnaenc.superblock import SB_BYTES, Superblock


COLLISION_TRIALS = 256


def _try_superblock(got: dict, ks: int, ms: int, lay: Layout, tag: int):
    """The superblock decoded from ``got`` (symbol index → payload) if it is valid for ``tag`` and ``lay``, else None."""
    try:
        data = CauchyRSCodec(ks, ms).decode(got, ks, lay.payload_bytes).reshape(-1).tobytes()
        sb = Superblock.unpack(data[:SB_BYTES])
    except (VNXDecodeError, VNXFormatError, VNXUnsupportedVersionError):
        return None
    return sb if int.from_bytes(sb.archive_id[:2], "big") == tag and sb.layout == lay else None


def _codeword(sb: Superblock, ks: int, ms: int, P: int) -> np.ndarray:
    data = np.zeros((ks, P), dtype=np.uint8)
    raw = sb.pack()
    data.reshape(-1)[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
    return CauchyRSCodec(ks, ms).encode(data)


def _colliding_superblocks(values: dict, ks: int, ms: int, lay: Layout, tag: int, seed_sb: Superblock | None = None) -> dict:
    """Spec §3.10 step 7: superblocks decodable from mutually consistent subsets of a tag's conflicting symbols.

    ``values``: symbol index → the distinct verified payloads seen for it. Every superblock found (by ``seed_sb``, the
    strict-majority decode, or by a deterministic random search seeded by the tag: draws of Ks indices with one value
    each) is *peeled*: the values its codeword explains are removed, and the remaining values are decoded directly.
    Each candidate must pass the superblock CRC-32 and the full field validation. Returns container SHA-256 →
    Superblock (stops at two)."""
    P = lay.payload_bytes
    vals = {i: sorted(v, key=lambda a: a.tobytes()) for i, v in values.items() if v}
    found: dict = {}

    def peel(sb: Superblock) -> None:
        cw = _codeword(sb, ks, ms, P)
        rest = {i: [v for v in vs if not np.array_equal(v, cw[i])] for i, vs in vals.items() if i < len(cw)}
        rest = {i: vs for i, vs in rest.items() if vs}
        if len(rest) >= ks:
            other = _try_superblock({i: vs[0] for i, vs in rest.items()}, ks, ms, lay, tag)
            if other is not None:
                found.setdefault(other.container_sha256, other)

    if seed_sb is not None:
        found[seed_sb.container_sha256] = seed_sb
        peel(seed_sb)
    idx = sorted(vals)
    if len(found) < 2 and len(idx) >= ks:
        rng = np.random.default_rng(0x56C0 ^ tag)
        trials = int(min(4096, max(COLLISION_TRIALS, 21 * 2 ** ks)))    # ≥ 1 − 1e-9 for two archives, Ks ≤ 7
        for _ in range(trials):
            pick = rng.choice(len(idx), size=ks, replace=False)
            got = {idx[i]: vals[idx[i]][int(rng.integers(len(vals[idx[i]])))] for i in pick}
            sb = _try_superblock(got, ks, ms, lay, tag)
            if sb is not None and sb.container_sha256 not in found:
                found[sb.container_sha256] = sb
                if len(found) < 2:
                    peel(sb)
            if len(found) >= 2:
                break
    return found


def _decode_superblock(spill: Spill, lay: Layout, opt: DecodeOptions, stats: Counter) -> tuple[Superblock, dict]:
    acc, pend = spill.load(0)
    acc_sb = acc[acc["kind"] == KIND_SUPER]
    # superblock symbols are group 0; a pending kind-1 record of another group has a corrupted header that can never
    # verify as a superblock frame, and which bucket it sits in depends on the bucket count (V6 Phase 2.8)
    pend_sb = pend[(pend["kind"] == KIND_SUPER) & (pend["group"] == 0)]
    symbols, conflicts = resolve_duplicates(acc_sb)
    ks, ms = Superblock.symbols(lay.payload_bytes)
    sc = stats.get("_stage")              # V7 stage counters (opt-in; observability only)
    if sc is not None:
        sc.add("superblock", "symbols_needed", ks)
        sc.add("superblock", "symbols_total", ks + ms)
        sc.add("superblock", "verified_reads_pass1", len(acc_sb))
        sc.add("superblock", "symbols_verified_pass1", len(symbols))
        sc.add("superblock", "pending_records", len(pend_sb))
        sc.add("superblock", "pending_records_other_group", int(((pend["kind"] == KIND_SUPER) & (pend["group"] != 0)).sum()))
        n0 = len(symbols)
    # consensus rescue for superblock symbols that no single read delivered
    if pend_sb.size:
        symbols.update(_consensus_symbols(pend_sb, symbols, lay, opt, stats))
    if sc is not None:
        sc.add("superblock", "symbols_from_consensus", len(symbols) - n0)
        per_tag = Counter(k[1] for k in symbols if k[0] == KIND_SUPER and k[2] == 0 and k[3] < ks + ms)
        sc.add("superblock", "symbols_best_tag", max(per_tag.values(), default=0))
        sc.add("superblock", "tags_seen", len(per_tag))
    # every distinct verified value of each superblock symbol, per tag (a tag with several values for one symbol may be
    # two archives sharing a tag: spec §3.10 step 7)
    values: dict = {}
    for rec in acc_sb[(acc_sb["group"] == 0) & (acc_sb["symbol"] < ks + ms)]:
        vs = values.setdefault(int(rec["tag"]), {}).setdefault(int(rec["symbol"]), [])
        if not any(np.array_equal(v, rec["payload"]) for v in vs):
            vs.append(np.array(rec["payload"]))
    tags = sorted({k[1] for k in symbols} | set(values))
    candidates = {}
    unsupported = None
    for tag in tags:
        per = values.get(tag, {})
        if any(len(v) > 1 for v in per.values()):
            seed = None
            maj = {k[3]: v for k, v in symbols.items() if k[1] == tag and k[0] == KIND_SUPER and k[2] == 0 and k[3] < ks + ms}
            if len(maj) >= ks:
                seed = _try_superblock(maj, ks, ms, lay, tag)
            found = _colliding_superblocks(per, ks, ms, lay, tag, seed)
            if len(found) > 1:
                raise VNXAddressError(
                    f"archive tag {tag:04x} is shared by {len(found)} different archives in these reads; refusing to "
                    "choose one", stage="superblock", code="ARCHIVE_TAG_AMBIGUOUS", retryable=False,
                    details={"archive_tag": f"{tag:04x}", "container_sha256": sorted(h.hex() for h in found),
                             "conflicting_symbols": sum(len(v) > 1 for v in per.values())},
                    hint="separate the pools physically (the archives' strands cannot be told apart by their tag)")
        got = {k[3]: v for k, v in symbols.items() if k[1] == tag and k[0] == KIND_SUPER and k[2] == 0 and k[3] < ks + ms}
        if len(got) < ks:
            continue
        try:
            data = CauchyRSCodec(ks, ms).decode(got, ks, lay.payload_bytes).reshape(-1).tobytes()
            sb = Superblock.unpack(data[:SB_BYTES])
        except (VNXDecodeError, VNXFormatError):
            continue
        except VNXUnsupportedVersionError as error:     # a newer archive, or a forgery: other candidates still count
            unsupported = unsupported or error
            continue
        if int.from_bytes(sb.archive_id[:2], "big") == tag and sb.layout == lay:
            candidates[tag] = sb
    if sc is not None:
        sc.add("superblock", "candidates", len(candidates))
    if not candidates:
        if unsupported is not None:
            raise unsupported
        raise VNXDecodeError("no superblock could be decoded (too few superblock strands survived, or the layout is wrong)",
                             stage="superblock", details={"superblock_symbols_seen": len(symbols), "tags_seen": [f"{t:04x}" for t in tags]},
                             hint="check --profile, increase coverage, or confirm the reads come from a VNX4 strand pool",
                             code="NO_SUPERBLOCK")
    want = opt.archive_tag
    if want is None and opt.expect_archive_id is not None:
        # V6-SEC-03: the expected archive ID names its tag (bytes 0-1); absent from the pool means another archive
        want = int(opt.expect_archive_id[:4], 16)
        if want not in candidates:
            raise archive_mismatch("archive tag", opt.expect_archive_id[:4],
                                   ",".join(f"{t:04x}" for t in sorted(candidates)), "superblock")
    if want is not None:
        if want not in candidates:
            raise VNXAddressError(f"archive tag {opt.archive_tag:04x} not found; pools present: {[f'{t:04x}' for t in candidates]}",
                                  code="ARCHIVE_TAG_NOT_FOUND")
        tag = want
    elif len(candidates) > 1:
        raise VNXAddressError(f"the reads contain several archives {[f'{t:04x}' for t in candidates]}; choose one with --archive-tag",
                              code="MULTIPLE_ARCHIVES")
    else:
        tag = next(iter(candidates))
    sb = candidates[tag]
    if opt.expect_archive_id is not None and sb.archive_id.hex() != opt.expect_archive_id:
        raise archive_mismatch("archive ID", opt.expect_archive_id, sb.archive_id.hex(), "superblock")
    if opt.expect_sha256 is not None and sb.container_sha256.hex() != opt.expect_sha256:
        raise archive_mismatch("container SHA-256", opt.expect_sha256, sb.container_sha256.hex(), "superblock")
    if sb.container_size > opt.max_container_bytes:
        # V6-SEC-01: the claim is untrusted (a CRC-valid forgery chooses it). Refused here, for every caller (pass 2,
        # the deferred smart/soft schedule, random access), before the work file is sized or any group is walked, so
        # decode work cannot grow with a claimed group count beyond the cap.
        raise VNXResourceError(f"the superblock claims a container of {sb.container_size:,} bytes, above the limit of "
                               f"{opt.max_container_bytes:,} bytes; nothing decoded", stage="superblock",
                               details={"container_size": sb.container_size, "groups": sb.group_count,
                                        "max_container_bytes": opt.max_container_bytes, "archive_tag": f"{tag:04x}"},
                               hint="if this archive is genuine, raise the limit with --max-container-bytes")
    return sb, {"archive_tags_seen": [f"{t:04x}" for t in tags], "superblock_conflicts": conflicts}
