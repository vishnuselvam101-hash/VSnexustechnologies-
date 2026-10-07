# V8 data provenance: D13 (Lopez et al. 2019)

PUBLIC-DATA-DERIVED. Reads produced by other groups; VNX-DNA synthesised, stored and sequenced nothing.

## Source

| field | value |
|---|---|
| publication | Lopez R, Chen YJ, Dumas Ang S, Yekhanin S, Makarychev K, Racz MZ, Seelig G, Strauss K, Ceze L. DNA assembly for nanopore data storage readout. Nat Commun 10:2933 (2019), doi:10.1038/s41467-019-10978-4 (PMC6610119) |
| source | GitHub: uwmisl/data-ncomms19-nanopore (FASTQ via Git LFS); https://github.com/uwmisl/data-ncomms19-nanopore |
| version | repository commit `4bf31ff40e0d280322d1009bad0971c25fc4ee90` (2019-05-20T04:33:08Z) |
| accession | none (no SRA/ENA accession is given for these files) |
| licence | NONE STATED: the GitHub repository has no licence file (GitHub API: license null; /license 404, checked 2026-10-05). The article is open access, but that licence covers the article, not this repository. Treated as all rights reserved: internal use only |
| usage constraints | Founder approval 2026-10-05 for internal use. Never commit or redistribute reads, reference sequences or per-read derived data (read segments, alignments, per-reference coverage); only aggregate statistics (rates, histograms, counts) may be published, with attribution to the article. Run 13 (space_shuttle) is the held-out file under docs/V7_PROTOCOL_AMENDMENT_D3.md (PR-3.1); before a PREREG commit it is opened only by this manifest script, for its hash and a gzip integrity check |
| read technology | Twist Bioscience synthesis; PCR random access with overhang primers, then Gibson assembly (apollo: 24 fragments, ~4.6 kb) or overlap-extension PCR (365-dishes: 10 groups); ONT MinION, R9.4 flow cells, 1D^2 ligation kit LSK-308, one flow cell per file (apollo: two, runs 15 and 18), 48 h; only 1D^2 reads are in the files (read IDs are two concatenated UUIDs). Basecaller and version are not stated in the article or in the FASTQ headers |
| basecaller | NOT IDENTIFIABLE FROM DATA (not stated in the article or the FASTQ headers) |
| qualities | Phred+33, not binned (run 15: mean 15.3, 90 distinct values) |
| library | Each reference is a 150-nt oligo: 20-nt file primer + 110-nt payload with address + 20-nt file primer (verified in the reference files: one primer pair per file, ten for 365-dishes). Reads are of assembled concatemers: several oligos of one file per read, joined by the assembly overhangs, in either orientation |

## Files (SHA-256 verified at download against the Git LFS pointers)

| file | bytes | SHA-256 | downloaded (UTC) | LFS match |
|---|---|---|---|---|
| `d13/read_me_data-ncomms19-nanopore.txt` | 218 | `9ec3ce831b72b222a8086b103eacedeca6c89a1958d14974d24c916df5466d7f` | 2026-10-05T17:52:06Z | n/a (raw file) |
| `d13/seqs_365-dishes.txt` | 320,594 | `1d5cfad9f2ef73dd7b5616e323afe35d01e545a857db1cd11ce5c5484b9a1cd1` | 2026-10-05T17:52:06Z | n/a (raw file) |
| `d13/seqs_Vitruvian.txt` | 1,358,050 | `1e53b4f08e02da68879ba62c8b028ba22f9b6c5daa7a10dd92738c997b0ccee5` | 2026-10-05T17:52:07Z | n/a (raw file) |
| `d13/seqs_apollo.txt` | 16,027,188 | `0f9461ad1fde66295a554bcb1ded100afa08268edc4eb8c51a4384ea53e5d975` | 2026-10-05T17:52:07Z | n/a (raw file) |
| `d13/seqs_space_shuttle.txt` | 1,157,561 | `d1ccbcc250b251e7c5701c6e210538cf40248534538706ae611b29d9ac6d8154` | 2026-10-05T17:52:09Z | n/a (raw file) |
| `d13/nanopore_run13.fastq.gz` | 575,880,507 | `8117677cbbb5a97060438cf024ac7de8ec7935f100a344098c557cd7415975a8` | 2026-10-05T17:52:09Z | yes |
| `d13/nanopore_run15.fastq.gz` | 846,735,934 | `44200cf19f687e479a51a38f4a70e2ee5e5328ec8c04e75dd9e5b341aba3db9a` | 2026-10-05T17:52:36Z | yes |
| `d13/nanopore_run16.fastq.gz` | 427,414,855 | `88a7e272a1d6baf7e2d6af1d3ab31913639b8882ebbee41afd90ae90baa619ad` | 2026-10-05T17:55:31Z | yes |
| `d13/nanopore_run18.fastq.gz` | 369,091,288 | `100543c9fe4684b7ce049ac97de2f6d9e911a79dcd5e5d8a0a32d39226fbd4de` | 2026-10-05T17:55:51Z | yes |
| `d13/nanopore_run20.fastq.gz` | 456,507,499 | `d138bebb557f7da5d271fd514ad5fad59d595ad5e4cb90a711224f80bf1ab3b8` | 2026-10-05T17:56:12Z | yes |

## Runs and splits

| run | file | split | reads | mean Phred |
|---|---|---|---|---|
| nanopore_run13 | space_shuttle | HELD-OUT (whole run) | not read (held out) | — |
| nanopore_run15 | apollo | FIT/DEV (by reference) | 187703 | 15.315 |
| nanopore_run16 | 365-dishes | FIT/DEV (by reference) | 274206 | 13.271 |
| nanopore_run18 | apollo | FIT/DEV (by reference) | 79449 | 15.465 |
| nanopore_run20 | vitruvian | FIT/DEV (by reference) | 371822 | 14.13 |

V8 splits (`docs/V8_PREREGISTRATION.md` §1): reference buckets FIT 0–5, DEV 6–7, HELD-OUT 8–9; run 13 held out whole;
read-level statistics split by read-ID bucket.

## Pinned processing chain

RAW FASTQ (hash-checked) → `nanolib.split_read` (frozen PR-4.2 constants) → SEGMENTS (reference-oriented, ambiguous
discarded) → NORMALISED OBSERVATIONS (per split; local cache under `/root/vnx-dna-lab/data/derived/v8/`, never committed,
content SHA-256 recorded) → FIT TABLES (`simulation/fit/tally`, Layout L = 150, min_runs 2..6, max_run 32, qualities,
read-rate field) → CHANNEL MODEL. Each stage logs its input and output hashes, parameters, code commit and counts in
`experiments/v8/d13/results/pipeline-<run>.json`.

Definitions (unchanged from V7): an insertion is read bases absent from the reference in the optimal alignment, and a
deletion is reference bases absent from the read. Indels are left-normalised (shifted to the leftmost equivalent
position), so an indel in a homopolymer is attributed to the run's first site. A homopolymer's run length is the length
of the maximal run of equal reference bases containing the site. No quality filter is applied to reads; a segment is kept
if its edit distance ≤ 0.30 × reference length. Whole-read lengths are concatemer lengths (~4.6 kb median) and are not
used as single-oligo read lengths.
