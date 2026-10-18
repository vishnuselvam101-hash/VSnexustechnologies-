# V14 molecular-memory research interface

Phase 3 specification (master directive §5.2). Implementation: `src/vnxdna/device/molecular.py`; tests:
`tests/v14/test_device_molecular.py`.

## What this is, and what it is not

A protocol (`vnx.molecular-memory/1`) that a future experimental molecular memory would have to implement, a
software **simulator** of it, and a conformance suite. No molecular memory exists in this project. The simulator
keeps bytes in Python objects. Its read-error probability, endurance and latency are model **inputs** chosen by the
caller (defaults: no errors, unlimited endurance, zero latency); they are not measurements and describe no physical
medium. VNX-RAM ([V14_RAM_AND_CACHE.md](V14_RAM_AND_CACHE.md)) is ordinary electronic RAM and is never called
molecular memory. The storage platform does not depend on this interface.

## Protocol

| operation | contract |
|---|---|
| `negotiate(versions)` | the highest common version, or VERSION_MISMATCH |
| `capabilities()` | protocol, version, block size, block count, operations, persistence (`volatile` or `retained-simulated`), `simulated` flag, endurance (None unless modelled or measured and cited), notes |
| `state()` | `ok`, `degraded` (some blocks worn out), `failed`, `unavailable` |
| `write(block, data)` | exactly `block_size` bytes; a worn-out block refuses with READ_ONLY |
| `read(block)` | exactly the bytes written, or CORRUPTION when the stored bytes fail their check — never other bytes; an unwritten block reads as zeros; returns an operation report |
| `verify(block, sha256)` | compares without returning the data |
| `health(block)` | writes, detected read errors, worn-out flag |
| errors | OUT_OF_RANGE (block outside the region), INVALID_ARGUMENT (wrong length), BACKEND_UNAVAILABLE (region failed or unavailable) |

Operation reports carry `model_latency_s`, the simulator's latency model output (a model input echoed back, not a
timing).

## Simulator

`SimulatedMolecularMemory(blocks, block_size, SimulatorModel(bit_flip_per_read, endurance_writes, read_latency_s,
write_latency_s, seed))`. Each block is stored with a CRC-32; a modelled read error flips one bit of the bytes read,
which the check detects (the simulator detects errors, it does not correct them). The flip is transient: the stored
block is unchanged and a later read can succeed; `health()` counts the detected errors. Runs are deterministic for
a seed. `set_state` puts the region into `failed` or `unavailable` for fault tests, and back to `ok`.

## Conformance suite

`conformance(make_region)` checks version negotiation, capabilities, zero reads of unwritten blocks, read-back of
written blocks, verify, write counting, range and length errors, and the healthy state. The simulator passes it;
a region that returns altered bytes fails it (tested).

## Path to a physical experiment

An experimental implementation would provide the same operations with measured, cited figures in `capabilities()`
and `simulated = False`, pass the conformance suite, and report results under evidence category E or F. Until then,
every result from this interface is SIMULATED.
