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

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:00:14Z; environment in `environment.json`; every trial in `results.json`.

| stage | value |
|---|---|
| archive_mb_s | 80.2 |
| channel_simulation_mbases_s | 11.34 |
| chunk_hash_mb_s | 422.1 |
| compression_mb_s | 246.4 |
| compression_ratio | 2.078 |
| decompression_mb_s | 735.9 |
| dna_encoding_mb_s | 4.23 |
| dna_encoding_strands_s | 1.058e+05 |
| dna_mapping_mb_s | 22.7 |
| dna_validation_mbases_s | 353.7 |
| encryption_mb_s | 1074 |
| frame_check_clean_frames_s | 1.292e+06 |
| inner_rs_decode_noisy_frames_s | 6.854e+04 |
| inner_rs_encode_mb_s | 71.5 |
| input_size | 8388608 |
| merkle_leaves_s | 8.847e+05 |
| outer_ecc_decode_mb_s_max_erasures | 4.2 |
| outer_ecc_encode_mb_s | 28 |
| peak_rss_mb | 484.7 |
| peak_rss_self_mb | 484.7 |
| peak_rss_workers_mb | 0 |
| profile | v4-balanced |
| sync_alignment_reads_s | 8700 |
| sync_inner_decode_reads_s | 7.271e+04 |

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
