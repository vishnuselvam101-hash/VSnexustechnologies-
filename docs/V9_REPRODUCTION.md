# V9 reproduction

Every V9 result is regenerated from pinned code, models, configuration and seeds (`experiments/v9/V9_MANIFEST.json`).
All decoder results are SIMULATED. No DNA was synthesised, stored or sequenced. The channel-model fit is
PUBLIC-DATA-DERIVED (D13 FIT split only).

## Requirements

- The V8 requirements (`docs/V8_REPRODUCTION.md`): Python 3.12 with the V8 manifest's packages, and a C compiler (gcc 13.3
  was used; clang 18 for the sanitizer and libFuzzer builds). `reproduce.sh` builds the native kernels first.
- `channel` only: the public D13 FIT files at `$VNX_PUBLIC_DATA/d13/`, SHA-256 checked. Every access is logged in
  `experiments/v8/datasets/ACCESS_LEDGER.jsonl`. The held-out split is never read.
- 4 cores; one heavy job at a time. Every timing row records the host load (`load1`).

## Spot check (about 1 h)

```
experiments/v9/reproduce.sh spot
```

This mode re-decodes a fixed subset into `repro-out-v9/` (or `$VNX_V9_REPRO_OUT`). It then compares each row with the
committed row that has the same identity: candidate, level, mode, channel, coverage, profile, size and seed. The subset
was chosen before the run:

- oracle-gap EVAL, seeds 91000–91001, for v8 and the frozen winner, at the production level, in all six primary cells
  (24 decodes);
- adaptive-coverage EVAL, seed 91000, both profiles (2 trajectories);
- noisy decodes at 20 KB, seeds 93000–93002 (3 decodes).

## Full regeneration (several days on 4 cores)

```
experiments/v9/reproduce.sh all        # channel → decoder → coverage → scale → verify
```

Each stage can also be run alone:

- `channel`: G1 fit and FIT pre-check.
- `decoder`: the 600-case oracle-gap EVAL, eligibility and selection.
- `coverage`: DEV trajectories, tune, EVAL and the envelope.
- `scale`: noisy decodes and the archive-size benchmark. At 1 GiB this needs up to 1 h per stage.
- `verify`: compares the regenerated files with `git show HEAD:...`.

The stages rewrite their results files in the working tree, so run them on a clean checkout.

## What is compared

`experiments/v9/verify_repro.py` compares deterministic content only. That includes:

- container and read-pool SHA-256;
- outcomes, terminal stages and frame counts;
- false-success and false-termination flags;
- per-strand and per-row success;
- stopping batches and margins;
- selection statistics.

It does not compare timings, load averages, peak RSS, timestamps or commit fields. A regenerated row with no committed
counterpart counts as a difference. The script exits 1 on any difference.

## Native kernels, sanitizers and fuzzing

```
PY=.venv/bin/python bash tools/sanitizers.sh            # align, reads, rs, cluster (incl. the V9 polish kernel)
clang -g -O1 -fsanitize=fuzzer,address,undefined -fno-sanitize-recover=undefined \
  benchmarks/v7/native_cluster/edit_costs_fuzz.c src/vnxdna/native/c/cluster.c -lm -o edit_costs_fuzz
./edit_costs_fuzz -max_total_time=600 -max_len=8192 corpus/
```

`experiments/v9/consensus/native_equivalence.py` repeats the native/reference byte-identity check on full decodes.
