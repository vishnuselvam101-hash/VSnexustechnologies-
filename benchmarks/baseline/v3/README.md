# VNX-DNA V3 baseline benchmark

> **Software and simulation only.** No DNA was synthesised, stored or sequenced. Strands are generated
> sequences; the noisy case uses VNX-DNA's seeded sequencing simulator. Nucleotide counts are information-
> theoretic counts for software strands, not physical densities.

Baseline: `vnx-dna 3.0.0 (writes archive format 5 / container v2 / frame 5; reads formats 5 and 4 and legacy V0.1)` at commit `9b5123ceb86805b9d0eabd7a5f74592c3d97eb58` (v3.0.0), measured 2026-10-03T01:51:46Z on Intel(R) Xeon(R) Gold 6240 CPU @ 2.60GHz (8 logical CPUs, 31.34 GiB RAM), Ubuntu 24.04.5 LTS, Python 3.12.3. Full environment: [environment.json](environment.json); all numbers: [results.json](results.json).

## Reproduce

```bash
git checkout v3.0.0            # or any commit whose src/ equals v3.0.0
python -m venv .venv && . .venv/bin/activate && pip install -e '.[dev]'
python benchmarks/baseline/v3/run_v3_baseline.py --repeats 3
```

The script generates every input deterministically (fixed seeds) under `.bench-tmp/` (git-ignored), runs the
installed CLI and deletes the generated data at the end unless `--keep-data` is given.

## Method

* Clean DNA path per input, profile `balanced` (defaults: 1 MiB chunks, zstd level 3 kept only if smaller, Cauchy RS 64+16, 2bit mapping, P = 40 B, inner RS r = 8, 252-nt strands), no encryption, default workers:
  `vnx-dna store` → `vnx-dna encode` (FASTA) → `vnx-dna recover` (decode + restore from the FASTA alone).
* Each command runs under `/usr/bin/time -v`. Times are wall-clock (median of 3 repeats). Peak RSS is `/usr/bin/time`'s *Maximum resident set size*, i.e. the largest single process (main process or one worker), maximum over repeats — **not** the sum over the process tree used in docs/BENCHMARKS.md, so the two are not directly comparable.
* Strands and nucleotides come from the `encode` report and are cross-checked by counting the FASTA records independently (`independent_fasta_count` in results.json).
* **ECC overhead** = parity strands / all strands, where parity strands = ECC groups × M (from the encode report, i.e. from the manifest's `erasure_code.stripe_count` × `parity_shards`). Metadata strands (Cauchy 8+8 copies of manifest and indexes) are reported separately; `(parity + metadata) / total` is also in results.json. Because the last group of every chunk is shortened (padding-only data shards are not emitted) while it still has M parity strands, small inputs show a higher ratio than the asymptotic M/(K+M) = 20 %.
* PASS = every repeat exited 0 and SHA-256(recovered) = SHA-256(input).

## Results (clean DNA path)

| input | bytes | stored bytes | strands | nucleotides | nt / input byte | ECC overhead (parity / strands) | encode s (store + encode) | decode s (recover) | peak RSS MiB store / encode / recover | SHA-256 |
|---|---|---|---|---|---|---|---|---|---|---|
| tiny | 100 | 87 | 115 | 28,980 | 289.800 | 13.9 % | 0.40 + 0.46 = 0.86 | 0.49 | 57 / 57 / 57 | PASS |
| small | 10,240 | 4,540 | 242 | 60,984 | 5.955 | 13.2 % | 0.43 + 0.48 = 0.91 | 0.48 | 57 / 57 / 57 | PASS |
| medium | 1,048,576 | 469,646 | 14,782 | 3,725,064 | 3.553 | 19.9 % | 0.44 + 0.63 = 1.08 | 0.63 | 57 / 65 / 76 | PASS |
| large | 10,485,760 | 4,650,068 | 145,537 | 36,675,324 | 3.498 | 20.0 % | 0.49 + 0.96 = 1.46 | 1.17 | 68 / 113 / 137 | PASS |
| repetitive | 1,048,576 | 128 | 116 | 29,232 | 0.028 | 13.8 % | 0.42 + 0.48 = 0.89 | 0.52 | 57 / 57 / 58 | PASS |
| random | 1,048,576 | 1,048,576 | 32,871 | 8,283,492 | 7.900 | 20.0 % | 0.49 + 0.83 = 1.30 | 0.77 | 58 / 82 / 102 | PASS |
| text | 1,048,576 | 352,021 | 11,105 | 2,798,460 | 2.669 | 19.9 % | 0.43 + 0.64 = 1.07 | 0.60 | 57 / 63 / 72 | PASS |
| binary | 1,048,576 | 594,043 | 18,676 | 4,706,352 | 4.488 | 20.0 % | 0.43 + 0.65 = 1.08 | 0.68 | 57 / 67 / 82 | PASS |

Process wall time includes a fixed interpreter + import start-up cost: `vnx-dna version` alone takes 0.37 s (median of 5) on this machine. For inputs up to ~1 MiB this start-up dominates. The time each command reports for its own work (`elapsed_s` in the CLI report, first repeat) is:

| input | store s | encode s | recover s | chunks (compressed) | ECC groups | parity strands | metadata strands |
|---|---|---|---|---|---|---|---|
| tiny | 0.013 | 0.049 | 0.053 | 1 (1) | 1 | 16 | 96 |
| small | 0.014 | 0.081 | 0.057 | 1 (1) | 2 | 32 | 96 |
| medium | 0.027 | 0.211 | 0.183 | 1 (1) | 184 | 2,944 | 96 |
| large | 0.080 | 0.548 | 0.726 | 10 (8) | 1,821 | 29,136 | 144 |
| repetitive | 0.020 | 0.050 | 0.076 | 1 (1) | 1 | 16 | 96 |
| random | 0.030 | 0.441 | 0.316 | 1 (0) | 410 | 6,560 | 96 |
| text | 0.031 | 0.165 | 0.158 | 1 (1) | 138 | 2,208 | 96 |
| binary | 0.033 | 0.242 | 0.227 | 1 (1) | 233 | 3,728 | 96 |

Inputs: **tiny** = 100 B English-like text (seed 1); **small** = 10 KiB English-like text (seed 2); **medium** = 1 MiB `vnx-dna benchmark generate --pattern mixed --seed 42`; **large** = 10 MiB `vnx-dna benchmark generate --pattern mixed --seed 42`; **repetitive** = 1 MiB of a repeated 24-byte ASCII pattern; **random** = 1 MiB numpy default_rng(3) uniform bytes; **text** = 1 MiB English-like text (seed 4); **binary** = 1 MiB packed little-endian 20-byte records (seed 5).

## Noisy pipeline (one case)

`vnx-dna pipeline` on the medium input (1 MiB mixed), coverage 10 (Poisson), substitution 0.001, insertion 0.0001, deletion 0.0001, dropout 0.02, seed 42, cluster + consensus (default at coverage > 1).

* result: **PASS** (exit 0), wall 20.22 s, CPU 24.1 s, peak RSS (largest process) 560 MiB
* SHA-256 input = recovered: True
* per-stage wall time inside the pipeline process (report `timings_s`): store 0.03 s, encode 0.19 s, sequence 2.58 s, cluster 9.01 s, consensus 7.62 s, decode 0.34 s, restore 0.01 s, verify 0.02 s
* channel: 14,782 designed strands → 145,350 reads (283 strands with zero reads); observed per-base rates: deletions 1.02e-04, insertions 9.82e-05, substitutions 1.00e-03
* clustering: 14,499 clusters; reads verified 138,201, tentative 6,889, orphan 260, reassigned 1,315
* consensus: 14,498 CRC-valid of 14,499
* outer decoding: 283 shards erased, 128 of 184 ECC groups needed the outer code, worst group lost 7 strands (guarantee: 16)

The full pipeline report is stored under `noisy_pipeline.report` in results.json.
