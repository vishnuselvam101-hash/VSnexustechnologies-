| channel | cov | decoder | SUCCESS eager / deferred | same outcome | time eager → deferred (s) | speed-up | smart attempts eager → deferred | reads tried / skipped | peak RSS eager → deferred (MB, coordinator + worker) |
|---|---|---|---|---|---|---|---|---|---|
| clean | 1 | V5-hard | 2 / 2 of 2 | yes | 0.53 → 0.57 | ×0.9 | 0 → 0 | 0 / 0 | 122 → 122 |
| clean | 1 | V5-soft-auto | 2 / 2 of 2 | yes | 0.55 → 0.53 | ×1.0 | 0 → 0 | 0 / 0 | 123 → 123 |
| clean | 2 | V5-hard | 2 / 2 of 2 | yes | 0.61 → 0.62 | ×1.0 | 0 → 0 | 0 / 0 | 131 → 132 |
| clean | 2 | V5-soft-auto | 2 / 2 of 2 | yes | 0.62 → 0.61 | ×1.0 | 0 → 0 | 0 / 0 | 132 → 132 |
| clean | 3 | V5-hard | 2 / 2 of 2 | yes | 0.69 → 0.70 | ×1.0 | 0 → 0 | 0 / 0 | 140 → 141 |
| clean | 3 | V5-soft-auto | 2 / 2 of 2 | yes | 0.73 → 0.75 | ×1.0 | 0 → 0 | 0 / 0 | 140 → 143 |
| clean | 5 | V5-hard | 2 / 2 of 2 | yes | 0.82 → 0.79 | ×1.0 | 0 → 0 | 0 / 0 | 161 → 159 |
| clean | 5 | V5-soft-auto | 2 / 2 of 2 | yes | 0.84 → 0.87 | ×1.0 | 0 → 0 | 0 / 0 | 160 → 161 |
| clean | 10 | V5-hard | 2 / 2 of 2 | yes | 0.97 → 0.95 | ×1.0 | 0 → 0 | 0 / 0 | 216 → 214 |
| clean | 10 | V5-soft-auto | 2 / 2 of 2 | yes | 0.97 → 0.93 | ×1.0 | 0 → 0 | 0 / 0 | 211 → 212 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 1 | V5-hard | 0 / 0 of 2 | yes | 4.04 → 2.35 | ×1.7 | 727 → 677 | 477 / 0 | 152 → 132 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 1 | V5-soft-auto | 0 / 0 of 2 | yes | 4.47 → 2.47 | ×1.8 | 709 → 659 | 477 / 0 | 152 → 132 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 2 | V5-hard | 2 / 2 of 2 | yes | 8.04 → 2.71 | ×3.0 | 1,446 → 185 | 136 / 729 | 170 → 141 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 2 | V5-soft-auto | 2 / 2 of 2 | yes | 9.52 → 2.72 | ×3.5 | 1,400 → 177 | 136 / 729 | 170 → 141 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 3 | V5-hard | 2 / 2 of 2 | yes | 11.85 → 1.95 | ×6.1 | 2,165 → 35 | 24 / 1,264 | 182 → 152 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 3 | V5-soft-auto | 2 / 2 of 2 | yes | 12.93 → 2.05 | ×6.3 | 2,093 → 34 | 24 / 1,264 | 184 → 151 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 5 | V5-hard | 2 / 2 of 2 | yes | 17.81 → 5.97 | ×3.0 | 3,654 → 62 | 46 / 2,126 | 193 → 186 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 5 | V5-soft-auto | 2 / 2 of 2 | yes | 20.21 → 6.22 | ×3.3 | 3,534 → 60 | 46 / 2,126 | 202 → 186 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 10 | V5-hard | 2 / 2 of 2 | yes | 25.75 → 2.65 | ×9.7 | 7,285 → 125 | 90 / 4,282 | 222 → 231 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 10 | V5-soft-auto | 2 / 2 of 2 | yes | 29.86 → 2.67 | ×11.2 | 7,060 → 120 | 90 / 4,282 | 246 → 227 |
| mixed (0.5%+0.5%, sub 1%) | 1 | V5-hard | 0 / 0 of 2 | yes | 6.20 → 2.97 | ×2.1 | 1,046 → 1,014 | 674 / 0 | 155 → 137 |
| mixed (0.5%+0.5%, sub 1%) | 1 | V5-soft-auto | 0 / 0 of 2 | yes | 7.18 → 3.16 | ×2.3 | 1,022 → 990 | 674 / 0 | 156 → 137 |
| mixed (0.5%+0.5%, sub 1%) | 2 | V5-hard | 2 / 2 of 2 | yes | 11.99 → 4.25 | ×2.8 | 2,062 → 2,008 | 1,340 / 0 | 162 → 146 |
| mixed (0.5%+0.5%, sub 1%) | 2 | V5-soft-auto | 2 / 2 of 2 | yes | 13.32 → 4.58 | ×2.9 | 2,014 → 1,958 | 1,340 / 0 | 167 → 147 |
| mixed (0.5%+0.5%, sub 1%) | 3 | V5-hard | 2 / 2 of 2 | yes | 18.19 → 3.55 | ×5.1 | 3,126 → 462 | 320 / 1,428 | 182 → 155 |
| mixed (0.5%+0.5%, sub 1%) | 3 | V5-soft-auto | 2 / 2 of 2 | yes | 21.17 → 3.63 | ×5.8 | 3,060 → 450 | 320 / 1,428 | 193 → 154 |
| mixed (0.5%+0.5%, sub 1%) | 5 | V5-hard | 2 / 2 of 2 | yes | 30.58 → 3.04 | ×10.1 | 5,298 → 94 | 60 / 2,870 | 199 → 184 |
| mixed (0.5%+0.5%, sub 1%) | 5 | V5-soft-auto | 2 / 2 of 2 | yes | 33.59 → 3.10 | ×10.8 | 5,172 → 90 | 60 / 2,870 | 226 → 184 |
| mixed (0.5%+0.5%, sub 1%) | 10 | V5-hard | 2 / 2 of 2 | yes | 40.87 → 6.47 | ×6.3 | 10,448 → 201 | 131 / 5,744 | 226 → 237 |
| mixed (0.5%+0.5%, sub 1%) | 10 | V5-soft-auto | 2 / 2 of 2 | yes | 47.73 → 7.11 | ×6.7 | 10,200 → 194 | 131 / 5,744 | 266 → 234 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 1 | V5-hard | 0 / 0 of 2 | yes | 7.43 → 2.66 | ×2.8 | — → — | — / — | 150 → 138 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 1 | V5-soft-auto | 0 / 0 of 2 | yes | 9.27 → 3.34 | ×2.8 | 1,460 → 1,459 | 995 / 0 | 162 → 141 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 2 | V5-hard | 0 / 0 of 2 | yes | 14.98 → 5.12 | ×2.9 | 2,942 → 2,940 | 2,010 / 0 | 164 → 150 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 2 | V5-soft-auto | 0 / 0 of 2 | yes | 19.42 → 6.58 | ×3.0 | 2,911 → 2,908 | 2,010 / 0 | 189 → 152 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 3 | V5-hard | 0 / 0 of 2 | yes | 23.62 → 8.65 | ×2.7 | 4,429 → 4,425 | 3,018 / 0 | 175 → 167 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 3 | V5-soft-auto | 0 / 0 of 2 | yes | 27.92 → 8.95 | ×3.1 | 4,378 → 4,374 | 3,018 / 0 | 213 → 167 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 5 | V5-hard | 0 / 0 of 2 | yes | 37.41 → 12.68 | ×2.9 | 7,374 → 7,366 | 5,028 / 0 | 195 → 198 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 5 | V5-soft-auto | 2 / 2 of 2 | yes | 44.05 → 11.37 | ×3.9 | 7,290 → 7,282 | 5,028 / 0 | 265 → 195 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 10 | V5-hard | 1 / 1 of 2 | yes | 50.58 → 14.98 | ×3.4 | 14,692 → 14,677 | 10,072 / 0 | 227 → 243 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 10 | V5-soft-auto | 2 / 2 of 2 | yes | 64.29 → 9.08 | ×7.1 | 14,519 → 1,502 | 1,092 / 6,488 | 337 → 241 |

| decoder | verified SUCCESS eager / deferred | total wall s eager / deferred | verified archives per hour eager / deferred | CPU s eager / deferred | cells with different outcome | false SUCCESS |
|---|---|---|---|---|---|---|
| V5-hard | 27 / 27 | 625.9 / 167.3 | 155.3 / 581.1 | 774.6 / 666.5 | 0 | 0 of 80 |
| V5-soft-auto | 30 / 30 | 737.3 / 161.5 | 146.5 / 668.8 | 901.8 / 620.8 | 0 | 0 of 80 |

| channel | cov | V5-hard eager pass 1 (s) | deferred: cheap pass / deferred stage / pass 2 (s) | needed addresses | groups decodable after cheap pass | round B | unique addresses tried | addresses skipped |
|---|---|---|---|---|---|---|---|---|
| clean | 1 | 0.42 | 0.45 / 0.00 / 0.00 | 0 | [14, 14] | [False, False] | 0 | 0 |
| clean | 2 | 0.43 | 0.42 / 0.00 / 0.00 | 0 | [14, 14] | [False, False] | 0 | 0 |
| clean | 3 | 0.43 | 0.44 / 0.00 / 0.00 | 0 | [14, 14] | [False, False] | 0 | 0 |
| clean | 5 | 0.49 | 0.47 / 0.00 / 0.00 | 0 | [14, 14] | [False, False] | 0 | 0 |
| clean | 10 | 0.58 | 0.57 / 0.01 / 0.01 | 0 | [14, 14] | [False, False] | 0 | 0 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 1 | 3.90 | 0.49 / 1.72 / 0.08 | 453 | [1, 1] | [True, True] | 427 | 0 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 2 | 7.14 | 0.56 / 1.21 / 0.85 | 18 | [13, 13] | [False, False] | 124 | 698 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 3 | 10.74 | 0.62 / 0.67 / 0.54 | 0 | [14, 14] | [False, False] | 24 | 1,156 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 5 | 17.36 | 0.79 / 0.73 / 4.27 | 0 | [14, 14] | [False, False] | 42 | 1,769 |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 10 | 25.40 | 1.06 / 0.90 / 0.37 | 0 | [14, 14] | [False, False] | 74 | 3,034 |
| mixed (0.5%+0.5%, sub 1%) | 1 | 5.87 | 0.51 / 2.16 / 0.25 | 661 | [0, 0] | [True, True] | 560 | 0 |
| mixed (0.5%+0.5%, sub 1%) | 2 | 11.27 | 0.60 / 2.89 / 0.67 | 355 | [1, 1] | [True, True] | 1,101 | 0 |
| mixed (0.5%+0.5%, sub 1%) | 3 | 17.10 | 0.67 / 1.45 / 1.30 | 72 | [9, 11] | [False, False] | 279 | 1,328 |
| mixed (0.5%+0.5%, sub 1%) | 5 | 28.28 | 0.86 / 0.76 / 1.24 | 0 | [14, 14] | [False, False] | 57 | 2,460 |
| mixed (0.5%+0.5%, sub 1%) | 10 | 40.52 | 1.09 / 0.96 / 4.10 | 0 | [14, 14] | [False, False] | 117 | 4,362 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 1 | — | — / — / — | — | [None, None] | [None, None] | — | — |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 2 | 13.39 | 0.58 / 3.18 / 2.60 | 910 | [None, 0] | [None, True] | 1,463 | 0 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 3 | 19.34 | 0.64 / 3.94 / 3.92 | 806 | [None, 0] | [True, True] | 2,192 | 0 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 5 | 31.62 | 0.87 / 6.01 / 5.57 | 586 | [0, 0] | [True, True] | 3,528 | 0 |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 10 | 46.77 | 1.14 / 10.21 / 3.20 | 92 | [9, 10] | [True, True] | 6,716 | 0 |
