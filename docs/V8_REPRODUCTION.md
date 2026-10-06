# V8 reproduction

Everything V8 reports can be regenerated from pinned code, data, models, configuration and seeds. This file tells you how.

## Requirements

- Python 3.12 with the packages in `experiments/v8/V8_MANIFEST.json` (`software.dependencies`): numpy, edlib (the `fit`
  extra), cryptography, reedsolo, zstandard, and pytest for the tests. For example: `pip install -e ".[fit,dev]"`.
- A C compiler (gcc 13.3 was used). The native kernels are built per checkout. Without them every decode falls back to the
  much slower Python reference path; `reproduce.sh` builds them first.
- The public D13 files (Lopez et al. 2019), from the URLs in `experiments/v7/datasets/MANIFEST.json`, placed in
  `$VNX_PUBLIC_DATA/d13/` (default `/root/vnx-dna-lab/data/public/d13/`). Every file is SHA-256 checked before use. The
  data has no stated licence and is used internally only; no read or per-read value is committed.
- About 6 GB of disk for the local observation cache (`$VNX_V8_DERIVED`, never committed) and 8 cores (4 workers used).

## One command

```
experiments/v8/reproduce.sh all        # data → model → decoder → verify
```

Stages can be run alone: `data` (D13 segments, FIT tables, extraction), `model` (F1 fit, FIT pre-check under amendment A1,
F2 rule, DEV tables, A–D comparison), `decoder` (matrix, coverage envelope, oracle bounds, archive check), `verify`.

`verify` (`experiments/v8/verify_repro.py`) compares the regenerated outputs in the working tree with the committed ones
(`git show HEAD:...`). It compares content, not timings:
- the observation-cache hashes and counts per run, and the table content hashes;
- the extraction statistics;
- the F1 parameter hash (SHA-256 of the canonical stages), the pre-check decision and stability classes, the F2-rule ratio
  and the comparison failures;
- every decoder-matrix and oracle row (container and read-file SHA-256, outcome, taxonomy), the envelope classes and the
  archive check.

The canonical model SHA-256 includes provenance (fitting commit and time), so it is reported but not compared. It exits
non-zero on any difference.

## Pinned inputs

| what | where |
|---|---|
| code | the V8 commit (`git rev-parse HEAD`); V7 parent `0fd7c54d1b35178abb38520913797873d1cb22bc` |
| data | `experiments/v7/datasets/MANIFEST.json` (D13 file SHA-256), `docs/V8_DATA_PROVENANCE.md` |
| pre-registration | `docs/V8_PREREGISTRATION.md`, `docs/V8_PREREGISTRATION-A1.md` |
| seeds | `experiments/v8/V8_MANIFEST.json` (`seeds`): fit 20261010, comparison 20261011, pre-check 20261012, extraction 20261013, F2 rule 20261014, decoder 83000–83009, archive check 83100, scale 83200 |
| models | `experiments/v8/d13/models/d13-nanopore-f1.json` (parameter hash in `provenance.fitting.parameter_sha256`); baselines listed in the manifest |
| access | `experiments/v8/datasets/ACCESS_LEDGER.jsonl` (every FIT/DEV request; no held-out request was granted) |

## Not part of the one command

The scale benchmark (`experiments/v8/scale/bench.py`) depends on the host's speed. Its numbers are measurements of this
host, and its exact-recovery result is checked rather than its timings.
