# Channel models

This page has two parts. **V4 channel** (`vnx channel simulate`, `vnxdna.v4.channel`) comes first. The **V3 channel**
(`vnx-dna sequence`, format 5) follows unchanged below the divider.

> **SIMULATED.** Both channels are configurable, seeded stress generators for the decoders. Neither is fitted to a
> synthesis chemistry or a sequencing platform. Recovery measured with them describes this software under the stated
> parameters, not physical DNA storage. No physical experiment has been performed.

## V4 channel

Each strand goes through: dropout → coverage (number of reads) → per-read errors → optional N calls, quality scores,
reverse complement → optional duplication → output (FASTQ or FASTA, optionally shuffled in windows).

| parameter | default | model |
|---|---|---|
| `dropout_rate` | 0 | strand lost entirely (Bernoulli per strand) |
| `coverage`, `coverage_model` | 1, `fixed` | reads per surviving strand: fixed integer, Poisson(mean), or negative binomial (`coverage_dispersion` k; Gamma–Poisson, smaller k = more uneven) |
| `gc_bias_strength`, `gc_bias_optimum` | 0, 0.5 | coverage weight `exp(−s·((gc − optimum)/0.1)²)` per strand (strands far from the optimum get fewer reads) |
| `substitution_rate` | 0 | base replaced by one of the other three uniformly (per base) |
| `insertion_rate` | 0 | random base inserted before a position (per base) |
| `deletion_rate` | 0 | base removed (per base) |
| `homopolymer_min_run`, `homopolymer_indel_multiplier`, `homopolymer_substitution_multiplier` | 3, 1, 1 | rates multiplied at positions inside runs ≥ min_run |
| `burst_rate`, `burst_max_len` | 0, 0 | per read, delete a contiguous run of 1…max bases |
| `n_rate` | 0 | base reported as N |
| `reverse_complement_rate` | 0 | read reported on the opposite strand |
| `duplication_rate` | 0 | read emitted twice (identical copy) |
| `quality_correct`, `quality_error`, `quality_informative` | 35, 12, 0 | Phred scores; with probability `quality_informative` an erroneous base gets `quality_error` |
| `shuffle_window` | 0 | seeded shuffle of reads within windows (0 = strand order) |
| `seed` | 12345 | see determinism |

Example (`channel.json`):

```json
{"substitution_rate": 0.001, "insertion_rate": 0.001, "deletion_rate": 0.001, "dropout_rate": 0.01,
 "coverage": 30, "coverage_model": "poisson", "duplication_rate": 0.05, "seed": 12345}
```

`vnx channel simulate strands.fasta reads.fastq --config channel.json [--workers N]`

**Determinism.** Strands are processed in fixed batches of 1024. Batch b draws all randomness from
`numpy.random.default_rng([seed, b])`, so the output is a pure function of (strands, configuration, seed),
independent of the worker count. This is tested byte for byte with 1 and 3 workers. A different seed gives different
reads (also tested).

**Validation.** Rates must be probabilities. The sum of substitution, insertion and deletion rates must be ≤ 0.5.
Unknown keys and wrong types are errors (`VNXConfigurationError`, exit 7). Measured event counts match the configured
rates within sampling error (`test_channel_rates_are_respected`).

**Not modelled** (NOT VALIDATED against any platform): position-dependent error profiles, synthesis truncation,
chimeras, primer/adapter sequences, PCR amplification dynamics, strand breakage, context-dependent substitution
matrices, spatial/temporal decay.

Recovery curves under this channel: EXP-0001 (substitution), EXP-0002 (insertion), EXP-0003 (deletion), EXP-0004
(dropout), EXP-0005 (coverage), EXP-0006 (mixed), EXP-0013 (homopolymer, GC bias, uneven coverage). They are
summarised in [V4_COMPLETION_REPORT.md](V4_COMPLETION_REPORT.md).

---

# V3 channel model (format 5, `vnx-dna sequence`) — unchanged

> **SOFTWARE SIMULATION.** The channel below is a configurable, reproducible stress generator for the decoder. It is
> not fitted to any synthesis chemistry or sequencing platform. Recovery rates measured with it describe *this
> software under this model*. They are not evidence about physical DNA storage, and no physical experiment has been
> performed.

## The simulator (`vnx-dna sequence`, `vnx-dna simulate`; `vnxdna.v2.sequencing`)

The V3 error model, the error-sweep command and the V3 sweep results are in [ERROR_MODEL.md](ERROR_MODEL.md). This
page keeps the V2 measurements, which were made with the V2 decoder.

Per designed strand (one molecule species), in this order:

| stage | parameter(s) | model |
|---|---|---|
| dropout | `--dropout-rate` | the species is lost (no molecule survives) |
| abundance / coverage | `--coverage`, `--coverage-model fixed\|poisson\|lognormal`, `--abundance-sigma` | reads per species: exactly `coverage`; Poisson(`coverage`); or Poisson(`coverage × w`) with `w ~ LogNormal(−σ²/2, σ)` (mean 1): uneven abundance, as after PCR |
| synthesis errors | `--synthesis-{substitution,insertion,deletion}-rate` | per base, independently for each read's molecule; not reflected in quality |
| sequencing errors | `--{substitution,insertion,deletion}-rate` | per base per read: substitution → deletion → insertion after the base |
| bursts (V3) | `--burst-rate`, `--burst-length`, `--burst-kind substitution\|deletion\|insertion\|mixed` | with probability `burst-rate` a read carries one contiguous run of Geometric(mean `burst-length`) bases, starting at a uniform position, that are substituted, deleted, or preceded by inserted random bases; not reflected in quality |
| truncation | `--truncation-rate` | the read keeps a uniform 50–99 % prefix |
| unreadable calls | `--n-rate` | a base becomes `N` (quality 2) |
| orientation | `--reverse-complement-rate` | the read is reverse-complemented |
| duplication | `--duplication-rate` | the read gets an identical copy, errors included (PCR/optical duplicate) |
| invalid reads | `--invalid-read-rate` | extra junk reads (20–400 random bases including `N`) |
| contamination | `--contamination-rate` | extra foreign reads (random A/C/G/T of strand length, from no archive) |
| order | `--shuffle` (default) | a uniform random permutation of all reads, computed out of core (random bucket files, each permuted in memory) |
| quality | `--quality-model informative\|flat`, `--quality-informativeness` | informative: correct bases Q30–40, sequencing-error bases Q2–20 with the given probability |

**Coverage levels** 1×, 2×, 5×, 10×, 20×, 50× and any value in (0, 1000] are supported (the `fixed` model needs an
integer). Zero-error mode
(`--coverage-model fixed`, all rates 0, `--no-shuffle`) yields exact copies, which is the only case where strands
are duplicated perfectly (tested).

**Reproducibility.** All randomness comes from `numpy.random.Generator(PCG64)` seeded with `(seed, batch index)`. Events
are drawn per batch of strands: 8,192 strands, or fewer when the expected read bases of a batch would exceed
8,192 × 4,096 (V3; memory stays bounded at high coverage, and ordinary channels, e.g. 276-nt strands up to
coverage 14.8, keep the V2 batches and therefore the V2 output bytes), and per-base events as Bernoulli processes via geometric gaps (exactly the same
distribution as one uniform draw per base, at a cost proportional to the number of events). The same input,
configuration and seed give a byte-identical output file (tested). Since V3 the number of shuffle buckets is derived
from the pool's bases, not from the input file's byte size, so the same strands as FASTA or as VXS give the same
reads (V2 differed); this changes shuffled output bytes relative to V2 for large pools and for VXS input. The report counts **events that happened**:
strands, dropped strands, strands with zero reads, reads, reads per strand, the coverage distribution (histogram),
duplicates, truncations, reverse complements, junk and foreign reads, every error class, and observed rates per
designed base.

**Output formats.** FASTQ (with qualities), FASTA, or VXS. VXS only for channels that keep every read at the strand
length with A/C/G/T only: no indels, truncation, `N`, junk or contamination.

`vnx-dna simulate` is the same engine with storage-channel defaults (coverage 1, fixed, flat quality): a damaged
molecule pool rather than a read set.

## Error classes and the layer that handles each

| class | handled by | beyond capacity |
|---|---|---|
| strand dropout, zero coverage | outer erasure code (any M per group) | `INSUFFICIENT_REDUNDANCY` |
| substitutions | consensus vote (coverage > 1), inner RS (≤ r/2 byte errors, or r erasures) | strand → erasure |
| insertions / deletions | consensus alignment (coverage > 1); single-read realignment (opt-in) | strand → erasure |
| bursts (V3) | substitution bursts: inner RS; lost/extra runs: consensus (coverage > 1) or single-read burst resynchronisation (`--burst-repair`, V3) | strand → erasure |
| `N` / low-quality bases | erasures for the inner RS | strand → erasure |
| duplicates | duplicate resolution (identical copies merge) | – |
| conflicting copies | strict majority; tie → erasure | – |
| truncated, junk, foreign reads | rejected: wrong length or CRC failure; foreign tag → counted and ignored | – |
| mixed archives in one pool | V3: the only archive whose metadata decodes is used; otherwise refused until `--archive-tag` picks one (V2 always refused) | – |

## Measured recovery

All tables below were produced by `research/v2/run_v2_research.py` through the experiment engine
([EXPERIMENTS.md](EXPERIMENTS.md)) and rendered by `research/v2/render_v2_tables.py`. "Exact recovery" means the
recovered file's SHA-256 equals the input's, checked outside the decoder. "Failed (detected)" means the decoder
refused (no output). "Undetected corruption" would be wrong output presented as success, and must be 0.

### Coverage

<!-- BEGIN GENERATED: coverage -->
*(generated by `research/v2/render_v2_tables.py` from `research/results/v2/coverage.json`)*

20,000 B mixed (seed 7), profile balanced (8 KiB chunks), 40 trials per point; channel: substitution 0.001, insertion 0.0001, deletion 0.0001, dropout 0.02, coverage model poisson.

| point | read processing | exact recovery | 95 % Wilson CI | failed (detected) | undetected corruption | mean reads/strand | mean groups repaired | worst group erasures |
|---|---|---|---|---|---|---|---|---|
| coverage 1x | direct | 0/40 | [0.0000, 0.0876] | 40 | 0 | 0.98 | n/a (no successful trial) | n/a |
| coverage 2x | cluster + consensus | 24/40 | [0.4460, 0.7365] | 16 | 0 | 1.97 | 6.0 | 16 |
| coverage 2x | direct | 23/40 | [0.4220, 0.7149] | 17 | 0 | 1.97 | 6.0 | 16 |
| coverage 5x | cluster + consensus | 40/40 | [0.9124, 1.0000] | 0 | 0 | 4.93 | 4.4 | 6 |
| coverage 5x | direct | 40/40 | [0.9124, 1.0000] | 0 | 0 | 4.93 | 4.4 | 6 |
| coverage 10x | cluster + consensus | 40/40 | [0.9124, 1.0000] | 0 | 0 | 9.77 | 3.8 | 6 |
| coverage 10x | direct | 40/40 | [0.9124, 1.0000] | 0 | 0 | 9.77 | 3.8 | 6 |
| coverage 20x | cluster + consensus | 40/40 | [0.9124, 1.0000] | 0 | 0 | 19.57 | 3.9 | 6 |
| coverage 20x | direct | 40/40 | [0.9124, 1.0000] | 0 | 0 | 19.57 | 3.9 | 6 |
| coverage 50x | cluster + consensus | 40/40 | [0.9124, 1.0000] | 0 | 0 | 49.05 | 3.6 | 5 |
| coverage 50x | direct | 40/40 | [0.9124, 1.0000] | 0 | 0 | 49.05 | 3.6 | 5 |
<!-- END GENERATED: coverage -->

### Substitution and indel rates

<!-- BEGIN GENERATED: errors -->
*(generated by `research/v2/render_v2_tables.py` from `research/results/v2/errors.json`)*

20,000 B mixed (seed 7), coverage 10 (poisson), dropout 2 %, 30 trials per point.

| point | read processing | exact recovery | 95 % Wilson CI | failed (detected) | undetected corruption | mean reads/strand | mean groups repaired | worst group erasures |
|---|---|---|---|---|---|---|---|---|
| substitution 0.001 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.80 | 3.6 | 4 |
| substitution 0.001 | direct | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.80 | 3.6 | 4 |
| substitution 0.005 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.80 | 3.7 | 6 |
| substitution 0.005 | direct | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.80 | 3.7 | 6 |
| substitution 0.01 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.81 | 3.5 | 6 |
| substitution 0.01 | direct | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.81 | 3.5 | 6 |
| substitution 0.02 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.84 | 3.7 | 5 |
| substitution 0.02 | direct | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.84 | 4.5 | 6 |
| substitution 0.04 | cluster + consensus | 20/30 | [0.4878, 0.8077] | 10 | 0 | 9.85 | 6.0 | 16 |
| substitution 0.04 | direct | 0/30 | [0.0000, 0.1135] | 30 | 0 | 9.85 | n/a (no successful trial) | n/a |
| substitution 0.06 | cluster + consensus | 0/30 | [0.0000, 0.1135] | 30 | 0 | 9.79 | n/a (no successful trial) | n/a |
| substitution 0.06 | direct | 0/30 | [0.0000, 0.1135] | 30 | 0 | 9.79 | n/a (no successful trial) | n/a |
| insertion = deletion = 0.0001 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.79 | 3.8 | 6 |
| insertion = deletion = 0.0001 | direct | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.79 | 3.8 | 6 |
| insertion = deletion = 0.0005 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.82 | 3.6 | 5 |
| insertion = deletion = 0.0005 | direct | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.82 | 3.6 | 5 |
| insertion = deletion = 0.001 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.81 | 3.9 | 5 |
| insertion = deletion = 0.001 | direct | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.81 | 3.9 | 5 |
| insertion = deletion = 0.003 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.82 | 4.3 | 6 |
| insertion = deletion = 0.003 | direct | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.82 | 5.9 | 15 |
| insertion = deletion = 0.005 | cluster + consensus | 30/30 | [0.8865, 1.0000] | 0 | 0 | 9.79 | 5.5 | 12 |
| insertion = deletion = 0.005 | direct | 0/30 | [0.0000, 0.1135] | 30 | 0 | 9.79 | n/a (no successful trial) | n/a |
| insertion = deletion = 0.01 | cluster + consensus | 2/30 | [0.0185, 0.2132] | 28 | 0 | 9.82 | 6.0 | 16 |
| insertion = deletion = 0.01 | direct | 0/30 | [0.0000, 0.1135] | 30 | 0 | 9.82 | n/a (no successful trial) | n/a |
<!-- END GENERATED: errors -->

### Uneven abundance

<!-- BEGIN GENERATED: abundance -->
*(generated by `research/v2/render_v2_tables.py` from `research/results/v2/abundance.json`)*

20,000 B mixed (seed 7), mean coverage 5 with log-normal abundance, 40 trials per point.

| point | read processing | exact recovery | 95 % Wilson CI | failed (detected) | undetected corruption | mean reads/strand | mean groups repaired | worst group erasures |
|---|---|---|---|---|---|---|---|---|
| sigma 0.0 | cluster + consensus | 40/40 | [0.9124, 1.0000] | 0 | 0 | 4.89 | 4.3 | 9 |
| sigma 0.5 | cluster + consensus | 40/40 | [0.9124, 1.0000] | 0 | 0 | 4.93 | 5.4 | 11 |
| sigma 1.0 | cluster + consensus | 25/40 | [0.4703, 0.7578] | 15 | 0 | 4.91 | 6.0 | 16 |
| sigma 1.5 | cluster + consensus | 0/40 | [0.0000, 0.0876] | 40 | 0 | 4.87 | n/a (no successful trial) | n/a |
<!-- END GENERATED: abundance -->

The V1 channel measurements (format 4, V1 simulator) remain in `research/results/channel_experiments.md` for
reference.
