| channel | cov | decoder | SUCCESS eager / deferred | same outcome | time eager → deferred (s) | speed-up | smart attempts eager → deferred | reads tried / skipped | peak RSS eager → deferred (MB, coordinator + worker) |
|---|---|---|---|---|---|---|---|---|---|
| clean | 1 | V5-hard | 2 / 2 of 2 | yes | 0.50 → 0.49 | ×1.0 | 0 → 0 | 0 / 0 | 122 → 122 |
| clean | 1 | V5-soft-auto | 2 / 2 of 2 | yes | 0.52 → 0.52 | ×1.0 | 0 → 0 | 0 / 0 | 122 → 122 |
| clean | 2 | V5-hard | 2 / 2 of 2 | yes | 0.57 → 0.57 | ×1.0 | 0 → 0 | 0 / 0 | 132 → 134 |
| clean | 2 | V5-soft-auto | 2 / 2 of 2 | yes | 0.60 → 0.62 | ×1.0 | 0 → 0 | 0 / 0 | 132 → 132 |
| clean | 3 | V5-hard | 2 / 2 of 2 | yes | 0.68 → 0.70 | ×1.0 | 0 → 0 | 0 / 0 | 140 → 140 |
| clean | 3 | V5-soft-auto | 2 / 2 of 2 | yes | 0.70 → 0.69 | ×1.0 | 0 → 0 | 0 / 0 | 141 → 140 |
| clean | 5 | V5-hard | 2 / 2 of 2 | yes | 0.78 → 0.76 | ×1.0 | 0 → 0 | 0 / 0 | 160 → 159 |
| clean | 5 | V5-soft-auto | 2 / 2 of 2 | yes | 0.78 → 0.79 | ×1.0 | 0 → 0 | 0 / 0 | 160 → 160 |
| clean | 10 | V5-hard | 2 / 2 of 2 | yes | 0.93 → 1.01 | ×0.9 | 0 → 0 | 0 / 0 | 216 → 221 |
| clean | 10 | V5-soft-auto | 2 / 2 of 2 | yes | 0.93 → 0.91 | ×1.0 | 0 → 0 | 0 / 0 | 211 → 211 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 1 | V5-hard | 0 / 0 of 2 | yes | 3.93 → 4.52 | ×0.9 | 727 → 677 | 477 / 0 | 152 → 147 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 1 | V5-soft-auto | 0 / 0 of 2 | yes | 4.62 → 5.04 | ×0.9 | 709 → 659 | 477 / 0 | 152 → 147 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 2 | V5-hard | 2 / 2 of 2 | yes | 8.02 → 3.01 | ×2.7 | 1,446 → 185 | 136 / 729 | 170 → 148 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 2 | V5-soft-auto | 2 / 2 of 2 | yes | 8.78 → 3.03 | ×2.9 | 1,400 → 177 | 136 / 729 | 170 → 148 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 3 | V5-hard | 2 / 2 of 2 | yes | 11.22 → 1.92 | ×5.8 | 2,165 → 35 | 24 / 1,264 | 182 → 151 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 3 | V5-soft-auto | 2 / 2 of 2 | yes | 12.62 → 1.99 | ×6.4 | 2,093 → 34 | 24 / 1,264 | 184 → 151 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 5 | V5-hard | 2 / 2 of 2 | yes | 17.27 → 6.01 | ×2.9 | 3,654 → 62 | 46 / 2,126 | 193 → 188 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 5 | V5-soft-auto | 2 / 2 of 2 | yes | 19.63 → 6.08 | ×3.2 | 3,534 → 60 | 46 / 2,126 | 202 → 186 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 10 | V5-hard | 2 / 2 of 2 | yes | 25.08 → 2.71 | ×9.3 | 7,285 → 125 | 90 / 4,282 | 222 → 232 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 10 | V5-soft-auto | 2 / 2 of 2 | yes | 29.53 → 2.89 | ×10.2 | 7,060 → 120 | 90 / 4,282 | 246 → 228 |
| mixed (0.5%+0.5%, sub 1%) | 1 | V5-hard | 0 / 0 of 2 | yes | 6.07 → 6.32 | ×1.0 | 1,046 → 1,014 | 674 / 0 | 155 → 149 |
| mixed (0.5%+0.5%, sub 1%) | 1 | V5-soft-auto | 0 / 0 of 2 | yes | 6.89 → 7.23 | ×1.0 | 1,022 → 990 | 674 / 0 | 156 → 150 |
| mixed (0.5%+0.5%, sub 1%) | 2 | V5-hard | 2 / 2 of 2 | yes | 11.82 → 8.21 | ×1.4 | 2,062 → 2,008 | 1,340 / 0 | 164 → 154 |
| mixed (0.5%+0.5%, sub 1%) | 2 | V5-soft-auto | 2 / 2 of 2 | yes | 13.32 → 9.18 | ×1.5 | 2,014 → 1,958 | 1,340 / 0 | 167 → 154 |
| mixed (0.5%+0.5%, sub 1%) | 3 | V5-hard | 2 / 2 of 2 | yes | 17.48 → 4.83 | ×3.6 | 3,126 → 462 | 320 / 1,428 | 181 → 159 |
| mixed (0.5%+0.5%, sub 1%) | 3 | V5-soft-auto | 2 / 2 of 2 | yes | 20.32 → 5.14 | ×4.0 | 3,060 → 450 | 320 / 1,428 | 193 → 160 |
| mixed (0.5%+0.5%, sub 1%) | 5 | V5-hard | 2 / 2 of 2 | yes | 29.80 → 3.30 | ×9.0 | 5,298 → 94 | 60 / 2,870 | 199 → 182 |
| mixed (0.5%+0.5%, sub 1%) | 5 | V5-soft-auto | 2 / 2 of 2 | yes | 32.95 → 3.49 | ×9.4 | 5,172 → 90 | 60 / 2,870 | 226 → 184 |
| mixed (0.5%+0.5%, sub 1%) | 10 | V5-hard | 2 / 2 of 2 | yes | 40.78 → 7.00 | ×5.8 | 10,448 → 201 | 131 / 5,744 | 225 → 235 |
| mixed (0.5%+0.5%, sub 1%) | 10 | V5-soft-auto | 2 / 2 of 2 | yes | 47.03 → 7.68 | ×6.1 | 10,200 → 194 | 131 / 5,744 | 266 → 233 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 1 | V5-hard | 0 / 0 of 2 | yes | 6.90 → 4.77 | ×1.4 | — → — | — / — | 150 → 144 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 1 | V5-soft-auto | 0 / 0 of 2 | yes | 8.87 → 6.15 | ×1.4 | 1,460 → 1,459 | 995 / 0 | 162 → 147 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 2 | V5-hard | 0 / 0 of 2 | yes | 14.66 → 7.64 | ×1.9 | 2,942 → 2,940 | 2,010 / 0 | 163 → 155 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 2 | V5-soft-auto | 0 / 0 of 2 | yes | 20.13 → 9.91 | ×2.0 | 2,911 → 2,908 | 2,010 / 0 | 188 → 156 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 3 | V5-hard | 0 / 0 of 2 | yes | 23.42 → 11.16 | ×2.1 | 4,429 → 4,425 | 3,018 / 0 | 175 → 166 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 3 | V5-soft-auto | 0 / 0 of 2 | yes | 28.13 → 12.26 | ×2.3 | 4,378 → 4,374 | 3,018 / 0 | 213 → 168 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 5 | V5-hard | 0 / 0 of 2 | yes | 37.22 → 15.25 | ×2.4 | 7,374 → 7,366 | 5,028 / 0 | 194 → 197 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 5 | V5-soft-auto | 2 / 2 of 2 | yes | 44.14 → 15.31 | ×2.9 | 7,290 → 7,282 | 5,028 / 0 | 265 → 199 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 10 | V5-hard | 1 / 1 of 2 | yes | 49.83 → 18.70 | ×2.7 | 14,692 → 14,677 | 10,072 / 0 | 227 → 242 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 10 | V5-soft-auto | 2 / 2 of 2 | yes | 63.09 → 11.16 | ×5.7 | 14,519 → 1,502 | 1,092 / 6,488 | 339 → 245 |

| decoder | verified SUCCESS eager / deferred | total wall s eager / deferred | verified archives per hour eager / deferred | CPU s eager / deferred | cells with different outcome | false SUCCESS |
|---|---|---|---|---|---|---|
| V5-hard | 27 / 27 | 613.9 / 217.8 | 158.3 / 446.3 | 761.7 / 566.2 | 0 | 0 of 80 |
| V5-soft-auto | 30 / 30 | 727.2 / 220.1 | 148.5 / 490.6 | 890.5 / 502.2 | 0 | 0 of 80 |

| channel | cov | V5-hard eager pass 1 (s) | deferred: cheap pass / deferred stage / pass 2 (s) | needed addresses | groups decodable after cheap pass | round B | unique addresses tried | addresses skipped |
|---|---|---|---|---|---|---|---|---|
| clean | 1 | 0.39 | 0.39 / 0.00 / 0.00 | 0 | [14, 14] | [False, False] | 0 | 0 |
| clean | 2 | 0.40 | 0.39 / 0.00 / 0.01 | 0 | [14, 14] | [False, False] | 0 | 0 |
| clean | 3 | 0.43 | 0.45 / 0.00 / 0.00 | 0 | [14, 14] | [False, False] | 0 | 0 |
| clean | 5 | 0.47 | 0.45 / 0.00 / 0.00 | 0 | [14, 14] | [False, False] | 0 | 0 |
| clean | 10 | 0.55 | 0.61 / 0.01 / 0.01 | 0 | [14, 14] | [False, False] | 0 | 0 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 1 | 3.79 | 0.49 / 3.89 / 0.08 | 453 | [1, 1] | [True, True] | 427 | 0 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 2 | 7.13 | 0.60 / 1.48 / 0.85 | 18 | [13, 13] | [False, False] | 124 | 698 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 3 | 10.13 | 0.60 / 0.63 / 0.57 | 0 | [14, 14] | [False, False] | 24 | 1,156 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 5 | 16.84 | 0.77 / 0.80 / 4.27 | 0 | [14, 14] | [False, False] | 42 | 1,769 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 10 | 24.75 | 0.97 / 1.05 / 0.38 | 0 | [14, 14] | [False, False] | 74 | 3,034 |
| mixed (0.5%+0.5%, sub 1%) | 1 | 5.76 | 0.48 / 5.52 / 0.26 | 661 | [0, 0] | [True, True] | 560 | 0 |
| mixed (0.5%+0.5%, sub 1%) | 2 | 11.06 | 0.54 / 6.93 / 0.65 | 355 | [1, 1] | [True, True] | 1,101 | 0 |
| mixed (0.5%+0.5%, sub 1%) | 3 | 16.42 | 0.65 / 2.79 / 1.28 | 72 | [9, 11] | [False, False] | 279 | 1,328 |
| mixed (0.5%+0.5%, sub 1%) | 5 | 27.42 | 0.80 / 1.03 / 1.28 | 0 | [14, 14] | [False, False] | 57 | 2,460 |
| mixed (0.5%+0.5%, sub 1%) | 10 | 40.41 | 1.05 / 1.65 / 3.98 | 0 | [14, 14] | [False, False] | 117 | 4,362 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 1 | — | — / — / — | — | [None, None] | [None, None] | — | — |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 2 | 13.30 | 0.56 / 6.60 / 2.58 | 910 | [None, 0] | [None, True] | 1,463 | 0 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 3 | 19.20 | 0.67 / 6.25 / 4.10 | 806 | [None, 0] | [True, True] | 2,192 | 0 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 5 | 31.55 | 0.81 / 8.90 / 5.33 | 586 | [0, 0] | [True, True] | 3,528 | 0 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 10 | 46.10 | 1.10 / 13.92 / 3.27 | 92 | [9, 10] | [True, True] | 6,716 | 0 |
