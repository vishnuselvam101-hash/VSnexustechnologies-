# VNX-DNA V13 — completion report (streaming engine, streams, volume; release candidate)

Version 13.0.0 on branch `build/v13-final` (release candidate; the tag, the merge into `main` and the push are the
founder's). Every channel result in this report is SIMULATED (VNX-DNA channel simulator on one 4C/8T Linux host); no
DNA was synthesised, stored or sequenced. Numbers come from the committed files named next to them.

## Release status

- Scope delivered (founder directive 2026-10-13 and its additions; docs/V13_PLAN.md, docs/V13_ENGINE_ARCHITECTURE.md):
  the pass-1 levers of the Python path (flat data path, aligner ABI 4, orientation kernel ABI 2–4, bounded
  reverse-complement retry, marker-anchored speculative projection, native frame packing, parser prefetch), the
  **streaming decode engine** (Rust core with the fuzzed C kernels linked in, address scheduler, persistent thread
  pool, memory-mapped IO) behind a **fallback ladder** `stream -> v13 -> reference`, **streaming encode/decode** of
  byte streams of any length with bounded memory (`vnx.stream/1`), a **volume** with journal, write-back, LRU extent
  cache, crash-consistent flush and scrub (`vnx.volume/1`), a **decode result cache** at the SDK layer (off by
  default), one configuration surface (options, SDK keys, environment, CLI), the engine in the kernel registry,
  `native_status`, CI, documentation, and the A/B + A/A benchmark harness with its evidence.
- **The 10× end-to-end target is not reached.** Measured against build/v12 6bf7749 on the pre-declared workloads at
  the final engine (docs/V13_BENCHMARK_REPORT.md, `experiments/v13/bench/ab-final2`): 2.89× [2.73, 2.99] on the
  4 MiB indel decode at 1 worker and 2.20× on the clean decode at 4 workers (Tier 1); 2.11× at 2 workers (Tier 1
  missed by the CI lower bound); 1.37–1.76× on the other decode cases (4 and 8 workers, reverse complement,
  dropout, substitution, 64 KiB, 16 MiB); encode unchanged. Tier 2 (5×) and Tier 3 (10×) are reached on no workload;
  the stage budget that explains the floor is in the benchmark report §3.1. An earlier run (`ab-final`) showed 5.23×
  at 1 worker with a scheduler that could skip a distinct strand on a shared payload window; that defect was fixed
  (f7586b9) and the slower, correct engine is what this release measures.
- **Not in this release:** a native pass 2 and layout probe (the serial remainder that caps the ratio at 4–8
  workers); a separate adaptive-recovery rung (the ladder's `v13` rung runs the full recovery instead); a linear
  pool-mode stream decode (today O(extents × pool), see Limitations); the web console's node tests were not run in
  the lab (no node_modules; the contracts file was updated for the new result kinds and status). Archive, container
  and strand formats are unchanged from 12.0.0 (no new format version); the stream manifest and the volume directory
  are new, versioned, opt-in containers of ordinary archives. Decode output is byte-identical to 6bf7749 on every
  benchmark seed.

## Implementation

| item | commits | where |
|---|---|---|
| Plan, verified V12 baseline, pre-declared workloads, V13 A/B harness | b0897bd, 10d2f51 | docs/V13_PLAN.md, experiments/v13/bench/ |
| Flat pass-1 data path, aligner ABI 4 (`vnx_align_batch_sel_lv`) | e4fd52c | v5/native/align.c, native/align.py, recovery/pass1.py |
| `nt_to_bytes` fast form, row ids by lexsort | 56b7c1b, bbdeb43 | dnaenc/mapping.py, recovery/consensus.py |
| Bounded reverse-complement retry (orient ABI 2) | d34cc89 | native/c/orient.c, recovery/pass1.py |
| Marker-anchored speculative projection (orient ABI 3) | 59e6463, 73bd641 | native/c/orient.c, recovery/pass1.py |
| `decode_frames` glue, native frame packing (orient ABI 4), parser prefetch | d5b2689, 20d2983, b1fa07d, 60e1e54 | dnaenc/frame4.py, native/c/orient.c, pipeline/decode.py |
| Engine design | 65fed82 | docs/V13_ENGINE_ARCHITECTURE.md |
| Streaming decode engine (Rust core, scheduler, pool, ladder) | 91e601d, c44b7b3, 0093376, 979de62 | src/vnxdna/native/rust/engine/, native/engine.py, recovery/engine.py, pipeline/decode.py |
| Engine in the kernel registry, configuration surface, crate unit tests, CI | 65047ff | native/_loader.py, native/__init__.py, recovery/options.py, cli, sdk |
| Streaming encode/decode (`vnx.stream/1`) | f76986e | pipeline/stream.py |
| Layering fix (binding takes plain parameters) | 08ad5c1 | native/engine.py, recovery/engine.py |
| Volume (`vnx.volume/1`: journal, write-back, LRU cache, scrub, lock) | 22bbf42 | src/vnxdna/volume/ |
| SDK and CLI for streams, volumes, result cache | f561449 | sdk/, cli/ |
| Documentation, engine sanitizer script, stream/volume bench, V3 SIGTERM test race | 8c7ea15 | docs/, benchmarks/v13/native_engine/, experiments/v13/bench/ |
| Single-run evidence, A/A wrapper, volume key via options | a798d89 | experiments/v13/bench/single-runs-0093376, aa.py |
| A/B bench + A/A noise band (13 scenarios each) | 6117262 | experiments/v13/bench/ab-final, baseline-aa |
| Stream/volume rates, sanitizer-script fix, pool-decode limitation | 29f8cb9 | experiments/v13/bench/stream-volume, benchmarks/v13/native_engine/sanitizers.sh, docs/STREAMING.md |
| Scheduler fix (claim split-off, corroborated skips), review findings (cache key and store, journal damage, engine binding checks, manifest validation, volume create/open, cancel path), tests on the V13 rung | f7586b9 | engine.rs, sched.rs, sdk/cache.py, volume/, pipeline/stream.py, native/engine.py, pipeline/decode.py, tests/ |
| Benchmark report (A/B at the fixed engine), completion report, README, CHANGELOG | (final commit) | docs/ |

## Validation

- Full suite at f7586b9 (the final code; the commits after it change docs and benchmark evidence only):
  **4,454 passed, 4 skipped, 0 failed** (498 s, `vnx-lab full`, 6 workers in the lab's memory-limited systemd scope,
  every kernel and the engine native; lab log `results/full-suite-f7586b9-20261018-0812.txt`). Skips: the
  aligner-level test for a level this CPU has and three opt-in install tests, as in V12. Collected 4,452 tests (v12.0.0 release gate at f712d91: 4,142 passed / 4 skipped; the V13 baseline
  count is the session-5 run at 521adb0, 4,209 passed / 4 skipped, docs/V13_PLAN.md; the count never drops below it). No test was weakened or removed; the V4/V5/V6 golden archives still decode (compatibility tests).
- `tests/v13/`: 237 tests (engine: scanner vs reference parser on randomized inputs, pass-1 record equality with the
  scheduler off, scheduler determinism across thread counts, byte-identical whole decodes, ladder rungs and fallbacks,
  wall-time budget, configuration surface; streams; volume incl. crash-consistency and scrub; SDK/CLI integration and
  the result cache; the pass-1 levers of the Python path with their reference forms).
- Engine crate: `cargo test` 16 passed with `RUSTFLAGS=-D warnings`; a debug-assertions + overflow-checks build of the
  library passes the engine tests (37); gcc ASan + UBSan build of the C kernels linked into the engine passes the
  engine tests with the sanitized library loaded; canary out-of-bounds write caught
  (`benchmarks/v13/native_engine/sanitizers.sh`; lab results `results/v13/engine-sanitizers-f7586b9.txt`, run on
  the final engine: overall PASS).
  Earlier kernels: align ABI 4 and orient ABI 2–4 sanitizer and differential-fuzz runs are listed in CHANGELOG 13.0.0
  and docs/V13_ARCHITECTURE.md (orient fuzz 125,273 cases, 0 mismatches).
- Fuzz smoke: `tests/fuzz` 58 passed (hypothesis campaigns, native fuzz smoke, regression corpus).
- Reviews before the final commit: an adversarial code review of the src diff (four MEDIUM findings: cache hit
  bypassing the key check, cache store hashing its own copy, journal mid-file damage treated as a torn tail, engine
  binding without dtype/length checks; four LOW) and an evidence audit of every number in the reports (three
  rounding/range corrections). All fixed in f7586b9 with tests (CHANGELOG 13.0.0 "Fixed").
- The first full suite at 29f8cb9 (46 failures, all in tests that compare a decode with a V13-path-only option
  against a default decode) exposed a real engine defect besides the test-side rung mismatches: on a clean
  one-reading-per-strand pool with stripes and column parity the scheduler skipped a distinct strand as a copy
  (symbol recovered by the outer code, output correct). Fixed in f7586b9 (claim split-off, corroborated skip rule),
  regression test `test_distinct_strands_sharing_payload_windows_are_all_attempted`.
- `ruff check` and `mypy` clean (CI configuration), release build of the engine at HEAD byte-identical to the library
  the rows of the earlier run loaded (`experiments/v13/bench/ab-final/operator-notes.txt`); the final run `ab-final2`
  reports no stale library on any row.

## Performance (SIMULATED; docs/V13_BENCHMARK_REPORT.md, experiments/v13/bench/ab-final2 vs baseline-aa)

Median ratio build/v12 seconds / V13 seconds at f7586b9, 95 % CI (min–max at n = 5; 3 seeds for 16 MiB), paired per
seed, outputs identical, 0 false successes, every decode scenario faster beyond the A/A band (`ab-final2`):

| scenario | ratio [CI95] | tier (1 ≥ 2×, 2 ≥ 5×, 3 ≥ 10×) |
|---|---|---|
| 4 MiB indel, 1 worker | 2.885 [2.732, 2.989] | 1 |
| 4 MiB indel, 2 / 4 / 8 workers | 2.113 [1.991, 2.313] / 1.704 / 1.374 | none (w2 misses Tier 1 by its lower bound) |
| reverse complement 50 %, 4 workers | 1.758 [1.606, 1.838] | none |
| dropout 5 %, 4 workers | 1.374 [1.259, 1.433] | none |
| clean coverage 3, 4 workers | 2.200 [2.095, 2.372] | 1 |
| substitution 2 %, 4 workers | 1.476 [1.173, 1.576] | none |
| 64 KiB indel, 1 worker | 1.552 [1.529, 1.639] | none |
| 16 MiB indel, 4 workers | 1.723 [1.593, 1.755] | none |
| encode 1 MiB w1, 4 MiB w1, 4 MiB w4 | 0.998 / 1.010 / 0.920 | unchanged |

Decode RSS is ~100 MiB at every worker count (V12: 101–733 MiB). Disk-style rates of the streaming codec and the
volume (`experiments/v13/bench/stream-volume`, software codec rates, never physical DNA throughput): stream encode
2.6–2.9 MB/s at 1 worker and 7.7–9.2 MB/s at 4; stream decode by range 3.4–4.1 MB/s; volume write 3.1 MB/s, cold
sequential read 4.2–4.3 MB/s, cached read 1.57–1.75 GB/s; 4 KiB random read ~250 ms cold (one extent decode) and
~0.02 ms hot.

## Decision: one verified copy per address (the address scheduler)

The engine attempts a read only while its address is not yet verified; further copies of a verified address are
skipped (`engine_dedup=True`, the default; `--no-engine-dedup` attempts every read and reproduces the V13 Python
path's pass-1 records). Decided on the 4 MiB indel fixture at 0093376 (`experiments/v13/bench/single-runs-0093376`:
1.75 s with the scheduler, 4.12 s without, V12 9.39 s). The full suite then exposed that this scheduler could skip a
distinct strand that shared a payload window with a verified one (CHANGELOG 13.0.0 "Fixed"); the corrected rule
(f7586b9) skips a read only when its raw header names a verified address and a window corroborates, or when every
usable window of an unreadable header matches, and a verified claim splits off members reading another address. At
the final engine the same fixture decodes in 3.05 s with the scheduler and 4.71 s on the V13 path
(`single-runs-f7586b9`); the decision stands on these numbers. Outputs were byte-identical and SUCCESS on every
fixture, test pool and benchmark seed. Risk accepted: a frame accepted wrongly needs an inner-RS miscorrection and a
CRC-32 match (about 2^-32 per corrected frame); such a frame is still caught by the whole-container SHA-256, after
which the ladder re-decodes on the V13 path, which votes over all copies. A copy wrongly skipped can only be a single
noisy reading whose raw header happens to read exactly as a verified address (not a whole strand, whose other
readings are attempted). Recorded in the lab and company decision logs (2026-10-17; amended 2026-10-18).

## Limitations and open risks

- 10× not reached (above); the next levers are structural: native pass 2 and layout probe, grouping inside the thread
  pool, a cheaper prep pass (benchmark report §3.1).
- Pool-mode stream decode (one shuffled pool, extents found by archive ID) is O(extents × pool size): 6.1 s for a
  4 MiB stream, 98 s for 16 MiB at 1 worker. The range path and the volume are linear. Fix: one partition pass by
  archive tag (planned, not in V13). The pool path also needs distinct 16-bit archive tags across extents and refuses
  otherwise (`ARCHIVE_TAG_AMBIGUOUS`).
- The scheduler's fingerprint tables are fixed-size and lossy (24 MiB); on very large pools evictions make the engine
  attempt more reads (slower, never wrong).
- The engine's thread pool does not scale the serial remainder; the ratio over V12 falls with the worker count
  (2.89× at 1 worker, 1.37× at 8; benchmark report §3).
- The engine rung emits no `pass1_progress` observer events (pass 1 is one native call); `decode_start`,
  `pass1_end`, `pass2_end`, `verify` and `decode_end` are emitted as before. The V13 rung (`engine=v13`) keeps the
  per-batch progress events.
- The scheduler's skip evidence is corroborated (header key + window, or every usable window), so a copy with a
  damaged header or damaged windows is attempted rather than skipped: slower on noisy pools, never fewer symbols.
- The web console's node tests were not run in the lab.
- The host's governor refused the full suite's admission on stale swap; the suite ran in the lab's memory-limited
  systemd scope instead (same tests, same workers).

## Next

1. A scheduler that tells a noisy copy from a neighbouring strand without the DP (a header check that survives an
   indel, or the marker-anchored projection before the skip decision): the pre-fix engine's 5.2× at 1 worker shows
   what safe skipping of those copies is worth.
2. One-pass partition of a shuffled pool by archive tag before the per-extent decodes (linear pool-mode stream decode).
3. Native pass 2 and layout probe; grouping inside the engine pool (the Tier 2/3 levers).
4. A separate adaptive-recovery rung with an escalating budget (today the `v13` rung runs the full recovery).
5. The low-coverage and nanopore-like scope deferred since V12.
