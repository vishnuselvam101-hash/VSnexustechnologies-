"""Container → DNA strands (streaming, parallel, deterministic): the encode stage graph of spec §5.1.

The container byte stream is cut into *groups* of K symbols of P bytes. Each group is one outer-code block (Cauchy
RS: K + M symbols; LT fountain: K + M droplets). Every symbol becomes one strand (:mod:`vnxdna.dnaenc.frame4`). A
*superblock* group (kind 1, :mod:`vnxdna.dnaenc.superblock`) makes a strand pool self-describing.

Strand order in the output: superblock strands, then groups 0, 1, … with symbols in index order. Group g occupies
container bytes [g·K·P, (g+1)·K·P).

V6 (opt-in, ``stripe_depth`` / ``column_parity`` / ``strand_order`` / ``outer_plan``): column-parity groups and
interleaved strand order, signalled by superblock version 2 (:mod:`vnxdna.pipeline.encode_v6`). Without these options
the output is unchanged (superblock version 1). Formerly ``vnxdna.v4.encoder`` (V6 Phase 2, M4)."""
from __future__ import annotations

import os
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from vnxdna.archive import container as ct
from vnxdna.codec.codecs import CauchyRSCodec, make_outer
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.core.util import sha256_file
from vnxdna.dnaenc.constraints import ConstraintConfig
from vnxdna.dnaenc.frame4 import build_strands
from vnxdna.dnaenc.layout import KIND_DATA, KIND_SUPER, Layout, PROFILES
from vnxdna.dnaenc.records import _labels, _serialize
from vnxdna.dnaenc.strandio import StrandWriter, format_for_output
from vnxdna.dnaenc.superblock import ORDER_IDS, Superblock, group_k


@dataclass
class DNAOptions:
    profile: str = "v4-balanced"
    layout: Layout | None = None
    outer_code: str = "cauchy-rs"
    data_symbols: int | None = None     # K
    parity_symbols: int | None = None   # M
    lt_seed: int = 0
    lt_distribution: str = "dense"
    constraints: ConstraintConfig = field(default_factory=ConstraintConfig)
    workers: int = 1
    groups_per_task: int = 64
    fmt: str | None = None
    experimental: bool = False
    # V6 outer code (opt-in; any of these switches to superblock version 2). stripe_depth 0 = automatic: every data
    # group in one stripe without column parity, else min(groups, 128). strand_order None = "sequential", or
    # "interleaved" with outer_plan="adaptive". redundancy_budget (adaptive only) = maximum redundant strands per data
    # strand; None = the profile's M / K.
    stripe_depth: int = 0
    column_parity: int = 0
    strand_order: str | None = None
    outer_plan: str = "fixed"
    redundancy_budget: float | None = None

    @property
    def v6(self) -> bool:
        return bool(self.stripe_depth or self.column_parity or self.strand_order not in (None, "sequential")
                    or self.outer_plan != "fixed")

    def resolve(self) -> tuple[Layout, int, int]:
        if self.profile not in PROFILES:
            raise VNXConfigurationError(f"unknown profile {self.profile!r}; available: {sorted(PROFILES)}")
        lay, k, m = PROFILES[self.profile]
        lay = (self.layout or lay).validate()
        k = self.data_symbols or k
        m = self.parity_symbols if self.parity_symbols is not None else m
        if self.outer_code == "lt-fountain" and not self.experimental:
            raise VNXConfigurationError("the lt-fountain outer code is EXPERIMENTAL; pass --experimental to use it")
        make_outer(self.outer_code, k, m, self.lt_seed, self.lt_distribution)  # validates
        if not 1 <= self.workers <= 256:
            raise VNXConfigurationError("workers must be in 1..256")
        self.constraints.validate()
        if self.outer_plan not in ("fixed", "adaptive"):
            raise VNXConfigurationError("outer_plan must be 'fixed' or 'adaptive'")
        if self.redundancy_budget is not None and self.outer_plan != "adaptive":
            raise VNXConfigurationError("redundancy_budget applies to outer_plan='adaptive' only")
        if self.redundancy_budget is not None and not 0 < self.redundancy_budget < 4:
            raise VNXConfigurationError("redundancy_budget must be in (0, 4)")
        if self.v6:
            if self.outer_code != "cauchy-rs":
                raise VNXConfigurationError("the V6 outer options need the cauchy-rs outer code")
            if self.strand_order not in (None, *ORDER_IDS):
                raise VNXConfigurationError(f"strand_order must be one of {sorted(ORDER_IDS)}")
            if not 0 <= self.stripe_depth <= 65535 or not 0 <= self.column_parity <= 255:
                raise VNXConfigurationError("stripe_depth must be in 0..65535 and column_parity in 0..255")
            if self.outer_plan == "adaptive" and (self.data_symbols or self.parity_symbols is not None or self.stripe_depth
                                                  or self.column_parity):
                raise VNXConfigurationError("outer_plan='adaptive' chooses K, M, stripe depth and column parity itself")
        return lay, k, m


# ============================================================================ worker
_W: dict = {}


def _init(container: str, lay: Layout, K: int, M: int, outer: str, seed: int, dist: str, cfg: dict, tag: int, size: int,
          fmt: str) -> None:
    _W.update(container=container, lay=lay, K=K, M=M, codec=make_outer(outer, K, M, seed, dist), cfg=ConstraintConfig.from_dict(cfg),
              tag=tag, size=size, fmt=fmt)


def _encode_task(task: tuple[int, int]) -> tuple[int, bytes, int, int, int]:
    g0, g1 = task
    lay: Layout = _W["lay"]
    K, P = _W["K"], lay.payload_bytes
    size = _W["size"]
    codec = _W["codec"]
    with open(_W["container"], "rb") as f:
        raw = os.pread(f.fileno(), (g1 - g0) * K * P, g0 * K * P)
    pays, groups, syms = [], [], []
    full = np.zeros(((g1 - g0) * K * P,), dtype=np.uint8)
    full[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
    blocks = full.reshape(g1 - g0, K, P)
    nfull = sum(1 for g in range(g0, g1) if group_k(size, K, P, g) == K)
    if isinstance(codec, CauchyRSCodec) and nfull:
        coded_full = codec.encode_many(blocks[:nfull])             # one vectorised parity pass for all full groups
        n_sym = K + codec.M
        pays.append(coded_full.reshape(-1, P))
        groups.append(np.repeat(np.arange(g0, g0 + nfull, dtype=np.int64), n_sym))
        syms.append(np.tile(np.arange(n_sym, dtype=np.int64), nfull))
        start = nfull
    else:
        start = 0
    for gi in range(start, g1 - g0):
        g = g0 + gi
        k = group_k(size, K, P, g)
        data = blocks[gi, :k]
        if isinstance(codec, CauchyRSCodec):
            coded = codec._encode_short(data) if k < K else codec.encode_many(blocks[gi:gi + 1])[0]
        else:
            coded = codec.encode_block(data, g)
        pays.append(coded)
        groups.append(np.full(coded.shape[0], g, dtype=np.int64))
        syms.append(np.arange(coded.shape[0], dtype=np.int64))
    payloads = np.concatenate(pays)
    groups_a = np.concatenate(groups)
    syms_a = np.concatenate(syms)
    strands, variants = build_strands(lay, _W["cfg"], _W["tag"], KIND_DATA, groups_a, syms_a, payloads)
    text = _serialize(strands, _labels(_W["tag"], KIND_DATA, groups_a, syms_a), _W["fmt"])
    return g0, text, strands.shape[0], int(strands.size), int(variants.max(initial=0))


# ============================================================================ main entry
def encode_container(container_path: str | os.PathLike, output: str | os.PathLike, options: DNAOptions | None = None, *,
                     overwrite: bool = False, progress=None) -> dict:
    opt = options or DNAOptions()
    lay, K, M = opt.resolve()
    if opt.v6:
        from vnxdna.pipeline.encode_v6 import encode_container_v6
        return encode_container_v6(container_path, output, opt, overwrite=overwrite, progress=progress)
    t0 = time.perf_counter()
    container_path = Path(container_path)
    c = ct.open_container(container_path)        # structural validation (no key needed)
    size = c.size
    aid = c.archive_id
    tag = int.from_bytes(aid[:2], "big")
    P = lay.payload_bytes
    groups = -(-size // (K * P))
    sb = Superblock(opt.outer_code, K, M, lay, opt.lt_distribution, opt.lt_seed, aid, size,
                    bytes.fromhex(sha256_file(container_path)), ct.HEADER_BYTES + c.manifest["counts"]["stored_bytes"], groups)
    fmt = format_for_output(output, opt.fmt)
    if fmt == "vxs":
        raise VNXConfigurationError("VXS output is not supported for V4 strands (use FASTA or FASTQ)")
    ks, ms = Superblock.symbols(P)
    sb_data = np.zeros((ks, P), dtype=np.uint8)
    raw = sb.pack()
    sb_data.reshape(-1)[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
    sb_coded = CauchyRSCodec(ks, ms).encode(sb_data)
    sb_strands, _ = build_strands(lay, opt.constraints, tag, KIND_SUPER, np.zeros(ks + ms, dtype=np.int64),
                                  np.arange(ks + ms, dtype=np.int64), sb_coded)
    tasks = [(g, min(groups, g + opt.groups_per_task)) for g in range(0, groups, opt.groups_per_task)]
    initargs = (str(container_path), lay, K, M, opt.outer_code, opt.lt_seed, opt.lt_distribution, opt.constraints.to_dict(), tag,
                size, fmt)
    strands_total = sb_strands.shape[0]
    bases_total = int(sb_strands.size)
    max_variant = 0
    with StrandWriter(output, fmt, overwrite=overwrite) as w:
        w.write_bytes(_serialize(sb_strands, _labels(tag, KIND_SUPER, np.zeros(ks + ms, dtype=np.int64), np.arange(ks + ms)), fmt),
                      sb_strands.shape[0], int(sb_strands.size))

        def take(res):
            nonlocal strands_total, bases_total, max_variant
            g0, text, n, bases, mv = res
            w.write_bytes(text, n, bases)
            strands_total += n
            bases_total += bases
            max_variant = max(max_variant, mv)
            if progress:
                progress({"stage": "encode", "groups_done": g0 + opt.groups_per_task, "groups": groups,
                          "elapsed": time.perf_counter() - t0})

        if opt.workers == 1:
            _init(*initargs)
            for t in tasks:
                take(_encode_task(t))
        else:
            with ProcessPoolExecutor(max_workers=opt.workers, initializer=_init, initargs=initargs) as pool:
                window: deque = deque()
                for t in tasks:
                    window.append(pool.submit(_encode_task, t))
                    if len(window) >= 2 * opt.workers:
                        take(window.popleft().result())
                while window:
                    take(window.popleft().result())
        info = w.commit()
    codec = make_outer(opt.outer_code, K, M, opt.lt_seed, opt.lt_distribution)
    data_symbols = -(-size // P)
    outer_parity = strands_total - (ks + ms) - data_symbols
    seconds = time.perf_counter() - t0
    content = c.manifest["counts"]["content_bytes"]
    return {
        "output": str(output), "format": fmt, "strands": strands_total, "bases": bases_total, "strand_nt": lay.strand_nt,
        "container_bytes": size, "content_bytes": content, "archive_id": aid.hex(), "archive_tag": f"{tag:04x}",
        "layout": lay.to_dict(), "outer_code": codec.configuration(), "outer_capabilities": codec.capabilities(),
        "groups": groups, "superblock_strands": ks + ms,
        "overhead": {
            "nt_per_container_byte": round(bases_total / size, 4), "nt_per_content_byte": round(bases_total / max(1, content), 4),
            "bits_per_nt_net": round(8 * content / bases_total, 4) if bases_total else 0,
            "outer_parity_fraction": round(outer_parity / max(1, strands_total), 5),
            "frame_payload_fraction": round(P / lay.frame_bytes, 5),
            "marker_fraction": round(lay.markers * lay.marker_len / lay.strand_nt, 5),
            "address_fraction": round(10 / lay.frame_bytes, 5),
        },
        "max_scrambler_variant": max_variant, "file_sha256": info["file_sha256"], "seconds": seconds,
        "container_mb_s": round(size / seconds / 1e6, 3) if seconds else None, "workers": opt.workers,
    }
