# V14 Phase 6 report: VNX-DNA integration and optimisation (workstreams A-F)

All channel results referenced here are SIMULATED. Phase 6 (tag `v14-p6`) delivered the six workstreams of
[V14_BRIEF_2026-10-18.md](V14_BRIEF_2026-10-18.md) §4-§9, and this page only points to where each one is documented,
to avoid duplicating content: A (linear-time shuffled-pool decode) and B (parallel per-extent pass 2) are measured in
`experiments/v14/bench/p6a-pool/` and `experiments/v14/bench/p6b-profile/` (README in each); C (recovery scheduler,
default `auto`, negative result for `strand`) in [V14_SCHEDULER.md](V14_SCHEDULER.md) and
`experiments/v14/bench/p6c-scheduler/`; D (volume reliability) in [V14_VOLUME_RELIABILITY.md](V14_VOLUME_RELIABILITY.md);
E (channel benchmark suite) in `experiments/v14/bench/p6e-channels/` and §1 of
[V14_BENCHMARK_REPORT.md](V14_BENCHMARK_REPORT.md), whose §9 lists the trade-offs and missed targets; F (configuration,
SDK, CLI and security hardening, including the open finding O-1) in [V14_HARDENING.md](V14_HARDENING.md). The release
summary is in [V14_IMPLEMENTATION_REPORT.md](V14_IMPLEMENTATION_REPORT.md).
