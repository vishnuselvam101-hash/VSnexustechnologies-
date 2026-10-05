"""The superblock: the self-describing kind-1 record of a strand pool (VNX4 §12, superblock versions 1 and 2;
V6_OUTER_CODE §1). Pack/unpack with full validation of untrusted fields, and the group sizes it implies.
Split from ``vnxdna.v4.encoder`` (V6 Phase 2, M4); it embeds the strand :class:`~vnxdna.dnaenc.layout.Layout`, so it
lives in the DNA-encoding layer."""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from vnxdna.codec.codecs import CODE_IDS
from vnxdna.core.errors import VNXConfigurationError, VNXFormatError
from vnxdna.dnaenc.layout import Layout


SB_MAGIC = b"VNX4SB"
SB_VERSION = 1
SB_VERSION_V6 = 2          # V6 outer code: bytes 18..21 = stripe depth D (u16), column parity Mc (u8), strand order (u8)
ORDER_IDS = {"sequential": 0, "interleaved": 1}
SB_BYTES = 96
DIST_IDS = {"dense": 0, "robust-soliton": 1}


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
        from vnxdna.codec.outer import Geometry
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
            from vnxdna.core.errors import VNXUnsupportedVersionError
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
