# B0 sweep c

Channel results are SIMULATED. Times and memory are MEASURED on this VPS. Wilson 95 % intervals. `exact` = output SHA-256 equals input SHA-256 (success). `(+n)` = n further trials whose output starts with the identical bytes but carries extra trailing bytes (counted by the harness as success, not counted as exact here). false-SUCCESS = exit 0 with different content.

| participant | err | cov | n | exact (+padded) | success (95 % CI) | false-SUCCESS | recov. frac | enc s | dec s | enc/dec RSS MiB | strand nt | nt/byte | bits/nt | GC mean | GC outside 40-60 % | max homopoly | load1 max |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dna-fountain-medium | 0.5% | 10 | 3 | 3 | 1.00 (0.44-1.00) | 0 | 1.000 | 1.3 | 1.2 | 87/80 | 152 | 7.98 | 1.002 | 0.498 | 0.012 | 4 | 2.1 |
| dna-fountain-medium | 0.5% | 10 | 3 | 3 | 1.00 (0.44-1.00) | 0 | 1.000 | 1.3 | 1.1 | 87/81 | 152 | 7.98 | 1.002 | 0.498 | 0.012 | 4 | 2.1 |
| dna-rs-medium | 0.5% | 10 | 3 | 3 | 1.00 (0.44-1.00) | 0 | 1.000 | 0.7 | 14.9 | 14/15 | 144 | 8.02 | 0.998 | 0.502 | 0.008 | 9 | 2.4 |
| dna-rs-medium | 0.5% | 10 | 3 | 3 | 1.00 (0.44-1.00) | 0 | 1.000 | 0.7 | 37.9 | 14/15 | 144 | 8.02 | 0.998 | 0.502 | 0.008 | 9 | 2.1 |
| vnx-s184 | 0.5% | 10 | 3 | 2 | 0.67 (0.21-0.94) | 0 | 0.667 | 0.8 | 0.9 | 57/73 | 184 | 8.07 | 0.992 | 0.501 | 0.000 | 4 | 2.4 |
| vnx-s184 | 0.5% | 10 | 3 | 0 | 0.00 (0.00-0.56) | 0 | 0.000 | 0.7 | 0.9 | 57/72 | 184 | 8.07 | 0.992 | 0.501 | 0.000 | 4 | 2.4 |
| vnx-s280 | 0.5% | 10 | 3 | 3 | 1.00 (0.44-1.00) | 0 | 1.000 | 0.7 | 0.9 | 57/70 | 280 | 7.89 | 1.014 | 0.499 | 0.000 | 4 | 2.6 |
| vnx-s280 | 0.5% | 10 | 3 | 0 | 0.00 (0.00-0.56) | 0 | 0.000 | 0.7 | 0.9 | 57/70 | 280 | 7.89 | 1.014 | 0.499 | 0.000 | 4 | 2.5 |

Total false-SUCCESS across all trials: 0.
