# VNX-DNA V5 — Phase 4 provenance validation (Gate A)

All channel results are **SIMULATED** (V4 channel simulator, harness-injected errors, synthetic quality models). No
DNA was synthesised or sequenced, and no posterior is calibrated against a real sequencer.

## 1. Result

**Gate A passed.** The critical Phase 4 experiments were re-run on a clean worktree of the Phase 4 commit
`c5b68d6`, with the native aligner rebuilt from that commit's own source. Every result file records
`commit_under_test = c5b68d684c757ece2f9f45084e44c1313bc21cef` and `worktree_dirty = false`.

| experiment | deterministic differences (A) | float-noise differences (B) | runtime, re-run / published (median, range) | integrity in the re-run |
|---|---|---|---|---|
| P4-EXP-01 substitution sweep | **0** | 0 | 0.98 (0.72–1.22) | 0 false |
| P4-EXP-03 indel + substitution | **0** | 0 | 0.98 (0.78–1.20) | 0 false |
| P4-EXP-04 RS boundary | **0** | 0 | 1.04 | 0 false, 0 ambiguous, 0 wrong-but-verified |
| P4-EXP-06 adversarial | **0** | 0 | 1.05 | 0 false, 0 ambiguous, 0 wrong-but-verified, 0 wrong address |
| P4-EXP-07 archives (126 decodes) | **0** | 0 | 1.02 (0.87–1.13); peak RSS 1.006 | 0 false SUCCESS |
| P4-EXP-08 coverage (60 decodes) | **0** | 0 | 1.04 (0.99–1.31); peak RSS 1.012 | 0 false SUCCESS |

Every outcome, count, hash, read-file SHA-256, recovery fraction, archive SUCCESS/PARTIAL/FAILURE, sweep value,
seed and configuration is identical, including the per-experiment configuration SHA-256. No float differs at all,
not even in the last bit. There are therefore no
class-B (float) and no class-D (behaviour) differences to explain. The only differences are runtime and memory
(class C), within ±5 % at the median.

Full test suite on the clean `c5b68d6` tree: **997 / 997 pass** (11 min 14 s).

The Phase 4 findings in `docs/V5_PHASE4_REPORT.md` therefore belong to the committed code. Its numbers are
unchanged. Their original provenance (`3633dfb`, `worktree_dirty = true`) stays in the published `results.json`
files, and this validation adds the clean-commit record beside them in
`experiments/v5/phase4/committed-c5b68d6/`.

## 2. Method

1. **State preserved.** The working checkout (`/root/VSnexustechnologies-v5`, branch `feature/vnx-dna-v5`, HEAD
   `c5b68d6`, no uncommitted changes) was not touched. A separate detached worktree of `c5b68d6` was created in a
   scratch directory.
2. **Imports pinned.** Every run used `PYTHONPATH=<clean tree>/src` (ahead of the editable install's `.pth`) and
   `PYTHONDONTWRITEBYTECODE=1`. The log records the imported module path (`<clean tree>/src/vnxdna/__init__.py`).
3. **Native rebuild.** The clean tree has no `.so` (build outputs are gitignored). The kernel was built from that
   tree's `align.c` with `python -m vnxdna.v5.native_alignment build`
   (`gcc -O3 -std=c11 -fPIC -shared -Wall -Wextra -Werror`) into `src/vnxdna/v5/native/libvnx_align.so`, and
   `VNXDNA_ALIGN_BACKEND=native` made every run fail rather than fall back silently. `status()` before the runs: backend
   `native`, library = the clean tree's file, ABI 2. `VNXDNA_NATIVE_LIB` was unset, so no library from another
   tree could be loaded.
4. **Clean between runs.** The driver checked `git status` (tracked files) before each experiment and aborted if
   anything was dirty. After each experiment it copied the results out and restored the tracked result files, so every
   experiment's own provenance was computed on a clean tree (`dirty before: 0` for all six; `dirty = 0` at the end).
5. **Compare.** `experiments/v5/phase4/committed_compare.py` walks every leaf of `results` and `config` and classifies
   each difference (A deterministic, B float ≤ 10⁻⁹ relative, C runtime/memory). It also sums the re-run's integrity
   counters. Output: `committed-c5b68d6/comparison.json`.

The published runs used the pip-built extension (`_vnx_align.cpython-312-x86_64-linux-gnu.so`, setuptools flags). The
re-runs used the in-place build. Phase 2 showed both are bit-exact with the reference aligner, and the identical
results here confirm it for these workloads.

## 3. Environment and source hashes

| item | value |
|---|---|
| commit under test | `c5b68d684c757ece2f9f45084e44c1313bc21cef`, clean |
| V4 control | `v4.0.0` → `a358ae8284066d0608f329b47a53e55018bbfa79` |
| tree hashes at `c5b68d6` | `src` `35a58d10…`, `tests` `ca1ef40c…`, `experiments` `e584a16f…` |
| `align.c` SHA-256 | `7005c242a2faf1dc91a1c6341fe6a2de652ae030c6b79efa3d7eb44ce8450fb2` |
| built library SHA-256 | `d69de95859f470fda34aca8cc47f6fcd28e8b8aae514ef176c0a0fc81edad5af` |
| compiler | gcc 13.3.0 (Ubuntu 13.3.0-6ubuntu2~24.04.1) |
| Python / NumPy | 3.12.3 / 2.5.3 |
| CPU | Intel Xeon Gold 6240, 8 CPUs |
| per-file source hashes | `committed-c5b68d6/source-sha256.txt` (all 96 files under `src/`), `source-manifest.txt` (git blob ids of `src`, `tests`, `experiments/v5/phase4`) |

## 4. Commands and seeds

Driver (reproducible from the log): for each experiment, from `experiments/v5/phase4/` in the clean tree:

| experiment | command | seeds | wall time |
|---|---|---|---|
| P4-EXP-01 | `exp_read_level.py subs --strands 4096` | strands 4101, channel 4102, graded Q 4109 | 1 min 16 s |
| P4-EXP-03 | `exp_read_level.py indel --strands 4096` | same | 9 min 47 s |
| P4-EXP-04 | `exp_frame_level.py exp04 --frames 500` | 4201 (+offset) | 2 min 29 s |
| P4-EXP-06 | `exp_frame_level.py exp06 --frames 500` | 4201 (+offset) | 14 s |
| P4-EXP-07 | `exp_archive.py exp07 --seeds 3` | input 4401, channel 44000 + 10·k | 8 min 29 s |
| P4-EXP-08 | `exp_archive.py exp08 --seeds 2` | input 4801, channel 44000 + 10·k | 18 min 40 s |
| suite | `python -m pytest -p no:cacheprovider -o addopts="" -q` | — | 11 min 14 s |

P4-EXP-02 and P4-EXP-05 (frame-level, same harness and code path as EXP-04) were not re-run; they were not on the
critical list, and EXP-04/06 cover the same code.

## 5. Runtime noise (class C)

Median runtime ratios are 0.98–1.05. The widest single values (0.72–1.31) come from sub-second decodes, where a
few milliseconds are a large fraction. While EXP-03 to EXP-08 ran, Gate B development ran niced on spare cores
(single-worker unit tests, and one 26-second three-worker test during EXP-08). This may have added a few percent to
some timings; it cannot affect any deterministic field. Memory ratios are 1.006 and 1.012.

## 6. Integrity gate

| requirement | result |
|---|---|
| 0 false acceptances | yes (EXP-01, 03, 04, 06) |
| 0 ambiguous verified frames | yes (EXP-04, 06) |
| 0 wrong-but-verified frames | yes (EXP-04, 06) |
| 0 false archive SUCCESS | yes (EXP-07: 126 decodes; EXP-08: 60 decodes) |
| hard decoding V4-compatible | yes (EXP-04: hard decoder exactly RS(70, 54); V4 suite green) |
| soft decoding opt-in, default off | yes (`DecodeOptions().soft_decoding == "off"`, tested) |
| existing V4/V5 tests green | 997 / 997 |
