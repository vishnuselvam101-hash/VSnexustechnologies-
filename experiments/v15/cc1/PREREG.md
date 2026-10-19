# V15 CC-1 decode speed: preregistration (SIMULATED inputs)

Written and committed before any change is measured. Every input is produced by the channel simulator; every number
is a software timing on one host, not a physical DNA measurement.

## Host and configurations

- Host: Intel Xeon Gold 6240 (8 vCPU, KVM), 31 GB RAM, Linux 6.8, Python 3.12. No other heavy job on the host.
  `OPENBLAS_NUM_THREADS=1` for every configuration. Each tree has its own freshly built native libraries
  (`python -m vnxdna.native`: all_native true, nothing older than its source); a row whose `stale_libraries` is
  non-empty or whose `native` map has a non-native kernel invalidates the run.
- `v12`: build/v12 6bf7749 (tier denominator; the V14 benchmark report's v12).
- `v14`: tag v14.0.0 = 7369e09 (the baseline this item is measured against).
- `base`: the head before the change under test (v14.0.0 for the first change, then the last kept change).
- `new`: the change under test; `newaa`: the same tree again as a separate paired configuration (A/A control).

## Workloads (primary)

From `experiments/v14/bench/p6e-channels/workloads.json` (sha256 d1ec94ac...e7e3f4), inputs prepared by the suite's
own `prepare` (archive from the v12 tree, strands + reads from the simulator, channel seed = seed + 7):

| id | workload | size | workers | seeds |
|---|---|---|---|---|
| T-A | T-A-indel-4m-w1 (INDEL channel) | 4 MiB | 1 | 95500-95508 (n = 9) |
| T-B | T-B-clean-4m-w4 (coverage 3, clean) | 4 MiB | 4 | 95100-95108 (n = 9) |

The prepared reads of seeds 95500 and 95100 reproduce the V14 full-run `reads_sha256` (f73a0c5b..., fed77edd...).

Secondary (effect reported, no target): indel-16m-w4 (seeds 97300-97302), sub-64m-w4 (98070-98072) for the
V13-rule bookkeeping regression; mixed-4m-w4 seed 98001 (and its other failing seeds) for time to FAILURE.

## Protocol

- One decode per process (`suite.py one`, the V14 harness), wall time from the harness (`seconds`).
- Paired per seed; configuration order as listed on even (seed + rep), reversed on odd; one discarded warm-up per
  configuration on the first seed. Driver: `experiments/v15/cc1/ab.py run`.
- Ratio = time(base) / time(new) per seed (> 1 = new faster). Statistic: median of the paired ratios. CI: percentile
  bootstrap 95 % CI of that median, 10000 resamples, RNG seed 11 (`suite.boot_ci`, identical to the V14 report).
- Component speedups (the decoder's own stage timers, e.g. `pass1_reads`, and the engine's `ns_*` counters) are
  reported separately from the end-to-end ratio and never substituted for it.

## Acceptance per change (keep / revert)

A change is kept only if ALL hold:
1. Correctness: output bytes and the decode report (every field except timings, RSS and host/path fields) identical
   to v14.0.0 on every primary seed and on every workload of the P6E campaign groups at smoke size; 0 false
   successes; identical across worker counts 1, 2, 4, 8 (determinism) where the workload allows.
2. Focused tests for the change, the full suite at the gate (>= 4852 passed / 5 skipped / 0 failed; the count
   never drops); for a changed native kernel, ASan/UBSan builds and the existing equivalence fuzzing.
3. Speed, on at least one primary workload: the end-to-end CI lower bound of base/new > 1 AND above the A/A
   (new/newaa) CI upper bound; and on the other primary workload, no slowdown beyond noise (CI upper bound of
   base/new not below the A/A CI lower bound).
A change that targets only the secondary workloads (bookkeeping, fail-fast) is kept if 1-2 hold, it clears the
same rule on its target workload, and the primary workloads show no slowdown beyond noise.

## Final measurement and claims

On the final head: configurations v12, v14, final, finalaa on T-A and T-B, n = 9 seeds each, same protocol. The
V14 tiers are claimed only when the CI lower bound of v12/final clears them: T-A >= 5x, T-B >= 3x. If a target is
missed, the remaining serial share (Amdahl bound from the stage profile) is quantified and reported. Deviations
from this document are reported as deviations.

## Amendment 1 (written after change 3 was measured and reverted, before change 4 was measured)

Change 3 (engine read-ahead scan) gave T-B 1.104 [1.04, 1.294] against an A/A band of [0.993, 1.074]: the lower
bound clears 1 but not the A/A upper bound, so it was reverted under the rule above. It is not re-tested. With n = 9
the A/A band is about +-4 %, wider than the gains the remaining candidates can give. From change 4 on, every per-change
A/B uses n = 18 seeds per primary workload (T-A 95500-95517, T-B 95100-95117), same configurations, order rule,
statistic and keep rule. The final measurement stays as registered (n = 9 seeds 95500-95508 / 95100-95108, plus the
same configurations on the 9 extension seeds, reported separately).

## Amendment 2 (protocol fix, written after change 4 was measured and before it was re-measured)

The change-4 run showed an A/A ratio new/newaa of 0.956 (T-B) and 0.971 (T-A) with identical code: with three
configurations and only the forward/reversed orders, `new` always runs second, and the first configuration of a seed
is the first to read that seed's 250-420 MB input after other seeds' inputs went through the page cache. From change 4
on (change 4 is re-measured under this amendment; its first run is kept in the repository as superseded):
- before the timed runs of a seed, the driver reads the seed's input file once (page-cache warm-up, untimed);
- the configuration order cycles through all six permutations of (base, new, newaa) by (seed + rep) mod 6;
- the keep rule is unchanged (base/new CI lower bound > 1 and above the new/newaa CI upper bound); base/newaa is
  reported as well.
Changes 1-3 were measured with the original order rule; their A/A medians were 0.98-1.005. The final measurement uses
this amended protocol for every configuration.
