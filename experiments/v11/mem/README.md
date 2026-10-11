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
  - The heaviest size (64 MiB) is refused if load1 ≥ 3 when it starts. It started at load1 1.85, 1.83 and 2.76, and that run's own preparation and decodes raised the per-stage load to 2.78–3.92. The first 64 MiB attempt refused its indel scenario on its own load; those two `NOT RUN` rows are kept in `rss.jsonl`, and the gate is now checked once per size.
- **Host.** 4C/8T, 31 GB, shared production VPS, with another work package building concurrently. Recorded per-stage load1 ranges from 2.78 to 8.31 (1–16 MiB rows: 6.06–8.31). Wall times are therefore indicative only. RSS is the measured quantity.

## Results (`results/rss.jsonl`, slopes in `results/summary.json`)

Values are the stage process's `ru_maxrss` in MiB. The process-tree peak is in brackets. The 64 MiB indel point is the mean of 2 runs per tree (V10 112 and 118; V11 124 and 115). All 18 decodes are `exact`, with 0 false SUCCESS, and no run came near the 12 GiB guard (largest tree peak 293 MiB).

| stage / scenario | tree | 1 MiB | 4 MiB | 16 MiB | 64 MiB | slope maxrss (tree), MiB per input MiB |
|---|---|---|---|---|---|---|
| encode / clean | V10 | 52 (120) | 54 (131) | 55 (129) | 54 (132) | 0.011 (0.111) |
| encode / clean | V11 | 52 (123) | 55 (129) | 55 (131) | 55 (132) | 0.017 (0.091) |
| decode / clean | V10 | 106 (269) | 134 (271) | 125 (290) | 137 (293) | 0.281 (0.347) |
| decode / clean | V11 | 107 (269) | 135 (273) | 125 (276) | 132 (280) | 0.193 (0.142) |
| decode / indel | V10 | 110 (270) | 111 (274) | 120 (278) | 116 (284) | 0.066 (0.197) |
| decode / indel | V11 | 111 (272) | 119 (276) | 116 (273) | 120 (281) | 0.087 (0.118) |

**Reading.**

- Peak RSS does not scale with input size in either tree. Every slope is below 0.35 MiB of RSS per MiB of input. From 1 MiB to 64 MiB, the `copies` factor falls from about 10 to 0.2 (encode) and from about 65 to 1.1–1.5 (decode). The peak is a fixed working set, not a multiple of the input.
- **V11 vs V10.** The tree-peak slope is lower for V11 on all three series. The maxrss slope is lower for decode / clean and higher by 0.006 (encode) and 0.021 (decode / indel) MiB per MiB, which is less than 2 MiB across 64 MiB. That is smaller than the run-to-run scatter: the two 64 MiB indel runs differ by 6 MiB (V10) and 9 MiB (V11), and the order between trees reversed on the repeat. No regression is detected, and none is claimed to be absent below that noise level. WP3 made no change aimed at peak RSS.
- Decode memory is bounded by one spill bucket (about 200 000 reads; `recovery/spill.py`): about 6 MiB of input at coverage 1, about 2 MiB at coverage 3. Below that, a single bucket holds the whole archive, which explains the 1 → 4 MiB step on decode / clean. The new slow test (`tests/v2/test_streaming_scale_v2.py::test_dna_pipeline_peak_memory_does_not_grow_with_input_size`) pins the plateau at test scale with 20 000-read buckets. The test fails if pass-2 memory follows the input instead of the bucket.

## Bounded queues and cleanup audit (V11 WP3)

**Bounded (no defect):**

- Pass-1 decode, V4 and V6 encode, and archive build each keep a submission window of 2 × workers.
- Deferred recovery's untimed `pool.map` is bounded by one round's records, which come from one bucket or one orphan slice.
- Cluster consensus submits all batches, but its inputs are already resident and capped by `max_unplaced_reads`.

**Defects fixed (adfa4f6; tests in `tests/v11/test_spill_cleanup.py`):**

- A pass-1 stop (recovery budget or cancel) left every spill file and the unplaced-read store open after the directory was removed. Their disk blocks stayed allocated while the caller held the exception.
- A stop in deferred recovery kept the orphan memory map.
- `Spill.__init__` leaked the handles already opened when an open failed part-way.

**Also fixed (4536787):** a stop inside read clustering, which is opt-in (`read_clustering="fallback"`), kept the unplaced-read memory map through the clustering frames that held views of it. The stage now clears those finished frames on an exception.

## Out of scope here

The 10 MB nanopore-like run and the candidate-pair budget (job #86) are deferred. The last result was EXPLICIT_FAILURE at 12ef9a9: the clustering budget was exceeded by 2,233 slots. The V10 candidate-pair rows are committed in `experiments/v10/mem/results/pairs.jsonl`.
