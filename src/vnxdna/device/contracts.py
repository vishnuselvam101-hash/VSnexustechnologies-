"""Storage contracts shared with the ``vnxd`` service (docs/V14_CONTRACTS.md §1, §2, §5).

The Python mirror of ``src/vnxdna/native/rust/vnxd/src/{error,state,addr}.rs``: error codes with their retry class
and host errno, the write-state machine, and the device geometry with its request splitting. Both implementations
are checked against the same golden files (``tests/v14/golden/contracts``).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.errors import CODES, VNXError

# errno values the Linux NBD driver passes to the block layer
EPERM, EIO, ENOMEM, EINVAL, ENOSPC, EOVERFLOW, ENOTSUP, ESHUTDOWN = 1, 5, 12, 22, 28, 75, 95, 108

#: code -> (class, errno, the closest code of the library-wide registry ``core.errors.CODES``)
ERROR_CODES: dict[str, tuple[str, int, str]] = {
    "INVALID_ARGUMENT": ("permanent", EINVAL, "FORMAT_ERROR"),
    "OUT_OF_RANGE": ("permanent", EINVAL, "FORMAT_ERROR"),
    "NO_SPACE": ("permanent", ENOSPC, "INTERNAL_ERROR"),
    "UNAUTHORIZED": ("permanent", EPERM, "INTERNAL_ERROR"),
    "READ_ONLY": ("permanent", EPERM, "INTERNAL_ERROR"),
    "NOT_FOUND": ("permanent", EIO, "INTERNAL_ERROR"),
    "CORRUPTION": ("permanent", EIO, "INTEGRITY_ERROR"),
    "BACKEND_UNAVAILABLE": ("retryable", EIO, "INTERNAL_ERROR"),
    "BACKEND_BUSY": ("retryable", EIO, "INTERNAL_ERROR"),
    "TIMEOUT": ("retryable", EIO, "INTERNAL_ERROR"),
    "RESOURCE_EXHAUSTED": ("retryable", ENOMEM, "RESOURCE_LIMIT"),
    "CANCELLED": ("permanent", ESHUTDOWN, "INTERNAL_ERROR"),
    "SHUTTING_DOWN": ("permanent", ESHUTDOWN, "INTERNAL_ERROR"),
    "PROTOCOL": ("permanent", EINVAL, "FORMAT_ERROR"),
    "VERSION_MISMATCH": ("permanent", ENOTSUP, "FEATURE_UNSUPPORTED"),
    "OVERFLOW": ("permanent", EOVERFLOW, "FORMAT_ERROR"),
    "INTERNAL": ("permanent", EIO, "INTERNAL_ERROR"),
}


class DeviceError(VNXError):
    """A failure reported by (or for) the storage service, with its device error code."""

    stage = "device"

    def __init__(self, device_code: str, message: str):
        if device_code not in ERROR_CODES:
            raise ValueError(f"unknown device error code {device_code!r}")
        cls, _, code = ERROR_CODES[device_code]
        super().__init__(f"{device_code}: {message}", retryable=cls == "retryable", code=code,
                         details={"device_code": device_code})
        self.category = CODES[code][0]
        self.device_code = device_code
        self.device_message = message

    @property
    def errno(self) -> int:
        return ERROR_CODES[self.device_code][1]

    @property
    def error_class(self) -> str:
        return ERROR_CODES[self.device_code][0]


class WriteState(str, Enum):
    """RECEIVED -> STAGED -> BACKEND_COMMITTED -> VERIFIED; any non-terminal state may become FAILED."""

    RECEIVED = "RECEIVED"
    STAGED = "STAGED"
    BACKEND_COMMITTED = "BACKEND_COMMITTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (WriteState.VERIFIED, WriteState.FAILED)

    @property
    def is_durable(self) -> bool:
        """May the host be told the write is durable (within the backend's stated guarantee)?"""
        return self in (WriteState.BACKEND_COMMITTED, WriteState.VERIFIED)

    def advance(self, to: WriteState) -> WriteState:
        if (self, to) not in _LEGAL:
            raise DeviceError("INTERNAL", f"illegal write-state transition {self.value} -> {to.value}")
        return to


_LEGAL = {
    (WriteState.RECEIVED, WriteState.STAGED), (WriteState.STAGED, WriteState.BACKEND_COMMITTED),
    (WriteState.BACKEND_COMMITTED, WriteState.VERIFIED), (WriteState.RECEIVED, WriteState.FAILED),
    (WriteState.STAGED, WriteState.FAILED), (WriteState.BACKEND_COMMITTED, WriteState.FAILED),
}

MIN_BLOCK_SIZE = 512
MAX_BLOCK_SIZE = 1 << 20
MAX_EXTENT_BYTES = 64 << 20
U64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class ExtentSpan:
    extent: int
    offset: int          # byte offset inside the extent
    length: int
    buf_offset: int      # byte offset inside the request buffer


@dataclass(frozen=True)
class Geometry:
    block_size: int
    blocks_per_extent: int
    capacity_blocks: int

    def __post_init__(self) -> None:
        bs, bpe, cap = self.block_size, self.blocks_per_extent, self.capacity_blocks
        if not (MIN_BLOCK_SIZE <= bs <= MAX_BLOCK_SIZE and bs & (bs - 1) == 0):
            raise DeviceError("INVALID_ARGUMENT",
                              f"block size {bs} must be a power of two in [{MIN_BLOCK_SIZE}, {MAX_BLOCK_SIZE}]")
        if bpe < 1 or bs * bpe > MAX_EXTENT_BYTES:
            raise DeviceError("INVALID_ARGUMENT",
                              f"blocks per extent {bpe} must be >= 1 with extent size <= {MAX_EXTENT_BYTES} bytes")
        if cap < 1:
            raise DeviceError("INVALID_ARGUMENT", "capacity must be at least one block")
        if cap * bs > U64_MAX:
            raise DeviceError("OVERFLOW", "capacity in bytes does not fit 64 bits")

    @property
    def capacity_bytes(self) -> int:
        return self.capacity_blocks * self.block_size

    @property
    def extent_bytes(self) -> int:
        return self.block_size * self.blocks_per_extent

    @property
    def extent_count(self) -> int:
        return -(-self.capacity_blocks // self.blocks_per_extent)

    def extent_len(self, extent: int) -> int:
        if not 0 <= extent < self.extent_count:
            raise DeviceError("OUT_OF_RANGE", f"extent {extent} beyond {self.extent_count} extents")
        return min(self.extent_bytes, self.capacity_bytes - extent * self.extent_bytes)

    def check_range(self, offset: int, length: int) -> None:
        if length == 0:
            raise DeviceError("INVALID_ARGUMENT", "zero-length request")
        if offset < 0 or length < 0 or offset + length > self.capacity_bytes:
            raise DeviceError("OUT_OF_RANGE",
                              f"request [{offset}, +{length}) beyond the device capacity {self.capacity_bytes}")

    def spans(self, offset: int, length: int) -> list[ExtentSpan]:
        self.check_range(offset, length)
        eb, out, pos, end = self.extent_bytes, [], offset, offset + length
        while pos < end:
            inner = pos % eb
            take = min(eb - inner, end - pos)
            out.append(ExtentSpan(pos // eb, inner, take, pos - offset))
            pos += take
        return out

    def is_block_aligned(self, offset: int, length: int) -> bool:
        return offset % self.block_size == 0 and length % self.block_size == 0
