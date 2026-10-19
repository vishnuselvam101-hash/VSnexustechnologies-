"""Molecular-memory research interface ``vnx.molecular-memory/1`` (master directive §5.2; docs/V14_MOLECULAR_MEMORY.md).

A protocol a future experimental molecular memory would implement, a SIMULATOR of it, and a conformance suite. It is
separate from VNX-RAM (the electronic working memory of the service) and nothing in the platform depends on it.

No molecular memory exists here. The simulator stores bytes in Python objects; its error rate, endurance and latency
are model INPUTS chosen by the caller (defaults: no errors, unlimited endurance, zero latency) — never measurements,
and never presented as properties of any physical medium.

Contract (checked by :func:`conformance`):

* ``negotiate(versions)`` returns the highest common protocol version or raises VERSION_MISMATCH;
* ``capabilities()`` describes the region (block size and count, operations, persistence, whether it is simulated);
* ``write(block, data)`` stores exactly ``block_size`` bytes; ``read(block)`` returns exactly what was written or
  raises CORRUPTION when the stored bytes fail their check — never other bytes; an unwritten block reads as zeros;
* ``verify(block, sha256)`` compares without returning data;
* ``health(block)`` reports writes, detected read errors and the block state; a worn-out block refuses writes
  (READ_ONLY) and stays readable while its data checks;
* a region in state FAILED or UNAVAILABLE refuses every operation with BACKEND_UNAVAILABLE.
"""
from __future__ import annotations

import hashlib
import random
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from .contracts import DeviceError

PROTOCOL = "vnx.molecular-memory"
VERSIONS = (1,)


class RegionState(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"        # some blocks are worn out (read-only); the rest serves normally
    FAILED = "failed"            # the region refuses every operation
    UNAVAILABLE = "unavailable"  # the region cannot be reached (e.g. instrument offline)


class Persistence(str, Enum):
    VOLATILE = "volatile"
    RETAINED_SIMULATED = "retained-simulated"   # kept for the simulator object's lifetime only


@dataclass(frozen=True)
class Capabilities:
    protocol: str
    version: int
    block_size: int
    blocks: int
    operations: tuple[str, ...]
    persistence: Persistence
    simulated: bool
    #: model input of a simulator (None = unlimited); for real hardware only a measured, cited value may go here
    endurance_writes: int | None
    notes: str = ""


@dataclass
class BlockHealth:
    writes: int = 0
    read_errors: int = 0
    worn_out: bool = False


@dataclass(frozen=True)
class OpReport:
    """Per-operation report. ``model_latency_s`` is the simulator's latency MODEL output, not a measurement."""

    block: int
    model_latency_s: float
    errors_detected: int


class MolecularMemoryRegion(ABC):
    @abstractmethod
    def negotiate(self, versions: tuple[int, ...]) -> int: ...

    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def state(self) -> RegionState: ...

    @abstractmethod
    def read(self, block: int) -> tuple[bytes, OpReport]: ...

    @abstractmethod
    def write(self, block: int, data: bytes) -> OpReport: ...

    @abstractmethod
    def verify(self, block: int, sha256: bytes) -> bool: ...

    @abstractmethod
    def health(self, block: int) -> BlockHealth: ...


@dataclass
class SimulatorModel:
    """Model inputs of the simulator (all chosen by the caller; none is a physical property)."""

    bit_flip_per_read: float = 0.0       # probability that a read sees one flipped bit (transient: storage unchanged)
    endurance_writes: int | None = None  # writes after which a block refuses further writes
    read_latency_s: float = 0.0
    write_latency_s: float = 0.0
    seed: int = 0


@dataclass
class _Cell:
    data: bytes
    crc: int


@dataclass
class SimulatedMolecularMemory(MolecularMemoryRegion):
    """In-process simulator of the protocol. SIMULATED: no molecule is involved."""

    blocks: int
    block_size: int
    model: SimulatorModel = field(default_factory=SimulatorModel)
    _state: RegionState = RegionState.OK
    _cells: dict[int, _Cell] = field(default_factory=dict)
    _health: dict[int, BlockHealth] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.blocks < 1 or self.block_size < 1:
            raise DeviceError("INVALID_ARGUMENT", "a region needs at least one block of at least one byte")
        if not 0.0 <= self.model.bit_flip_per_read <= 1.0:
            raise DeviceError("INVALID_ARGUMENT", "bit_flip_per_read must be a probability")
        if self.model.endurance_writes is not None and self.model.endurance_writes < 1:
            raise DeviceError("INVALID_ARGUMENT", "endurance_writes must be >= 1 (or None for unlimited)")
        self._rng = random.Random(self.model.seed)

    # --- protocol ------------------------------------------------------------------------------------------------
    def negotiate(self, versions: tuple[int, ...]) -> int:
        common = set(versions) & set(VERSIONS)
        if not common:
            raise DeviceError("VERSION_MISMATCH", f"{PROTOCOL} versions {sorted(versions)}; supported {list(VERSIONS)}")
        return max(common)

    def capabilities(self) -> Capabilities:
        return Capabilities(PROTOCOL, max(VERSIONS), self.block_size, self.blocks, ("read", "write", "verify", "health"),
                            Persistence.RETAINED_SIMULATED, True, self.model.endurance_writes,
                            "software simulator; error, endurance and latency figures are model inputs")

    def state(self) -> RegionState:
        if self._state in (RegionState.FAILED, RegionState.UNAVAILABLE):
            return self._state
        # read errors are transient events, reported per block by health(); only worn-out blocks degrade the region
        return RegionState.DEGRADED if any(h.worn_out for h in self._health.values()) else RegionState.OK

    def _check(self, block: int) -> BlockHealth:
        if self._state in (RegionState.FAILED, RegionState.UNAVAILABLE):
            raise DeviceError("BACKEND_UNAVAILABLE", f"region is {self._state.value}")
        if type(block) is not int or not 0 <= block < self.blocks:
            raise DeviceError("OUT_OF_RANGE", f"block {block!r} outside 0..{self.blocks - 1}")
        return self._health.setdefault(block, BlockHealth())

    def read(self, block: int) -> tuple[bytes, OpReport]:
        h = self._check(block)
        cell = self._cells.get(block)
        report = OpReport(block, self.model.read_latency_s, 0)
        if cell is None:
            return bytes(self.block_size), report
        data = cell.data
        if self.model.bit_flip_per_read and self._rng.random() < self.model.bit_flip_per_read:
            flipped = bytearray(data)
            bit = self._rng.randrange(len(flipped) * 8)
            flipped[bit // 8] ^= 1 << (bit % 8)
            data = bytes(flipped)
        if zlib.crc32(data) != cell.crc:
            h.read_errors += 1
            raise DeviceError("CORRUPTION", f"block {block} failed its check (simulated read error)")
        return data, report

    def write(self, block: int, data: bytes) -> OpReport:
        h = self._check(block)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise DeviceError("INVALID_ARGUMENT", f"block data must be bytes, got {type(data).__name__}")
        if len(data) != self.block_size:
            raise DeviceError("INVALID_ARGUMENT", f"a block holds {self.block_size} bytes, got {len(data)}")
        if h.worn_out:
            raise DeviceError("READ_ONLY", f"block {block} reached its modelled endurance")
        self._cells[block] = _Cell(bytes(data), zlib.crc32(data))
        h.writes += 1
        if self.model.endurance_writes is not None and h.writes >= self.model.endurance_writes:
            h.worn_out = True
        return OpReport(block, self.model.write_latency_s, 0)

    def verify(self, block: int, sha256: bytes) -> bool:
        data, _ = self.read(block)
        return hashlib.sha256(data).digest() == sha256

    def health(self, block: int) -> BlockHealth:
        h = self._check(block)
        return BlockHealth(h.writes, h.read_errors, h.worn_out)

    # --- simulator controls (test and experiment use) --------------------------------------------------------------
    def set_state(self, state: RegionState) -> None:
        """Fault injection: FAILED or UNAVAILABLE take the region down; OK brings it back (DEGRADED is derived)."""
        if state is RegionState.DEGRADED:
            raise DeviceError("INVALID_ARGUMENT", "degraded is derived from block health, it cannot be set")
        self._state = state


def conformance(make_region) -> list[str]:
    """Run the protocol contract against fresh regions from ``make_region()``; returns the violations found."""
    bad: list[str] = []

    def expect_error(code: str, fn, what: str) -> None:
        try:
            fn()
        except DeviceError as e:
            if e.device_code != code:
                bad.append(f"{what}: {e.device_code} instead of {code}")
            return
        bad.append(f"{what}: no error (expected {code})")

    r = make_region()
    caps = r.capabilities()
    if caps.protocol != PROTOCOL or caps.version not in VERSIONS:
        bad.append(f"capabilities name {caps.protocol}/{caps.version}")
    if r.negotiate((1, 99)) != 1:
        bad.append("negotiate did not pick version 1")
    expect_error("VERSION_MISMATCH", lambda: r.negotiate((99,)), "negotiate without a common version")
    bs, n = caps.block_size, caps.blocks
    if r.read(0)[0] != bytes(bs):
        bad.append("an unwritten block does not read as zeros")
    payloads = {b: bytes((b * 31 + i) % 256 for i in range(bs)) for b in range(min(n, 8))}
    for b, p in payloads.items():
        r.write(b, p)
    for b, p in payloads.items():
        if r.read(b)[0] != p:
            bad.append(f"block {b} read back different bytes")
        if not r.verify(b, hashlib.sha256(p).digest()) or r.verify(b, hashlib.sha256(p + b"x").digest()):
            bad.append(f"verify of block {b} is wrong")
    if r.health(0).writes != 1:
        bad.append("health does not count writes")
    expect_error("OUT_OF_RANGE", lambda: r.read(n), "read past the region")
    expect_error("OUT_OF_RANGE", lambda: r.write(-1, bytes(bs)), "write before the region")
    expect_error("INVALID_ARGUMENT", lambda: r.write(0, bytes(bs + 1)), "write of a wrong length")
    if r.state() != RegionState.OK:
        bad.append(f"a healthy region reports {r.state().value}")
    return bad
