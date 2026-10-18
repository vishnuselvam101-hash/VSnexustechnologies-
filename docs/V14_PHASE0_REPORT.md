# V14 Phase 0 report: audit, baseline and plans

Date 2026-10-18. Branch `build/v14` from `v13.0.0` (58171b8). Scope: master directive §2 (investigation before code).
No code under `src/` or `tests/` changed in this phase.

## Implemented

* Audit of the V13 tree: components, languages, build and test commands, CI, kernel features of the host; the pool
  decode defect and its Phase 6 design ([V14_PLAN.md](V14_PLAN.md) §1.2, [V14_STATE.md](V14_STATE.md)).
* Baseline measurements ([V14_BASELINE.md](V14_BASELINE.md)): full suite, V12/V13 A/B, V13 pool-decode scaling.
* Benchmark harnesses `experiments/v14/bench/bench.py` and `pool_bench.py` (fix in this phase: tree paths given as
  relative paths are resolved before the tree check).
* Documents: [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md), [V14_ARCHITECTURE.md](V14_ARCHITECTURE.md),
  [V14_BASELINE.md](V14_BASELINE.md), [V14_LANGUAGE_AND_RUNTIME_STRATEGY.md](V14_LANGUAGE_AND_RUNTIME_STRATEGY.md),
  [V14_DEPENDENCY_GRAPH.md](V14_DEPENDENCY_GRAPH.md); [V14_PLAN.md](V14_PLAN.md) completed.

## Exact results

| check | result | file |
|---|---|---|
| full suite, 58171b8 | 4,454 passed / 4 skipped / 0 failed, 434.5 s | lab log `results/full-suite-58171b8-20261018-1021.txt` |
| A/B indel w1 (V12/V13) | 2.811x [2.769, 2.968] vs report 2.885x [2.732, 2.989] | `experiments/v14/bench/phase0-ab/` |
| A/B indel w4 | 1.741x [1.688, 2.115] vs report 1.704x | same |
| A/B clean w4 | 2.257x [2.010, 2.427] vs report 2.200x | same |
| A/B correctness | 45 runs SUCCESS, outputs identical, 0 false successes; V13 peak RSS ~100 MiB | same |
| pool decode, V13, 1 worker | 4.45 / 16.83 / 63.69 s at 4 / 8 / 16 extents (x3.78 per doubling), 9/9 SUCCESS | `experiments/v14/bench/phase0-pool/` |
| doc links | 0 broken (`tools/check_doc_links.py`) | — |

All channel results are SIMULATED. Evidence categories reached: A (software codec) and D (simulated channel) only.

## Benchmark changes

None: Phase 0 measures the starting point. No V14 speed-up is claimed; the 10x target of the brief is not reached.

## Known defects and open items

* O(extents x pool) pool decode (measured above) — Phase 6 workstream A.
* Both Rust crates declare `license = "Proprietary"` while the project is MIT ([V14_DEPENDENCY_GRAPH.md](V14_DEPENDENCY_GRAPH.md) §5).
* Not run at baseline: sanitizers, `cargo test`, fuzz smoke, web console tests (V14_BASELINE §6).
* No ARM64 toolchain and no Raspberry Pi: Pi acceptance will remain INCOMPLETE unless hardware is provided.
* The branch is not pushed: pushes from the agent are not permitted; the founder pushes `build/v14`.

## Acceptance (Phase 0 criteria of V14_MASTER_PLAN)

| criterion | result |
|---|---|
| suite reproduces the V13 gate | PASS |
| A/B reproduces the V13 report within CIs | PASS (all three scenarios overlap) |
| pool-scaling reference committed | PASS |
| every pending cell of V14_PLAN filled from committed files | PASS |
| the five Phase 0 documents exist | PASS |

Phase 0: **PASS**. Next: P1 contracts and core storage model.
