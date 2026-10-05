"""Decode stage D8: superblock candidates per archive tag and the selection of one archive. Formerly
``vnxdna.v4.decoder._decode_superblock`` (V6 Phase 2, M4)."""
from __future__ import annotations

from collections import Counter

from vnxdna.codec.codecs import CauchyRSCodec
from vnxdna.core.errors import VNXAddressError, VNXDecodeError, VNXFormatError, VNXUnsupportedVersionError
from vnxdna.dnaenc.layout import KIND_SUPER, Layout
from vnxdna.recovery.consensus import _consensus_symbols, resolve_duplicates
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.spill import Spill
from vnxdna.dnaenc.superblock import SB_BYTES, Superblock


def _decode_superblock(spill: Spill, lay: Layout, opt: DecodeOptions, stats: Counter) -> tuple[Superblock, dict]:
    acc, pend = spill.load(0)
    acc_sb = acc[acc["kind"] == KIND_SUPER]
    pend_sb = pend[pend["kind"] == KIND_SUPER]
    symbols, conflicts = resolve_duplicates(acc_sb)
    ks, ms = Superblock.symbols(lay.payload_bytes)
    # consensus rescue for superblock symbols that no single read delivered
    if pend_sb.size:
        symbols.update(_consensus_symbols(pend_sb, symbols, lay, opt, stats))
    tags = sorted({k[1] for k in symbols})
    candidates = {}
    unsupported = None
    for tag in tags:
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
    if not candidates:
        if unsupported is not None:
            raise unsupported
        raise VNXDecodeError("no superblock could be decoded (too few superblock strands survived, or the layout is wrong)",
                             stage="superblock", details={"superblock_symbols_seen": len(symbols), "tags_seen": [f"{t:04x}" for t in tags]},
                             hint="check --profile, increase coverage, or confirm the reads come from a VNX4 strand pool")
    if opt.archive_tag is not None:
        if opt.archive_tag not in candidates:
            raise VNXAddressError(f"archive tag {opt.archive_tag:04x} not found; pools present: {[f'{t:04x}' for t in candidates]}")
        tag = opt.archive_tag
    elif len(candidates) > 1:
        raise VNXAddressError(f"the reads contain several archives {[f'{t:04x}' for t in candidates]}; choose one with --archive-tag")
    else:
        tag = next(iter(candidates))
    return candidates[tag], {"archive_tags_seen": [f"{t:04x}" for t in tags], "superblock_conflicts": conflicts}
