# EXP-0014-constraints

**Purpose.** constraint violations before/after scrambler screening, cost and recoverability

**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.

**Method.** Random frames are built once. 'Before' violations use one random scrambler variant per strand, without screening; 'after' uses the encoder's screening (first variant that satisfies every rule). Every screened strand is decoded back to verify recoverability.

**Reproduce.**

```bash
vnx experiment run experiments/EXP-0014-constraints/config.json      # re-run (overwrites results.json)
vnx experiment reproduce experiments/EXP-0014-constraints            # re-run in scratch and compare deterministic fields
```

## Results

Commit `a387b34141161092ace2dcd8017dabe546b95831`, 2026-10-03T03:00:21Z; environment in `environment.json`; every trial in `results.json`.

20000 random v4-balanced frames (313 nt). Before = one uniformly random scrambler variant per strand, no screening.

| rule set | status | violating before | by rule (before) | violating after | mean variant | max variant | strands/s | decodes |
|---|---|---|---|---|---|---|---|---|
| default (GC 40-60, homopolymer<=4) | SATISFIED | 11010 (0.5505) | {'GC_CONTENT': 4, 'HOMOPOLYMER': 11007} | 0 | 0.982 | 19 | 110894.2 | True |
| relaxed (GC 30-70, homopolymer<=6) | SATISFIED | 909 (0.04545) | {'HOMOPOLYMER': 909} | 0 | 0.037 | 3 | 164721.9 | True |
| strict (GC 45-55, homopolymer<=3) | UNSATISFIABLE | 19342 () | {'GC_CONTENT': 1138, 'HOMOPOLYMER': 19308} | — | — | — | — | — |
| windowed GC (window 50 nt, 30-70) | SATISFIED | 11535 (0.5767) | {'GC_CONTENT': 4, 'GC_WINDOW': 1424, 'HOMOPOLYMER': 11007} | 0 | 1.125 | 20 | 72031.6 | True |
| motifs (EcoRI, BamHI, HindIII) + tandem<=12 | SATISFIED | 12420 (0.621) | {'FORBIDDEN_MOTIF': 3129, 'GC_CONTENT': 4, 'HOMOPOLYMER': 11007, 'TANDEM_REPEAT': 3} | 0 | 1.354 | 19 | 65402.2 | True |
| very strict (GC 48-52, homopolymer<=2) | UNSATISFIABLE | 20000 () | {'GC_CONTENT': 9634, 'HOMOPOLYMER': 20000} | — | — | — | — | — |

screening changes only the 1-byte scrambler variant inside each frame, so it costs no extra nucleotides; the alternative is to fail explicitly (VNXConstraintError) — sequences are never emitted unscreened

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
