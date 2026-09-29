# VNX-DNA V0.1 legacy evidence (archived)

This directory preserves the V0.1 / "R&D-1" research material exactly as committed. Nothing here
is used by the V0.2+ production code or tests. The complete V0.1 source tree is available at
git tag **`v0.1-baseline`** (commit `cf7d1f5`); see `docs/V0.1_BASELINE.md` for the forensic analysis.

| Path | Original location | Content |
|---|---|---|
| `v0_1_results/raw/E00*.json`, `v0_1_results/processed/E00*.csv` | `results/` | Output of the legacy experiment runner `vnxdna.experiments.runner` |
| `v0_1_scripts/` | `scripts/` | V0.1 experiment and acceptance scripts (import V0.1 modules and do not run against V0.2+) |
| `v0_1_datasets/` | `datasets/` | Sample inputs. `manifest.json` also lists `random.bin` and `video_like.bin`, which were never committed because `*.bin` is gitignored |
| `v0_1_paper/` | `paper/` | Draft manuscript with no results or references |
| `v0_1_src/`, `v0_1_tests/` | `src/vnxdna/`, `tests/` | Replaced V0.1 implementation and tests (moved when the V0.2 core landed) |

To re-run any of this, check out the tag: `git worktree add ../v01 v0.1-baseline`.

## Status of the V0.1 experiments E001–E008

Status labels: **INVALID** means the result does not measure what it claims. **NON-REPRODUCIBLE** means the
provenance does not identify the code that produced it. **SUPERSEDED** means it is replaced by a V0.2+ experiment.

Issues common to **all** eight result files:

* `environment.git_commit` is `49defd3`, the initial README-only commit. The runner code did not exist at
  that SHA, so the generating code is unidentified → **NON-REPRODUCIBLE**.
* Environment: Python 3.14.4 on Linux 6.18.44. That machine is not the one where the code was committed.
* **Every row has `ecc = none`.** The runner selects `DEFAULT_ECC = 'reed_solomon' if RSCodec is not None
  else 'none'`, so `reedsolo` must have been missing. Every "Reed–Solomon" case quietly ran without ECC.
* Each case is a single trial with one fixed seed (`20260908`) and uses the *legacy JSON archive* pipeline,
  not the dataset pipeline that the V0.1 CLI shipped.
* Inputs are the byte pattern `(j*31+i) % 256`. It compresses by roughly 100×, so sizes and timings
  describe a tiny compressed payload rather than the stated input size.

| ID | Claimed purpose | Additional finding | Status |
|---|---|---|---|
| E001 | Round trip at 1/10/100 KB | Round trips of highly compressible input, no ECC | NON-REPRODUCIBLE, SUPERSEDED by V0.2 round-trip tests and benchmarks |
| E002 | Substitution rates 0–5%, ECC vs no ECC | ECC arm never executed. Failures at ≥0.5% show only that no-ECC fails | INVALID (no ECC comparison), SUPERSEDED by `research/experiments/` substitution sweep |
| E003 | Insertion/deletion/mixed at 0.1% | `mixed` case failed with a harness bug (`unexpected keyword argument 'mixed_rate'`). The "successful" insertion case may have had no insertion | INVALID, SUPERSEDED by the indel study |
| E004 | Dropout 0–20% | Re-run at `v0.1-baseline`: the input yields 3 chunks and **0 chunks were dropped in every case**. "Success at 20% dropout" measured nothing | INVALID, SUPERSEDED by the dropout sweep |
| E005 | ECC vs no-ECC at 0.1% substitutions | Only the `none` arm ran (see above) | INVALID, SUPERSEDED |
| E006 | Chunk size 128–4096 | Runner passes `chunk_size=min(x, 239)`, so every case above 239 used 239. The variable was not varied | INVALID |
| E007 | 256-byte round trip | Single trivial round trip | NON-REPRODUCIBLE |
| E008 | 1 KB–1 MB scaling | Compressible pattern input, one run per size, no ECC | NON-REPRODUCIBLE, SUPERSEDED by `vnx-dna benchmark` suite |

These files are kept unchanged as historical evidence. They must not be cited as measurements of VNX-DNA behaviour.
