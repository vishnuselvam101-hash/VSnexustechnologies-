# b0-dropout secondary grid, about 0.5 bit/nt (SIMULATED; DNA-RS-low incomplete)

All channel results SIMULATED; time and RSS MEASURED on a shared host. Cells: exact (SHA-256) / n (Wilson 95 %).

## Participants: rate and cost

| participant | strand nt | nt/byte | bits/nt | trials | exact | false-SUCCESS | decode median s [max] | decode peak RSS MiB (max) | encode peak RSS MiB |
|---|---|---|---|---|---|---|---|---|---|
| dna-fountain-low | 152 | 15.91 | 0.5027 | 60 | 52 | 8 | 1.1 [1] | 81 | 88 |
| dna-rs-low | 150 | 16.02 | 0.4994 | 14 | 14 | 0 | 313.3 [610] | 17 | 14 |
| vnx-hd-l184-i2-r05 | 184 | 15.99 | 0.5002 | 60 | 60 | 0 | 0.9 [1] | 66 | 53 |
| vnx-hd-l232-i2-r05 | 232 | 16.01 | 0.4996 | 60 | 60 | 0 | 0.9 [1] | 70 | 52 |

## Exact recovery per cell

### error 0.5 %

| participant | dropout 10 % | dropout 20 % | dropout 30 % | all cells |
|---|---|---|---|---|
| dna-fountain-low | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 30/30 (0.89-1.00) |
| dna-rs-low | 7/7 (0.65-1.00) | 7/7 (0.65-1.00) | - | 14/14 (0.78-1.00) |
| vnx-hd-l184-i2-r05 | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 30/30 (0.89-1.00) |
| vnx-hd-l232-i2-r05 | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 30/30 (0.89-1.00) |

### error 1 %

| participant | dropout 10 % | dropout 20 % | dropout 30 % | all cells |
|---|---|---|---|---|
| dna-fountain-low | 9/10 (0.60-0.98) | 8/10 (0.49-0.94) | 5/10 (0.24-0.76) | 22/30 (0.56-0.86) |
| dna-rs-low | - | - | - | - |
| vnx-hd-l184-i2-r05 | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 30/30 (0.89-1.00) |
| vnx-hd-l232-i2-r05 | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 10/10 (0.72-1.00) | 30/30 (0.89-1.00) |

## Pooled over error rates, per dropout

| participant | dropout 10 % | dropout 20 % | dropout 30 % | all |
|---|---|---|---|---|
| dna-fountain-low | 19/20 (0.76-0.99) | 18/20 (0.70-0.97) | 15/20 (0.53-0.89) | 52/60 (0.76-0.93) |
| dna-rs-low | 7/7 (0.65-1.00) | 7/7 (0.65-1.00) | - | 14/14 (0.78-1.00) |
| vnx-hd-l184-i2-r05 | 20/20 (0.84-1.00) | 20/20 (0.84-1.00) | 20/20 (0.84-1.00) | 60/60 (0.94-1.00) |
| vnx-hd-l232-i2-r05 | 20/20 (0.84-1.00) | 20/20 (0.84-1.00) | 20/20 (0.84-1.00) | 60/60 (0.94-1.00) |

## Per-cell separation (PREREG rule: beats = Wilson lower bound above the other's upper bound)

| VNX profile | reference | beats (cells) | loses (cells) | no separation (cells) |
|---|---|---|---|---|
| vnx-hd-l184-i2-r05 | dna-fountain-low | 0 | 0 | 6 |
| vnx-hd-l184-i2-r05 | dna-rs-low | 0 | 0 | 2 |
| vnx-hd-l232-i2-r05 | dna-fountain-low | 0 | 0 | 6 |
| vnx-hd-l232-i2-r05 | dna-rs-low | 0 | 0 | 2 |
