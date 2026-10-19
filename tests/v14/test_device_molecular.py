"""V14 Phase 3: the molecular-memory research interface and its simulator (SIMULATED; no molecule involved)."""
from __future__ import annotations

import hashlib

import pytest

from vnxdna.device import DeviceError
from vnxdna.device import molecular as mm


def _region(**model):
    return mm.SimulatedMolecularMemory(blocks=16, block_size=64, model=mm.SimulatorModel(**model))


def test_the_simulator_passes_the_conformance_suite():
    assert mm.conformance(_region) == []
    caps = _region().capabilities()
    assert caps.simulated and caps.persistence is mm.Persistence.RETAINED_SIMULATED
    assert caps.endurance_writes is None, "no endurance figure unless the caller models one"


def test_a_broken_region_fails_conformance():
    class Lossy(mm.SimulatedMolecularMemory):
        def read(self, block):
            data, rep = super().read(block)
            return data[:-1] + b"\x00", rep

    assert any("different bytes" in v for v in mm.conformance(lambda: Lossy(blocks=16, block_size=64)))


def test_simulated_read_errors_are_detected_never_returned():
    r = _region(bit_flip_per_read=0.3, seed=5)
    data = bytes(range(64))
    r.write(3, data)
    ok = errors = 0
    for _ in range(500):
        try:
            got, _ = r.read(3)
        except DeviceError as e:
            assert e.device_code == "CORRUPTION"
            errors += 1
            continue
        assert got == data
        ok += 1
    assert errors > 50 and ok > 50
    assert r.health(3).read_errors == errors
    assert r.state() is mm.RegionState.OK, "read errors are transient: the stored block is intact"
    r.model.bit_flip_per_read = 0.0
    assert r.read(3)[0] == data


def test_modelled_endurance_makes_a_block_read_only_but_readable():
    r = _region(endurance_writes=3)
    for v in range(3):
        r.write(1, bytes([v]) * 64)
    with pytest.raises(DeviceError) as e:
        r.write(1, bytes(64))
    assert e.value.device_code == "READ_ONLY"
    assert r.read(1)[0] == bytes([2]) * 64
    assert r.health(1).worn_out and r.state() is mm.RegionState.DEGRADED
    r.write(2, bytes(64))     # other blocks are unaffected


def test_failed_or_unavailable_regions_refuse_everything():
    for state in (mm.RegionState.FAILED, mm.RegionState.UNAVAILABLE):
        r = _region()
        r.write(0, bytes(64))
        r.set_state(state)
        assert r.state() is state
        for op in (lambda: r.read(0), lambda: r.write(0, bytes(64)), lambda: r.health(0),
                   lambda: r.verify(0, hashlib.sha256(bytes(64)).digest())):
            with pytest.raises(DeviceError) as e:
                op()
            assert e.value.device_code == "BACKEND_UNAVAILABLE"


def test_latency_is_a_model_output_and_invalid_models_are_refused():
    r = _region(read_latency_s=0.002, write_latency_s=0.01)
    assert r.write(0, bytes(64)).model_latency_s == 0.01
    assert r.read(0)[1].model_latency_s == 0.002
    for bad in ({"bit_flip_per_read": 1.5}, {"bit_flip_per_read": -0.1}):
        with pytest.raises(DeviceError):
            _region(**bad)
    with pytest.raises(DeviceError):
        mm.SimulatedMolecularMemory(blocks=0, block_size=64)
    for endurance in (0, -1):
        with pytest.raises(DeviceError):
            _region(endurance_writes=endurance)
    with pytest.raises(DeviceError) as e:
        _region().write(0, "x" * 64)
    assert e.value.device_code == "INVALID_ARGUMENT"
    r = _region()
    with pytest.raises(DeviceError):
        r.set_state(mm.RegionState.DEGRADED)
    r.set_state(mm.RegionState.FAILED)
    r.set_state(mm.RegionState.OK)
    assert r.read(0)[0] == bytes(64)


def test_seeded_simulation_is_deterministic():
    def run():
        r = _region(bit_flip_per_read=0.2, seed=11)
        r.write(0, bytes(64))
        out = []
        for _ in range(100):
            try:
                r.read(0)
                out.append(1)
            except DeviceError:
                out.append(0)
        return out

    assert run() == run()
