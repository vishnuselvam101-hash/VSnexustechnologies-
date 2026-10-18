# A/A noise band for the V13 harness: build/v12 6bf7749 against itself — SIMULATED

`aa.py run` (= `bench.py run` with the "v13" label pointed at a second pristine checkout of 6bf7749, `AA_TREE`), so
both configurations run the same code and the measured ratio is the noise of this host and session. Same scenarios,
seeds, inputs, pairing, warm-ups and RSS sampler as `../ab-final`. Files: `e2e.jsonl` (126 rows), `warmup.jsonl`,
`e2e-summary.json`, `summary.txt`, `run.log`, `operator-notes.txt`. `../ab-final/vs-aa.txt` compares each A/B scenario
with this band (`compare_aa.py`): a scenario is "beyond noise" only when its A/B CI does not overlap the A/A CI.

| scenario | A/A median ratio [CI95] |
|---|---|
| indel-w1 | 0.974 [0.950, 1.036] |
| indel-w2 | 0.974 [0.942, 1.034] |
| indel-w4 | 1.028 [0.974, 1.065] |
| indel-w8 | 1.004 [0.987, 1.033] |
| indel-rc-w4 | 1.015 [0.989, 1.046] |
| indel-dropout-w4 | 0.982 [0.955, 1.036] |
| clean-w4 | 1.069 [0.998, 1.168] |
| substitution-w4 | 0.990 [0.962, 1.030] |
| indel-64k-w1 | 0.999 [0.908, 1.140] |
| indel-16m-w4 | 1.019 [0.950, 1.027] |
| encode-1m-w1 | 1.012 [0.953, 1.037] |
| encode-4m-w1 | 0.992 [0.971, 0.999] |
| encode-4m-w4 | 0.974 [0.942, 1.020] |

Every row SUCCESS/ENCODED, outputs identical, 0 false successes. The band is within ±7 % except clean-w4 (+17 %,
the shortest decode) and indel-64k-w1 (±14 %, the shortest run); every V13 decode ratio in `../ab-final` lies far outside it, every encode ratio
lies inside it. No DNA was synthesised, stored or sequenced; one host (4C/8T Xeon Gold 6240).
