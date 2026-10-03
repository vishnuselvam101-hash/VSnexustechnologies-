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

Commit `44d3ae932bd4896879fe517d4b7ab52157256da6`, 2026-10-03T02:20:17Z; environment in `environment.json`; every trial in `results.json`.

20000 random v4-balanced frames (296 nt). Before = variant 0.

| rule set | status | violating before | by rule (before) | violating after | mean variant | max variant | strands/s | decodes |
|---|---|---|---|---|---|---|---|---|
| default (GC 40-60, homopolymer<=4) | SATISFIED | 9894 (0.4947) | {'GC_CONTENT': 3, 'HOMOPOLYMER': 9892} | 0 | 1.013 | 15 | 115973.0 | True |
| relaxed (GC 30-70, homopolymer<=6) | SATISFIED | 751 (0.03755) | {'HOMOPOLYMER': 751} | 0 | 0.039 | 3 | 170509.2 | True |
| strict (GC 45-55, homopolymer<=3) | UNSATISFIABLE | 20000 () | {'GC_CONTENT': 1115, 'HOMOPOLYMER': 20000} | — | — | — | — | — |
| windowed GC (window 50 nt, 30-70) | SATISFIED | 10426 (0.5213) | {'GC_CONTENT': 3, 'GC_WINDOW': 1316, 'HOMOPOLYMER': 9892} | 0 | 1.141 | 16 | 76879.1 | True |
| motifs (EcoRI, BamHI, HindIII) + tandem<=12 | SATISFIED | 11601 (0.58) | {'FORBIDDEN_MOTIF': 3345, 'GC_CONTENT': 3, 'HOMOPOLYMER': 9892, 'TANDEM_REPEAT': 3} | 0 | 1.415 | 19 | 67976.6 | True |
| very strict (GC 48-52, homopolymer<=2) | UNSATISFIABLE | 20000 () | {'GC_CONTENT': 10469, 'HOMOPOLYMER': 20000} | — | — | — | — | — |

screening changes only the 1-byte scrambler variant inside each frame, so it costs no extra nucleotides; the alternative is to fail explicitly (VNXConstraintError) — sequences are never emitted unscreened

## Limitations

* The channel is a stress model, not fitted to any synthesis or sequencing platform.
* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty (with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).
* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what `reproduce` compares.
