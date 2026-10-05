# VNX-DNA benchmark lab (stage B0)

**All channel results here are SIMULATED** (software strands through the harness's software error generator). Timings and
peak memory are MEASURED on this VPS, under shared load (see "Timing caveat"). Nothing here is a physical result and
nothing here states or implies that VNX-DNA is better or worse than any other system beyond the numbers shown; where VNX-DNA
does worse in these tables it is stated in "Where VNX-DNA loses".

B0 runs VNX-DNA as an external codec inside the ETH `dt4dds-benchmark` harness next to public codecs, on identical input
files, the same channel generator, the same seeds and the same success check. Plan: `docs/VNX_BENCHMARK_PLAN.md` (stage B0).

## What was run

| item | value |
|---|---|
| VNX-DNA under test | this checkout, base commit `c76009d` (version 5.0.0 with V6 opt-in features), `src` unmodified, tree clean; imported from `<repo>/src` (`PYTHONPATH` override, import path checked by `adapters/vnx/env.sh` on every call) |
| CLI | `python -m vnxdna.v4.cli` (`encode`, `decode`); the `vnx` binary on the host is a different tool |
| VNX native backends | alignment: native (`libvnx_align.so`), V6 reads: native, V6 RS: avx2 (avx512 present, not auto-selected); recorded in `results/b0/provenance.json`. Availability and the `auto` selection are recorded; the per-call backend is not traced, and the adapter does not enable V6 opt-in recovery |
| Harness | `fml-ethz/dt4dds-benchmark` 0928fd7f26 (GPL-3.0), Python 3.10.21 venv, `Full` pipeline (encode, workflow, clustering, decode) |
| Channel | harness `ErrorGenerator` (iid substitutions, deletions, insertions per base, strand dropout, fixed coverage per strand), error composition 53 / 45 / 2 % sub / del / ins (the composition of the published ETH protocol). The generator is unseeded upstream; a 3-line patch reads `LAB_CHANNEL_SEED` (trial seed 1, 2, 3). The same seed gives the same random stream, not the same reads, because each codec produces different strands |
| Not used | the dt4dds digital-twin workflows (synthesis / PCR / ageing / Illumina), the harness's other clusterers (CD-HIT, Clover, LSH, MMseqs2, Starcode), the 53/45/2 protocol's 30-trial logistic fit and 1 h limit. They belong to B1 |
| Machine | Intel Xeon Gold 6240 @ 2.60 GHz, 8 vCPU (KVM), 31 GiB; one trial at a time, 1 core (`vnx-lab exp`, CPUQuota 100 %, MemoryMax 8G) |
| Inputs | harness files `input_files/random/19kB` (19,456 B) and `random/5kB` (5,120 B); both random bytes |
| Step limit | 900 s per step (600 s in sweep B); a step that hits it is recorded as a failure with `timeout_suspected` |
| Trials | 280 in total (repro 4, sweep A 180, sweep B 72, sweep C 24), every one stored, none filtered: `results/b0/trials_*.jsonl` |

Per-trial fields: outcome, `exact_sha256` (output SHA-256 equals input SHA-256), harness criterion, positional byte-recovery
fraction, per-step wall time, per-step peak RSS (largest single process of the step), strand statistics (length, nt/byte,
bits/nt, GC mean, share of strands outside 40-60 % GC, longest homopolymer), SHA-256 of strands, reads and output,
1-minute load average.

## Participants

| participant | upstream, pinned SHA | licence | how it runs | status in B0 |
|---|---|---|---|---|
| VNX-DNA | this repository | proprietary (own) | adapter `adapters/vnx/encode.sh`, `decode.sh` (separate processes) | run |
| DNA-RS (Grass / Heckel) | `reinhardh/dna_rs_coding` 455e1a5182 | Apache-2.0 | harness wrapper `DNARS`, binary built with Boost 1.83.0; one-line include patch as in the harness installer | run |
| DNA-Aeon | `MW55/DNA-Aeon` 74bf780fa7 (submodules: NOREC4DNA 9147b7eae3 AGPL-3.0, ConstrainedKaos b7be172a53, Zippy a838de8, abseil) | MIT; bundles AGPL-3.0 NOREC4DNA | harness wrapper `DNAAeon`; whole tool is a separate process, nothing copied | run |
| DNA Fountain (jdbrody Python-3 port) | `jdbrody/dna-fountain` f97c1b8a81 | GPL-3.0 | harness wrapper `DNAFountain`, separate process | run |
| dt4dds-benchmark | `fml-ethz/dt4dds-benchmark` 0928fd7f26 | GPL-3.0 | the harness itself, run unmodified except the seed patch and `chmod +x` on its scripts | run |
| dt4dds | `fml-ethz/dt4dds` 784b635c88 | GPL-3.0 | dependency of the harness; digital-twin workflows not run in B0 | installed |
| HEDGES (Press) | `whpress/hedges` 86812c5049 | MIT; bundles Schifra Reed-Solomon under the "General Schifra License" (not OSI; treated as internal benchmarking only, never redistributed or embedded) | C++ demo built; no text-I/O adapter yet | built, not run (B1) |

Third-party tools live outside this repository in `/root/vnx-dna-lab/bench-tools/<owner>__<repo>@<sha>/` with their own venv,
`INSTALL.log` and licence file. No code of any of them is in this repository, and none is imported by VNX code: the
repository's tests assert this (`tests/bench_lab/test_lab.py`). The only code that imports the GPL harness (the driver that
calls its pipeline, the `VNXCodec` wrapper subclass and the peak-RSS wrapper) is in `bench-tools/lab-driver/` and is not part of
this repository; its file hashes are in the provenance JSON. The harness's installer would have cloned the unlicensed fork
`shulp2211/hedges`; this lab does not use it.

## Exclusions

| excluded | reason |
|---|---|
| AtlasBase (Atlas Data Storage), Biomemory (including the Catalog assets), Iridia, Catalog Technologies, Mimulus Code | proprietary; no public codec or code |
| `jeplb/mahoraga-codec` | PolyForm Noncommercial 1.0.0 |
| `HaolingZHANG/DNASpiderWeb` | custom BGI licence, commercialisation needs permission |
| `dna-storage/hedges-soft-decoder` | Oxford Nanopore Public License, research-only; needs a GPU |
| repositories with no licence (unlicensed fountain ports, `shulp2211/hedges`, ...) | no licence grant |

Full list and sources: `docs/VNX_BENCHMARK_PLAN.md` section 10. Published numbers of excluded systems stay in
`benchmarks/competitors/records.json`; this lab produces none for them.

## VNX-DNA adapter design

`adapters/vnx/encode.sh INPUT SEQUENCES PROFILE` and `decode.sh READS OUTPUT PROFILE [SEED]`, text in and text out, called by the
harness codec wrapper (`VNXCodec`, outside the repository) exactly like the `encode.sh` / `decode.sh` pairs of the built-in codecs.

* **Strands out:** `vnx encode` writes FASTA; the adapter strips the headers, so `SEQUENCES` holds one plain A/C/G/T strand per
  line, no primers. No side information file: VNX-DNA keeps its superblock in the DNA itself.
* **Reads in:** one read per line. The adapter shuffles the lines with a fixed seed (the harness's "no clustering" step copies
  reads in strand order, which would leak cluster boundaries), converts to FASTA and calls `vnx decode --extract`.
* **Clustering and consensus:** VNX-DNA's own address-based grouping and consensus are used (it receives **raw reads**:
  harness clusterer `NoClustering`). The built-in codecs get the harness's default `BasicSet` step (reads sorted by abundance, duplicates collapsed, as in the harness demo).
  One extra row, `vnx-s184-basicset`, gives VNX-DNA the same `BasicSet` input as the others; its results are the same as raw reads in this grid.
* **Parameters:** a profile name selects a VNX configuration file in `adapters/vnx/profiles/`. Both calls read the same file, like the other wrappers' codec parameters
  (the decoder is given the layout; VNX-DNA can also detect its standard layouts from read length, custom layouts need the file).
* **Success and exit codes:** a non-zero exit (including 9, PARTIAL) produces no output file; the harness then counts a failed step. Output is the extracted file only if the archive verified.
* **No compression:** profiles set `archive.compression: none` because the inputs are random bytes and no other participant compresses.

### Profiles used (strand length and rate)

| profile | strand nt | layout (payload B / inner parity B / outer K+M) | bits/nt at 19 kB | bits/nt at 5 kB | target |
|---|---|---|---|---|---|
| `s148` | 148 | 19 / 4 / 64+4 | 0.885 | 0.717 | shortest strand with a usable rate; ~0.9 |
| `s184` | 184 | 28 / 4 / 64+8 | 0.992 | 0.804 | closest short-strand point to 1.0 |
| `s280` | 280 | 44 / 12 / 64+8 (the V4 "dense" layout) | 1.014 | not run | 1.0 |
| `s456` | 456 | 100 / 0 / 64+2 (no inner parity) | 1.552 | not run | 1.5 |
| `default313` | 313 | the default `v4-balanced` profile | 0.737 | not run | not rate-matched, for reference |
| `dense` | 280 | `v4-dense` with outer 64+16 | 0.908 | 0.71 | defined, not swept |

`bits/nt` = input bits / all nucleotides written (superblock, parity, headers, markers, padding, container manifest). The built-in
codecs reach 0.998 (DNA-RS medium), 1.002 (DNA Fountain medium), 1.006 (DNA-Aeon medium) and about 1.50 (RS high, Fountain high) at 144-152 nt.

## Matched-rate and comparability caveats

1. **Rate.** At 19 kB VNX-DNA is within 2 % of 1.0 bit/nt for `s184` and `s280`; `s148` is 11 % below. The nearest VNX point to 1.5 bit/nt is `s456`, which is 3.5 % **above** 1.5 and has no inner parity (its only protection is the outer code and consensus), so it is not a like-for-like comparison with DNA-RS-high / Fountain-high. At 5 kB the fixed container (manifest, Merkle data, superblock strands) costs about 1.3 kB, so VNX-DNA reaches only 0.72-0.80 bit/nt, 20-28 % below the others: sweep B is **not** rate-matched for VNX-DNA.
2. **Strand length.** The harness codecs use 144-152 nt. VNX-DNA writes 10 header bytes and a 4-byte CRC into every strand (14 B = 56 nt), so a 148 nt strand carries at most 19 payload bytes with 4 bytes of inner parity. 184 nt is the shortest length at which VNX-DNA reaches 1.0 bit/nt in this lab. VNX-DNA's default is 313 nt. Strand length is configurable (`dna.layout`), the rate loss at short length is a property of the strand format; at matched rate (about 1.0) VNX-DNA strands are 21-28 % longer than the others' with `s184` and 94 % longer with `s280`.
3. **Success criterion.** Primary success is byte-exact (output SHA-256 equals input SHA-256). The harness's own check ignores extra trailing bytes (`compare_files`: "ignores if the file to compare is longer"). DNA-Aeon and DNA Fountain-high write a few extra bytes after the data (chunk padding), so they are `exact` only after trimming to the known length; they are shown as `+ trailing bytes`, not as `exact`. A harness-style prefix check would count them as success.
4. **false-SUCCESS** = every step exited 0 and an output file exists, but its content differs from the input. The harness's `ErrorGenerator`-based runs: VNX-DNA 0 of 157 trials. DNA-RS 2 (RS-high, 1 %, coverage 5), DNA Fountain 16 (high at 0.5-1 %; medium at 1 %): these decoders return wrong data with exit 0 (they carry no whole-file check the harness can see). Reported as observed, not filtered.
5. **Different decoder inputs.** VNX-DNA receives raw reads; the others the harness's `BasicSet` output. The harness protocol would also let those codecs use stronger clusterers (B1).
6. **Channel.** iid error model with 53/45/2 composition, fixed coverage per strand, no synthesis/PCR bias, no sequencing context effects. VNX-DNA has not been validated on real reads in this lab.
7. **Not the published protocol.** Levels, trial counts and limits differ from the ETH protocol (30 trials, logistic threshold, 1 h). Results are labelled by the lab as "SIMULATED, same harness, run by VNX-DNA" and are not `DIRECTLY COMPARABLE` in the sense of `classify.py`; they are not written to `records.json`.
8. **Small n.** 3 seeds per point (6 per pooled cell): intervals are wide (a pooled 6/6 has Wilson 95 % 0.61-1.00). Differences between neighbouring cells are within noise.

### Timing caveat

Timings are MEASURED wall times of the whole step (shell wrapper, Python start, I/O) on a shared VPS; the 1-minute load average
during the trials (median 5.5, up to 15 in sweep A, 2.4 in sweep C) was far above the plan's 1.5 limit, because other jobs (fuzzers, test suites) were running.
Treat times as indicative to within a factor of about 2 and do not cite them as benchmarks. VNX-DNA's ~0.8 s per step on 19 kB is mostly process start-up.
Peak RSS is not affected by load. For steps killed at the limit, no RSS is recorded (shown as 0 or 15 MiB in the aggregates).

## Results so far (SIMULATED)

### Published-style run (harness demo conditions)

random_5kB, 1 % substitutions only, coverage 30, no dropout, 1 trial (`results/b0/aggregate_repro.md`).

| codec | outcome | decode |
|---|---|---|
| DNA-RS, high code rate (1.497 bit/nt, 144 nt) | byte-exact success | 1.5 s |
| DNA-Aeon, high code rate (1.505 bit/nt) | no result: decoding hit the 900 s limit | 900 s (killed) |
| DNA-Aeon, medium code rate (1.006 bit/nt) | no result: decoding hit the 900 s limit | 900 s (killed) |
| VNX-DNA `s184` (0.804 bit/nt at 5 kB) | byte-exact success | 0.7 s |

DNA-Aeon is installed and works (identical data plus 5 trailing bytes at 0.1-0.2 % errors in sweep B, 12 of 12 trials), but at the demo's 1 % x 30 conditions with the harness's default
`BasicSet` input its stack decoder did not finish within 900 s here; the published protocol allows 3600 s, so this is not a reproduction of a published Aeon success. Re-run with the full limit in B1.

### Grid tables

Pooled over 2 coverages (5, 10) x 3 seeds in sweeps A and B; sweep C pools 3 seeds at coverage 10. Per-coverage rows: `results/b0/aggregate_*.md`; trial data: `results/b0/trials_*.jsonl`.

## Sweep A (19 kB)

| participant | strand nt | nt/byte | bits/nt | error | dropout | exact / n (Wilson 95 %) | + trailing bytes | false-SUCCESS | median decode s [max] | decode peak RSS MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| dna-fountain-high | 152 | 5.33 | 1.501 | 0.2% | 0% | 0/6 (0.00-0.39) | 6 | 0 | 1.0 [1] | 80 |
| dna-fountain-high | 152 | 5.33 | 1.501 | 0.5% | 0% | 0/6 (0.00-0.39) | 1 | 5 | 1.0 [1] | 80 |
| dna-fountain-high | 152 | 5.33 | 1.501 | 1% | 0% | 0/6 (0.00-0.39) | 0 | 6 | 1.2 [1] | 80 |
| dna-fountain-medium | 152 | 7.98 | 1.002 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.0 [1] | 81 |
| dna-fountain-medium | 152 | 7.98 | 1.002 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.2 [1] | 80 |
| dna-fountain-medium | 152 | 7.98 | 1.002 | 1% | 0% | 1/6 (0.03-0.56) | 0 | 5 | 1.4 [1] | 81 |
| dna-rs-high | 144 | 5.34 | 1.497 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.4 [1] | 15 |
| dna-rs-high | 144 | 5.34 | 1.497 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 2.3 [4] | 15 |
| dna-rs-high | 144 | 5.34 | 1.497 | 1% | 0% | 3/6 (0.19-0.81) | 0 | 2 | 43.6 [109] | 776 |
| dna-rs-medium | 144 | 8.02 | 0.998 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.4 [1] | 15 |
| dna-rs-medium | 144 | 8.02 | 0.998 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 3.9 [8] | 16 |
| dna-rs-medium | 144 | 8.02 | 0.998 | 1% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 123.8 [261] | 16 |
| vnx-default313 | 313 | 10.86 | 0.737 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.3 [2] | 76 |
| vnx-default313 | 313 | 10.86 | 0.737 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.3 [1] | 76 |
| vnx-default313 | 313 | 10.86 | 0.737 | 1% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.5 [2] | 75 |
| vnx-s148 | 148 | 9.04 | 0.885 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 70 |
| vnx-s148 | 148 | 9.04 | 0.885 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 70 |
| vnx-s148 | 148 | 9.04 | 0.885 | 1% | 0% | 4/6 (0.30-0.90) | 0 | 0 | 0.9 [1] | 69 |
| vnx-s184 | 184 | 8.07 | 0.992 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 71 |
| vnx-s184 | 184 | 8.07 | 0.992 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 71 |
| vnx-s184 | 184 | 8.07 | 0.992 | 1% | 0% | 5/6 (0.44-0.97) | 0 | 0 | 0.9 [1] | 72 |
| vnx-s184-basicset | 184 | 8.07 | 0.992 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.3 [1] | 63 |
| vnx-s184-basicset | 184 | 8.07 | 0.992 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.1 [1] | 68 |
| vnx-s184-basicset | 184 | 8.07 | 0.992 | 1% | 0% | 5/6 (0.44-0.97) | 0 | 0 | 0.9 [1] | 72 |
| vnx-s280 | 280 | 7.89 | 1.014 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 72 |
| vnx-s280 | 280 | 7.89 | 1.014 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 72 |
| vnx-s280 | 280 | 7.89 | 1.014 | 1% | 0% | 3/6 (0.19-0.81) | 0 | 0 | 0.9 [1] | 72 |
| vnx-s456 | 456 | 5.16 | 1.552 | 0.2% | 0% | 3/6 (0.19-0.81) | 0 | 0 | 1.3 [2] | 67 |
| vnx-s456 | 456 | 5.16 | 1.552 | 0.5% | 0% | 0/6 (0.00-0.39) | 0 | 0 | 1.3 [1] | 67 |
| vnx-s456 | 456 | 5.16 | 1.552 | 1% | 0% | 0/6 (0.00-0.39) | 0 | 0 | 1.2 [1] | 65 |

## Sweep B (5 kB)

| participant | strand nt | nt/byte | bits/nt | error | dropout | exact / n (Wilson 95 %) | + trailing bytes | false-SUCCESS | median decode s [max] | decode peak RSS MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| dna-aeon-medium | 148 | 7.95 | 1.006 | 0.1% | 0% | 0/6 (0.00-0.39) | 6 | 0 | 75.9 [107] | 1925 |
| dna-aeon-medium | 148 | 7.95 | 1.006 | 0.2% | 0% | 0/6 (0.00-0.39) | 6 | 0 | 194.0 [297] | 1942 |
| dna-aeon-medium | 148 | 7.95 | 1.006 | 0.5% | 0% | 0/6 (0.00-0.39) | 0 | 0 | 600.3 [601] | 15 |
| dna-rs-medium | 144 | 8.02 | 0.998 | 0.1% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.4 [1] | 14 |
| dna-rs-medium | 144 | 8.02 | 0.998 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.4 [1] | 14 |
| dna-rs-medium | 144 | 8.02 | 0.998 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 1.6 [2] | 14 |
| vnx-s148 | 148 | 11.16 | 0.717 | 0.1% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 63 |
| vnx-s148 | 148 | 11.16 | 0.717 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.7 [1] | 62 |
| vnx-s148 | 148 | 11.16 | 0.717 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 63 |
| vnx-s184 | 184 | 9.95 | 0.804 | 0.1% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 62 |
| vnx-s184 | 184 | 9.95 | 0.804 | 0.2% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 62 |
| vnx-s184 | 184 | 9.95 | 0.804 | 0.5% | 0% | 6/6 (0.61-1.00) | 0 | 0 | 0.8 [1] | 62 |

## Sweep C (dropout, 19 kB, error 0.5 %, coverage 10)

| participant | strand nt | nt/byte | bits/nt | error | dropout | exact / n (Wilson 95 %) | + trailing bytes | false-SUCCESS | median decode s [max] | decode peak RSS MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| dna-fountain-medium | 152 | 7.98 | 1.002 | 0.5% | 5% | 3/3 (0.44-1.00) | 0 | 0 | 1.2 [1] | 80 |
| dna-fountain-medium | 152 | 7.98 | 1.002 | 0.5% | 10% | 3/3 (0.44-1.00) | 0 | 0 | 1.1 [1] | 81 |
| dna-rs-medium | 144 | 8.02 | 0.998 | 0.5% | 5% | 3/3 (0.44-1.00) | 0 | 0 | 14.9 [15] | 15 |
| dna-rs-medium | 144 | 8.02 | 0.998 | 0.5% | 10% | 3/3 (0.44-1.00) | 0 | 0 | 37.9 [53] | 15 |
| vnx-s184 | 184 | 8.07 | 0.992 | 0.5% | 5% | 2/3 (0.21-0.94) | 0 | 0 | 0.9 [1] | 73 |
| vnx-s184 | 184 | 8.07 | 0.992 | 0.5% | 10% | 0/3 (0.00-0.56) | 0 | 0 | 0.9 [1] | 72 |
| vnx-s280 | 280 | 7.89 | 1.014 | 0.5% | 5% | 3/3 (0.44-1.00) | 0 | 0 | 0.9 [1] | 70 |
| vnx-s280 | 280 | 7.89 | 1.014 | 0.5% | 10% | 0/3 (0.00-0.56) | 0 | 0 | 0.9 [1] | 70 |

Total false-SUCCESS: VNX-DNA 0 of 157 trials; DNA-RS 2; DNA Fountain 16; DNA-Aeon 0.

### Where VNX-DNA loses (in these runs)

* **Error tolerance at about 1.0 bit/nt.** At 1 % errors with coverage 5 and 10 pooled, DNA-RS-medium recovered 6/6; VNX-DNA `s184` 5/6, `s280` 3/6, `s148` 4/6. At 1 % error and coverage 5 alone VNX-DNA `s280` recovered 0/3 and `s184` 2/3, DNA-RS-medium 3/3 (but with a median decode time of 124 s).
* **Dropout.** At 5 % dropout `s184` recovered 2/3 and `s280` 3/3; at 10 % dropout both 0/3. DNA-RS-medium and DNA Fountain-medium recovered 3/3 at both dropout levels. VNX-DNA's outer parity in these profiles is 8 of 72 strands per group.
* **Strand length and fixed overhead.** 184 nt at 1.0 bit/nt versus 144-152 nt; at 5 kB VNX-DNA writes 9.95-11.2 nt/byte against 8.0.
* **Memory.** VNX-DNA peak RSS 56-76 MiB per step versus 14-16 MiB (DNA-RS; one RS-high decode at 1 % errors peaked at 776 MiB) and 80 MiB (Fountain); DNA-Aeon 330 MiB encode, up to 1.9 GiB decode.
* **~1.5 bit/nt.** `s456` recovered 0/6 at 0.5 % and 1 % errors (3/6 at 0.2 %); DNA-RS-high recovered 6/6 up to 0.5 % and 3/6 at 1 %. The `s456` profile has no inner parity.

### Where VNX-DNA does not lose (in these runs)

* No false-SUCCESS in 157 trials; when it fails it exits non-zero and writes nothing. DNA-RS-high, DNA Fountain-high and DNA Fountain-medium returned wrong data with exit 0 in 18 trials.
* DNA-Aeon-medium needed 76-194 s median decode at 0.1-0.2 % errors (5 kB) and exceeded 600 s at 0.5 %, against about 0.8 s for VNX-DNA at the same input; DNA-RS-medium's decode time grew from 1.4 s to a 124 s median (19 kB, 1 % errors). With the timing caveat above, VNX-DNA's decode time did not grow with error rate in this grid.
* Constraints: VNX-DNA strands have GC within 40-60 % (0 % of strands outside) and homopolymers of at most 4 nt; DNA-RS strands reach homopolymers of 7-9 nt (8-10 % of strands have a run of 6 or more) and 0.8-1.4 % of strands outside 40-60 % GC; DNA Fountain at most 4 nt, 0.7-1.2 % outside; DNA-Aeon at most 3 nt, none outside.

## How to reproduce

Prerequisites: `git`, `gcc`/`g++`, `cmake`, `uv`; no Docker.

```bash
# 1. third-party tools (outside the repo); exact SHAs and per-tool notes are in each INSTALL.log
cd /root/vnx-dna-lab/bench-tools
git clone https://github.com/fml-ethz/dt4dds-benchmark   # checkout 0928fd7f26709ce63fd4d0765c50db1be8af0f7d
git clone https://github.com/fml-ethz/dt4dds             # checkout 784b635c88327a467dcbe338ff6ee320157508ee
git clone --recurse-submodules https://github.com/MW55/DNA-Aeon          # 74bf780fa786b1934fcd54feb6861233356eba00
git clone https://github.com/reinhardh/dna_rs_coding     # 455e1a5182bf64281f54d434a4ed5ca456c2373e
git clone https://github.com/jdbrody/dna-fountain        # f97c1b8a81f5c5b819209d5b5e26c9c8f4439495
git clone https://github.com/whpress/hedges              # 86812c5049 (built only)
# venvs (Python 3.10), Boost 1.83.0 program_options, cmake build of DNA-Aeon, symlinks codecs/bin/codec_*/{code,venv}:
# see the INSTALL.log in each directory. Apply bench-tools/lab-driver/patches/errorgenerator_seed.diff to the harness.

# 2. VNX-DNA native kernels (this checkout)
export PYTHONPATH=$PWD/src
python -m vnxdna.v5.native_alignment build; python -m vnxdna.v6.native_reads build; python -m vnxdna.v6.native_rs build
python -c 'import vnxdna;print(vnxdna.__file__)'      # must print <checkout>/src/vnxdna/__init__.py

# 3. a sweep chunk (one core, governed); CONFIG is one of benchmarks/competitors/lab/configs/*.json
bench-tools/lab-runs/run_chunk.sh CONFIG OUTDIR participant1,participant2 2700
#    = vnx-lab exp ... python bench-tools/lab-driver/run_b0.py CONFIG OUTDIR --only ...   (resumable)

# 4. aggregate and provenance (pure VNX-side code)
python benchmarks/competitors/lab/aggregate.py OUTDIR/trials.jsonl --json agg.json --md agg.md
python benchmarks/competitors/lab/collect_provenance.py /root/vnx-dna-lab/bench-tools OUTDIR provenance.json
```

Configs: `repro_published_style.json`, `b0_sweep_a_19kB.json`, `b0_sweep_b_5kB.json`, `b0_sweep_c_dropout_19kB.json`.
Driver and wrapper (GPL side, outside the repository): `bench-tools/lab-driver/` (git commit `6b5781c8ded6`, file SHA-256 in `results/b0/provenance.json`).
Tests of the VNX-side files: `python -m pytest tests/bench_lab`.

Provenance JSON `results/b0/provenance.json`: VNX commit and clean flag, native backends, CPU/kernel/RAM, gcc/cmake versions, every tool's SHA, remote, licence-file hash and submodules, patch hashes, driver hashes, commands, seeds, step limits.

## Follow-up: b0-dropout (SIMULATED, pre-registered)

The pre-registered follow-up on heavy strand dropout at matched rate is in
[`results/b0-dropout/README.md`](results/b0-dropout/README.md). The grid was 19 kB, coverage 10, errors 0.5 % and 1 %,
dropout 0-20 %, with 10 seeds per cell. At 0.998 bit/nt, `hd-l256-i4` recovered 88/100 against 35/100 for `s184`, with
0 false SUCCESS. DNA-RS-medium recovered 100/100. `hd-l256-i4` was accepted under the pre-registered rule and ships as the
opt-in redundancy profile `high-dropout`. New lab profiles are `adapters/vnx/profiles/hd-*.json`, and the report script is
`dropout_report.py`.

## Next (B1)

Full sweep with HEDGES (text-I/O adapter around the C++ build), YYC and the DNA-RS / Fountain / Aeon grid on the same seeds; the ETH protocol re-run (30 trials, logistic threshold, 1 h limit, 0.5/1.0/1.5 bit/nt) through the harness; Aeon demo conditions with the 3600 s limit; stronger clusterers for the built-in codecs; VNX profiles with a higher outer-parity share (to test the dropout result) and with indel-marker layouts at matched rate; the dt4dds digital-twin workflows; timing cells at load below 1.5.
