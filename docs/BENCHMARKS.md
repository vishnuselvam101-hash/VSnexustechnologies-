# Benchmarks

All numbers below were **measured** by `vnx-dna benchmark --sizes 1K,10K,100K,1M,10M --repeats 3` (median of 3 timed runs per stage)
at commit `bb33372cedf018e26013e9e2a10cca6eaffe9d01` (dirty=False), vnx-dna 1.0.0. Raw data: `research/results/benchmarks.json`.
Re-run on your machine with the same command; absolute numbers will differ.

## Machine and software

- Linux-6.8.0-139-generic-x86_64-with-glibc2.39, x86_64, 8 logical CPUs (processor string: 'x86_64'); single-threaded Python
- Python 3.12.3 (CPython); cryptography 46.0.7, numpy 2.5.3, reedsolo unknown, zstandard 0.25.0
- profile: default `StoreOptions` (64+16 outer, 40 B payload, 8 B inner parity, zstd 9, chunk 262,144 B), **encrypted** (AES-256-GCM)
- input: deterministic, half PRNG bytes (incompressible) + half repeated text; damaged-decode stages use the channel `substitution 0.2 %, dropout 2 %, shuffled`
- memory: peak bytes traced by `tracemalloc` (Python + numpy allocations) in a separate untimed run; not RSS

## Storage density (all overheads included)

| input | stored (compressed+encrypted) | strands | DNA bases | bases / input byte | net bits / base | outer parity bytes | frame overhead bytes |
|---|---|---|---|---|---|---|---|
| 1,000 | 612 | 128 | 31,232 | 31.232 | 0.256 | 640 | 2,688 |
| 10,000 | 5,116 | 256 | 62,464 | 6.246 | 1.281 | 1,280 | 5,376 |
| 100,000 | 50,435 | 1,677 | 409,188 | 4.092 | 1.955 | 12,800 | 35,217 |
| 1,000,000 | 502,148 | 15,884 | 3,875,696 | 3.876 | 2.064 | 127,360 | 333,564 |
| 10,000,000 | 5,003,118 | 157,537 | 38,439,028 | 3.844 | 2.081 | 1,269,760 | 3,308,277 |

Small inputs are dominated by fixed costs (80 metadata strands for the manifest, one shortened stripe with 16 parity strands). For large inputs the density converges to ≈3.8 bases per input byte on this 50 %-compressible data. Without compression (incompressible data) the geometry gives 244 nt per 40 payload bytes × 80/64 outer overhead ≈ 7.6 bases per stored byte; compression gain is reported separately and is not a DNA-storage efficiency.

## Stage timings

```
vnx-dna benchmark (median of 3 runs; seed 1; python 3.12.3, x86_64, 8 CPUs)

input 1,000 B -> stored 612 B -> 128 strands, 31,232 nt (31.232 nt/B)
  stage                                            wall s      cpu s      MB/s  peak MiB
  compression_zstd9                                0.0000     0.0000     20.81       0.0
  dna_decode_clean                                 0.0011     0.0011      0.54       0.1
  dna_decode_damaged                               0.0128     0.0128      0.05       0.2
  dna_encode                                       0.0134     0.0134      0.05       0.6
  ecc_outer_decode_max_erasures                    0.0008     0.0008      3.02       0.4
  ecc_outer_encode                                 0.0006     0.0006      4.45       0.5
  encryption_aes256gcm                             0.0000     0.0000     64.16       0.0
  end_to_end_store_encode_simulate_recover         0.0304     0.0304      0.03       1.4
  random_access_one_chunk_from_reads               0.0004     0.0004      2.28       0.0
  recovery_outer_decrypt_decompress_verify         0.0005     0.0005      2.08       0.0
  restore_from_container                           0.0001     0.0001     19.61       0.0
  simulate_channel                                 0.0034     0.0034      0.18       1.3
  store_container                                  0.0010     0.0010      1.03       0.0

input 10,000 B -> stored 5,116 B -> 256 strands, 62,464 nt (6.246 nt/B)
  stage                                            wall s      cpu s      MB/s  peak MiB
  compression_zstd9                                0.0000     0.0000    227.14       0.0
  dna_decode_clean                                 0.0019     0.0019      2.75       0.3
  dna_decode_damaged                               0.0264     0.0264      0.19       0.3
  dna_encode                                       0.0157     0.0157      0.32       1.1
  ecc_outer_decode_max_erasures                    0.0013     0.0013      3.86       0.4
  ecc_outer_encode                                 0.0006     0.0006      8.35       0.5
  encryption_aes256gcm                             0.0000     0.0000    721.06       0.0
  end_to_end_store_encode_simulate_recover         0.0513     0.0513      0.19       2.8
  random_access_one_chunk_from_reads               0.0013     0.0013      7.69       0.3
  recovery_outer_decrypt_decompress_verify         0.0014     0.0014      7.05       0.3
  restore_from_container                           0.0001     0.0001    103.69       0.0
  simulate_channel                                 0.0061     0.0061      0.83       2.6
  store_container                                  0.0008     0.0008     12.93       0.0

input 100,000 B -> stored 50,435 B -> 1,677 strands, 409,188 nt (4.092 nt/B)
  stage                                            wall s      cpu s      MB/s  peak MiB
  compression_zstd9                                0.0001     0.0001    929.34       0.1
  dna_decode_clean                                 0.0115     0.0115      4.39       1.9
  dna_decode_damaged                               0.1768     0.1768      0.29       1.8
  dna_encode                                       0.0560     0.0560      0.90       6.8
  ecc_outer_decode_max_erasures                    0.0131     0.0131      3.91       0.5
  ecc_outer_encode                                 0.0056     0.0056      9.15       1.2
  encryption_aes256gcm                             0.0000     0.0000   2069.99       0.0
  end_to_end_store_encode_simulate_recover         0.2841     0.2841      0.35      18.0
  random_access_one_chunk_from_reads               0.0109     0.0109      9.20       0.4
  recovery_outer_decrypt_decompress_verify         0.0107     0.0107      9.35       0.4
  restore_from_container                           0.0007     0.0007    143.09       0.1
  simulate_channel                                 0.0418     0.0418      1.21      17.4
  store_container                                  0.0016     0.0016     63.40       0.2

input 1,000,000 B -> stored 502,148 B -> 15,884 strands, 3,875,696 nt (3.876 nt/B)
  stage                                            wall s      cpu s      MB/s  peak MiB
  compression_zstd9                                0.0060     0.0060    167.92       1.0
  dna_decode_clean                                 0.1266     0.1266      3.97      15.9
  dna_decode_damaged                               1.6042     1.6041      0.31      16.0
  dna_encode                                       0.3335     0.3335      1.51      36.2
  ecc_outer_decode_max_erasures                    0.1455     0.1454      3.47       0.9
  ecc_outer_encode                                 0.0537     0.0536      9.40       8.3
  encryption_aes256gcm                             0.0003     0.0003   1832.80       0.5
  end_to_end_store_encode_simulate_recover         2.3199     2.3198      0.43      72.1
  random_access_one_chunk_from_reads               0.0012     0.0012    222.88       0.3
  recovery_outer_decrypt_decompress_verify         0.0988     0.0988     10.12       1.9
  restore_from_container                           0.0067     0.0067    148.21       2.4
  simulate_channel                                 0.3494     0.3494      1.44      66.1
  store_container                                  0.0097     0.0097    102.73       1.2

input 10,000,000 B -> stored 5,003,118 B -> 157,537 strands, 38,439,028 nt (3.844 nt/B)
  stage                                            wall s      cpu s      MB/s  peak MiB
  compression_zstd9                                0.0126     0.0126    795.46       9.6
  dna_decode_clean                                 1.7027     1.7026      2.94     157.2
  dna_decode_damaged                              16.8223    16.8201      0.30     165.7
  dna_encode                                       2.6571     2.6569      1.88      92.7
  ecc_outer_decode_max_erasures                    1.2210     1.2209      4.10       5.7
  ecc_outer_encode                                 0.5126     0.5125      9.76       9.6
  encryption_aes256gcm                             0.0024     0.0024   2127.57       4.8
  end_to_end_store_encode_simulate_recover        24.0350    24.0319      0.42     268.2
  random_access_one_chunk_from_reads               0.0054     0.0054     48.85       0.3
  recovery_outer_decrypt_decompress_verify         1.0455     1.0453      9.56      19.1
  restore_from_container                           0.0691     0.0692    144.62      23.9
  simulate_channel                                 3.2275     3.2267      1.55     197.1
  store_container                                  0.0923     0.0923    108.38       9.7
```

## Interpretation

- **Bottleneck:** decoding *damaged* reads (`dna_decode_damaged`, ≈0.3 MB/s of stored data) — every read that fails its CRC goes through a per-read `reedsolo` errors-and-erasures decode. Clean reads decode at ≈3–4 MB/s. A vectorized inner decoder is the top V1.x performance item (ROADMAP).
- Outer erasure decoding with the **maximum** 16 erasures in *every* stripe runs at ≈4 MB/s; encoding at ≈9.5 MB/s.
- Compression and AES-GCM are negligible (hundreds of MB/s to GB/s).
- Random access to one chunk from already-scanned reads is proportional to that chunk's stripes, not the archive (0.005 s vs 1.05 s for full recovery at 10 MB).
- End-to-end (store → encode → simulate → scan → recover) at 10 MB: ≈24 s, peak traced memory ≈270 MiB (≈27× input). Memory was reduced from 1.6 GiB by batching (commit `095e52b`).
- These are single-run-per-repeat medians of 3 on one machine: they indicate scale, not statistically rigorous performance claims.

## GPU

Not implemented. The CPU reference is fast enough for the sizes tested, the dominant cost is branchy per-read RS decoding, and correctness must never depend on an accelerator. Revisit only if profiling on target workloads says otherwise.
