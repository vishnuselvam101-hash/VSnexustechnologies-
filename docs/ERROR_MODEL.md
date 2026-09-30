# Error model and recovery guarantees (V3)

> **SOFTWARE SIMULATION.** The error channel is a seeded stress generator for the decoder, not a model fitted to a
> synthesis chemistry or a sequencing platform. Every rate below is a stress parameter. Every recovery figure
> describes *this decoder under this simulated channel*, never physical DNA storage. No physical experiment has been
> performed.

## Channel

```
original DNA (strand file) ──▶ channel simulator (vnxdna.v2.sequencing, seeded PCG64) ──▶ corrupted reads
        ──▶ [cluster → consensus] ──▶ decoder (vnxdna.v2.decoder) ──▶ recovered bytes ──▶ SHA-256 vs input
```

| error type | parameter | model | modelled since |
|---|---|---|---|
| substitution | `--substitution-rate`, `--synthesis-substitution-rate` | per base, independently per read | V2 |
| insertion | `--insertion-rate`, `--synthesis-insertion-rate` | a random base after the position | V2 |
| deletion | `--deletion-rate`, `--synthesis-deletion-rate` | the base is lost | V2 |
| dropout | `--dropout-rate` | a strand species is lost entirely | V2 |
| uneven abundance | `--coverage-model lognormal --abundance-sigma` | log-normal read counts (PCR-like skew) | V2 |
| duplication | `--duplication-rate` | an identical extra read, errors included | V2 |
| reordering | `--shuffle` (default) | uniform permutation of all reads, out of core | V2 |
| truncation | `--truncation-rate` | the read keeps a 50–99 % prefix | V2 |
| unreadable bases | `--n-rate` | a base becomes `N` | V2 |
| reverse complement | `--reverse-complement-rate` | the read is reverse-complemented | V2 |
| junk / contamination | `--invalid-read-rate`, `--contamination-rate` | random extra reads | V2 |
| **burst** | `--burst-rate`, `--burst-length`, `--burst-kind` | one contiguous run of Geometric(mean) bases per read, substituted / deleted / inserted / mixed | **V3** |
| mixed | any combination of the above | independent processes applied in the order given in [CHANNEL_MODEL.md](CHANNEL_MODEL.md) | V2 (+ bursts in V3) |

**Not modelled:** position- or context-dependent error rates (homopolymer slippage), GC-dependent dropout, strand
breakage into fragments, chimeras, primer and adapter sequences, cross-contamination between archives beyond random
foreign reads, and long-term decay. See [LIMITATIONS.md](LIMITATIONS.md).

**Determinism.** The same input, channel configuration and seed give byte-identical reads, whatever the worker count
(tested). Sweeps and experiments use seeds `seed, seed + 1, …` per trial, so their statistics do not depend on the
number of workers either (tested).

## What the decoder corrects, and the guarantee for each type

"Guaranteed" means a proven property of the code that is tested at its boundary. "Measured" means a recovery rate
under the simulated channel, with confidence intervals, and never a guarantee.

| error type | mechanism | guarantee | evidence |
|---|---|---|---|
| dropout, zero coverage, rejected reads | outer Cauchy RS | **any M strands per ECC group** (balanced: 16 of 80) | `tests/v2/test_dna_v2.py` (M recovers, M + 1 fails cleanly) |
| substitutions (per read) | inner RS | **`2e + f ≤ r`** byte errors e and erasures f per strand (balanced r = 8) | `tests/v3/test_ecc_decoder_v3.py` |
| `N`, IUPAC symbols, low-quality bases | erasures for the inner RS | as above (each costs 1, an error costs 2) | `tests/v3/test_ecc_decoder_v3.py` (D5), `tests/v2/test_dna_v2.py` |
| substitution bursts | inner RS (a burst of L nt touches ⌈L/4⌉ + 1 bytes at most with the 2bit mapping) | covered by `2e ≤ r` when the touched bytes fit | measured below |
| one indel per read (coverage 1) | single-read realignment (`--experimental-indel-repair`) | **one indel plus ⌊(r − 1)/2⌋ byte errors** | `tests/v3/test_ecc_decoder_v3.py`, [SYNCHRONIZATION.md](SYNCHRONIZATION.md) |
| 2–3 same-direction indels per read (coverage 1) | realignment with `--max-indel 2..3` | exhaustive only while the hypotheses fit the budget (API `max_indel_candidates`, default 4,096 per orientation) | measured in [SYNCHRONIZATION.md](SYNCHRONIZATION.md#v3-measurements) |
| one lost/extra burst of L nt per read (coverage 1) | **burst resynchronisation** (`--burst-repair N`, V3) | **one burst with ⌈(L + b − 1)/b⌉ + 2e ≤ r**, L ≤ N (b = 4 nt per byte in 2bit: L ≤ 29 at r = 8 without other errors; L = 29 recovered 30/30 and L = 30 0/30 in a check) | `tests/v3/test_v3_features.py` |
| indels and bursts at coverage > 1 | clustering + consensus alignment | none (a statistical method) | measured below and in [CHANNEL_MODEL.md](CHANNEL_MODEL.md) |
| duplicates, reordering | duplicate vote; order-independent assembly | always handled | `tests/v2/test_dna_v2.py` |
| truncation, junk, contamination, foreign archives | rejected (length, CRC, tag) | never accepted as data | `tests/v2/test_dna_v2.py`, fuzz tests |
| anything beyond the above | detected | **never wrong output**: the decoder refuses (exit 5 or 3), writes nothing | 0 undetected corruptions in every sweep and experiment |

VNX-DNA does **not** claim to correct: reads whose net length is unchanged by indels (+1 −1) at coverage 1, several
bursts in one read at coverage 1, errors shared by every read of a strand, or more than M unusable strands in any
ECC group.

## Error sweeps: `vnx-dna simulate-errors`

```bash
vnx-dna simulate-errors input.bin -o sweep/ --coverage 1 --trials 20 --seed 1000 \
    --sweep substitution=0,0.004,0.008 --sweep burst-deletion=0.2,0.5 --sweep "mixed=substitution:0.002+deletion:0.0005" \
    --experimental-indel-repair --max-indel 2 --burst-repair 24
```

The input is stored and encoded once. Each point changes one error parameter (or a `mixed` combination) on top of
the base channel. Each trial runs the full decoder, and the outcome is classified by comparing SHA-256 with the
input: exact, detected failure, undetected corruption, or internal error. The command writes `sweep.json` (every
trial), `sweep.csv` and `sweep.md`, and exits 70 if any trial produced undetected corruption or an internal error.

### V3 sweep at coverage 1 (every read of every strand once, shuffled)

"Repairs off" is the decoder with the V2 defaults. "Repairs on" enables single-read indel repair (`--max-indel 2`) and
burst resynchronisation (`--burst-repair 24`).

<!-- BEGIN GENERATED: v3-sweep-cov1 -->
*(generated by `research/v3/render_v3_tables.py` from `research/results/v3/sweep-cov1-*.json`)*

vnx-dna 3.0.0, profile `balanced`, 100,000 B input (1,793 strands of 252 nt); base channel coverage 1.0 (fixed), reads shuffled, seeds 1000…1019; consensus False; 20 trials per point. Undetected corruption over all points: **0**; internal errors: **0**.

| point | exact (repairs off) | exact (repairs on) | 95% CI (on) | detected failures (on) | undetected (off / on) |
|---|---|---|---|---|---|
| substitution:0 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| substitution:0.002 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| substitution:0.004 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| substitution:0.006 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| substitution:0.008 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| insertion:0.0005 | 16/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| insertion:0.001 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| insertion:0.002 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| deletion:0.0005 | 16/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| deletion:0.001 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| deletion:0.002 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| dropout:0.05 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| dropout:0.1 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| dropout:0.15 | 3/20 | 3/20 | [0.052, 0.360] | 17 | 0 / 0 |
| duplication:0.5 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| n:0.005 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| n:0.01 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| truncation:0.02 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| truncation:0.05 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| reverse-complement:0.5 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| burst-substitution:0.2 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| burst-substitution:0.5 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| burst-deletion:0.1 | 20/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| burst-deletion:0.2 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| burst-deletion:0.5 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| burst-insertion:0.2 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| burst-insertion:0.5 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| burst-mixed:0.3 | 0/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
| mixed:substitution:0.002+deletion:0.0005+dropout:0.05 | 2/20 | 20/20 | [0.839, 1.000] | 0 | 0 / 0 |
<!-- END GENERATED: v3-sweep-cov1 -->

### V3 sweep at coverage 5 (Poisson) with clustering and consensus

<!-- BEGIN GENERATED: v3-sweep-cov5 -->
*(generated by `research/v3/render_v3_tables.py` from `research/results/v3/sweep-cov5-consensus.json`)*

vnx-dna 3.0.0, profile `balanced`, 100,000 B input (1,793 strands of 252 nt); base channel coverage 5.0 (poisson), reads shuffled, seeds 2000…2009; consensus True; 10 trials per point. Undetected corruption over all points: **0**; internal errors: **0**.

| point | exact | 95% CI | detected failures | undetected | internal |
|---|---|---|---|---|---|
| substitution:0.005 | 10/10 | [0.723, 1.000] | 0 | 0 | 0 |
| substitution:0.01 | 10/10 | [0.723, 1.000] | 0 | 0 | 0 |
| substitution:0.02 | 10/10 | [0.723, 1.000] | 0 | 0 | 0 |
| insertion:0.002 | 10/10 | [0.723, 1.000] | 0 | 0 | 0 |
| insertion:0.005 | 8/10 | [0.490, 0.943] | 2 | 0 | 0 |
| insertion:0.01 | 0/10 | [0.000, 0.278] | 10 | 0 | 0 |
| deletion:0.002 | 10/10 | [0.723, 1.000] | 0 | 0 | 0 |
| deletion:0.005 | 9/10 | [0.596, 0.982] | 1 | 0 | 0 |
| deletion:0.01 | 0/10 | [0.000, 0.278] | 10 | 0 | 0 |
| dropout:0.1 | 9/10 | [0.596, 0.982] | 1 | 0 | 0 |
| dropout:0.2 | 0/10 | [0.000, 0.278] | 10 | 0 | 0 |
| burst-deletion:0.2 | 10/10 | [0.723, 1.000] | 0 | 0 | 0 |
| burst-deletion:0.5 | 10/10 | [0.723, 1.000] | 0 | 0 | 0 |
| burst-mixed:0.5 | 10/10 | [0.723, 1.000] | 0 | 0 | 0 |
| mixed:substitution:0.005+insertion:0.002+deletion:0.002+dropout:0.05 | 2/10 | [0.057, 0.510] | 8 | 0 | 0 |
<!-- END GENERATED: v3-sweep-cov5 -->

How to read these tables: a Wilson 95 % interval of [0.839, 1.000] from 20/20 trials means the true success
probability *under this simulated channel* is plausibly above about 84 %. It is not a proof of 100 %. Points where
all trials fail show where the configuration's redundancy is exhausted; the decoder refused in every such trial.
