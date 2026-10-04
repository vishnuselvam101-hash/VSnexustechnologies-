"""Container → DNA strands (streaming, parallel, deterministic).

The container byte stream is cut into *groups* of K symbols of P bytes. Each
group is one outer-code block (Cauchy RS: K + M symbols; LT fountain: K + M
droplets). Every symbol becomes one strand (vnxdna.v4.frame). A *superblock*
group (kind 1) makes a strand pool self-describing: geometry, outer code,
archive ID, container size and SHA-256. It is Cauchy-coded with Ks data and
3·Ks parity symbols (any Ks of its 4·Ks strands suffice).

Strand order in the output: superblock strands, then groups 0, 1, … with
symbols in index order. Group g occupies container bytes [g·K·P, (g+1)·K·P),
so the strands holding any container byte range — and therefore any chunk or
file (``vnx locate``) — are known without an index file.

V6 (opt-in, ``stripe_depth`` / ``column_parity`` / ``strand_order`` / ``outer_plan``): column-parity groups and
interleaved strand order, signalled by superblock version 2 (vnxdna.v6, docs/V6_OUTER_CODE.md). Without these
options the output is unchanged (superblock version 1).
"""
from __future__ import annotations

import os
import struct
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..v2.strandio import StrandWriter, format_for_output
from . import container as ct
from .codecs import CODE_IDS, CauchyRSCodec, make_outer
from .constraints import ConstraintConfig
from .errors import VNXConfigurationError, VNXFormatError
from .frame import KIND_DATA, KIND_SUPER, PROFILES, Layout, build_strands
from .util import sha256_file

SB_MAGIC = b"VNX4SB"
SB_VERSION = 1
SB_VERSION_V6 = 2          # V6 outer code: bytes 18..21 = stripe depth D (u16), column parity Mc (u8), strand order (u8)
ORDER_IDS = {"sequential": 0, "interleaved": 1}
SB_BYTES = 96
DIST_IDS = {"dense": 0, "robust-soliton": 1}


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


@dataclass
class Superblock:
    outer_code: str
    K: int
    M: int
    layout: Layout
    lt_distribution: str
    lt_seed: int
    archive_id: bytes
    container_size: int
    container_sha256: bytes
    index_offset: int
    group_count: int
    version: int = SB_VERSION
    stripe_depth: int = 0           # version 2 only
    column_parity: int = 0          # version 2 only
    strand_order: str = "sequential"

    def geometry(self):
        """The V6 outer geometry (version 2 only)."""
        from ..v6.outer import Geometry
        return Geometry(self.K, self.M, self.stripe_depth, self.column_parity, self.layout.payload_bytes,
                        self.container_size, self.strand_order)

    @property
    def total_groups(self) -> int:
        """Data groups plus V6 column-parity groups."""
        return self.group_count if self.version == SB_VERSION else self.geometry().total_groups

    def pack(self) -> bytes:
        code_id = {v: k for k, v in CODE_IDS.items()}[self.outer_code]
        lay = self.layout
        if self.version == SB_VERSION:
            word = struct.pack(">I", self.lt_seed)
        else:
            word = struct.pack(">HBB", self.stripe_depth, self.column_parity, ORDER_IDS[self.strand_order])
        body = (SB_MAGIC + bytes([self.version, code_id]) + struct.pack(">HHHBBBB", self.K, self.M, lay.payload_bytes,
                                                                          lay.inner_parity, lay.marker_period // 4,
                                                                          lay.marker_len, DIST_IDS[self.lt_distribution])
                + word + self.archive_id + struct.pack(">Q", self.container_size) + self.container_sha256
                + struct.pack(">QI", self.index_offset, self.group_count) + b"\x00\x00")
        assert len(body) == SB_BYTES - 4
        return body + struct.pack(">I", zlib.crc32(body))

    @classmethod
    def unpack(cls, data: bytes) -> "Superblock":
        if len(data) < SB_BYTES:
            raise VNXFormatError("superblock truncated", stage="superblock")
        data = data[:SB_BYTES]
        if data[:6] != SB_MAGIC or zlib.crc32(data[:-4]) != struct.unpack(">I", data[-4:])[0]:
            raise VNXFormatError("superblock magic/CRC invalid", stage="superblock")
        if data[6] not in (SB_VERSION, SB_VERSION_V6):
            from .errors import VNXUnsupportedVersionError
            raise VNXUnsupportedVersionError(f"unsupported superblock version {data[6]}")
        code = CODE_IDS.get(data[7])
        if code is None:
            raise VNXFormatError(f"unknown outer code id {data[7]}", stage="superblock")
        k, m, p, r, mp4, ml, dist = struct.unpack(">HHHBBBB", data[8:18])
        seed = struct.unpack(">I", data[18:22])[0]
        aid = data[22:38]
        size = struct.unpack(">Q", data[38:46])[0]
        sha = data[46:78]
        index_offset, groups = struct.unpack(">QI", data[78:90])
        inv = {v: k_ for k_, v in DIST_IDS.items()}
        if dist not in inv:
            raise VNXFormatError("unknown fountain distribution id", stage="superblock")
        # a superblock is untrusted input: every field is checked here so that a CRC-valid forgery is a format error
        # (and the decoder moves on to the next candidate), never a crash or a configuration error
        if not 1 <= k or k + m > (256 if code == "cauchy-rs" else 65535):
            raise VNXFormatError(f"superblock outer code parameters K = {k}, M = {m} are invalid", stage="superblock")
        try:
            lay = Layout(p, r, mp4 * 4, ml).validate()
        except VNXConfigurationError as error:
            raise VNXFormatError(f"superblock layout is invalid: {error}", stage="superblock") from None
        if groups != -(-size // (k * p)) or index_offset > size:
            raise VNXFormatError("superblock geometry is inconsistent", stage="superblock")
        if data[6] == SB_VERSION:
            return cls(code, k, m, lay, inv[dist], seed, aid, size, sha, index_offset, groups)
        depth, mc, order = struct.unpack(">HBB", data[18:22])
        orders = {v: k_ for k_, v in ORDER_IDS.items()}
        if code != "cauchy-rs" or dist != 0 or order not in orders:
            raise VNXFormatError("superblock version 2 fields are invalid", stage="superblock")
        sb = cls(code, k, m, lay, "dense", 0, aid, size, sha, index_offset, groups, SB_VERSION_V6, depth, mc, orders[order])
        try:
            sb.geometry().validate()
        except VNXConfigurationError:
            raise VNXFormatError("superblock version 2 geometry is invalid", stage="superblock") from None
        return sb

    @staticmethod
    def symbols(payload_bytes: int) -> tuple[int, int]:
        ks = -(-SB_BYTES // payload_bytes)
        return ks, 3 * ks


def group_k(sb_or_size: int, K: int, P: int, g: int) -> int:
    """Number of source symbols of group g for a container of ``sb_or_size`` bytes."""
    groups = -(-sb_or_size // (K * P))
    if g < groups - 1:
        return K
    rem = sb_or_size - (groups - 1) * K * P
    return -(-rem // P)


# ============================================================================ worker
_W: dict = {}


def _init(container: str, lay: Layout, K: int, M: int, outer: str, seed: int, dist: str, cfg: dict, tag: int, size: int,
          fmt: str) -> None:
    _W.update(container=container, lay=lay, K=K, M=M, codec=make_outer(outer, K, M, seed, dist), cfg=ConstraintConfig.from_dict(cfg),
              tag=tag, size=size, fmt=fmt)


_ASCII = np.frombuffer(b"ACGTN", dtype=np.uint8)


def _labels(tag: int, kind: int, groups: np.ndarray, symbols: np.ndarray) -> list[bytes]:
    pre = f"vnx4|{tag:04x}|{kind}|".encode()
    return [pre + b"%d|%d" % (g, s) for g, s in zip(groups.tolist(), symbols.tolist())]


def _serialize(codes: np.ndarray, labels: list[bytes], fmt: str) -> bytes:
    """Vectorised FASTA/FASTQ text: one table lookup for all bases, one join for all records."""
    n, length = codes.shape
    seq = np.empty((n, length + 1), dtype=np.uint8)
    seq[:, :length] = _ASCII[codes]
    seq[:, length] = 10
    rows = seq.tobytes()
    w = length + 1
    if fmt == "fastq":
        q = b"+\n" + b"I" * length + b"\n"
        return b"".join(b"@" + lab + b"\n" + rows[i * w:(i + 1) * w] + q for i, lab in enumerate(labels))
    return b"".join(b">" + lab + b"\n" + rows[i * w:(i + 1) * w] for i, lab in enumerate(labels))


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
        from ..v6.encoder import encode_container_v6
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
