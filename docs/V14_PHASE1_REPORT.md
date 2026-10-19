# V14 Phase 1 report: contracts and core storage model

Date 2026-10-18. Branch `build/v14` on top of the Phase 0 commit f0983a4. Scope: [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md)
P1 (master directive §3.4, §5.1, §13). Specification: [V14_CONTRACTS.md](V14_CONTRACTS.md).

## Implemented

* Rust crate `src/vnxdna/native/rust/vnxd` (library + `vnxd` command; std only, no `unsafe`, `license = "MIT"`,
  `overflow-checks = true` in release):
  `addr` (geometry, extents, generations, request spans), `state` (write-state machine), `error` (17 codes with retry
  class and NBD errno), `digest` (CRC-32, SHA-256), `record` (`vnx.record/1`: superblock, map update, generation
  release; strict decoder; torn-tail journal scan; JSON description), `json` (strict parser + canonical encoder equal
  to Python's `canonical_json`), `proto` (`vnx.mgmt/1`, `vnx.codec-ipc/1` schemas and validation), `media`
  (media-adapter trait, `Durability`, conformance suite, volatile `MemoryMedia` for tests). `vnxd version`,
  `vnxd inspect-journal`.
* Python package `vnxdna.device` (layer 6, added to `tests/architecture/test_layers.py` LAYERS and to the mypy gate):
  `contracts`, `records`, `protocol` — the same contracts, error codes mapped onto the library error registry.
* Cross-language golden files `tests/v14/golden/contracts/` (5 records, 15 malformed records, 9 valid and 17 invalid
  messages) with their generator; CI `rust` job runs the new crate's tests.
* `vnxdna.device` is not in `tests/architecture/public_paths.json`: that file is the frozen V6 compatibility contract
  and is never regenerated (the V13 `volume` package is not in it either).

## Tests and exact results

| check | result |
|---|---|
| `cargo test --release --offline --locked`, `RUSTFLAGS=-D warnings` (vnxd) | 36 passed (30 unit, 2 CLI, 4 golden) |
| `pytest tests/v14 tests/architecture` | 188 passed (80 new in `tests/v14`) |
| full suite (6 workers, systemd scope) | **4,534 passed, 4 skipped, 0 failed**, 401.9 s (gate 4,454 + the 80 new tests; same 4 skips) |
| `ruff check src tests research`, `mypy` (169 files), doc links | clean |

What the tests establish: every record kind round-trips and every single-bit flip and every truncation of a record is
rejected (both languages); a torn journal tail loses only the torn record; the write-state transition table is exact
(all 25 pairs); request splitting covers every byte exactly once over an exhaustive offset/length grid; the Rust code
reproduces every golden entry the Python code generated and the Python test compares the error table and op lists
with the Rust source; the media conformance suite passes `MemoryMedia` and rejects an adapter that returns wrong
bytes and one whose `remove` deletes other generations.

## Review

An independent review of the P1 diff (a reviewer comparing both implementations on 89 edge-case messages) found:
one canonical-JSON disagreement (Rust left DEL 0x7f unescaped, Python escapes it; the Rust unit test had asserted the
wrong behaviour), five minor parity gaps (record descriptions with generation 0 or 32-bit overflow, record
descriptions above 2^63 − 1, Python builders accepting integers the wire cannot carry, a non-`DeviceError` exception
on lone surrogates, and an error-response API that could confuse a carried PROTOCOL error with an invalid message),
one conformance-suite gap (nothing checked that `remove` leaves other generations alone) and four spec ambiguities.
All were fixed, each with a test or golden entry that pins it; no panic was found reachable from untrusted bytes or
text.

## Benchmark changes

None: Phase 1 adds no data path. Evidence category: B (software storage device) contracts only; no device exists yet.

## Known defects and remaining work

* The contracts are not yet used by a running service: the volume, VNX-IO, cache, backends and block device are
  P2–P8. The management client and the codec worker of `vnxdna.device` come with the phases that need them.
* The `integrity` and `engine` crates declared `license = "Proprietary"`; fixed to MIT right after this phase
  ([V14_DEPENDENCY_GRAPH.md](V14_DEPENDENCY_GRAPH.md) §5).
* The branch is not pushed (the founder pushes `build/v14`).

## Acceptance (P1 criteria of V14_MASTER_PLAN)

| criterion | result |
|---|---|
| `cargo test` green with `-D warnings` and overflow checks | PASS |
| records round-trip and reject every corrupted byte | PASS (every bit flip and truncation, both languages) |
| Python and Rust agree on every schema (cross-language golden tests) | PASS |
| layer test includes `device` | PASS |
| full suite not below the gate, 0 failed | PASS |

Phase 1: **PASS**. Next: P2 Volume + VNX-IO.
