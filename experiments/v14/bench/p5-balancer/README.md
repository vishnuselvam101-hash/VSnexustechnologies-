# P5 load-balancer benchmark

Single- vs multi-worker sweeps of `vnxd-lb-bench` (docs/V14_LOAD_BALANCER.md §5). Run:

    cargo build --release --offline --locked --manifest-path src/vnxdna/native/rust/vnxd/Cargo.toml
    python experiments/v14/bench/p5-balancer/run.py

`results.jsonl`: 45 runs (one process each), with provenance; `summary.json`: median and range per configuration.
Evidence category B: the software device over a modelled backend (`LatencyMedia`; latencies and concurrency are
model inputs, not measurements of any medium).

Provenance note: the runs were made at commit 4578db4, a local pre-squash commit of Phase 5 that is not on the
branch. The released Phase 5 commit 3aa6834 differs from it in `src/` only by the new default worker counts
(`QueueConfig::for_backend`, `BalancerConfig::default`) and their test; the benchmark passes explicit worker counts,
so these defaults do not affect it.
