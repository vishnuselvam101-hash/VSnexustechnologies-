# Channel models

This page has three parts. The **V6 channel-model framework** (`vnx.channel-model/1`, `vnxdna.simulation`, V6 Phase 3)
comes first. The **V4 channel** (`vnxdna.simulation.channel`, formerly `vnxdna.v4.channel`) follows; the V6 framework
reproduces it byte for byte. The **V3 channel** (`vnx-dna sequence`, format 5) follows unchanged below the divider.

> **SIMULATED.** All channels here are configurable, seeded software models used to test the decoders. None is fitted
> to a synthesis chemistry, a storage condition or a sequencing platform. Recovery measured with them describes this
> software under the stated parameters, not physical DNA storage. No DNA has been synthesised, stored, amplified or
> sequenced by VNX-DNA. Reads simulated from any model are SIMULATED, whatever the origin of the model's parameters.

## V6 channel-model framework (`vnx.channel-model/1`)

A channel model is a JSON document with four stages that have independent parameters:

```
strands ─► synthesis ─► storage ─► amplification ─► sequencing ─► reads (FASTQ/FASTA) + vnx.simulation-metadata/1
```

Code: `vnxdna.simulation.model` (documents), `errormodels` (error models), `engine` (simulator), `registry` (shipped
models), `montecarlo` (trials and sweeps). JSON Schemas: `vnx.channel-model/1` and `vnx.simulation-metadata/1` in
`vnxdna/core/schemas` (`vnxdna.core.schema.validate`). The layer rule R2 holds: no codec layer (`core` … `pipeline`)
imports `vnxdna.simulation` (`tests/architecture/test_layers.py`).

### Document

| key | meaning |
|---|---|
| `schema` | `"vnx.channel-model/1"` |
| `name`, `version` | identifier `[a-z0-9][a-z0-9._+-]*` and semantic version. A version is an identifier and is never refused |
| `description`, `note` | free text; `note` says whether the parameters are fitted (and to what) or synthetic |
| `data_source` | where the **parameter values** come from: `SIMULATED` (synthetic stress settings), `SYNTHETIC` (software test data), `LABORATORY` (fitted to laboratory or public sequencing data), `PHYSICAL_VALIDATION` (a VNX physical validation round) |
| `evidence_class` | label of the parameter file (spec §9.3 vocabulary). Allowed: SIMULATED → `SIMULATED`; SYNTHETIC → `SYNTHETIC SOFTWARE TEST`; LABORATORY → `PUBLIC-DATA-DERIVED` or `REAL PHYSICAL RESULT`; PHYSICAL_VALIDATION → `REAL PHYSICAL RESULT` |
| `provenance` | `converted_from` (schema, file, SHA-256 of the source bytes), `datasets` (each with `accession` and the `sha256` of the data used; required for LABORATORY and PHYSICAL_VALIDATION), `fitter`, `references`, `derived` (every parameter change made from another model: source `name@version`, its SHA-256, label, changes) |
| `stages` | the four stage objects below. A reader fills omitted fields with their defaults (identity: no errors, coverage 1); the canonical form, and the model SHA-256 (of its canonical JSON), has every field |

Error-model objects used in several stages:

- **substitution** `{"rate", "matrix", "from_multipliers"}`: per-base rate; `matrix` is 4×4 P(to | from, substitution)
  with rows and columns A, C, G, T, diagonal 0, rows summing to 1 (`null` = uniform over the other three bases);
  `from_multipliers` multiplies the rate by the original base (`null` = 1, 1, 1, 1).
- **insertion** `{"rate", "base_weights"}`: per-base rate of inserting one base *before* the position; `base_weights` =
  P(inserted base) (`null` = uniform).
- **deletion** `{"rate", "run_length": {"distribution": "single" | "geometric", "mean"}}`: `rate` is the per-base rate of
  deletion **events** (run starts). A geometric run removes Geometric(1/mean) consecutive bases (mean ≥ 1), so the
  expected deleted fraction is about `rate × mean`.
- **position_profile** `null` or `{"basis": "absolute" | "relative", "substitution", "insertion", "deletion"}`: per-position
  rate multipliers (each list may be `null`). `absolute`: entry i applies to position i, the last entry beyond the list.
  `relative`: B equal-width bins over each sequence's own length.

| stage | field | default | model |
|---|---|---|---|
| synthesis | `dropout_rate` | 0 | strand species absent from the pool (Bernoulli per strand; V4 `dropout_rate`) |
| | `substitution`, `insertion`, `deletion`, `position_profile` | 0 | errors per base **per molecule**, shared by every read of that molecule; not reflected in quality |
| | `truncation` `{"rate", "min_fraction"}` | 0, 0.5 | an incomplete product keeps a 3′ suffix of uniform length in [⌈min_fraction·n⌉, n−1] |
| | `yield_sigma` | 0 | synthesis bias: per-strand abundance × LogNormal(−σ²/2, σ) (mean 1) |
| | `molecules_per_strand` | 1 | independent molecule variants per strand (1–64); each read samples an intact variant uniformly |
| storage | `strand_loss` `{"rate", "burst_count", "burst_length"}` | 0, 0, 0 | pool-order strand loss: i.i.d. plus contiguous runs (`vnxdna.simulation.loss`, the Phase 1 `loss` section) |
| | `retention` | 1 | fraction of molecules retained: multiplies the expected coverage |
| | `damage` (substitution) | 0 | degradation: per-base damage on molecules (e.g. a C>T/G>A matrix with `from_multipliers` [0, 1, 1, 0]) |
| | `breakage_rate` | 0 | degradation: per-base break; a broken molecule is unreadable (P(intact) = (1 − b)ⁿ); a strand with no intact molecule gives no reads |
| | `contamination_rate` | 0 | expected fraction (≤ 0.5) of output reads that are foreign uniform random sequences of the strand length |
| amplification | `gc_bias` `{"strength", "optimum"}` | 0, 0.5 | coverage weight exp(−s·((gc − optimum)/0.1)²) (V4 GC bias) |
| | `efficiency_sigma` | 0 | uneven representation: per-strand weight × LogNormal(−σ²/2, σ) |
| | `cycles`, `substitution_per_cycle` | 0 | polymerase substitutions, rate × cycles per base per read |
| | `duplicate_rate` | 0 | PCR duplicates: an extra read of the same molecule, with its own sequencing errors |
| sequencing | `coverage` `{"model", "mean", "dispersion", "sigma"}` | fixed, 1, 5, 0 | reads per strand: `fixed` (integer; Poisson when any abundance weight applies, as in V4), `poisson`, `negative-binomial` (Gamma–Poisson, shape `dispersion`), `lognormal` (Poisson(mean × LogNormal(−σ²/2, σ))) |
| | `substitution`, `insertion`, `deletion`, `position_profile` | 0 | per base per read, jointly: one uniform u per position decides deletion, insertion or substitution (V4 rule) |
| | `homopolymer` `{"min_run", "indel_multiplier", "substitution_multiplier"}` | 3, 1, 1 | rates multiplied inside runs ≥ min_run |
| | `bursts` `{"rate", "max_length"}` | 0, 0 | per read, one contiguous deletion of 1…max_length bases |
| | `n_rate`, `reverse_complement_rate` | 0 | base called N; read reported on the opposite strand |
| | `quality` `{"correct", "error", "informative", "sd", "position_slope"}` | 35, 12, 0, 0, 0 | Phred scores: `error` on an erroneous base with probability `informative`; then Gaussian variation `sd` and a decrease of `position_slope` per base along the read as reported, rounded and clipped to 0–93 |
| | `read_length` `{"max_length", "truncation_rate", "min_fraction"}` | null, 0, 0.5 | reads cut to `max_length`; with probability `truncation_rate` a read keeps a uniform prefix in [⌈min_fraction·n⌉, n−1] |
| | `duplicate_rate` | 0 | identical copy of a read, errors included (V4 duplication) |
| | `missing_read_rate` | 0 | read lost after sequencing (failed call, filtering) |
| | `shuffle_window` | 0 | seeded shuffle of reads within windows (0 = strand order) |

Validation refuses unknown keys, wrong types and out-of-range values (exit 7, `CONFIGURATION_ERROR`), and the sum of a
stage's substitution, insertion and deletion rates above 0.5.

**Fields for fitting to public data (roadmap V7).** The research gate (`research/competitive-2026-10-05/40-datasets.md`,
plan (a)) asked for per-position profiles, a 4×4 substitution matrix, a deletion-run length, lognormal coverage, a
synthesis-versus-sequencing split and provenance. They are all in `/1`: `position_profile` (absolute or binned), `matrix`
plus `from_multipliers`, `deletion.run_length`, `coverage.model: "lognormal"` with `sigma`, the `synthesis` stage
(errors shared by the reads of a molecule) versus the `sequencing` stage (independent per read), and
`data_source`/`evidence_class`/`provenance.datasets`. A fitted parameter file would be `data_source: "LABORATORY"`,
`evidence_class: "PUBLIC-DATA-DERIVED"`; reads simulated from it are still SIMULATED. No model has been fitted yet.

### Reading rules (spec §4.1, §4.5)

| input | read as |
|---|---|
| `"schema": "vnx.channel-model/1"` | /1 |
| no `schema`, with `name`, `loss`, `channel` (the Phase 1 files) | `/0`, converted exactly: `loss` → `storage.strand_loss`; `channel.dropout_rate` → `synthesis.dropout_rate`; `gc_bias_*` → `amplification.gc_bias`; every other field → `sequencing` |
| no `schema` otherwise, or `vnx.channel-config/0` or `/1` | a V4 `ChannelConfig` (its own validation, unchanged; its `seed` is the default seed) |
| `"schema": "vnx.channel-model/2"` | /2 (V7, see below) |
| any other `vnx.channel-model/N` or `vnx.channel-config/N`, or another schema | refused: `SCHEMA_UNSUPPORTED`, exit 6 |

`/1 → /0` is available when a model uses only V4 mechanisms (`ChannelModel.to_v0()`, `vnx channel show NAME --schema
vnx.channel-model/0`); otherwise it is refused with the list of parameters `/0` cannot express.

### `vnx.channel-model/2` (V7)

`/2` (V7 protocol section 5.3; code `vnxdna.simulation.model2`) is `/1` plus the provenance of fitted models, per-parameter
confidence intervals and opt-in effects that `/1` cannot express. Every `/1` model loads unchanged (tests assert the SHA-256 of
all 14 shipped models and their simulated reads against the pre-`/2` code). A `/1` document with a `/2` field is refused
("unknown keys"). `/2` is a superset: a `/2` model with no active effect converts to `/1` (`ChannelModel.to_v1`); one with an
active effect is refused by `to_v1` and `to_v0` instead of being converted with the effect dropped.

| key | meaning |
|---|---|
| `model_id` | identifier of this fitted model |
| `provenance.datasets[]` | `id`, `accession`, `url`, `files[]` (`name`, `sha256`) and `sha256` (digest of the file list: SHA-256 of sorted `name:sha256` lines) |
| `provenance.split` | `name` (`FIT` or `HELDOUT`) and `manifest_sha256` of `experiments/v7/split/SPLIT_MANIFEST.json` |
| `provenance.fitting` | `method`, `version`, `commit` (40 hex), `dirty` (bool), `seed`, `timestamp_utc`, `software` (versions). Required for `data_source: LABORATORY`; refused for any other data source |
| `parameters` | per fitted parameter, keyed by its dotted path in `stages`: `value` (must equal the value in `stages`), `ci95` (`[low, high]`, or `{lo, hi}` with the shape of a vector value; bootstrap over references) and `basis` (`measured`, `estimated`, `inferred`, `assumed`, `synthetic`) |
| `fit_report` | optional: `adequacy` (`ADEQUATE`, `INADEQUATE`, `UNVALIDATED`), `failed_metrics`, `metrics`, `misfit`, `measured_statistics`, `validation`, `notes` |

Opt-in effects in `stages.sequencing` (all default off):

| field | meaning | simulator |
|---|---|---|
| `insertion.run_length` `{distribution: single \| geometric, mean}` | bases inserted at one gap (geometric: Geometric(1/mean), capped at 64) | **honoured** |
| `context` `{k: 3, substitution, insertion, deletion}` | 64 rate multipliers each (or null), indexed by the reference 3-mer centred on the site (`16*previous + 4*base + next`, A=0 C=1 G=2 T=3; an edge uses the base itself as the missing neighbour) | **honoured** |
| `correlation` `{lag, p_event_given_event, p_event_given_no_event}` | error correlation | **refused** |
| `asymmetry` `{orientation, backward_substitution_matrix}` | forward/backward difference | **refused** |

A model that sets `correlation` or `asymmetry` is valid as a document but `engine.Simulator`, `simulate_file`, the SDK and the CLI
refuse to simulate it (`CONFIGURATION_ERROR`, exit 7): the effect is never silently ignored. Measured statistics that
the simulator cannot represent are reported in `fit_report.misfit` / `measured_statistics`, not as model fields.

Parser limits (all schemas, `read_file`): at most 16 MiB, 24 levels of nesting, 4,000,000 JSON values, strings of at most
1 MiB, no duplicate object keys, no `NaN`/`Infinity`; position profiles at most 100,000 entries, at most 1,024 `parameters`.
Violations raise `CONFIGURATION_ERROR` (never an untyped error; `tests/simulation/test_sim_model2.py` includes a mutation fuzz).

### Shipped models

`vnx channel models` lists them; `vnx channel show NAME[@VERSION]` prints one. The 14 Phase 1 models (clean,
substitution-heavy, insertion-heavy, deletion-heavy, mixed-mild, mixed-harsh, dropout-5, dropout-10, dropout-20,
burst-loss, uneven-coverage, quality-degradation, illumina-like, nanopore-like; all 1.0.0) ship as package data in
canonical /1 form (`src/vnxdna/simulation/models`), each with the SHA-256 of its /0 source in
`provenance.converted_from`. All are `data_source: SIMULATED`; "illumina-like" and "nanopore-like" follow qualitative
descriptions only and are not fitted to any platform. `NAME` means the highest shipped version of that name;
`NAME@VERSION` an exact one; a path any readable file.

### Error-model interface

`vnxdna.simulation.errormodels.ErrorModel`: `kind`, `parameters()`, `active`, `apply(pool, rng)` on a `SequencePool`
(padded sequences, lengths, source strand, optional qualities, event counts). Implementations: `SubstitutionModel`,
`InsertionModel`, `DeletionModel`, `DropoutModel`, `CoverageModel`, `QualityModel`, and `CompositeModel`, which applies
models in order and applies adjacent substitution/insertion/deletion models jointly (competing events per position, as
in V4). Each can be used alone on a pool; the engine builds them from the stage objects.

### Determinism and compatibility

- Pool-level strand loss uses `default_rng([seed, 0x56360001])` (as `vnxdna.simulation.loss`). The surviving strands are
  processed in batches of 1,024.
- Batch b draws every V4 mechanism from `default_rng([seed, b])` **in the V4 order**. Every other mechanism draws from a
  stage generator `default_rng([seed, b, tag])` (synthesis, storage, amplification and sequencing tags in
  `engine.TAG_*`). Shuffle windows use `default_rng([seed, 0x5F5F])`.
- Consequences: a model that uses only V4 mechanisms (all 14 shipped models, every V4 `ChannelConfig`) gives the V4
  reads byte for byte; switching on an extension does not re-randomise the V4 events (for example a substitution matrix
  changes only which base is substituted, contamination only appends reads); the output is a pure function of
  (strands, model, seed) and independent of the worker count.
- Evidence (SIMULATED, software reproducibility only): **EXP-SIM-1** (`experiments/v6/phase3/EXP-SIM-1`, clean commit
  54ce9bc): 14 models × 3 strand files × 5 seeds, the current simulator versus the engine with the /0 file and with the
  shipped /1 model: **210/210 cells with identical read-file SHA-256**. Regression tests: `tests/simulation/test_sim_compat.py`
  (also 3 workers, and old `ChannelConfig` JSON against `vnxdna.simulation.channel.simulate_file`).

### Metadata (`vnx.simulation-metadata/1`)

Every simulation returns, in the result body under `metadata` (and in a file with `--metadata PATH`): `data_source:
"SIMULATED"`, `evidence_class: "SIMULATED"`, a statement; the `seed`; the model (`name`, `version`, schema, the schema it
was read as, SHA-256, source file and its SHA-256, the parameter file's `data_source`, `evidence_class`, `provenance`);
the full `parameters` and any `overrides`; the generator layout (`rng`); `versions` (software, simulator 1.0.0, model and
metadata schema, NumPy, Python); input (file, SHA-256, strands, strand length); output (file, SHA-256, bytes, format,
reads); and event counts (`stats`: the V4 counters plus synthesis/storage/amplification/sequencing extension counters and
`storage_lost`).

### Use

```
vnx channel simulate strands.fasta reads.fastq --model illumina-like --seed 7
vnx channel simulate strands.fasta reads.fastq --model burst-loss@1.0.0 --seed 7 --metadata reads.meta.json
vnx channel simulate strands.fasta reads.fastq --model nanopore-like --seed 7 \
    --param sequencing.coverage='{"model": "lognormal", "mean": 20, "dispersion": 5, "sigma": 0.58}'
vnx channel simulate strands.fasta reads.fastq --config channel.json        # V4 ChannelConfig: the V4 reads, byte for byte
vnx channel convert experiments/v6/channel/models/clean.json clean.v1.json   # /0 or ChannelConfig → canonical /1
vnx channel sweep strands.fasta --model clean --out-dir runs --trials 20 --base-seed 100 \
    --grid sequencing.substitution.rate='[0.001, 0.005, 0.01]' -o sweep.json
```

`--seed`, `--coverage`, `--substitution-rate`, `--insertion-rate`, `--deletion-rate` and `--dropout-rate` apply to
models as to `ChannelConfig` (a non-integer coverage on a fixed-coverage model switches it to Poisson, as in V4);
`--param PATH=JSON` changes any /1 parameter. Changed parameters are recorded in `provenance.derived` and in the
metadata `overrides`. Python: `sdk.simulate(strands, reads, model="illumina-like", seed=7)`, `sdk.channel_models()`,
`sdk.channel_model(ref)`, `sdk.channel_convert(src, dst)`, `sdk.channel_sweep(...)`.

**Monte Carlo and sweeps** (`vnxdna.simulation.montecarlo`). `monte_carlo(model, strands, out_dir, trials, base_seed)`
runs seeds `base_seed + i` and summarises the realised rates (mean, sd, min, max); an optional `evaluate(reads, metadata)`
callback (for example a decode) is stored per trial. `sweep(model, strands, out_dir, grid)` runs one Monte Carlo per
point of a grid of dotted parameter paths; every point is a derived model, validated before the first run. Output:
`vnx.channel-sweep/1` with every trial's read SHA-256 and metadata.

### Not modelled (NOT VALIDATED against any platform)

Context-dependent error rates beyond homopolymer runs and the per-base-from multipliers; chimeras; primer and adapter
sequences; PCR amplification dynamics (efficiency per cycle, jackpotting, lineage-shared PCR errors); strand-specific
or time-dependent decay laws; paired-end reads. The parameter values of every shipped model are stress settings.

## V4 channel (`vnxdna.simulation.channel`)

The V6 framework reads every V4 `ChannelConfig` and reproduces its reads byte for byte (above). The V4 module is unchanged.


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
