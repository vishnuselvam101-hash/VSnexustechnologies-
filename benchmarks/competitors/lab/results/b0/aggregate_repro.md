# B0 sweep repro

Channel results are SIMULATED. Times and memory are MEASURED on this VPS. Wilson 95 % intervals. `exact` = output SHA-256 equals input SHA-256 (success). `(+n)` = n further trials whose output starts with the identical bytes but carries extra trailing bytes (counted by the harness as success, not counted as exact here). false-SUCCESS = exit 0 with different content.

| participant | err | cov | n | exact (+padded) | success (95 % CI) | false-SUCCESS | recov. frac | enc s | dec s | enc/dec RSS MiB | strand nt | nt/byte | bits/nt | GC mean | GC outside 40-60 % | max homopoly | load1 max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dna-aeon-high | 1% | 30 | 1 | 0 | 0.00 (0.00-0.79) | 0 | 0.000 | 3.1 | 900.3 | 330/15 | 144 | 5.32 | 1.505 | 0.498 | 0.000 | 3 | 8.9 |
| dna-aeon-medium | 1% | 30 | 1 | 0 | 0.00 (0.00-0.79) | 0 | 0.000 | 3.4 | 900.7 | 331/0 | 148 | 7.95 | 1.006 | 0.500 | 0.000 | 3 | 4.4 |
| dna-rs-high | 1% | 30 | 1 | 1 | 1.00 (0.21-1.00) | 0 | 1.000 | 0.2 | 1.5 | 14/15 | 144 | 5.34 | 1.497 | 0.500 | 0.011 | 8 | 6.0 |
| vnx-s184 | 1% | 30 | 1 | 1 | 1.00 (0.21-1.00) | 0 | 1.000 | 0.5 | 0.7 | 56/74 | 184 | 9.95 | 0.804 | 0.492 | 0.000 | 4 | 4.3 |

Total false-SUCCESS across all trials: 0.
