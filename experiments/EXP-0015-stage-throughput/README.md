# EXP-0015-stage-throughput

**Purpose.** single-core throughput of every pipeline stage

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** Single-core throughput of each pipeline stage on the same deterministic data, in a fresh process.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0015-stage-throughput/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0015-stage-throughput            # re-run in scratch and compare deterministic fields
```

## Results

Commit `44d3ae932bd4896879fe517d4b7ab52157256da6`, 2026-10-03T02:20:10Z; environment in `environment.json`; every trial in `results.json`.

| stage | value |
|---|---|
| archive_mb_s | 78.8 |
| channel_simulation_mbases_s | 11.25 |
| chunk_hash_mb_s | 397.5 |
| compression_mb_s | 230.4 |
| compression_ratio | 2.078 |
| decompression_mb_s | 529.8 |
| dna_encoding_mb_s | 4.34 |
| dna_encoding_strands_s | 1.085e+05 |
| dna_mapping_mb_s | 23.8 |
| dna_validation_mbases_s | 376.3 |
| encryption_mb_s | 711.9 |
| frame_check_clean_frames_s | 1.243e+06 |
| inner_rs_decode_noisy_frames_s | 6.702e+04 |
| inner_rs_encode_mb_s | 70.7 |
| input_size | 8388608 |
| merkle_leaves_s | 8.618e+05 |
| outer_ecc_decode_mb_s_max_erasures | 3.5 |
| outer_ecc_encode_mb_s | 27.5 |
| peak_rss_mb | 467 |
| peak_rss_self_mb | 467 |
| peak_rss_workers_mb | 0 |
| profile | v4-balanced |
| sync_alignment_reads_s | 9520 |
| sync_inner_decode_reads_s | 7.097e+04 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
