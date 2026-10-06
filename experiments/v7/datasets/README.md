# V7 public sequencing datasets: provenance manifest (PUBLIC-DATA-DERIVED)

Every dataset here was produced by another group. VNX-DNA has synthesised, stored and sequenced nothing. The data files
live outside the repository (`/root/vnx-dna-lab/data/public/`, or `$VNX_DATA_DIR`); only metadata is committed here. No
train/dev/held-out split is defined in this directory: the V7 protocol defines it.

- `MANIFEST.json` (`vnx.dataset-manifest/1`): per dataset the source, URLs, publication, accession, licence and usage
  constraints, download timestamp (UTC) and SHA-256 of every file, published-md5 cross-check, format, record counts, read
  length min/median/p95/max, quality availability, cluster-size or depth information, platform, library-prep notes and
  data state (raw FASTQ versus processed, pre-clustered text).
- `sources.json`: the hand-written part (URLs, licences, notes).
- `fetch_d3.py`: downloads (or `--verify-only` checks) the CAS9 reads at the pinned commit against the git blob SHA-1 of
  the saved GitHub listing. The D13 files were fetched by hand from the Git LFS media URLs in `sources.json` and are
  checked against the LFS pointer SHA-256 by `build_manifest.py`. Split and held-out rules for D13 and CAS9:
  `docs/V7_PROTOCOL_AMENDMENT_D3.md`; the held-out D13 run 13 gets no read-level statistic here.
- `ACCESS_LOG.jsonl`: every opening of held-out material before a PREREG commit (protocol 4.2).
- `build_manifest.py`: standard library only; recomputes everything else from the data directory.
  `python experiments/v7/datasets/build_manifest.py` rewrites the manifest, `--check` recomputes and compares byte for byte
  (about 50 s). Download timestamps are read from `<file>.downloaded_utc` next to each data file. For the CNR and
  DT4DDS files these stamps were written after the download from each file's modification time (UTC), i.e. when the
  download completed; the D03 stamps were written at download start.

| dataset | files used | data state | references | reads | quality |
|---|---|---|---|---|---|
| CNR (MIT) | Centers.txt, Clusters.txt | processed, pre-clustered nanopore text | 10,000 x 110 nt | 269,709 (16 empty clusters) | none |
| DT4DDS Twist_GCfix (ENA PRJEB65931), R1 only | ERR12033806 (Aging_0a), ERR12033810 (0b), ERR12033850 (PhiX) | raw iSeq FASTQ, unmapped | 12,000 x 108 nt design (design FASTA has no licence: hashed, never committed) | 964,204 / 1,061,917 / 267,581 x 150 nt | yes, binned to 3 values (11, 25, 37) |
| D03 nanopore (Zenodo 10943282, CC BY 4.0) | 12 passQ-true TX/RX files (3 files x acc/fast basecaller x forward/backward); passQ-false archives hashed only | processed, primer-segmented, pre-clustered text | 91,766 x 150 nt pool; 30,589 / 30,589 / 30,588 per file | 96k to 641k per group | none |
| D13 Lopez 2019 (uwmisl/data-ncomms19-nanopore @ `4bf31ff`, no licence: internal use) | 5 MinION 1D² FASTQ via Git LFS (2.68 GB gz), 4 reference lists, read-me | basecalled FASTQ of assembled concatemers (several 150-nt oligos per read) | 365-dishes 2,042 (2,041 distinct), apollo 102,084, space_shuttle 7,373, vitruvian 8,650; files pairwise disjoint | runs 15/16/18/20: 187,703 / 274,206 / 79,449 / 371,822 reads (median 5,040 / 1,594 / 5,148 / 1,214 nt); run 13 held out (hash only) | yes, full range |
| CAS9 Imburgia 2025 (uwmisl/cas9-random-access @ `03f029c`, no licence: internal use) | 288 basecalled FASTQ chunks of one run (1.67 GB, stored gzip, 0.66 GB) + README, splint_all.fasta, notebook export | basecalled FASTQ (Q ≥ 9) of rolling-circle concatemers, not demultiplexed | none published (26 addresses of 24 nt only) | counted by the characterisation on FIT/DEV reads (g13 held out) | yes |

## Findings that bear on a 3-way split

- **CNR**: 10,000 references only, and the README warns the centres are not uniformly random (long-range dependencies), so
  a clustering error may contaminate any split. Empty clusters (16) cannot be told apart from clustering failure.
- **DT4DDS**: Aging_0a and 0b are replicates of the same 12,000-design pool, so their references are identical; splitting
  by run would put the same references in every split. PhiX has no designed references (genome only). R1 FASTQ is raw: a
  reference assignment step is needed first, so the reference counts per split are not known until then.
- **D03**: the 12 groups reuse 3 disjoint reference sets (one per file); forward and backward groups list the same strands
  (backward is reverse-complemented), and acc/fast groups repeat them. Split on canonical reference, never on group. After
  that there are 91,766 usable references, but per-reference coverage is low in file 0 (median cluster 3 to 7 reads).
- ENA `read_count` counts both mates of a pair; R1 alone is half of it (checked).
- A first download of ERR12033850 was corrupt (md5 differed, gzip failed); it was re-downloaded and now matches the ENA md5.
- Quality values are binned (3 levels) in the iSeq data, so a quality model has little to fit.
