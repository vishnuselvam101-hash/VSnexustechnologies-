# VNX-DNA 15.0.0 release gate

Gated commit: `d2a23ffde431e02009f3b907334b640a97593784` (clean tree; all source, test and doc changes of the release;
the commit that adds this directory only adds these logs). Run 2026-10-22 on the lab host (x86_64, 8 vCPU).

**The full Python suite was not run by this lane.** The host is loaded and the authoritative gate is the lead
engineer's run, so `pytest-full-suite.log` is a placeholder that the lead engineer's gate run replaces. The suite
counts of this release are therefore not stated here: they are not known to this lane, and this directory must not
carry a number that no run produced. The rows below are the checks that were actually run, with the numbers they
actually reported.

| check | result | log |
|---|---|---|
| full Python suite, 8 workers | **not run by this lane** - the lead engineer's gate produces it | pytest-full-suite.log (placeholder) |
| `cargo test --release --locked`, RUSTFLAGS=-D warnings, `vnxd` | **293 passed, 0 failed, 3 ignored** (lib 198, cli 2, concurrency stress 2, digital twin 9, e2e matrix 30, fault matrix 8, golden 4, media adapters 12, media worker 0 (2 ignored), metrics 9, nbd no-space 4, seal key check 2, seal worker 0 (1 ignored), worm snapshots 13) | cargo-test.log |
| same, `engine` | **26 passed, 0 failed** | cargo-test.log |
| same, `integrity` | **5 passed, 0 failed** | cargo-test.log |
| `ruff check .` | all checks passed | ruff.log |
| `ruff check src tests research` (the V14 gate scope) | all checks passed | ruff.log |
| `tests/v11/test_version_changelog.py` (focused) | 6 passed | - |
| `tests/compat/test_v4_v5_archives.py` + `tests/v14/test_volume_reliability.py::test_volume_written_by_the_base_code_still_opens` (focused) | 77 passed | - |
| `tests/v15/test_ap16_monitor.py` (focused) | 25 passed | - |
| `tools/offline-check.sh tests/v15/test_ap17_offline.py tests/v11/test_version_changelog.py` (focused, inside the network namespace) | 20 passed, exit 0 | - |

The Rust crates were tested with `RUSTFLAGS=-D warnings` and `cargo test --release --locked`; every crate exited 0
(`## exit vnxd 0`, `## exit engine 0`, `## exit integrity 0` in `cargo-test.log`). The crate versions are unchanged
from V14 (`vnxd` 0.1.0, `vnx_engine` 1.0.0, `vnx_integrity`), as V14 left them.

`ruff check .` was red on the tree this lane started from: six pyflakes F401 unused imports (`experiments/v11/bench/bench.py`,
`src/vnxdna/secure/monitor.py`, `tests/v15/test_ap16_monitor.py`). They are fixed in the commit before this one
(`chore(v15): drop the unused imports the lint gate reports`); no test was weakened, skipped or deleted, and the test
count does not drop.

The offline check runs the suite inside a network namespace; only a focused selection was run here, and the full-suite
run inside the namespace belongs to the lead engineer's gate.

Not part of this gate: the E2E integration runner with `/dev/nbdN` (`tools/v14-e2e.sh --integration`), the sanitizers,
the fuzz campaigns, aarch64, and a clean-checkout reproducible build.

All channel results in this release are SIMULATED: no DNA was synthesised, stored or sequenced, and the evidence claims
are categories A-D only (software codec, software storage device, electronic-backend persistence, simulated DNA channel).
