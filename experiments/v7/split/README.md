# V7 data split (PUBLIC-DATA-DERIVED partition of reference designs)

Implements `docs/V7_PROTOCOL.md` section 4.1 (Amendment 1) and the access guard of section 4.2. Computed from the dataset
manifest and the reference/design files only; no read file (RX, Clusters.txt, FASTQ) is opened by the split script
(`SPLIT_MANIFEST.json` records `read_files_opened: false`). Reproduce: `python experiments/v7/split/split.py --check`.

| file | purpose |
|---|---|
| `refsplit.py` | the rule as pure functions (canonical sequence, bucket, held-out run, list hash) |
| `split.py` | computes the lists and `SPLIT_MANIFEST.json`; verifies every input file against the dataset manifest SHA-256 |
| `lists/<dataset>/<SPLIT>.refs.txt`, `.runs.txt` | reference IDs (indices: CNR line of Centers.txt, D02 design FASTA ID, D03 oligos.fasta ID) and run/group IDs per split |
| `SPLIT_MANIFEST.json` | counts, SHA-256 of every list, input hashes, protocol and manifest hashes |
| `guard.py` | the only reader of read files; refuses held-out reads without a PREREG commit SHA; logs to `../datasets/ACCESS_LOG.jsonl` |

No sequence is stored in any output. The DT4DDS design FASTA has no licence: only design IDs (indices) and hashes are committed.

## Result (reference counts per split)

| dataset | FIT | DEV | HELDOUT | held-out run | split kind |
|---|---|---|---|---|---|
| D04 CNR | 6,017 | **1,971** | 2,012 | none | by reference only (weaker) |
| D03 Zenodo 10943282 | 36,883 | 12,167 | 42,716 | `file-1` (all 4 groups) + buckets 8-9 of file-0, file-2 | held-out run + references |
| D02 DT4DDS Twist | 7,236 | 2,348 | 2,416 | none (0a/0b share one pool) | by reference only (weaker) |

Protocol 4.3: **CNR DEV has 1,971 references, below 2,000**, so the CNR DEV split is reported as weak (the bucket rule is
fixed, so it is not rebalanced). The PhiX run ERR12033850 has no designed references: it is listed under FIT as a
sequencing-only control for the instrument stage and is never used for validation.

## Guard behaviour

* FIT / DEV: reads of references outside the split are skipped before reaching the caller. For raw D02 reads the assigner
  must look at every read to decide its reference; reads not assigned to the split are dropped and only counted.
* HELDOUT: needs a 40-hex PREREG commit SHA that exists, is an ancestor of HEAD and contains an `experiments/v7/**/PREREG*`
  file. Every held-out request (granted or refused) is appended to `ACCESS_LOG.jsonl`. The log is empty at this commit.
* The D03 held-out run (file-1) is in no FIT/DEV run list, so it cannot be requested for FIT or DEV with any SHA.
* Limits: Python cannot stop code that bypasses `guard.py`; a test asserts that no fitting module names a read file.
  The dataset manifest build (before the split) hashed all files, including held-out ones, as the protocol allows.
