# V7 A2: native read-clustering kernels (MEASURED speed; SIMULATED data)

`src/vnxdna/native/c/cluster.c` (binding `vnxdna.native.cluster`) accelerates the V7 item A reference in
`src/vnxdna/recovery/cluster/`: MinHash sketch, candidate pairs, banded edit distance (Myers bit-vector plus the exact
band program where the band matters), the verification loop with its union-find, and the forward-backward certain
calls of the consensus (baseline and AVX2, int16 and int32 lanes, selected at run time). The NumPy reference is the
specification; `VNXDNA_CLUSTER_BACKEND=auto|native|reference` selects the backend (`docs/NATIVE_KERNELS.md`).

| file | what |
|---|---|
| `bench_a1smoke.py` | A1-SMOKE decode wall time, reference vs native, with outcome identity check and hyperfine |
| `stress_fuzz.py` | differential fuzz of every kernel (and every forward-backward variant) against the reference |
| `sanitizers.sh` | warning report; gcc and clang ASan+UBSan, clang UBSan trap; tests + fuzz under each; ASan canary |
| `make_golden.py` | writes `tests/v7/native_cluster_golden.json` from the reference |
| `results/` | `a1smoke_bench.json` (+ log, hyperfine JSON), `sanitizers.txt` |

## A1-SMOKE decode wall time (MEASURED, 2026-10-05)

Inputs: exactly the A1-SMOKE trials (`experiments/v7/a1-smoke`: A-DIAG archive, unfitted V6 nanopore-like and
deletion-heavy coverage-10 models, seeds 82020-82024; every re-simulated read file has the SHA-256 recorded in its
`trials.jsonl`). Decoder: the smoke's `fallback` arm, `DecodeOptions(stage_counters=True, read_clustering="fallback",
workers=1)`; one decode per process; only `VNXDNA_CLUSTER_BACKEND` differs (align, reads and RS kernels native in
both). Code `be33d30` (clean tree), in-place library built `--strict` from the committed source; Xeon Gold 6240, 8
logical CPUs, Python 3.12.3, NumPy 2.5.3, AVX2 forward-backward level; shared development host (load average 2.3 before,
3.1-5.7 after). Command: `PYTHONPATH=src python benchmarks/v7/native_cluster/bench_a1smoke.py run --hyperfine-runs 3`.

In-process decode seconds (one decode per backend per trial; median of 5 trials per cell):

| cell | outcome (both backends) | reference s | native s | speed-up (median) | per-trial range |
|---|---|---|---|---|---|
| nanopore-like/cov10 | EXPLICIT FAILURE 5/5 | 54.58 | 2.80 | 19.5x | 17.3x-20.8x |
| deletion-heavy/cov10 | EXACT 5/5 | 35.77 | 2.59 | 13.8x | 13.6x-14.4x |

hyperfine (whole process including interpreter start, 3 runs each, seed 82020):

| cell | reference mean ± sd | native mean ± sd | speed-up |
|---|---|---|---|
| nanopore-like/cov10 | 52.32 ± 1.55 s | 3.21 ± 0.21 s | 16.3x |
| deletion-heavy/cov10 | 35.86 ± 0.05 s | 2.73 ± 0.02 s | 13.2x |

Outcomes are identical in all 10 trials: status, protocol §6 outcome, container SHA-256 and the whole decode report
(timings and the `native_backends` provenance excluded) are equal on both backends (`all_identical: true`). The 6.0
path of the same decode took about 0.4 s in A1-SMOKE, so the clustering stage still accounts for most of the
remaining 2.5-3.3 s; how that time splits between the kernels and the Python around them was not profiled here.
