# V14 load balancer

Phase 5 specification (master directive §9; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P5). Implementation:
`src/vnxdna/native/rust/vnxd/src/balance.rs`, with the host-request queue of
[V14_VOLUME_AND_IO.md](V14_VOLUME_AND_IO.md) §6 (`IoQueue`).

## 1. What is scheduled where

| work | scheduler | ordering rule |
|---|---|---|
| host requests (read, write, flush) | `IoQueue` | conflict order: overlapping requests (one writing) run in submission order; flush is a barrier |
| encode, decode, verification, scrub, GC, compaction, maintenance | `Balancer` | weighted fair queuing between classes, FIFO within a class (first runnable) |
| backend operations from both | `LimitedMedia` around the adapter | at most the adapter's `max_in_flight` at once |

Transaction order is kept below the schedulers (the volume's extent locks and the journal's commit point); the
schedulers decide only when work starts.

## 2. Balancer

| item | rule |
|---|---|
| classes and weights | foreground 16, codec 8, verify 4, gc 2, maintenance 1 (configurable, 1..65,536) |
| fairness | the class with the smallest virtual time among those with a runnable task starts next; its virtual time then advances by 2^20 / weight. Under saturation, starts follow the weights exactly (tested: 160/80/40/20/10 of the first 310); every class with work starts within one round (31 starts at the default weights). A class that becomes active after being idle has its virtual time raised to the smallest active one, so idle time is not banked |
| resources | a task may name a resource with a concurrency limit (`synthesis` = 1, `media:…` = the adapter's limit); it starts only while fewer than that many tasks hold it. A task whose resource is busy does not hold back the tasks behind it in its class |
| backpressure | bounded queue per class (default 256); `submit` waits for room, `try_submit` fails with RESOURCE_EXHAUSTED (retryable) |
| cancellation and deadlines | a task cancelled or past its deadline before it starts completes at once with CANCELLED or TIMEOUT and never runs. A reaper thread resolves them, so this holds while every worker is busy |
| nested waits | a task may submit tasks but never wait for one of the same balancer (refused with INTERNAL; a worker submitting into a full queue gets RESOURCE_EXHAUSTED instead of waiting): with every worker waiting, nothing could run |
| panics | a panicking task completes with INTERNAL; its worker and its resource slot are released |
| shutdown | new submissions get SHUTTING_DOWN; queued tasks are drained or failed; workers and the reaper are joined |
| metrics | per class: submitted, started, completed, failed, cancelled, timed out, rejected, total and largest queue wait, run time; utilisation = run time / (workers x wall time) |

`IoQueue` received the same reaper in P5 (before, a cancelled or expired request waited for a free worker; its test
now uses a single, stuck worker).

## 3. Backend concurrency

`LimitedMedia` admits at most `limit` operations at once (default: the adapter's `max_in_flight`) and records the
largest number in flight and the time spent waiting for a slot. A physical synthesis or sequencing backend is not
as parallel as CPU work; its adapter declares a small `max_in_flight` and every caller (host requests, scrubs,
GC) shares those slots.

## 4. Deadlock freedom

Lock order, top to bottom: `IoQueue`/`Balancer` scheduler locks (never held while running work) → cache extent
stripes (ascending; others only try-locked) → volume extent stripes (ascending) → volume state → journal →
`LimitedMedia` slot (a leaf: held only around one adapter call, released on panic) → adapter. No layer calls
upward while holding a lock. Waits that could close a cycle are refused: a balancer task waiting for a task of its
own balancer, a balancer worker waiting for room in its own queue. `tests/concurrency_stress.rs` runs 8 clients of
random host I/O through the queue, the cache (both modes), the volume, the fallback layer and a backend limited to
4 operations, while the balancer runs scrubs, compactions and cache flushes, under a 240-second watchdog; it also
checks every client's region against a byte model and the volume against the cache.

## 5. Single- vs multi-worker benchmark and the chosen defaults

Files: `experiments/v14/bench/p5-balancer/{run.py,results.jsonl,summary.json}` (45 runs, one process each, 3 seeds per
configuration, commit 4578db4, clean tree, x86_64 with 8 CPUs, Linux 6.8). Evidence category B: the software
device over a **modelled** backend (`LatencyMedia`; latencies and concurrency are model inputs, not measurements of
any medium). Workload per run: 16 clients, 2/3 reads, 4–256 KiB requests over a 16 MiB volume of 64 KiB extents,
with the balancer kept saturated by a codec stand-in (SHA-256 over 1 MiB per task) sharing the CPUs. Medians of 3
runs; the full ranges are in `summary.json`.

**I/O queue workers** (balancer 2 workers):

| backend model | workers | req/s (range) | p50 ms | p99 ms | queue wait ms | backend in flight | peak RSS MiB |
|---|---|---|---|---|---|---|---|
| electronic (put 300 µs, get 150 µs, 32 in flight) | 1 | 373 (345–377) | 41.9 | 76.6 | 40.1 | 1 | 25.1 |
| | 2 | 584 (563–628) | 26.0 | 55.4 | 23.8 | 2 | 28.0 |
| | 4 | 735 (731–810) | 20.2 | 48.8 | 16.0 | 4 | 29.9 |
| | **8** | **872 (867–957)** | 15.7 | 54.8 | 8.8 | 8 | 33.7 |
| | 16 | 730 (585–904) | 12.7 | 109.7 | 2.5 | 15 | 40.0 |
| slow, barely parallel (put 20 ms, get 10 ms, 2 in flight) | 1 | 34.7 (31–38) | 456 | 794 | 420 | 1 | 16.0 |
| | 2 | 64.6 (57–69) | 235 | 518 | 203 | 2 | 16.5 |
| | **4** | **69.0 (62–72)** | 200 | 497 | 157 | 2 | 17.5 |
| | 8 | 70.5 (64–76) | 143 | 811 | 80 | 2 | 19.5 |
| | 16 | 69.6 (63–76) | 91 | 1128 | 30 | 2 | 23.0 |

**Balancer workers** (I/O queue 8 workers, electronic model):

| workers | host req/s (range) | host p99 ms | background tasks/s | peak RSS MiB |
|---|---|---|---|---|
| 1 | 1081 (1013–1131) | 43.7 | 178 | 34.0 |
| **2** | **1028 (1015–1078)** | 45.3 | 359 | 33.6 |
| 4 | 744 (639–928) | 66.4 | 706 | 34.1 |
| 8 | 650 (637–671) | 75.6 | 1239 | 34.5 |
| 16 | 538 (522–551) | 94.4 | 1258 | 36.3 |

Rule (fixed before the runs): the smallest worker count whose median throughput is within 5 % of the best and
whose p99 is at most 20 % above the best p99; for the balancer, whose cost is borne by the host requests sharing
the CPUs, the largest count that keeps host throughput within 5 % and host p99 within 20 % of the 1-worker value.

* **I/O queue: `QueueConfig::for_backend(max_in_flight)` = min(CPUs, 2 x max_in_flight)** (default
  `for_backend(32)`). Electronic model: 8 (= the CPUs) is the only count within 5 % of the best, p99 +12 %;
  16 workers lose throughput and double p99 (CPU oversubscription next to the balancer). Slow model: throughput is
  flat from 4 workers on (the backend admits 2), while p99 grows from 497 ms (4) to 811 ms (8) and 1128 ms (16):
  workers beyond about twice the backend's limit only wait for backend slots. The former default of 4 workers was
  not measured; it is replaced.
* **Balancer: max(1, CPUs / 4)** (2 here): host throughput 95.1 % and p99 +3.7 % of the 1-worker value, with twice
  the background throughput; 4 workers cost 31 % of host throughput. Background work scales to 8 workers (1239
  tasks/s, the CPU count) and not beyond. The balancer is saturated by construction in this benchmark, so its own
  utilisation (≈1.0 in every run) carries no information; the background tasks/s column is its throughput.
* Variance: the same configuration (8 I/O, 2 balancer workers) ran in both sweeps with medians 872 and 1028 req/s
  (runs at different times on a shared host); the decisions above rest on differences whose ranges do not overlap,
  and are re-checked on the integrated device in P9 with the real image backend (P7).
