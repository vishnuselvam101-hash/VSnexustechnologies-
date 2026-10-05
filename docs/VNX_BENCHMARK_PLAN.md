# VNX-DNA competitive benchmark lab: plan

Status: **PLAN, nothing built.** Written 2026-10-05 on `work/research-gate` (base 081697b). No third-party codec has been
run by this plan. Every VNX-DNA codec result is **SIMULATED** (software strands and a software channel), and every
competitor result produced by this lab will be SIMULATED as well. Speed and memory figures from the lab are **MEASURED**
on this machine. Nothing here states or implies that VNX-DNA is better or worse than any listed system.

Inputs:

- the research gate of 2026-10-05: `research/competitive-2026-10-05/` (`00-vnx-baseline-and-readiness.md`,
  `10-repos-cluster1.{md,json}`, `20-repos-cluster2.{md,json}`, `30-companies.{md,json}`, `40-datasets.{md,json}`).
  Below these are cited as `[00]`, `[10]`, `[20]`, `[30]`, `[40]`;
- the existing methodology: [BENCHMARKING.md](BENCHMARKING.md) (metrics, outcomes, fairness rules),
  [BENCHMARKS.md](BENCHMARKS.md) (rendered tables), `benchmarks/competitors/` (`records.json`, `classify.py`,
  comparability labels), `benchmarks/v5/provenance.py`, `benchmarks/v6/`, and the 14 named channel models in
  `experiments/v6/channel/models/`;
- the workforce pipelines in `/root/vnx-dna-ai` (`BENCHMARK_PIPELINE.md`, `EXPERIMENT_PIPELINE.md`, `JOBS.md`).

Licence statements in this document summarise the research gate's reading of each licence. They are not legal advice.

---

## 1. Purpose and scope

The lab answers one question under fixed, recorded conditions: **given the same input bytes, the same channel model and
the same seeds on the same machine, what does each codec cost (nucleotides, time, memory) and what does it recover?**

What already exists and is kept unchanged:

| Existing piece | Role in the lab |
|---|---|
| [BENCHMARKING.md](BENCHMARKING.md) metrics and outcomes | the lab reuses the definitions (`encode_mb_s`, `decode_mb_s`, `recoverable_mb_s`, `nt_per_input_byte`, `peak_rss_mb`) and the never-filtered outcome list; §6 extends them to external tools |
| `experiments/v6/channel/` | channel layer C1 (§5): the lab calls `simulate.py` with a named model; it does not copy or modify it |
| `benchmarks/competitors/` | published results stay there; the lab never writes competitor numbers into `records.json` (§11) |
| `benchmarks/v5/provenance.py` | the provenance block is extended, not replaced (§9) |
| `vnx-experiment` (workforce) | every lab run is started through it so it gets a manifest, seed, hardware and artifact SHA-256 |
| `vnx-bench-compare` (workforce) | stays the tool for VNX-vs-VNX regressions; the lab does not replace it |
| `vnx-jobs` | each phase B0–B3 below becomes one job, plus one job per participant integration |

What is new: running **other** codecs, as external processes, through one adapter contract, on the same workloads and
channels as VNX-DNA, with every outcome kept.

Out of scope: wet-lab work, molecular (PCR) random access, cost per MB in currency, and any comparison of VNX-DNA
simulated results with other groups' wet-lab results (those stay NOT COMPARABLE under `classify.py`).

---

## 2. Architecture

```
 workloads.json ──► workload generator (seeded) ──► input file  (SHA-256 recorded)
                                                      │
                     adapter/<codec>/encode  (separate process, governed scope, timeout)
                                                      │
                               strands.fasta  (+ declared side-information file)
                                                      │
                     strand analyser (in-repo, deterministic): length, nt/byte, GC, homopolymers
                                                      │
       channel layer: C1 VNX named models │ C2 dt4dds digital twin (external) │ C3 fitted models (B2)
                                                      │
                                   reads.fastq  (SHA-256 recorded; model name/version/SHA-256; seed)
                                                      │
                     adapter/<codec>/decode  (separate process, governed scope, timeout)
                                                      │
                     verifier (in-repo): SHA-256(output) == SHA-256(input)  → outcome (never filtered)
                                                      │
                     results JSONL (header = provenance, one record per trial, summary) → render.py → tables
```

Layout (proposed):

| Path | Content | In the VNX repo? |
|---|---|---|
| `benchmarks/lab/run.py` | runner: workload × participant × channel × seed grid; calls adapters as subprocesses | yes |
| `benchmarks/lab/metrics.py` | strand analyser and metric computation (§6) | yes |
| `benchmarks/lab/provenance.py` | extends `benchmarks/v5/provenance.py` with participant and channel provenance (§9) | yes |
| `benchmarks/lab/workloads.json` | workload definitions and expected SHA-256 of each generated file (§4) | yes |
| `benchmarks/lab/channels/*.json` | cross-codec channel variants (§5.1); the 14 V6 models are referenced by name, not copied | yes |
| `benchmarks/lab/adapters/<name>/adapter.json`, `encode`, `decode` | adapter manifest and two thin shell wrappers that only `exec` an installed tool | yes (wrappers contain no third-party code) |
| `benchmarks/lab/render.py` | renders tables from results JSON; no number is hand-written | yes |
| `benchmarks/lab/results/<phase>/summary-*.json` | small committed summaries | yes |
| `tests/bench_lab/` | metric, outcome-taxonomy and provenance tests with fixture strands; no third-party tool needed | yes |
| `/root/vnx-dna-lab/bench-tools/<owner>__<repo>@<sha>/` | pinned upstream clone, its own venv or build, licence file, install log, any compatibility patch | **no** |
| `/root/vnx-dna-lab/results/bench-lab/<run-id>/` | full raw outputs (strands, reads, logs, per-trial JSON) | no |

The name `benchmarks/lab/` avoids the untracked `benchmarks/v6/{run.py,provenance.py,perf_gate/}` that another session
holds uncommitted in the v6 worktree `[00 §header]`. When that work is committed, B0 reuses whatever it provides instead
of duplicating it.

---

## 3. Adapter contract

### 3.1 Rule: process boundary = licence boundary

Every participant, including VNX-DNA itself, runs as a **separate operating-system process** that exchanges **files of
text** (FASTA/FASTQ/plain sequences, JSON) with the runner. Nothing in the VNX repository imports, links, vendors or
copies third-party codec code. This keeps GPL-3.0 and AGPL-3.0 tools usable for internal measurement (run unmodified,
never linked) `[10 §"Benchmark-harness candidates"]`, `[20 §8.4]`, and it measures every codec the same way (process
start, file I/O and all).

### 3.2 Commands

```
<adapter>/encode --input FILE --strands OUT.fasta --side-info OUT.json --params PARAMS.json \
                 --seed N --threads T --workdir DIR
<adapter>/decode --reads READS.fastq --side-info IN.json --output OUT.bin --params PARAMS.json \
                 --seed N --threads T --workdir DIR [--select NAME]
```

| Item | Rule |
|---|---|
| Strand output | FASTA, one record per strand, alphabet `ACGT` only, no primers or adapters unless the codec itself writes them as part of its design (recorded in `adapter.json`) |
| Side information | anything the decoder needs that is **not** in the strands (YYC rule model, DNA-Aeon config/codebook, fountain chunk count, file length) goes into `--side-info`. Its size in bytes is reported next to `nt_per_input_byte` and the codec is flagged `needs_side_info`. VNX-DNA writes `{}` (the superblock is in DNA) |
| Reads input | FASTQ as produced by the channel layer. An adapter may convert format (e.g. to FASTA or plain lines); that conversion is part of the decode time |
| Clustering | if the upstream decoder expects clustered or ordered reads, the adapter runs a declared clusterer inside `decode`, its time is counted, and the pipeline label records it (`raw` or `clustered:<tool>@<sha>`). VNX-DNA consumes raw reads |
| Exit status | `0` = the decoder claims success and wrote `--output`; any non-zero = the decoder reports failure. VNX-DNA exit 9 (PARTIAL) is mapped to `PARTIAL` |
| Determinism | `--seed` is passed to any internal randomness; an adapter whose codec cannot take a seed is flagged `nondeterministic` and its encode output SHA-256 is recorded per run |
| Threads | passed only if `adapter.json` declares a threads parameter; the runner never splits an input to parallelise a codec externally |
| Limits | each call runs in a transient systemd scope under `vnxdna.slice` (`MemoryMax`, `CPUQuota`) with a wall-clock timeout. Defaults: 1 core, 8 GiB, 1 h per call, the constraints of the published ETH protocol `[records.json gimpel2026_codec_benchmark, "software_time"]` |
| Network | none during a run; installs happen in a separate, logged step |

### 3.3 `adapter.json`

```json
{
  "name": "dna-aeon",
  "upstream": "https://github.com/MW55/DNA-Aeon",
  "commit": "<full SHA pinned at install>",
  "licence": {"spdx": "MIT", "rule": "subprocess", "notes": "NOREC4DNA submodule is AGPL-3.0: whole tool runs as a separate process"},
  "install": "/root/vnx-dna-lab/bench-tools/MW55__DNA-Aeon@<sha>/INSTALL.log",
  "runtime": {"python": "3.x", "compiler": "gcc 13.3", "container_image": null},
  "patches": [],
  "params": {"default": {...}, "rate-0.5": {...}, "rate-1.0": {...}, "rate-1.5": {...}},
  "capabilities": {"indels": true, "dropout": true, "needs_clustering": false, "random_access": false,
                   "threads_param": "n_threads", "needs_side_info": true, "max_tested_bytes": null},
  "source_of_defaults": "dt4dds-benchmark codecs/aeon.py at 0928fd7f26 / paper",
  "audit_ref": "[20 §3], [20 §7]"
}
```

`patches` lists any compatibility patch (file path and SHA-256). A patch lives with the upstream code in `bench-tools/`,
never in the VNX repo; a patch to GPL code stays under GPL.

### 3.4 Outcome taxonomy (never filtered)

Mapped onto the outcomes of [BENCHMARKING.md](BENCHMARKING.md):

| Lab outcome | Condition | BENCHMARKING.md name |
|---|---|---|
| `SUCCESS` | exit 0 and SHA-256(output) = SHA-256(input) | SUCCESS |
| `FALSE_SUCCESS` | exit 0 and output differs from input (wrong data returned as if correct) | INTEGRITY_FAILURE (must stay 0 for VNX-DNA; counted and shown for every participant) |
| `PARTIAL` | VNX-DNA exit 9: some files individually verified | PARTIAL |
| `DECODER_FAILURE` | non-zero exit, decoder reported it could not recover | DECODER_FAILURE |
| `TIMEOUT` | wall-clock limit reached | DECODER_FAILURE, sub-class timeout |
| `RESOURCE_LIMIT` | killed by `MemoryMax` (OOM) | DECODER_FAILURE, sub-class resource |
| `HARNESS_ERROR` | adapter missing, malformed FASTA from the channel, runner exception | ERROR: **aborts the run**, never counted against a participant (lesson L9, [BENCHMARKING.md](BENCHMARKING.md)) |

A participant that crashes inside its own code (traceback from the upstream tool) is a `DECODER_FAILURE` with the
stderr tail stored, not a harness error. The distinction is made by the adapter's exit code convention and reviewed
when a participant is integrated.

---

## 4. Workload set

All inputs are generated from `benchmarks/lab/workloads.json` by a seeded generator; each file's SHA-256 is fixed in
that file and checked before every run.

| ID | Content | Sizes | Purpose |
|---|---|---|---|
| `W-RAND` | uniform random bytes (seeded) | 4 KiB, 19 kB, 64 KiB, 256 KiB, 1 MiB, 16 MiB, 256 MiB, 1 GiB | primary workload for every cross-codec comparison; incompressible, so VNX-DNA's zstd stage gives no size advantage |
| `W-MIXED` | the existing VNX `mixed` pattern (half random, half zeros) as in `tools/bench/roundtrip.py` | 1 MiB, 16 MiB | shows the effect of compression; reported separately and labelled "compressible input" |
| `W-TEXT` | generated ASCII text from a seeded word list | 64 KiB, 1 MiB | compressible, realistic text |
| `W-FILES` | 16 files of 64 KiB (`W-RAND` content, different seeds) as one archive or tar | 1 MiB total | random-access latency (§6.11) |
| `W-ETH` | the input file of the ETH protocol (19 kB) taken from the protocol's published repository | 19 kB | protocol re-run only (§11); licence of the file checked before use |
| `W-CORPUS` | the UNACORM 9-file corpus | as published | optional, only if its licence allows internal use `[10 §5]`, `[20 §7.2]` |

Seeds: base seed per workload in `workloads.json`; trial seed = base seed + trial index, the rule already used by
`vnx-experiment` and `experiments/v6/channel/evaluate.py`. Channel seeds are drawn the same way and are identical for
every participant at a given grid point.

Compression fairness: VNX-DNA compresses before encoding; most other codecs do not. Every headline comparison uses
`W-RAND`. Results on `W-MIXED` and `W-TEXT` carry the label "compressible input; VNX-DNA compresses, participant X does
not". The lab does not pre-compress inputs for other codecs, so that each codec runs as its authors ship it.

---

## 5. Channel layer

| ID | Channel | Licence and route | Status |
|---|---|---|---|
| C1 | VNX-DNA named models, `experiments/v6/channel/simulate.py --model <name>` (14 models: clean, substitution-heavy, insertion-heavy, deletion-heavy, mixed-mild, mixed-harsh, dropout-5/10/20, burst-loss, uneven-coverage, quality-degradation, illumina-like, nanopore-like) | in repo | exists; all models are labelled SIMULATED and "not fitted to any measured platform" (`experiments/v6/channel/README.md`) |
| C2 | dt4dds digital twin (`fml-ethz/dt4dds`, GPL-3.0, HEAD 784b635c88 at audit): synthesis (electrochemical / material deposition), PCR, aging and Illumina iSeq workflows, parameters fitted by its authors on 40 sequencing experiments `[20 §4]`, `[40 "Published parameters"]` | external process in its own venv under `bench-tools/`; FASTA in, FASTQ out; never imported by VNX code | to install in B1 |
| C3 | fitted models from public data (`illumina-twist-fit`, `illumina-electrochem-fit`, `ont-guppy-hac-fit`, `ont-guppy-fast-fit`) produced by the fitting plan in `[40 "Plan (a)"]`, backlog R-03 (job #19) | in repo; parameter files labelled PUBLIC-DATA-DERIVED; reads simulated from them stay SIMULATED | B2 |

### 5.1 Cross-codec channel variants

Some C1 models are not neutral across codecs:

- `illumina-like` and `nanopore-like` set `reverse_complement_rate` 0.5. VNX-DNA detects orientation; several other
  decoders may not. Cross-codec sweeps use variants in `benchmarks/lab/channels/` with `reverse_complement_rate` 0 and
  otherwise identical parameters (named `xcodec-<model>`). The original model is run as well and labelled "includes
  reverse-complement reads".
- `simulate.py` requires equal-length input strands. Participants that emit variable-length strands use C2 or a
  length-tolerant variant added to the lab (not to `experiments/v6/channel/`).
- Error rates are per nucleotide. VNX-DNA strands are 313 nt (v4-balanced) `[00 B.1]`, most other codecs use about
  110–160 nt `[40 "Recommended first three"]`, so at the same per-nt rate a VNX strand carries about twice as many
  errors. Every result row reports strand length and expected errors per strand.

### 5.2 Rules

- One channel realisation per (participant, grid point, trial): same model file (SHA-256), same seed. Reads files are
  kept with their SHA-256 in the raw results directory.
- No primers are added by the channel in the default grid; every participant is treated the same.
- Gimpel et al. 2026 use a fixed error composition of 53 % substitutions, 45 % deletions, 2 % insertions
  `[40 "Published parameters"]`; the lab adds this as `xcodec-eth-mix` for the combined error sweep (§6.8).

---

## 6. Metrics

Units: MB = 10^6 bytes, MiB = 2^20 bytes, as in [BENCHMARKING.md](BENCHMARKING.md). "Wall seconds" are measured by the
runner from `exec` of the adapter process to its exit (includes process start, interpreter start and file I/O; excludes
install and build). Every metric is stored per trial; summaries are computed by `render.py`.

| # | Metric | Definition |
|---|---|---|
| 6.1 | **Encode speed** `encode_mb_s` | input MB / encode wall seconds. For VNX-DNA the encode call is `vnx archive` + `vnx encode` (archive + DNA encode, as in BENCHMARKING.md). Also stored: `encode_cpu_s` (user + system of the process tree), and a cold first-run time kept separate from the warm repeats |
| 6.2 | **Decode speed** `decode_mb_s`, **`recoverable_mb_s`** | `decode_mb_s` = input MB / decode wall seconds (reads → output file, including any clustering the adapter runs). `recoverable_mb_s` = the same value **if and only if** the outcome is `SUCCESS`, else 0 (BENCHMARKING.md). `reads_per_second` = reads in the file / decode seconds. Recoverable MB/s is the figure of merit; decode MB/s alone is not |
| 6.3 | **Memory** | `peak_rss_mb`: as in BENCHMARKING.md, max of `ru_maxrss` of the adapter process and of its largest reaped child (not a sum). `peak_cgroup_mb`: `memory.peak` of the transient scope, i.e. the whole process tree including page cache charged to it. Both are reported; tables say which |
| 6.4 | **Encoded length** | number of strands; strand length min/median/max; total nucleotides of **every** strand the codec emits (data, parity, index, header, metadata). Side-information bytes reported separately (§3.2). Primers: none unless part of the codec's design, stated per row |
| 6.5 | **nt/byte** `nt_per_input_byte` | total nucleotides / input bytes (BENCHMARKING.md). Also shown as bits per nt = 8 / `nt_per_input_byte`. Not called "density" without the qualifier "logical, software strands" |
| 6.6 | **GC distribution** | per strand: GC fraction. Reported: histogram (bin 0.01), mean, standard deviation, min, max, share of strands outside [0.40, 0.60]. Windowed GC: for windows of 20 nt and 50 nt, the max and min window GC over all strands and the share of windows outside [0.40, 0.60]. Window sizes are parameters recorded in the result |
| 6.7 | **Homopolymer statistics** | per strand: longest run. Reported: distribution of longest run (1 … ≥8), the count of runs by length over all strands, share of strands with a run > 3 and > 4, global maximum |
| 6.8 | **Substitution / insertion / deletion / dropout tolerance thresholds** | one parameter is swept with the others fixed at the base channel (coverage Poisson mean 10, all other error rates 0, no reverse complement). Grids: substitution, insertion, deletion per-nt rates {0, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10, 0.14}; dropout (i.i.d. strand loss) {0, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.45, 0.65}; combined `xcodec-eth-mix` total rate on the substitution grid. **Threshold** = the parameter value at which the success probability is 0.95, estimated by logistic regression of outcome (`SUCCESS` vs anything else) on the parameter over ≥ 30 trials per point (the ETH protocol's definition `[records.json gimpel2026_codec_benchmark, "conditions"]`), with a bootstrap 95 % interval. Also reported: the highest grid point at which every lower grid point has an observed success rate ≥ 0.95, with Wilson 95 % intervals. `FALSE_SUCCESS` counts are shown next to every threshold |
| 6.9 | **Coverage requirement** | minimum mean coverage (reads per designed strand) at which success probability is 0.95, by the same logistic-regression method. Coverage grid {1, 1.5, 2, 3, 5, 7, 10, 15, 20, 30}; coverage models Poisson and negative binomial (C1 `uneven-coverage`) and, in B1, the lognormal coverage of C2. Fixed error model per sweep: `xcodec-illumina-like`, `xcodec-mixed-mild`, and the C2 Illumina workflow |
| 6.10 | **Recovery %** | (a) trial success rate = `SUCCESS` trials / all trials, Wilson 95 % interval, no trial removed; (b) byte recovery = bytes of the output equal to the input at the same offset / input length (a shorter output counts its missing bytes as wrong; a longer output counts its extra bytes as wrong). (b) is informative only: a decode that is not `SUCCESS` is a failed decode. For VNX-DNA PARTIAL, also the share of files verified |
| 6.11 | **Random-access latency** | `W-FILES` pool, request file k for 5 different k: wall seconds from start of the selective decode to the verified bytes of file k. VNX-DNA: `vnx decode reads.fastq --select <path> --extract DIR`. A participant without file-level selection is measured as full decode + extraction and labelled "no random access: full decode". This is digital (software) selection only; molecular (PCR) random access is not simulated. VNX-DNA `--select` on V6 stripe archives is broken (job #56, `[00 B.6]`); B3 depends on its fix |
| 6.12 | **Scaling** | `W-RAND` 4 KiB → 1 GiB on the clean channel and on `xcodec-mixed-mild` at coverage 10: encode and decode wall seconds and `peak_cgroup_mb` per size; slope of log(time) on log(size) with a 95 % interval; the largest size each participant completes within the per-call limits |
| 6.13 | **Parallel scaling** | for participants that declare a threads parameter (VNX-DNA workers; others as found in B0/B1): wall time T(w) for w = 1, 2, 4, 8; speed-up S(w) = T(1)/T(w); efficiency S(w)/w; 95 % intervals. Single-threaded participants are listed as such; the runner does not split their inputs |

Derived and always shown: `false_success_count` per participant and grid point; `side_info_bytes`; expected errors per
strand at each channel point.

### 6.14 Statistics

- **Timing**: ≥ 5 repeats per cell (10 for cells under 10 s), 1 warm-up discarded; median and 95 % interval
  (bootstrap of the median). Two timings whose intervals overlap, or whose medians differ by less than the measured noise
  (§12), are reported as "no measurable difference".
- **Recovery**: ≥ 30 trials per grid point; Wilson 95 % intervals for rates; logistic-regression thresholds with
  bootstrap intervals. Recovery trials may run in parallel processes (outcome only); timing cells never run in parallel
  with anything else.

---

## 7. Fairness rules

The rules of [BENCHMARKING.md](BENCHMARKING.md) apply unchanged (same input bytes, same hardware run sequentially, same
channel implementation and per-trial seeds, same success definition, harness errors abort). Additional rules for
external codecs:

1. **Defaults first, no per-channel tuning.** Each participant runs with the defaults of its paper or of the ETH
   protocol wrapper (`source_of_defaults` in `adapter.json`). No participant, VNX-DNA included, is tuned to a channel
   model. VNX-DNA runs its default profile; any non-default option (e.g. `--indel-recovery smart`, `--soft-decoding`)
   is listed in every row, and an equivalent option of another codec, if documented, is enabled for it too.
2. **Matched code rate.** Besides defaults, every participant is run at the code rates the ETH protocol uses, 0.5, 1.0
   and 1.5 bits/nt (16, 8 and 5.33 nt/byte) `[40 "Recommended first three"]`. A participant that cannot reach a rate is
   reported "rate not reachable", never approximated. VNX-DNA v4-balanced is about 9.85 nt per input byte
   (≈ 0.81 bits/nt) `[00 B.1]`; reaching 1.0 and 1.5 bits/nt may need a VNX profile that does not exist yet, which is
   reported as such.
3. **Strand length is reported, not equalised.** VNX-DNA (313 nt) and most participants (~110–160 nt) differ; rows show
   strand length and expected errors per strand. If a short-strand VNX profile is added later (proposed in
   `[40 "Plan (b)"]`), it is run as a separate participant row.
4. **Same pipeline boundary.** Decode time includes everything from reads to output bytes, including any clustering or
   consensus the participant needs. The clusterer is named in the row.
5. **Compatibility patches only to make a tool run**, never to change its algorithm; each is listed in `adapter.json`
   and the row is marked "patched". Preference order: pinned older interpreter (e.g. a Python 3.11 venv for YYC) over a
   patch.
6. **Side information is disclosed** (§3.2): a codec that needs out-of-band data to decode is marked in every row.
7. **No silent exclusions.** A participant that fails to install, times out or crashes still appears, with the outcome
   and the stderr tail.
8. **Quiet machine.** Timing runs only when no scheduled heavy job runs (§12).
9. **No ranking text from the lab.** Tables are rendered by `render.py`; any sentence that compares VNX-DNA with
   another system passes `vnx-claims check` and the comparability rules of §11 before it is written.

---

## 8. Participants

Commit SHAs are those recorded by the research gate on 2026-10-05; B0/B1 pins the exact SHA at install time.

| Participant | Upstream (commit at audit) | Licence | Licence rule | Integration route | Known issues | Phase |
|---|---|---|---|---|---|---|
| **VNX-DNA** | this repository (commit under test) | proprietary (own) | n/a | adapter calls `vnx archive` + `vnx encode`; `vnx decode … --extract`; profiles from `src/vnxdna/v6/profiles.py` | `--select` broken on V6 stripe archives (job #56) | B0 |
| **DNA-Aeon** | `MW55/DNA-Aeon` (6e33bb6fc4, 2025-01-14; core C++ c13efeae84) | MIT; bundles NOREC4DNA submodule (AGPL-3.0) | whole tool as separate process; nothing copied | own adapter calling its encode/decode CLI with the parameters of the ETH protocol wrapper (`codecs/aeon.py`: sync 4, chunk 14, package redundancy 0.45, CRC) | needs config + codebook as side information | B0 |
| **DNA-RS** (Grass/Heckel) | `reinhardh/dna_rs_coding` (455e1a5182, 2021-03-10) | Apache-2.0 | subprocess (permissive) | own adapter; reference wrapper in the ETH protocol | old build environment | B0 |
| **HEDGES** | `whpress/hedges` (86812c5049, 2024-11-04), C++ rewrite | MIT at top level, **bundles Schifra Reed-Solomon** whose own terms allow open-source / non-commercial use only `[10 §3]` | separate binary, internal measurement only; never embedded or shipped; see open question Q2 | own adapter around the C++ build (the research gate built it in 2.5 s and ran its demo) `[10 §3]` | decoder ≈ 1.1 KB/s of message in the gate's demo run (125 s for 20 packets, single thread) `[10 §3]`, so large workloads hit the 1 h limit; the ETH wrapper installs an unlicensed fork (`shulp2211/hedges`) `[20 §2.5]`, which this lab does not use | B1 |
| **YYC** (Yin-Yang) | `BGI-SynBio/YinYangCode` (f1dedbe9b7, 2025-10-23); older `ntpz870817/DNA-storage-YYC` | MIT | subprocess | own adapter, Python 3.11 venv | the Chamaeleo copy raises `TypeError: 'float' object cannot be interpreted as an integer` on Python 3.12 (`random.randint` with a `math.pow` result) `[20 §2.2]`; decoding needs the pickled rule model (side information); no ECC inside the codec (paper uses an outer RS) `[20 §2.3]` | B1 |
| **DNA Fountain** | `jdbrody/dna-fountain` (f97c1b8a81, archived 2023-06-29), the Python 3 port used by the ETH wrapper; original `TeamErlich/dna-fountain` (code 2016, Python 2) | GPL-3.0 | run unmodified as subprocess; no import, no copy | own adapter or the ETH wrapper (in `bench-tools/` only) | discards reads with errors (no indel correction) `[20 §3]`; port is archived | B1 |
| **NOREC4DNA** (LT / Online / Raptor) | `umr-ds/NOREC4DNA` (51f9660970, 2025-10-13) | **AGPL-3.0** | run unmodified as a separate local process; never linked, copied or offered over a network | own adapter, one row per code (LT, Online, RU10) | no indel handling inside the fountain layer `[20 §6]` | B1 |
| **DNABoundedHomopolymerEncoding** (Microsoft) | `microsoft/DNABoundedHomopolymerEncoding` (fbb8ae203f, 2025-09-25) | MIT | subprocess | **mapping-only track**: rate, speed, homopolymer statistics on the clean channel; no channel sweeps (no error correction: one error garbles a whole block) `[10 §1c]` | GMP dependency; homopolymer constraint only (no GC) | B1 |
| **TrellisBMA** (Microsoft) | `microsoft/TrellisBMA` (77cb3d3965, archived) | MIT | subprocess | **reconstruction-only track**: trace reconstruction from read clusters on the CNR dataset (D04) and on simulated clusters; compared with VNX-DNA consensus only on that sub-task; not an end-to-end codec `[10 §1a]` | notebook-driven Python + numba; CNR centres are not uniformly random (dataset README, 2024) `[40 D04]` | B2 |
| **Chamaeleo** | `ntpz870817/Chamaeleo` (54bdf37bef; PyPI 1.34) | MIT | subprocess | **mapping-level baseline only**: nt/byte, GC and homopolymer statistics and encode/decode speed for Base, Church, Goldman, Grass, Blawat, DNA Fountain mappers; no robustness numbers (its error model applies one random edit to a fraction of sequences, no dropout or coverage) `[20 §2.2]` | YYC class fails on Python 3.12; Grass needs segment length divisible by 16 | B1 |

### 8.1 Alternative route: plug VNX-DNA into the existing harnesses

| Harness | Licence | How VNX-DNA plugs in | Use |
|---|---|---|---|
| `fml-ethz/dt4dds-benchmark` (0928fd7f26) | GPL-3.0 | codec class with `encode.sh` / `decode.sh` calling the `vnx` CLI; the Python wrapper subclasses the harness's `BaseCodec`, so it is a GPL-covered file and lives in `bench-tools/`, not in the VNX repo `[20 §1.2]`, `[20 §7.1]` | **required** for any DIRECTLY COMPARABLE label (re-run of the published ETH protocol, §11) |
| `AAnzel/UNACORM` (8b1dbd81f9) | GPL-3.0 | wrapper in its `Source/Encodings` (kept in `bench-tools/`) `[20 §7.2]` | optional; preprint only (arXiv 2608.09673), so results stay PARTIALLY COMPARABLE at most |

Environment: the ETH harness needs Python 3.10 or its Docker image `agimpel/dt4dds-benchmark` `[20 §7.1]`. Docker on
this host serves the VNX Weather production containers (`TOOL_REGISTRY.md`: "do not touch"); the lab installs from
source in a venv unless the founder approves a separate container setup (Q3).

### 8.2 Candidates audited but not in the first participant list

Permissively or copyleft-licensed codecs that could be added after B1 through the same contract: Gungnir (BSD-3, Go),
StairLoop (GPL-3.0), DBGPS (GPL-3.0), Chandak LDPC / nanopore convolutional codes (MIT), Derrick (MIT), MGCP (MIT)
`[10 §5]`, `[20 §7.3]`. `dna-storage/reframed` has a custom BSD-style licence that GitHub reports as NOASSERTION; it is
held until the licence is read in full `[10 §2]`.

---

## 9. Provenance (every benchmark)

Every results file starts with a header record; every trial record carries the keys needed to reproduce it. Built on
`benchmarks/v5/provenance.py` and the `vnx-experiment` manifest.

| Group | Fields |
|---|---|
| Hardware | CPU model, logical CPUs, RAM, CPU flags (`cpu_flags()`), kernel, virtualisation note ("shared VPS"), governor scope limits (MemoryMax, CPUQuota) |
| Software | OS, Python, gcc/clang versions; VNX-DNA commit and `worktree_dirty`; per participant: upstream URL, commit SHA, licence SPDX, patch SHA-256 list, install-log SHA-256, interpreter version, container image digest if any |
| Dataset / workload | workload ID, size, generator seed, input SHA-256; for C3, dataset accessions and SHA-256 of downloaded files |
| Parameters | adapter params (resolved JSON and its SHA-256), channel model name, version and SHA-256, all seeds, coverage, threads, limits |
| Command | exact argv of encode, channel and decode calls, working directory |
| Date | UTC start and end of each call, ISO 8601 |
| Result | outcome (§3.4), exit code, metrics (§6), output SHA-256, strands and reads SHA-256, stderr tail (last 4 KiB), evidence label (SIMULATED for recovery, MEASURED for time and memory) |

Rules: a run on a dirty VNX tree is marked dirty and is not cited (EXPERIMENT_PIPELINE rule 1); results are never edited
by hand; a run is reproducible from its header alone (`run.py --reproduce <results.jsonl>`), and the hashes of
strands and reads must match on re-run for deterministic participants.

---

## 10. Exclusions

| Excluded | Reason | Source |
|---|---|---|
| **AtlasBase** (formerly Atlas Data Storage) | proprietary codec, patent application CA3249936A1 "Codecs for DNA data storage"; GitHub orgs `atlas-data-storage` and `atlasds` have 0 public repositories; its stated "open-source script" for read-back was not found | `[30 §4]`, `30-companies.json` |
| **Biomemory** (including the Catalog assets acquired Mar 2026) | proprietary; no public code or codec found | `[30 §1]`, `[30 §8]` |
| **Iridia** | proprietary integrated chip + ECC; no public code | `[30 §1]`, `[30 §4]` |
| **Catalog Technologies** | assets now owned by Biomemory; no public codec or performance numbers | `[30 §1]`; `records.json` `not_found` |
| Mimulus Code (with GenScript) | proprietary; no metrics published | `[30 §1]` |
| `jeplb/mahoraga-codec` | PolyForm Noncommercial 1.0.0: no for-profit internal use without a licence | `[20 §5]`, `[20 §7.6]` |
| `HaolingZHANG/DNASpiderWeb` (SPIDER-WEB) | custom BGI-Research licence: commercialisation needs separate permission from BGI; held until BGI permission or a legal reading (Q4) | `[20 §2.4]` |
| `dna-storage/hedges-soft-decoder` | Oxford Nanopore Public License 1.0, research-only; also needs a CUDA GPU | `[10 §2]` |
| Repositories with no licence (e.g. TReconLM, Sabary Reconstruction, ArchiGen, DNArSim, DNAStorageToolkit, unlicensed fountain ports, `shulp2211/hedges`) | no licence grant | `[10 "Blocked / unclear"]`, `[20 §3]`, `[20 §7.6]` |

Published numbers of excluded systems, where they exist, remain in `benchmarks/competitors/records.json` with their
computed labels; the lab produces no numbers for them.

---

## 11. Relation to the comparability labels

`benchmarks/competitors/classify.py` labels a published result per metric. The lab does not change those rules:

1. **Lab head-to-head results** (a participant's software run by VNX-DNA on this machine) are measurements of that
   software **as configured by this lab**. They are reported in the lab's own tables, labelled
   "SIMULATED, same harness, run by VNX-DNA", and are **never written into `records.json`** and never presented as the
   authors' result.
2. **DIRECTLY COMPARABLE** is reachable only by re-running a **published protocol unchanged**, with scenario IDs recorded
   on both sides (`classify.py --protocol-runs runs.json`). The one protocol record today is
   `gimpel2026_codec_benchmark` (Gimpel et al., Nat. Commun. 17:3963, 2026, doi 10.1038/s41467-026-70548-3: six codecs,
   code rates 0.5/1.0/1.5 bits/nt, 53/45/2 % sub/del/ins composition, DT4DDS workflows, 95 % threshold by logistic
   regression over 30 trials, 1 h / 8 GB / 1 core). The route is §8.1 (dt4dds-benchmark).
3. Before any VNX-DNA protocol result is shown, the lab must **reproduce the protocol's published results for at least
   two of its codecs** within the published uncertainty (or document the discrepancy). This validates the harness
   installation.
4. Time metrics stay PARTIALLY COMPARABLE: the protocol used an AMD EPYC 7763 core `[records.json]`; this lab runs on a
   Xeon Gold 6240 VPS, a different `hardware_class`.
5. UNACORM is a preprint and has no `protocol: true` record; its results stay PARTIALLY COMPARABLE at most.
6. Real reads of the ETH pool (ENA PRJEB90546, `[40 D01]`) let the lab rerun the six codecs' decoders on real reads to
   validate the pipeline. VNX-DNA strands are not in that pool, so VNX-DNA can only be compared there under a fitted
   simulation (C3), labelled SIMULATED with PUBLIC-DATA-DERIVED parameters `[40 "Recommended first three"]`.

---

## 12. Hardware and scheduling

- One shared VPS: Intel Xeon Gold 6240, 8 vCPU, 31 GiB RAM, about 46 GB free disk on `/` (2026-10-05). Run-to-run noise
  is about 10 % (`[00 B.9]`, WORKFORCE_VALIDATION §8). Hence repeats and intervals (§6.14); no conclusion from a single
  timing; differences inside the noise are reported as such.
- Timing cells run one at a time, in the governor slice, outside the scheduled heavy window (nightly lint/tests/security/
  fuzz 01:15–04:00 daily; weekly security and regression Saturday 04:00–05:00; nightly review 23:00) (`WORKFLOWS.md`).
  The runner refuses to start a timing cell if the 1-minute load average exceeds 1.5 and records the load average with
  every timing.
- Recovery sweeps may use up to 6 parallel trial processes (as `vnx-lab full` uses 6 CPUs); their timings are not used.
- Parallel scaling at w = 8 shares the machine with the system itself and is reported with that caveat.
- Disk: raw reads for large sweeps are deleted after their SHA-256 and summary are stored, except for a 1 % random
  sample kept for audit. Public datasets for B2 (§13) need about 12 GB for the first four datasets `[40 Registry]`.

---

## 13. Phased build plan

Effort is in working sessions (one session ≈ one day of supervised implementation). Compute is wall time on this
machine. Each phase is one `vnx-jobs` entry of type `benchmark` (P4 unless the founder raises it), with the acceptance
criteria below as its validation text, built in its own `vnx-task` worktree and reviewed before merge.

### B0 — adapter contract, runner, VNX-DNA + two codecs

Scope: `benchmarks/lab/` skeleton (runner, metrics, provenance, workloads, render), outcome taxonomy, governed
execution, adapters for VNX-DNA, DNA-Aeon (MIT) and DNA-RS (Apache-2.0). Both codecs are permissive and both are in the
ETH protocol, so B0 also prepares §8.1.

Acceptance:

1. Clean channel: VNX-DNA, DNA-Aeon and DNA-RS each give `SUCCESS` for `W-RAND` 4 KiB, 64 KiB and 1 MiB, 3 seeds each.
2. Metric unit tests (`tests/bench_lab/`) on fixture strands with hand-computed values: nt/byte, GC histogram and
   windows, homopolymer counts, byte-recovery fraction, Wilson and logistic-regression helpers.
3. Taxonomy tests with fake adapters: an adapter that returns wrong bytes with exit 0 produces `FALSE_SUCCESS` and is
   kept; one that exceeds the timeout produces `TIMEOUT`; a missing adapter aborts the run as `HARNESS_ERROR`.
4. Provenance: a results file missing any §9 field is rejected by the validator; `run.py --reproduce` re-creates
   identical strands and reads SHA-256 for the deterministic participants.
5. Licence check: a test asserts that no file under the VNX repository imports a participant module or contains files
   from `bench-tools/` (path and SHA-256 scan).
6. One C1 channel point (`xcodec-mixed-mild`, coverage 10, 30 trials) runs end to end for the three participants;
   the results render through `render.py`; `vnx-claims check` passes on the rendered page.

Effort: 4–6 sessions. Compute: under 2 h.

### B1 — full sweep and protocol re-run

Scope: remaining participants (HEDGES, YYC, DNA Fountain, NOREC4DNA ×3 codes, Chamaeleo mapping track,
DNABoundedHomopolymerEncoding mapping track); C2 (dt4dds) installed; all metrics §6.1–6.10 on `W-RAND` 64 KiB (HEDGES
also at 19 kB because of its speed); matched code rates; ETH protocol re-run through dt4dds-benchmark (§8.1, §11).

Acceptance:

1. Every participant × grid cell has an outcome or a documented exclusion reason; no cell is silently empty.
2. ≥ 30 trials per recovery grid point; thresholds with bootstrap intervals; `FALSE_SUCCESS` reported per cell; VNX-DNA
   `FALSE_SUCCESS` = 0 (otherwise the phase stops and a P1 debugging job is opened).
3. Protocol re-run: published results reproduced for ≥ 2 of the six protocol codecs within the published uncertainty,
   or the discrepancy documented; `classify.py --protocol-runs` then labels only the re-run scenarios DIRECTLY COMPARABLE.
4. Each participant integration has its own job record with the pinned SHA, licence rule and any patch.
5. Rendered report passes `vnx-claims check` and an independent evidence review.

Effort: 10–15 sessions. Compute: about 10 000 decodes (≈ 8 participants × 5 sweeps × 8 grid points × 30 trials);
at a median of about 20 s each this is about 55 CPU-hours, about 10–12 h of wall time with 6 parallel trial processes,
plus about 4 h of sequential timing cells. Estimates to be replaced by B0 measurements.

### B2 — channel models fitted to public data

Scope: the fitter and model schema v2 of `[40 "Plan (a)"]` (backlog R-03, job #19): CNR (D04) → Sokolovskii/Welter
nanopore (D03) → DT4DDS (D02) → ETH pool (D01); `datasets.lock.json` (URL, SHA-256, size) and a verifying `fetch.py`;
new evidence class PUBLIC-DATA-DERIVED in the physical-record schema; rerun a B1 subset (thresholds and coverage) on
the fitted models; TrellisBMA reconstruction track on D04 and the D28 subsets.

Acceptance:

1. Fitter round-trip test: simulate from a known model, fit, recover every parameter within binomial intervals.
2. Fitted D02 rates fall inside the published ranges (deletions 6.7 ± 6.9, substitutions 7.9 ± 2.0, insertions
   < 0.3 ± 0.2 per 1000 nt; Gimpel 2023) or the deviation is explained `[40 "Published parameters"]`.
3. Every fitted model file carries accessions, file SHA-256 and fitter commit; reads simulated from it are labelled
   SIMULATED.
4. Licence of each dataset recorded; no dataset without an explicit or INSDC-policy basis is used `[40 "How entries were verified"]`.

Effort: 8–12 sessions. Downloads: about 12 GB (D01 ≈ 1.0 GB, D02 ≈ 10.6 GB, D03 153 MB, D04 31 MB `[40 Registry]`);
founder approval for downloads above 5 GB (Q5).

### B3 — scaling, parallel scaling, random access

Scope: §6.11–6.13 for every participant up to its limit; VNX-DNA up to 1 GiB.

Acceptance:

1. Per participant: time and memory curves from 4 KiB to the largest size completed within limits, with the slope and
   its interval; the limiting size and reason (timeout or memory) stated.
2. Parallel scaling with intervals for every participant that declares threads; single-threaded ones listed.
3. Random-access latency for VNX-DNA on `W-FILES` after job #56 is fixed (B3 depends on it); full-decode latency for
   the others, labelled.
4. All timing cells recorded with load average ≤ 1.5 at start.

Effort: 5–7 sessions. Compute: about 24–48 h, dominated by the 256 MiB and 1 GiB cells (the V4 1 GiB clean round trip
took 96.8 s encode and 244.4 s decode `[00 A.2]`; slower participants stop at the 1 h limit).

---

## 14. Open questions for the founder

| # | Question |
|---|---|
| Q1 | Approve the participant list (§8) and the exclusions (§10)? |
| Q2 | HEDGES bundles Schifra RS (open-source / non-commercial terms). Is internal benchmarking by VNX acceptable, or should HEDGES wait for a legal reading or Schifra's commercial terms? |
| Q3 | Docker on this host runs VNX Weather production. Install the ETH harness from source only (default), or allow a separate, resource-capped container? |
| Q4 | SPIDER-WEB: ask BGI for permission to evaluate internally, or leave it excluded? |
| Q5 | Approve about 12 GB of public-dataset downloads for B2? |
| Q6 | Priority of the B-phases relative to the V6 critical path (they are proposed as P4). |
