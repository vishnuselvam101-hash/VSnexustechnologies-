# V11 WP3: peak RSS vs input size, v10.0.0 vs V11

**SIMULATED.** Reads come from a software channel model. No DNA was synthesised, stored or sequenced.

## Method

`rss.py` (see its docstring) generates random input at 1, 4, 16 and 64 MiB (seed 111000 + size index).

- **Preparation.** Strands are encoded once (`sdk.encode`, v4-balanced). Reads are simulated once per scenario:
  - `clean`: shipped `clean` model, coverage 3.
  - `indel`: shipped `illumina-like` model with insertion = deletion = 1e-3 per base, coverage 10.
- **Runs.** Each tree runs `encode` of the same input and `decode` of the same reads, 2 workers, one fresh process per stage. Tree order alternates per size.
  - V10 tree: `/root/vnx-dna-lab/v10-rel` at tag v10.0.0 (c364264).
  - V11 tree: this branch.
- **Measured per stage:**
  - `ru_maxrss` of the stage process, read before the output is hashed (streamed);
  - process-tree RSS sampled from /proc every 0.2 s (includes pool workers);
  - `copies` = (peak − post-import baseline) / input bytes.
- **Correctness check.** Every decode output's SHA-256 is compared with the archive kept at preparation (`exact`).
- **Guards.**
  - `RLIMIT_AS` and a process-tree RSS kill at 12 GiB, plus `ulimit -v` 12 GiB on the job. Every run is under `tools/vnx-job` with a timeout.
  - The heaviest size (64 MiB) is refused while load1 ≥ 3.
- **Host.** 4C/8T, 31 GB, shared production VPS. Recorded load1 for these rows: **6.06–8.31**, because another work package was building concurrently. Wall times are therefore indicative only. RSS is the measured quantity.

## Results (`results/rss.jsonl`, slopes in `results/summary.json`)

Values are the stage process's `ru_maxrss` in MiB. The process-tree peak is in brackets. All 12 decodes are `exact`, with 0 false SUCCESS.

| stage / scenario | tree | 1 MiB | 4 MiB | 16 MiB | slope maxrss (tree) MiB per input MiB |
|---|---|---|---|---|---|
| encode / clean | V10 | 51 (119) | 53 (131) | 55 (129) | 0.21 (0.39) |
| encode / clean | V11 | 52 (123) | 54 (128) | 54 (130) | 0.12 (0.41) |
| decode / clean | V10 | 105 (268) | 134 (271) | 124 (290) | 0.67 (1.47) |
| decode / clean | V11 | 106 (268) | 135 (272) | 124 (275) | 0.61 (0.41) |
| decode / indel | V10 | 110 (269) | 111 (273) | 119 (277) | 0.65 (0.48) |
| decode / indel | V11 | 110 (272) | 119 (276) | 115 (272) | 0.17 (−0.06) |

**Reading.**

- Peak RSS is flat in input size for both trees: below 1.5 MiB of RSS per MiB of input on every series. The `copies` factor falls from about 10 (encode) and about 65 (decode) at 1 MiB to 0.8 and 5 at 16 MiB. The peak is a fixed working set, not a multiple of the input.
- V11 ≤ V10 on every slope except encode tree-peak: 0.41 vs 0.39, a difference of 0.02 MiB per MiB, within sampling noise (the per-point differences are ≤ 4 MiB).
- No regression. WP3 made no change aimed at peak RSS.
- Decode memory is bounded by one spill bucket (about 200 000 reads; `recovery/spill.py`): about 6 MiB of input at coverage 1, about 2 MiB at coverage 3. Below that, a single bucket holds the whole archive, which explains the 1 → 4 MiB step on decode / clean. The new slow test (`tests/v2/test_streaming_scale_v2.py::test_dna_pipeline_peak_memory_does_not_grow_with_input_size`) pins the plateau at test scale with 20 000-read buckets. The test fails if pass-2 memory follows the input instead of the bucket.

**64 MiB.** This size runs only at load1 < 3. Its rows are appended to `results/rss.jsonl` when the host allows. If the load never fell, there are no 64 MiB rows and the size is reported as NOT RUN.

## Bounded queues and cleanup audit (V11 WP3)

**Bounded (no defect):**

- Pass-1 decode, V4 and V6 encode, and archive build each keep a submission window of 2 × workers.
- Deferred recovery's untimed `pool.map` is bounded by one round's records, which come from one bucket or one orphan slice.
- Cluster consensus submits all batches, but its inputs are already resident and capped by `max_unplaced_reads`.

**Defects fixed (adfa4f6, tests `tests/v11/test_spill_cleanup.py`):**

- A pass-1 stop (recovery budget or cancel) left every spill file and the unplaced-read store open after the directory was removed. Their disk blocks stayed allocated while the caller held the exception.
- A stop in deferred recovery kept the orphan memory map.
- `Spill.__init__` leaked the handles already opened when an open failed part-way.

**Residual (not fixed):** a stop inside read clustering, which is opt-in (`read_clustering="fallback"`), can keep the unplaced-read memory map until the caller drops the exception, because the clustering frames hold views of it.

## Out of scope here

The 10 MB nanopore-like run and the candidate-pair budget (job #86) are deferred. The last result was EXPLICIT_FAILURE at 12ef9a9: the clustering budget was exceeded by 2,233 slots. The V10 candidate-pair rows are committed in `experiments/v10/mem/results/pairs.jsonl`.
