# VNX-DNA 14.0.0 release gate

Gated commit: `4d0e3cef8a186cbfd15608df2f6fd34ff9dcbc6e` (clean tree; all source, test and doc changes of the release; the commit that adds this
directory only adds these logs and the gate table in docs/V14_IMPLEMENTATION_REPORT.md §4). Run 2026-10-19 on the lab
host (x86_64, 8 vCPU) with no other heavy job running.

| check | result | log |
|---|---|---|
| full Python suite, 8 workers, systemd scope MemoryMax=24G | **4,852 passed, 5 skipped, 0 failed**, 383 s | pytest-full-suite.log |
| `cargo test --release --locked`, RUSTFLAGS=-D warnings, `vnxd` | **240 passed, 0 failed, 3 ignored** (lib 182, cli 2, concurrency 2, e2e matrix 30, fault matrix 8, golden 4, media adapters 12) | cargo-test.log |
| same, `engine` | **25 passed, 0 failed** | cargo-test.log |
| same, `integrity` | **5 passed, 0 failed** | cargo-test.log |
| `ruff check src tests research` | all checks passed | ruff.log |

Not part of this gate: the E2E integration runner with `/dev/nbdN` (`tools/v14-e2e.sh --integration`), sanitizers, fuzz, aarch64 (last results in docs/V14_PHASE9_REPORT.md and
docs/V14_PHASE10_REPORT.md), and a clean-checkout build (criterion 2, INCOMPLETE).
