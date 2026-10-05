# V6 deferred work

Status: V6 directive Phase 10 (documentation), against `build/v6-sprint` @ 9466479. Version labels in the target column
are the plan of record in [VNX_GLOBAL_ROADMAP.md](VNX_GLOBAL_ROADMAP.md) and [V6_ARCHITECTURE.md](V6_ARCHITECTURE.md);
they are plans, not commitments.

Labels: **VERIFIED** (checked by a committed test or script), **SIMULATED** (software strands through a software channel
model), **THEORETICAL** (specified or computed, not run), **PHYSICAL** (real DNA; nothing in VNX-DNA is). No DNA has been
synthesised, stored or sequenced by VNX-DNA, so no statement below is PHYSICAL.

This page lists what V6 intentionally does not contain. Each row gives the reason and the committed evidence behind it.
Defects that are open but not deferred by design are in section 8.

## 1. Strand and format work (V7 and V8)

| Item | Status in 6.x | Reason | Target | Evidence |
|---|---|---|---|---|
| Frame 6 (compact class: 1 B group-index prefix, 3 B group index, 1 B symbol index) | layout specified, no code (THEORETICAL) | Freezing the candidate profiles needs EXP-F6-1 on fitted channel models, which do not exist yet. The motivation is measured: VNX-DNA writes a 14 B (56 nt) header and CRC into every strand, which costs rate at short strand length | V7 | `docs/spec/VNX-DNA-SPEC-V6.md` §3.5 and open question 3; `benchmarks/competitors/lab/README.md` caveat 2 (SIMULATED) |
| Superblock 3 | layout specified, no code (THEORETICAL) | Carries the frame-6 geometry and the reserved pointer for a hierarchical index | V7 | spec §3.8 |
| Primers, primer flanks, trimming, `vnx order-export` | layout specified, no code | Primer design and orthogonality checks need their own module and vendor constraints. The 313-nt default plus two 20-nt primers is 353 nt, over a 350-nt pool limit | V7 | spec §3.6 and §3.2 notes; `docs/LAB_INTERFACE.md` (the export package records a vendor length limit and checks it) |
| Short-strand vendor profiles (about 150-300 nt) | none (founder decision 5) | Frozen only after experiments on fitted channels | V7 | `docs/V6_ARCHITECTURE.md`, decisions table |
| CRC-16 for the shortest profile | none (founder decision 6) | Revisit with V7 experiment data | V7 | same table |
| Wide address class (frame 6 class 1: 4 B prefix, u48 index) | layout specified, no code | The flat 32-bit group index limits one archive; the 16-bit archive tag collides at about 300 random archive IDs in one pool (THEORETICAL arithmetic) | V8 | spec §3.5 table; `docs/COMPETITIVE_GAP_ANALYSIS.md` §10 |
| Hierarchical, partition-placed index; pool catalogue | none | Needs a new superblock version; selective decode still parses every read | V8 (catalogue V8/V9) | spec §2.4 |
| Append-only versions and tombstones, per-object key domains, content-defined chunking | none | Each needs a new required feature and breaks the fixed-chunk rule or the single-key layout | V9 | spec §2.4, §2 table rows on chunking and keys |
| `pydantic` as an optional legacy extra | none | Needs the legacy package split | 7.0 | decisions table, question 7 |

## 2. Nanopore-like decoding (V7, job #82)

Nothing in V6 decodes the simulated `nanopore-like` model. The failure was measured in three places and is not a single
parameter.

| Observation | Value | Source (SIMULATED unless stated) |
|---|---|---|
| Reads aligned at band 6 / 12 / 16 / 24 (nanopore-like, coverage 10) | 0.358 / 0.871 / 0.981 / 1.000 | `experiments/v6/align-band/README.md`, AB-DIAG table |
| Reads whose header address is exact / within 1 byte (band 16) | 0.038 / 0.094 | same table |
| Erased bytes per aligned read, of 70 (band 16) | 41.3 | same table |
| Strands an oracle count vote decodes (band 16), coverage 10 / 15 | 0.289 / 0.509 | same table |
| Retry band 16, nanopore-like, coverage 3 / 5 / 10 | 0/20 at each, same as the default (C2 REJECT) | AB-EXP-01, same README |
| Public CNR reads with no indel (PUBLIC-DATA-DERIVED) | 4.34 % | `experiments/v6/phase4/P4-EXP-03-cnr-ids/README.md` |
| Position-wise vote over exactly-110-nt CNR reads: clusters recovered exactly (PUBLIC-DATA-DERIVED) | 1,288 / 10,000 | same |

Job #80 (the opt-in `--retry-band`) was the V6 attempt at the net-drift part of the problem: it aligns reads whose drift exceeds the default band of 6, and it helps deletion-heavy and high-indel models (deletion-heavy coverage 10: 0/20 to 6/20, SIMULATED). It does not help the nanopore-like model, so the drift beyond the band is only one of the causes and the V7 job below stays open.

The two walls are the read-to-address step (the header is corrupted in most reads, and a wrong byte 0, the scrambler
seed, garbles all of it) and the whole-segment erasure rule. The V7 job is header-independent clustering of reads to
strands, plus indel placement below segment level. Neither fits in V6 without a format change.

## 3. Decoder defaults and opt-in options

Three decoder options were built and measured in V6. All three stay off by default because their pre-registered criteria
were not met or have not been run.

| Option | Default | Result | Rule applied | Evidence |
|---|---|---|---|---|
| Smart indel recovery + soft decoding as the default schedule | off | Fixes the consensus-limited cells (20/20 where the default gives 0/20 in the listed cells) at 3-10 times the decode time. No pre-registered default decision has been run | Pre-registered comparison pending: job #81 | `experiments/v6/phase4/P4-EXP-01-failure-taxonomy/README.md` (SIMULATED) |
| `consensus_weighting=quality` (`--consensus-weighting quality`) | `count` | Efficacy C2 REJECT: 213/520 vs 211/520 exact, difference +0.0038, 95 % CI [-0.0016, +0.0093]. C1, C4, C5, C6, C7 ACCEPT. The simulator's qualities are informative by construction and the CNR data has no qualities, so a calibrated test needs fitted data | Default-change rule not satisfied; refit and re-test on fitted models in V7 | `experiments/v6/phase4/P4-EXP-02-qw-consensus/README.md`, `verdict.json` (SIMULATED) |
| `retry_band` (`--retry-band 16`) | 0 (off) | C2 REJECT (nanopore-like 0/60 vs 0/60) and C5 REJECT (peak RSS ratio up to 1.258 on deletion-heavy at 1 MiB). S1 high-indel gain +0.0292 [+0.0074, +0.0590], no harm in any cell | Pre-registered rule: KEEP OPT-IN | `experiments/v6/align-band/README.md`, `PREREG.md` (SIMULATED) |

## 4. Resilience and benchmark follow-ups

| Item | Status | Reason | Target | Evidence |
|---|---|---|---|---|
| Profiles with more outer parity for heavy dropout at matched rate | **done as an opt-in profile** (job #79): `high-dropout` (`--redundancy-profile high-dropout`), pre-registered rule 3 ACCEPT, 88/100 exact against 35/100 for `s184`, 10/10 in every cell up to 10 % dropout (SIMULATED). Not the default. It still loses to DNA-RS-medium (100/100 pooled; 3/10 against 10/10 at 1 % errors and 20 % dropout) and its strands are 256 nt against 144 nt | The B0 gap at 10 % dropout (`s184` 0/3, DNA-RS and DNA Fountain 3/3) was the motivation. Defaults and the format are unchanged | 6.x: no default change is planned; revisit with B1 | `benchmarks/competitors/lab/results/b0-dropout`; `CHANGELOG.md` (job #79); spec §7 |
| Benchmark lab B1: HEDGES adapter, YYC, the ETH protocol (30 trials, logistic threshold, 3600 s, 0.5/1.0/1.5 bit/nt), DNA-Aeon at the published demo limit, stronger clusterers, timing at load below 1.5 | not run | B0 has 3 seeds per point, unseeded upstream error generation (patched), and timings taken under load. It is not the published protocol and is not comparable to it | **V7** (job #78); not required by directive section 26, deferred under section 27 | lab README, caveats 7-8 and "Next (B1)" |
| P4-EXP-04 re-run with the child's own `VmHWM` (peak-RSS cost of `consensus_weighting=quality`) | not done; the committed RSS column is withdrawn | The `wait4` method reported the harness's own peak | 6.x | `experiments/v6/phase4/README.md` correction; `experiments/v6/align-band/README.md` deviation 1 |
| Fitting channel models to public datasets (CNR, nanopore Zenodo 10943282, DT4DDS, ETH) | none; the `vnx.channel-model/1` schema has the fields | All 14 shipped models are unfitted. The only public-data result in V6 is the CNR per-read IDS statistics | V7 (job #19 is tagged V8) | `docs/CHANNEL_MODEL.md`; `docs/DNA_STORAGE_DATASET_REGISTRY.md` |
| Streaming encode/decode with bounded memory on noisy channels; parallel pipeline scaling 1-8 workers; large-input benchmark framework | open jobs | Existing noisy-channel runs stop at 16 MiB (V4 methodology) | V8 | `docs/BENCHMARKING.md`; roadmap V8 |

## 5. Engineering items

| Item | Status | Reason | Target | Evidence |
|---|---|---|---|---|
| `vnx.experiment/1` manifest, `vnx experiment reproduce` against a manifest, `docs/CONFORMANCE.md` (Phase 8 part 2) | **done** (work/v6-repro) | Manifests cover two re-runnable SIMULATED kinds (`channel-simulation`, `experiment`). The Phase 1 scripts (`experiments/v6/phase1/exp_outer.py`) don't write manifests. `reproduce` reports software, spec, backend and commit differences but doesn't refuse on them: the result hash is the verdict | Phase 1 manifests: 6.x | `docs/CONFORMANCE.md`; `tests/reproducibility/test_experiment_manifest.py` |
| Encode events (`vnx.event/1`) | decode only | `--events` is an option of `vnx decode`; `vnx encode` does not emit events | 6.x | `src/vnxdna/commands/cli.py` |
| M6: `experiments/v6/channel` and `experiments/v6/physical` as thin wrappers over `vnxdna.simulation` and `vnxdna.physical` | not done | The packages exist and are tested. The experiment scripts still carry their own copies, so old reproduction instructions keep working | 6.x | `docs/V6_ARCHITECTURE.md` §7.1 M6 and risk 3 |
| M7: V0.1-V3 code moved into `vnxdna.legacy` | not done | `src/vnxdna/legacy/` holds only `v0_1.py`; `api`, `cli`, `v2`, `v3` and the rest stay at their old paths | 7.0 | `docs/V6_ARCHITECTURE.md` §7.1 M7 |
| MSan on any native kernel | NOT RUN | Needs an MSan-instrumented CPython and NumPy, which the host lacks | open | `docs/V6_BASELINE_AUDIT.md`; `docs/security/V6_FUZZ_REPORT.md` §6 |
| Non-x86 builds (arm64) | NOT RUN | The RS kernel selects AVX2 or AVX-512BW at run time and has a scalar path; no other architecture has been built or tested | open | same |
| Fuzz campaigns of 1 CPU-hour per target | **done**: 13 targets x 3600 s, all exit 0, 0 new crashes, 312,741,708 executions in total (sum of per-target `runs=`; build/v6-sprint @ b693254). The log directory is outside the repository, so the committed fuzz report still shows only the 320-630 s campaigns. The remaining gaps are MSan and non-x86 (rows above) | Results are in the completion report | 6.x: copy the per-target summary into `docs/security/V6_FUZZ_REPORT.md` | `docs/V6_COMPLETION_REPORT.md` §6; `/root/vnx-dna-lab/results/fuzz-1h-20261005-1313/` (not committed) |
| Open security findings (LOW/INFO): V6-SEC-05, -06, -07, -08, -09, -10, -13 and the pool rule for -11 | open or accepted | Hardening, none rated CRITICAL or HIGH | 6.x | `docs/security/V6_SECURITY_MODEL.md` §1 |
| Replace `assert` on untrusted-input paths with typed errors (job #74) | open | `assert` vanishes under `python -O` | 6.x | job list |
| Pinned-dependency decision (`cryptography`, `pytest`) | open (founder) | Advisories triaged as not reachable | founder decision | `docs/security/V6_SECURITY_MODEL.md` V6-SEC-20 |

## 6. Coding and decoding research (V8-V10)

| Item | Reason | Target | Evidence |
|---|---|---|---|
| LDPC and polar outer codes | VNX-DNA has none; competitors use them. A go/no-go needs a same-protocol benchmark (B1) | V10 (job #18 is tagged V8) | `docs/ALGORITHM_COMPARISON.md`; roadmap V10 |
| ML trace reconstruction (DNAformer class) | Licence and benchmark evidence needed before adoption | V10 | `docs/ALGORITHM_COMPARISON.md`; roadmap V10 |
| Iterative inner/outer decoding, adaptive redundancy from an estimated channel, soft-input decoding end to end | Needs fitted channels (V7) and calibration | V10 | roadmap V10 |
| Inner-code decision: markers plus dynamic programming against a HEDGES or DNA-Aeon style inner code | B1 is the prerequisite | V10 | roadmap V10 |

## 7. Interoperability and physical work (V9, V11)

| Item | Status | Reason | Target | Evidence |
|---|---|---|---|---|
| DDSA Sector Zero / Sector One writers | mapping table only; roles reserved in the schema, never written (a test checks this) | No vendor or codec ID allocation; the specification texts were not available | V9 | `docs/DDSA_MAPPING.md` |
| Vendor adapters for synthesis and sequencing | none; `ReferenceSimulatorProvider` is the only provider | Order formats differ per vendor and no physical run exists | V11 | `docs/INTEROPERABILITY.md` §1 |
| SNIA Swordfish DNA resources, object/S3 API, enterprise server | none | Outside V6 scope | V11-V12 | `docs/INTEROPERABILITY.md`; roadmap |
| Physical validation of any kind | none | The roadmap places the first small oligo order after the V7 short-strand profile and order export. Until then every channel result is SIMULATED | milestone after V7 | roadmap V7 |

## 8. Known open defects (not deferrals by design)

| Job | Defect |
|---|---|
| #62 | `vnx locate` / `dna_location` assume the V4 sequential layout and give wrong strand ranges for V6 striped or interleaved pools |
| #66 | `--select` fails with "archive index could not be decoded" when the full decode is FAILURE with some failed groups outside the index; this does not break the select-iff-full rule |
| #58 | Latent mypy errors in `v2`, `v4` and `ecc` modules |

Source: `vnx-jobs list` at the time of writing; these are not tied to a committed test result.
