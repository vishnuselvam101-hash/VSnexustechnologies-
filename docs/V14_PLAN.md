# VNX-DNA V14 plan: linear pool decoding, native pass 2, safe scheduler, volume reliability, benchmark suite

Written at the end of Phase 0 (repository audit and baseline reproduction) and committed before any optimisation is
measured. It fixes the baseline revisions, the environment, the pre-registered workloads with their goals, the
correctness gates, the workstreams and their dependencies, the benchmark method with its stop conditions, and the
baseline claims this host could or could not reproduce. Every channel result in this repository is SIMULATED (the
VNX-DNA channel simulator); no DNA was synthesised, stored or sequenced. V14 does not implement V15 (see
docs/V15_FINALIZATION_PLAN.md).

## 1. Baseline revisions and environment (Phase 0, 2026-10-18)

| item | verified value |
|---|---|
| V13 baseline (the code V14 changes) | `origin/main` = tag `v13.0.0` = **58171b8** (merge of PR #19 2064993 + CITATION bump; V13 code f7586b9, docs 9e2e0ac). Pristine checkout `/root/vnx-dna-lab/v14-base` (detached, every kernel and the engine built in place; the engine library is byte-identical to the V14 tree's build) |
| V12 baseline (the tier denominator, unchanged from V13) | `build/v12` = **6bf7749**, pristine checkout `/root/vnx-dna-lab/v13-base` (all eight kernels native; V12 has no engine) |
| V14 branch | `build/v14` from 58171b8, worktree `/root/vnx-dna-lab/v14` |
| toolchains | Python 3.12.3 (lab venv: numpy, pytest, pytest-xdist, hypothesis, ruff, mypy), gcc 13.3 / clang 18, rustc/cargo 1.75 (`--offline --locked`, std only, no crates), node 24.21 (web console; `node_modules` absent in the lab, `npm ci` needs the registry) |
| host | Intel Xeon Gold 6240, 4 physical cores / 8 threads visible, ~31 GiB RAM, Linux 6.8.0-146, Ubuntu 24.04. Heavy jobs run in the lab's memory-limited systemd scope (`VNX_LAB_GOVERN=systemd`: MemoryMax 12 GiB for the suite, 8 GiB for benchmarks, no CPU quota); the governor CLI refuses admission on stale swap (2.6 GB used while >25 GB RAM is free), as in V13 |
| build/test commands | `python -m vnxdna.<kernel> build` per kernel (`v5.native_alignment`, `v6.native_reads`, `v6.native_rs`, `native.cluster|strand|orient|integrity|outerlist`), `python -m vnxdna.native.engine build` (cargo, in place); `python -m vnxdna.native` lists backends; `pytest -o addopts= -p no:cacheprovider -n 6`; `ruff check src tests research`; `mypy`; `cargo test --release --offline --locked`; CI (`.github/workflows/ci.yml`): the same plus `tools/sanitizers.sh`, `tools/msan.sh`, `tests/fuzz`, `python -m vnxdna.native --require-native`, `tools/check_doc_links.py`, web `npm ci && npm run typecheck && npm run build` |
| full suite at 58171b8 (this host) | **4,454 passed / 4 skipped / 0 failed**, 434.5 s, 6 xdist workers, systemd scope (the V13 release gate, reproduced; the 4 skips are the 3 opt-in install tests and the CPU-level test). Lab log `results/full-suite-58171b8-20261018-1021.txt` (not in the repository); recorded in [V14_BASELINE.md](V14_BASELINE.md) |
| evidence available | `experiments/v13/bench/{ab-final2, baseline-aa, single-runs-f7586b9, single-runs-0093376, stream-volume, ab-final}` (raw rows, summaries, READMEs); lab logs `results/v13/*` (sanitizers, profiles) |

### 1.1 Report-derived V13 baselines and what this host reproduces

The founder's brief lists report-derived baselines. Each one is either reproduced here (Phase 0 C, same harness, same
fixtures, SIMULATED) or marked unverified:

| claim (docs/V13_BENCHMARK_REPORT.md) | Phase 0 reproduction at 58171b8 vs 6bf7749 | status |
|---|---|---|
| indel-w1 2.885x [2.732, 2.989] (v12 8.899 s, v13 3.121 s) | 2.811x [2.769, 2.968] (v12 8.921 s, v13 3.174 s) | reproduced (CIs overlap) |
| indel-w4 1.704x [1.644, 1.770] (3.169 s / 1.855 s) | 1.741x [1.688, 2.115] (2.847 s / 1.659 s) | reproduced (CIs overlap) |
| clean-w4 2.200x [2.095, 2.372] (1.321 s / 0.600 s) | 2.257x [2.010, 2.427] (1.301 s / 0.582 s) | reproduced (CIs overlap) |
| decode RSS ~100 MiB at every worker count | V13 tree peak RSS median 99.9 / 100.9 / 98.8 MiB (indel-w1 / indel-w4 / clean-w4); V12 105.5 / 415.9 / 381.3 MiB | reproduced |
| pool-mode stream decode O(extents x pool): 6.1 s (4 MiB) / 98 s (16 MiB) at 1 worker | 4.45 s (4) / 16.83 s (8) / 63.69 s (16 extents of 1 MiB), x3.78 per doubling (`experiments/v14/bench/phase0-pool/`) | scaling reproduced; absolute times differ (other harness) |
| the 5.23x of `ab-final` (pre-fix scheduler) | not reproduced on purpose: it measured a scheduler that skipped distinct strands; it is not a V13 result and is never used as a baseline | superseded |
| engine sanitizers PASS, cargo test 16, fuzz 58 | re-run in Phase 6 of V14 on the V14 code (not re-run on the baseline) | unverified at baseline |
| web console node tests | never run in V13 (no node_modules); V14 workstream F runs them or records why not | unverified |

Every cell of this table is filled from the committed Phase 0 runs; nothing in it is estimated.

### 1.2 Audit findings (what is actually in the tree)

1. **Pool-path stream decode is O(extents x pool) by construction.** `pipeline/stream.py::decode_stream` calls
   `decode_extent` once per extent; each call runs `decode_reads` on the whole pool with `expect_archive_id` and
   `archive_tag` set. Pass 1 (engine or Python) processes every read of every archive on every call; only pass 2
   filters by tag (`recovery/outer.py::_bucket_consensus`: `acc["tag"] == tag`; `_decode_superblock` picks the tag).
   Pass 1 does not depend on the tag options (`recovery/engine.py::params_for` ignores them), so one pass 1 over the
   pool followed by a per-tag partition of the spill and one pass 2 per extent on its own partition sees, extent by
   extent, exactly the records the per-extent decode sees. That is the basis of workstream A.
2. **Frame header** (`dnaenc/frame4.py`, docs/VNX4_FORMAT.md §8): byte 0 scrambler variant (clear), byte 1
   `(4 << 4) | kind`, bytes 2–3 archive tag (first two bytes of the 16-byte archive ID), bytes 4–7 group, bytes 8–9
   symbol, payload, CRC-32, inner RS parity. The raw (uncorrected) header is readable only when no indel precedes it;
   a substitution in bytes 2–3 reads as another tag. Verified records (inner RS + CRC) carry the corrected tag; pending
   records carry the raw one. A 16-bit tag is not unique in general: the stream manifest refuses the pool path when two
   extents share a tag (`ARCHIVE_TAG_AMBIGUOUS`), and workstream A keeps that refusal.
3. **Scheduler (engine `sched.rs`/`engine.rs`, rule of f7586b9)**: a read is skipped only with a readable raw header
   naming a verified address *and* a matching 24-nt window, or, with an unreadable header, when every usable window
   (two or more) matches; a claim that verifies address K splits off members whose header reads another address.
   The pre-fix rule (window alone, no split-off) is the one that produced 5.23x and skipped distinct strands; it must
   not return. Counters in `report["engine"]["pass1"]` (`attempted`, `skipped`, `cheap_attempts`, `full_attempts`,
   `split_groups`, `ns_*`).
4. **Serial remainder** (single runs at f7586b9): w1 indel 3.047 s = engine pass 1 2.689 (scan 0.10, prep 0.31,
   attempts 2.20 = cheap 1.06 + full DP 0.75 + grouping 0.21, spill 0.06) + pass 2 0.23 + verify 0.01 + ~0.12 probe
   and bookkeeping; at w4 1.681 s = pass 1 1.30 + pass 2 0.25. Pass 2, the layout probe and the grouping are Python
   and serial.
5. **Volume (`vnxdna.volume`, `vnx.volume/1`)**: write-back buffer (`write_back_bytes`, default 8 MiB) flushed
   automatically or on `flush()`/`close()`; each flush encodes a new extent version to `e<i>-v<n>.fasta` (atomic
   publish + directory fsync) and then appends a CRC-32 journal record (fsync + directory fsync). Open replays the
   journal: a torn last line is ignored and cut on the next append; a bad record followed by a valid one raises
   `VNXIntegrityError` (`journal.py`). `rw` open removes unreferenced extent files. LRU decoded-extent cache bounded
   in bytes. `scrub()` reports per extent and repairs nothing. Not yet verified by fault-injection tests in V13:
   interrupted encode mid-flush with a partially written strand file, truncated journal at every byte offset,
   corrupted records at every position, process termination during flush, repeated reopen + scrub, write-back
   bound under large writes, cache bound under range reads, and the durability contract of unflushed writes (lost
   on a crash: to be stated).
6. **Tests**: `tests/v13` (12 files: engine, stream, volume, pass-1 levers), `tests/fuzz` (hypothesis + native
   fuzz smoke + regression corpus), `tests/sdk`, `tests/conformance`, V4/V5/V6 golden archives. `tests/v13/test_stream.py`
   has one pool-path test (4 extents, 32 KiB, mixed channel) and no test for duplicate tags in a pool, reordered or
   truncated pools, missing extents, or multi-archive-ID pools beyond that case.

## 2. Pre-registered workloads and goals

The end-to-end operation and the denominator are unchanged from docs/V13_PLAN.md §2: `decode_reads(reads.fastq,
out.vnx, DecodeOptions(workers=W))` as `experiments/arch/compare.py one --step decode` runs it (parsing through the
output container write), outputs byte-identical to the input archive, ratio = baseline seconds / V14 seconds per seed,
median with the percentile-bootstrap 95 % CI (min–max at n = 5). Tiers: **1 >= 2x, 2 >= 5x, 3 >= 10x**, reached
only when the CI lower bound clears the line and the scenario is faster beyond the A/A band. Component ratios are
never reported as end-to-end.

| id | input (SIMULATED channel) | workers | seeds | V14 goal (brief §10) |
|---|---|---|---|---|
| `indel-w1` (primary A) | 4 MiB random, sub 1 % / ins 0.2 % / del 0.2 %, coverage 5 | 1 | 95500–95504 | median >= 5x over V12; Tier 2 only if the CI lower bound clears 5x |
| `clean-w4` (primary B) | 4 MiB random, coverage 3, no errors | 4 | 95100–95104 | median >= 3x over V12; CI lower bound clears 3x |
| `indel-w4`, `indel-w2`, `indel-w8` | as indel-w1 | 4 / 2 / 8 | 95300–95304 (w1 seeds for w2) | worker scaling reported; no target |
| `indel-rc-w4`, `indel-dropout-w4`, `substitution-w4`, `indel-64k-w1`, `indel-16m-w4` | the V13 secondary set | as named | as V13 | no regression beyond the A/A band; no false success |
| `encode-1m-w1`, `encode-4m-w1`, `encode-4m-w4` | the V13 encode set | as named | as V13 | within the A/A band (goal E) |
| `pool-E` (workstream A) | stream of E x 1 MiB random extents, strands shuffled into one pool (coverage 3, sub 1 %, seed 7), decoded by archive ID with the pool path forced; E = 4, 8, 16, 32 (V13 reference at E = 4, 8, 16) | 1 (and 4 at E = 16) | fixed data seed 2000+E, 3 reps | partition/index time ~ O(P + E); V14 total pool-decode time grows ~linearly with E (16 -> 32 extents about 2x, not 4x); V13 reference O(E x P) re-measured |
| memory (goal C) | process-tree peak RSS of every decode scenario | — | — | ~100 MiB at w1–w8 on the 4 MiB decodes, or the trade-off explained |
| workstream E suite (P1) | mixed sub/ins/del, 50 % RC, dropout + uneven (negative-binomial) coverage, duplicates (`duplication_rate`), low coverage (2), structured payloads (`repetitive`, `binary`, zero-heavy), adversarial pools (foreign-archive reads, truncated and malformed records); 4 MiB; fixed seeds | 1 and 4 | declared in `experiments/v14/bench/` before the first run | every declared row reported: SUCCESS/failure, output SHA-256 equality, false-success count, recovery and fallback counters, RSS, CIs |

Goal F (10x, Tier 3) is investigated, not promised: the serial-stage floor is measured per workstream and the
remaining bottleneck quantified if it is missed.

## 3. Correctness gates (every workstream; nothing ships that fails one)

1. Output byte-identical to the input archive and to the V13 reference path on every benchmark seed and every test
   pool; 0 false successes (a SUCCESS with a different output) anywhere in the declared campaign.
2. Full suite: never below the V13 release gate count (4,454 passed / 4 skipped at f7586b9; reproduced at
   58171b8 on this host), 0 failures; no test weakened, skipped or removed; V4/V5/V6 golden archives decode.
3. Differential tests against the Python references for every native or engine change (attempted-read records,
   spill records, pass-2 symbols); determinism across thread/worker counts (same records, same output).
4. Native code: ASan/UBSan (gcc and clang) + `cargo test` with `-D warnings`, overflow checks on, the fuzz smoke
   suite, and the differential fuzz of any changed kernel; a failure is reproduced and classified (code, test
   assumption, resources, environment) before anything changes, and expected outputs are never edited to pass.
5. The engine's fallback ladder and the observer-event contract are preserved; intentional differences documented.
6. Scheduler changes (workstream C): no correctness regression and no false success on the adversarial campaign
   (structured, zero-heavy, repeated patterns, column parity, noisy headers, indels, RC, dropout, substitutions,
   duplicates, neighbouring strands) with recovery records compared with the reference; a diagnostic mode that attempts
   every eligible read stays available.
7. ruff + mypy clean (CI configuration); commits by the repository author, no tool attribution, no scratch paths.

## 4. Workstreams, order and dependencies (brief §11 protocol)

| step | workstream | depends on | acceptance |
|---|---|---|---|
| 1–2 | Phase 0 audit + this plan + harness `experiments/v14/bench/` | — | committed before any optimisation |
| 3–4 | **A** linear pool decode (P0): one layout probe + one pass 1 over the pool, per-tag partition of the spill (O(P) records, one pass), one pass 2 per extent on its partition; records that cannot be classified (unreadable or ambiguous raw tag) stay in every extent's candidate set as pending records exactly as today; per-extent failure falls back to the existing whole-pool decode of that extent (ladder preserved); manifest tag-ambiguity refusal kept; no format change | Phase 0 | reference-decoder equivalence tests (shuffles, extent counts, missing/duplicated/corrupted records, ambiguous tags, several archive IDs, empty streams, partial final extent, determinism across workers); `pool-E` rows: partition ~O(P + E), decode accounted separately, V14 total no longer dominated by per-extent rescans |
| 5–6 | **B** native pass 2 / layout probe / parallel grouping (P0): stage profile first (probe, pass-1 prep, grouping, pass 2, verify, output), then the smallest change per bottleneck with a differential test and an independent measurement; keep only reproducible end-to-end gains | A (shares pass-2 code) | each change: identical symbols and output; end-to-end gain on `indel-w1`/`clean-w4` beyond the A/A band; deterministic across thread counts; no oversubscription |
| 7–8 | **C** adaptive recovery scheduler (P0, correctness-sensitive): start from the f7586b9 rule; investigate skips justified by format invariants (indel-robust header validation, marker-anchored projection before the skip decision, corroborating windows, explicit evidence categories); observable decisions (skip reasons, attempts, alignment counts, fallbacks); diagnostic attempt-all mode | B's stage profile | adversarial + regression campaign with 0 false successes and no symbol loss versus the reference; measurable end-to-end gain on `indel-w1`; otherwise the conservative behaviour stays and the cost is documented |
| 9 | **D** volume reliability (P1): durability contract stated; fault-injection tests (interrupted encode, failed flush, truncated journal at every offset, corrupted records, partial extents, SIGKILL during flush, reopen + scrub after each); bounded, observable cache; format unchanged | — (independent of A–C) | documented crash-recovery contract backed by repeatable tests |
| 9–10 | **F** configuration, SDK, CLI, security hardening (P1): consistency of options/SDK keys/env/CLI/engine bindings; native-boundary validation; cache key + hit verification + isolation; manifest validation; resource limits; cancellation; web console node tests in a reproducible environment or a recorded reason | A, D (new options) | tests + docs for each corrected defect |
| 11 | **E** realistic-channel suite (P1) + the full pre-registered benchmark set, A/A band | A–C code frozen | every declared workload reported, including regressions and missed targets |
| 12–13 | reviews (code, security, evidence), V14 RC, reports, V15 plan | all | no tag, merge or push |

Stop conditions: a workstream whose change is not byte-identical to the reference on any seed or test pool is
reverted; a change whose end-to-end CI overlaps the A/A band is not kept as a speed-up (it may stay for other
reasons, documented); the scheduler keeps the conservative rule unless the adversarial campaign is clean.

## 5. Benchmark method

`experiments/v14/bench/bench.py` (three trees: `v12` = 6bf7749, `v13` = 58171b8 pristine, `v14` = the branch;
inputs generated by the V12 tree; configuration order reversed on odd seeds; one discarded warm-up per configuration;
process-tree RSS sampled every 0.5 s; ratios v12/v14, v13/v14 and v12/v13 with bootstrap CIs) and
`experiments/v14/bench/pool_bench.py` (pool decode scaling with E; V13 tree vs V14 tree, reps alternating order).
The A/A noise band is `experiments/v13/bench/baseline-aa` (6bf7749 against itself on this host, same harness
protocol); a V14 A/A of 58171b8 against `v14-base` is run if any decision is within 10 % of that band. Benchmarks run
alone on the host (load recorded per row), in the lab's systemd scope, never during the test suite. Every reported
number is read from the committed `e2e.jsonl` / `pool.jsonl` rows or their summaries; missing or failed runs are
reported as such.

## 6. Authorization boundaries

Local commits on `build/v14` only. No push, tag, merge, release or publication; `main`, `build/v12`, the V12 and V13
checkouts and other worktrees are not touched. The founder decides release actions after the V14 engineering and
benchmark reports.
