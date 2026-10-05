# P4-EXP-04: fresh-process decode cost of `consensus_weighting=quality` (SIMULATED channel; time and memory MEASURED)

**SIMULATED channel** (models mixed-mild and mixed-harsh); wall time and peak RSS are MEASURED on this VPS (8 vCPU,
shared with two other build jobs, so times carry load noise; the load average is in `results.json`). No DNA was
synthesised, stored or sequenced.

Command: `PYTHONPATH=src nice -n 10 python experiments/v6/phase4/cost.py --out experiments/v6/phase4/P4-EXP-04-cost`
(commit `6b6b7db`, script committed in `38f1a5b` with the pre-registration). 1 MiB random payload, v4-balanced, seeds
61000-61002; each decode is a fresh `python -m vnxdna.v4.cli decode --workers 1` child; peak RSS = the child's
`ru_maxrss` from `wait4`; arm order alternates by seed.

| model | seed | count: s / MiB / outcome | quality: s / MiB / outcome | time ratio | RSS ratio |
|---|---|---|---|---|---|
| mixed-mild | 61000 | 5.56 / 282.9 / exact | 7.33 / 282.9 / exact | 1.32 | 1.000 |
| mixed-mild | 61001 | 5.19 / 284.7 / exact | 7.13 / 284.7 / exact | 1.37 | 1.000 |
| mixed-mild | 61002 | 5.47 / 285.2 / exact | 7.43 / 285.2 / exact | 1.36 | 1.000 |
| mixed-harsh | 61000 | 25.1 / 362.3 / failed (exit 5) | 33.9 / 362.3 / failed (exit 5) | 1.35 | 1.000 |
| mixed-harsh | 61001 | 24.5 / 362.3 / failed (exit 5) | 34.9 / 362.3 / failed (exit 5) | 1.42 | 1.000 |
| mixed-harsh | 61002 | 24.5 / 363.6 / failed (exit 5) | 34.1 / 363.6 / **exact** | 1.39 | 1.000 |

**Correction (Phase 10 documentation audit): the RSS columns below are not valid.** `wait4` `ru_maxrss` of an exec'd child
inherits the parent's high-water mark on Linux, so the values show the harness's own size, not the decode's. See
`experiments/v6/align-band/README.md`, "Deviations from the pre-registration", item 1. The time columns and ratios stand.
The sentence that follows is kept as originally written and is withdrawn.

False SUCCESS: 0. Peak RSS is identical per pair: the peak is reached in a phase the option does not change (the extra
285 bytes per pending record are written to the spill files). The wall-time cost is 32-42 %. One 1 MiB mixed-harsh seed
decoded only with the weighted vote; 3 seeds are not evidence of a gain (pre-registered efficacy is P4-EXP-02 C2) but
show that 1 MiB mixed-harsh is a failing regime for the default decoder (0/3), which 20 kB at coverage 15 is not (19/20 in
P4-EXP-01).
