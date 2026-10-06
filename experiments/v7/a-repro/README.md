# A-REPRO: independent rerun of the A-CONS and A-PAR held-out experiments (EXPERIMENTAL, SIMULATED)

Question: do the committed held-out results reproduce from a clean checkout with the registered protocols unchanged?

| item | value |
|---|---|
| Software | clean detached checkout of `build/v7-sprint` at `0a05444697cc78216fd4702757f0a34fd3a31aab` (later than the original runs: includes the A2 native cluster kernel merge `93d491a` and everything since) |
| Native | all four kernels built (`python -m vnxdna.native --require-native` → `all_native: true`) |
| Commands | `experiments/v7/a-cons/run.py heldout --jobs 4`, then `experiments/v7/a-par/run.py heldout --jobs 4`, both into empty results files; the protocols and runners are unchanged |
| Seeds, parameters | as registered: seeds 82060-82099 × coverage 3/5/10; A-CONS arms wildcard/full on v4-balanced; A-PAR v7-lowcov with full consensus |
| Machine | x86_64, 8 threads, Linux 6.8.0-146, Python 3.12, NumPy 2.5.3; shared host, load average 2.9-4.3 during the runs |
| Started / ended (UTC) | A-CONS 2026-10-06T13:15:25Z-13:31:29Z (16 min 04 s, 240 decodes); A-PAR 13:31:29Z-13:52:02Z (20 min 33 s, 120 decodes) |
| Peak RSS (parent, `time -v`) | 346 MiB / 356 MiB |

## Result: REPRODUCED

| experiment | rows committed / rerun | rows differing | SHA-256 of the deterministic projection (committed = rerun) |
|---|---|---|---|
| A-CONS held-out (`a-cons/results/heldout.jsonl`, 607f1e7) | 240 / 240 | 0 | `d54cc48e5c597777b6444b1ce8a0f56e14bca5b00a3429e939a0dbd1dd6adce2` |
| A-PAR held-out (`a-par/results/heldout.jsonl`, 39e180a) | 120 / 120 | 0 | `0ab45b78518ff67d57f821c13092a841f3073015979a9a502ca89e07c1f05ee5` |

Every deterministic field is identical in every row: outcome, SHA-256 match, false success, funnel counts, consensus-
valid strands, data and superblock frames, false frames, decodable rows, strands written. The raw files are **not**
byte-identical, and cannot be: each row also records its own `decode_seconds`, `case_seconds`, `peak_rss_bytes` and
`load1`, which are measurements of the run. The projection hash is SHA-256 over the rows sorted by key (`case`,`arm` /
`case`,`profile`) with those four fields removed, one `json.dumps(sort_keys=True)` line each.

Consequence: the A-CONS and A-PAR held-out results reproduce exactly on the current code, so the native kernel merge
and later commits did not change any decode outcome. These seeds were used for acceptance of Phase 1, so they are
**not** confirmatory; the confirmatory test is A-CONF (seeds 82100-82139, `experiments/v7/a-conf/`).
