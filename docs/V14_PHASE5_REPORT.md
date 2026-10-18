# V14 Phase 5 report: load balancer

Date 2026-10-18. Branch `build/v14` on top of fc80ced (tag `v14-p4`). Scope: [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P5
(master directive §9). Specification: [V14_LOAD_BALANCER.md](V14_LOAD_BALANCER.md).

## Implemented

* `vnxd/src/balance.rs`: `Balancer` — five task classes with weighted fair queuing, bounded per-class queues
  (backpressure), named resources with concurrency limits, cancellation and deadlines resolved by a reaper thread,
  refusal of nested waits, panic containment, drain/no-drain shutdown, per-class metrics and utilisation;
  `LimitedMedia` — a per-backend in-flight limit (the adapter's `max_in_flight` by default).
* `IoQueue` (P2) gained the same reaper: a cancelled or expired host request now completes at once even when every
  worker is busy (found while testing the balancer; the P2 test now uses a single, stuck worker).
* `fault.rs`: `LatencyMedia`, a modelled-latency adapter for scheduling benchmarks (model inputs, never measurements).
* `tests/concurrency_stress.rs`: queue + cache (both modes) + volume + fallback + limited backend + balancer
  (scrub, compaction, cache flush) under a watchdog, checked against byte models.
* `vnxd-lb-bench` + `experiments/v14/bench/p5-balancer/run.py`: the single- vs multi-worker benchmark.

## Tests and exact results

| check | result |
|---|---|
| `cargo test --release --offline --locked`, `RUSTFLAGS=-D warnings` (vnxd) | 141 passed (125 unit, 2 CLI, 2 concurrency stress, 8 fault matrix, 4 golden) + `seal_worker` via pytest |
| the vnxd tests, 30 repeated runs | 30/30 passed |
| `cargo test` debug build | passed |
| `ruff`, `mypy` (`vnxdna.device`) | clean |
| full suite (6 workers, systemd scope) | **4,570 passed, 4 skipped, 0 failed**, 367.0 s (P5 adds Rust tests only) |

## Benchmark and the chosen defaults

45 runs (3 seeds x 15 configurations, one process each) of `vnxd-lb-bench` at commit 4578db4, over a modelled
backend (evidence category B; latencies are model inputs). Tables and the decision rule:
[V14_LOAD_BALANCER.md](V14_LOAD_BALANCER.md) §5; data: `experiments/v14/bench/p5-balancer/`.

| default | value | why (medians of 3) |
|---|---|---|
| I/O queue workers | min(CPUs, 2 x backend `max_in_flight`) — 8 here for the electronic model, 4 for the slow model | electronic: 872 req/s at 8 vs 735 at 4 (ranges disjoint), p99 +12 %; 16 workers lose throughput, p99 x2. Slow model (2 in flight): throughput flat from 4 workers, p99 497 → 811 → 1128 ms at 4/8/16 |
| balancer workers | max(1, CPUs / 4) — 2 here | host I/O 95.1 % and p99 +3.7 % of 1 worker with twice the background throughput; 4 workers cost 31 % of host throughput |

Memory: peak RSS 16–40 MiB across all runs (16 MiB volume, modelled backend in memory), growing with the worker
count. The previous default of 4 I/O workers (P2) was a guess; it is replaced by the measured rule.

## Review

Found by our own tests while P5 was written: a cancelled or expired task waited for a free worker — in the balancer
and, since P2, in `IoQueue` (with every worker busy, a cancelled request did not complete); both now have a reaper
thread, and the P2 test uses a single, stuck worker.

The independent review of the P5 diff:

| finding | severity | fix |
|---|---|---|
| an idle class banked credit: after a long foreground-only period, a burst of maintenance tasks ran ~1,250 times before the next foreground task (reproduced) | blocking | system virtual clock; a class becoming backlogged, or blocked on a busy resource, is raised to it; tests for both |
| a submitter waiting for room in one class could be left waiting while its queue had room (the single wake-up went to another class's submitter; reproduced) | blocking | wake every room waiter after a start; test |
| failed and panicked tasks were counted as completed | minor | counted as failed; test |
| a task dropping the last handle of its balancer panicked joining its own thread | minor | never join the current thread; test |
| a worker waiting for an already finished task was refused | minor | a ready result is always returned; test |
| the reaper shared the workers' condition variable (a start could wait up to 10 ms) | minor | its own condition variable |
| shutdown without drain waited for a free worker before failing queued tasks (balancer and `IoQueue`) | minor | the reaper fails them at once; test |
| virtual-time overflow after ~2^44 starts would panic under the lock | minor | saturating arithmetic (and the system clock keeps classes close) |
| `LatencyMedia` hid the wrapped adapter's durability and sealing | minor | forwarded |
| the stress test ran one background task at a time | test gap | 3 threads x 4 tasks in flight; the serialised resource is checked to never run twice at once |
| benchmark: balancer utilisation is ~1 by construction; queue wait excluded failed requests | doc/minor | documented as such; failed requests counted |

## Acceptance (P5 criteria of V14_MASTER_PLAN)

| criterion | result |
|---|---|
| no deadlock under the concurrency stress tests | PASS (queue + cache in both modes + volume + fallback + limited backend + 12 concurrent balancer tasks, watchdog, 3 seeds per mode, 30 repeated runs; byte models and volume/cache agreement checked) |
| single- vs multi-worker benchmark (queue latency, utilisation, throughput, memory) committed | PASS (`experiments/v14/bench/p5-balancer/`, 45 runs) |
| the chosen default is justified by that file | PASS (rule fixed before the runs; §5 of V14_LOAD_BALANCER) |
| full suite not below the gate, 0 failed | PASS (4,570 passed) |

Phase 5: **PASS**. Next: P6 codec workstreams.
