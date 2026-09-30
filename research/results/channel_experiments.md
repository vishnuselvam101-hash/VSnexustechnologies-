# VNX-DNA channel experiments (generated)

Commit `bb33372cedf018e26013e9e2a10cca6eaffe9d01` (dirty=False), vnx-dna 1.0.0, Python 3.12.3, Linux-6.8.0-139-generic-x86_64-with-glibc2.39, 8 CPUs.

Dataset: 60,000 B (sha256 `35a8b45340c47ff3…`), default profile (64+16 outer, 40 B payload, 8 B inner parity, 244 nt strands), 1063 strands. 10 seeds per point.

Outcome counts are real decode attempts; *wrong* counts runs that returned incorrect bytes (must be 0).

## X1-dropout (64+16 outer code; varying `dropout_rate`; base {}; indel repair off)

| value | recovered | wrong data | mean observed dropped strands | mean observed events (sub/ins/del) |
|---|---|---|---|---|
| 0.0 | 10/10 | 0 | 0.0 | 0 / 0 / 0 |
| 0.01 | 10/10 | 0 | 9.9 | 0 / 0 / 0 |
| 0.05 | 10/10 | 0 | 48.2 | 0 / 0 / 0 |
| 0.1 | 9/10 | 0 | 100.9 | 0 / 0 / 0 |
| 0.15 | 2/10 | 0 | 154.0 | 0 / 0 / 0 |
| 0.2 | 0/10 | 0 | 211.1 | 0 / 0 / 0 |
| 0.25 | 0/10 | 0 | 262.8 | 0 / 0 / 0 |
| 0.3 | 0/10 | 0 | 314.7 | 0 / 0 / 0 |

## X2-substitution (64+16 outer code; varying `substitution_rate`; base {}; indel repair off)

| value | recovered | wrong data | mean observed dropped strands | mean observed events (sub/ins/del) |
|---|---|---|---|---|
| 0.0 | 10/10 | 0 | 0.0 | 0 / 0 / 0 |
| 0.001 | 10/10 | 0 | 0.0 | 256 / 0 / 0 |
| 0.005 | 10/10 | 0 | 0.0 | 1293 / 0 / 0 |
| 0.01 | 10/10 | 0 | 0.0 | 2575 / 0 / 0 |
| 0.015 | 0/10 | 0 | 0.0 | 3849 / 0 / 0 |
| 0.02 | 0/10 | 0 | 0.0 | 5135 / 0 / 0 |
| 0.03 | 0/10 | 0 | 0.0 | 7725 / 0 / 0 |

## X3-sub+dropout (64+16 outer code; varying `substitution_rate`; base {'dropout_rate': 0.1}; indel repair off)

| value | recovered | wrong data | mean observed dropped strands | mean observed events (sub/ins/del) |
|---|---|---|---|---|
| 0.001 | 9/10 | 0 | 100.9 | 233 / 0 / 0 |
| 0.005 | 9/10 | 0 | 100.9 | 1172 / 0 / 0 |
| 0.01 | 0/10 | 0 | 100.9 | 2333 / 0 / 0 |

## X4-indel-no-repair (64+16 outer code; varying `deletion_rate`; base {'insertion_rate': 0.0}; indel repair off)

| value | recovered | wrong data | mean observed dropped strands | mean observed events (sub/ins/del) |
|---|---|---|---|---|
| 0.0002 | 10/10 | 0 | 0.0 | 0 / 0 / 54 |
| 0.0005 | 7/10 | 0 | 0.0 | 0 / 0 / 134 |
| 0.001 | 0/10 | 0 | 0.0 | 0 / 0 / 256 |
| 0.002 | 0/10 | 0 | 0.0 | 0 / 0 / 516 |

## X5-indel-with-repair (64+16 outer code; varying `deletion_rate`; base {'insertion_rate': 0.0}; indel repair on)

| value | recovered | wrong data | mean observed dropped strands | mean observed events (sub/ins/del) |
|---|---|---|---|---|
| 0.0002 | 10/10 | 0 | 0.0 | 0 / 0 / 54 |
| 0.0005 | 10/10 | 0 | 0.0 | 0 / 0 / 134 |
| 0.001 | 10/10 | 0 | 0.0 | 0 / 0 / 256 |
| 0.002 | 10/10 | 0 | 0.0 | 0 / 0 / 516 |

## X6-mixed-indel-repair (64+16 outer code; varying `insertion_rate`; base {'deletion_rate': 0.0005, 'substitution_rate': 0.002}; indel repair on)

| value | recovered | wrong data | mean observed dropped strands | mean observed events (sub/ins/del) |
|---|---|---|---|---|
| 0.0005 | 10/10 | 0 | 0.0 | 516 / 135 / 125 |
| 0.001 | 10/10 | 0 | 0.0 | 516 / 263 / 125 |

## X7-dropout-96+48 (96+48 outer code; varying `dropout_rate`; base {}; indel repair off)

| value | recovered | wrong data | mean observed dropped strands | mean observed events (sub/ins/del) |
|---|---|---|---|---|
| 0.1 | 10/10 | 0 | 123.3 | 0 / 0 / 0 |
| 0.15 | 10/10 | 0 | 187.3 | 0 / 0 / 0 |
| 0.2 | 10/10 | 0 | 254.9 | 0 / 0 / 0 |
| 0.25 | 10/10 | 0 | 316.2 | 0 / 0 / 0 |
| 0.3 | 4/10 | 0 | 379.6 | 0 / 0 / 0 |
