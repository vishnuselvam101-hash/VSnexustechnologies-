# VNX-DNA 15.0.0 release gate

Gated commit: `281d28388b97eeb9a40cbdb25aeb4494f71246ee` (clean tree; all source and test changes of the release).
Run 2026-10-22 on the lab host (x86_64, 8 vCPU, 31 GB RAM) on an otherwise idle host. The commit that follows the
gated commit only adds these logs and the completion report `docs/V15_COMPLETION_REPORT.md`; it changes no source and
no test file.

| check | result | log |
|---|---|---|
| full Python suite, 8 workers | **5,177 passed, 7 skipped, 0 failed**, 209.14 s | pytest-full-suite.log |
| `cargo test --release --locked`, RUSTFLAGS=-D warnings, `vnxd` | **295 passed, 0 failed, 5 ignored** (lib 200, cli 2, concurrency stress 2, digital twin 9, e2e matrix 30 (2 ignored), fault matrix 8, golden 4, media adapters 12, media worker 0 (2 ignored), metrics 9, nbd no-space 4, seal key check 2, seal worker 0 (1 ignored), worm snapshots 13) | cargo-test.log |
| same, `engine` | **26 passed, 0 failed** | cargo-test.log |
| same, `integrity` | **5 passed, 0 failed** | cargo-test.log |
| `ruff check .` | all checks passed | ruff.log |
| `ruff check src tests research` (the V14 gate scope) | all checks passed | ruff.log |

The Python suite ran exactly as the merge gate runs it: the nine native kernels are built first (so the ctypes
bindings load the kernels of this tree and not a stale library), then
`PYTHONPATH=src OPENBLAS_NUM_THREADS=1 pytest -o addopts= -q -n 8 -p no:cacheprovider -W ignore::DeprecationWarning`.
The two deselected files are `tests/v14/test_device_nbd.py` and `tests/v14/integration/test_v14_e2e_blockdev.py`; they
need `/dev/nbdN` device nodes, which this environment does not provide, and they are deselected by the merge gate as
well, so the gate and this run cover the same tests. The command line, the host, the interpreter version and the
start and end times are recorded at the head of `pytest-full-suite.log`.

The Rust crates were tested with `RUSTFLAGS=-D warnings` and `cargo test --release --locked`; every crate exited 0
(`--- crate vnxd exit code: 0`, `--- crate engine exit code: 0`, `--- crate integrity exit code: 0` in
`cargo-test.log`). The crate versions are unchanged from V14 (`vnxd` 0.1.0, `vnx_engine` 1.0.0, `vnx_integrity`), as
V14 left them.

The test count of this release does not drop against the V14 gate: V14 recorded 4,852 passed / 5 skipped, this release
records 5,177 passed / 7 skipped. No test was weakened, skipped or deleted in V15, and the V4/V5 archives still decode
(`tests/compat/`).

One test in this release is sensitive to the load of the host: `tests/v15/test_hardware_lane.py::test_the_documented_runtime_is_still_the_measured_runtime`
measures the reference decoder against the runtime table of `docs/V15_REFERENCE_DECODER.md` and requires the measurement
to stay within a factor of two of the stated number. An earlier run of this gate on a loaded host measured 25.1 ms for
`encrypted-2bit` against a stated 12 ms and failed on that bound; the same four archives measured on an idle host give
10.86 / 31.44 / 44.14 / 12.69 ms against a stated 11 / 31 / 44 / 12 ms, so the table and the code agree and the
earlier failure was host contention, not a drift of the code. The test was not changed, and the run recorded in
`pytest-full-suite.log` is the one on the quiet host.

The 7 skips of this run are the same kind as the 5 of the V14 gate: three `tests/cli/test_readme.py` tests (they
need the installed `vnx-dna` on PATH and bash), three `tests/v6/native/test_install.py` tests (the install test is
opt-in: `VNXDNA_INSTALL_TEST=1`) and one `tests/v5/test_native_align_levels.py` test (this CPU and OS support every
alignment level; the refusal path is covered by `test_masked_library_refuses_missing_levels`). The two
`/dev/nbdN` files listed above are deselected, not skipped, and are therefore not counted here.

The count is two higher than the run of the previous candidate (5,175) because one test fixture was corrected and two
tests were added, in the same commit (`b5141a0`): the V2 end-to-end property test now runs on the shipped `balanced`
geometry (64 data + 16 parity shards per group) instead of the shrunken 8+4 group of the `FAST` fixture, which no
shipped profile uses and whose 4-strand tolerance a lossy channel can exceed; the 8+4 geometry is kept by two new
deterministic tests. No assertion was weakened, and the same failure reproduces on the released v14.0.0 tag, so it was
not a V15 regression. The reasoning is in the docstring of the test and in `docs/V15_COMPLETION_REPORT.md`.

Not part of this gate: the E2E integration runner with `/dev/nbdN` (`tools/v14-e2e.sh --integration`), the sanitizers,
the fuzz campaigns, aarch64, and a clean-checkout reproducible build (QA-1).

All channel results in this release are SIMULATED: no DNA was synthesised, stored or sequenced, and the evidence claims
are categories A-D only (software codec, software storage device, electronic-backend persistence, simulated DNA channel).
