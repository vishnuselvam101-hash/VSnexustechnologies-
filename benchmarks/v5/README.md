# V5 benchmarks

All results are **SIMULATED**.

* `provenance.py` builds the provenance block written with every result: V4 control tag and commit, commit under
  test, worktree state, OS, CPU and flags, Python, packages, native toolchain and the configuration SHA-256.
* `phase1_baseline.py` produces the V4 control measurements in `baseline-v4/`:
  `python benchmarks/v5/phase1_baseline.py {align,align-workers,e2e,profile,overhead,all}`.
  Run it on an otherwise idle machine. Timing fields vary between runs. The hashes (`projection_sha256`,
  `strands_sha256`, `reads_sha256`, input/output SHA-256) are deterministic and must reproduce exactly.

Interpretation: [docs/V5_PHASE1_BASELINE.md](../../docs/V5_PHASE1_BASELINE.md).
