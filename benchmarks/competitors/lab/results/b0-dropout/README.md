# b0-dropout: VNX-DNA profiles for heavy strand dropout at matched code rate (SIMULATED)

**SIMULATED.** All channel results come from the harness's software `ErrorGenerator`. Times and peak memory are MEASURED
on a shared VPS under load (indicative only). No DNA was synthesised, stored or sequenced.

Decision: **ACCEPT** under the pre-registered rule 3. The lead profile `hd-l256-i4` becomes the opt-in named redundancy
profile `high-dropout`. DNA-RS-medium still recovers more on this grid: 100/100 exact against 88/100.

## Question and pre-registration

In B0, VNX `s184` (about 1.0 bit/nt) recovered 0/3 at 10 % dropout, while DNA-RS-medium and DNA-Fountain-medium recovered 3/3.
Can VNX-DNA reach heavy-dropout tolerance at the same code rate using only existing format options? The protocol, the
participants and decision rules 1-4 are in [`PREREG.md`](PREREG.md). It was committed in 1946f3c before any
pre-registered trial ran. The screen in `screen/` used separate seeds (101-104) and is not part of the comparison.

## Method

* Input `random/19kB` (19,456 B), coverage 10, errors 0.5 % and 1 % (53/45/2 sub/del/ins), dropout 0/5/10/15/20 %,
  seeds 1-10, so 100 trials per participant. Harness `dt4dds-benchmark` 0928fd7f26 through `bench-tools/lab-driver/run_b0.py`, as in B0.
  VNX-DNA gets raw reads (`NoClustering`). DNA-RS and DNA Fountain get `BasicSet` reads. The step limit is 900 s.
* VNX trials ran at commit 50f6441: build/v6-sprint e27bf2e merged in, clean tree. They used 3 parallel lanes, each limited
  to one core. Every trial has a fixed seed, so how the trials are split across lanes does not change the results.
  The same 720 VNX trials had also run at 1946f3c, before the merge. All 720 have identical strand, read and output SHA-256.
* The DNA-RS and DNA Fountain trials come from the run at 1946f3c. Those codecs run as separate third-party processes,
  so their results do not depend on the VNX commit. They were not re-run.
* Provenance is in `provenance.json`: VNX commit f3c703a (clean), native backends, machine, tool SHAs, driver hashes, and
  every `driver_meta`. The command and per-trial output are in `log.txt`. All trials are in `trials_*.jsonl`; none was filtered.
  The tables are written by `../../dropout_report.py`, which also applies rule 3 (`report_main.md` / `.json`, `report_r05.md` / `.json`).

## Results: main grid, about 1.0 bit/nt (`report_main.md`)

Exact (SHA-256) recoveries out of 10 per cell, then the total out of 100. FS = false SUCCESS.

| participant | strand nt | bits/nt | e0.5 % d0/5/10/15/20 % | e1 % d0/5/10/15/20 % | total | FS | decode median s | decode peak RSS MiB |
|---|---|---|---|---|---|---|---|---|
| vnx-s184 (B0 baseline) | 184 | 0.9871 | 10 8 0 0 0 | 10 7 0 0 0 | 35 | 0 | 0.8 | 63 |
| vnx-hd-l208-i2 | 208 | 0.9964 | 10 10 10 9 3 | 10 10 10 9 0 | 81 | 0 | 0.9 | 65 |
| vnx-hd-l232-i2 | 232 | 0.9998 | 10 10 10 10 7 | 10 10 10 8 0 | 85 | 0 | 0.8 | 64 |
| **vnx-hd-l256-i4** | 256 | 0.9984 | 10 10 10 10 7 | 10 10 10 8 3 | **88** | 0 | 0.9 | 63 |
| vnx-hd-l280-i4 | 280 | 0.9998 | 10 10 10 10 10 | 10 10 10 7 0 | 87 | 0 | 0.8 | 63 |
| vnx-hd-l280-i4-c1 | 280 | 0.9909 | 10 10 10 10 10 | 10 10 10 6 0 | 86 | 0 | 0.8 | 63 |
| dna-rs-medium | 144 | 0.9981 | 10 10 10 10 10 | 10 10 10 10 10 | 100 | 0 | 61.0 | 16 |
| dna-fountain-medium | 152 | 1.0020 | 10 10 10 10 10 | 3 0 0 0 0 | 53 | 47 | 1.2 | 81 |

### Pre-registered criteria against results

| criterion (PREREG) | result | pass |
|---|---|---|
| Rule 1: VNX false SUCCESS = 0 | 0 of 600 VNX trials (0 of 120 more in the secondary grid) | yes |
| Rule 3, lead profile | `hd-l256-i4`, 88/100 (next: `hd-l280-i4` 87, `hd-l280-i4-c1` 86, `hd-l232-i2` 85, `hd-l208-i2` 81) | - |
| (a) bits/nt in 0.995-1.005 at 19 kB | 0.9984 (target 1.0, 0.16 % low; job criterion "within 2 %" met) | yes |
| (b) false SUCCESS 0 | 0/100 | yes |
| (c) at least `s184` in every cell | 10/10 cells at least equal | yes |
| (c) strictly better (Wilson-separated) in at least one cell | 5 cells: 0.5 % error at dropout 10/15/20 %; 1 % error at dropout 10/15 % (for example 10/10, Wilson 0.72-1.00, against 0/10, 0-0.28) | yes |
| (d) at least 8/10 in every cell with dropout up to 10 % | 10/10 in all 6 such cells | yes |
| **Decision** | **ACCEPT**: added as named redundancy profile `high-dropout` | |

### Where VNX-DNA loses (rule 4)

* **Against DNA-RS-medium**, `hd-l256-i4` loses one cell: 1 % errors at 20 % dropout, 3/10 against 10/10. The other 9 cells
  show no separation. Pooled over all cells, DNA-RS is ahead: 100/100 (Wilson 0.96-1.00) against 88/100 (0.80-0.93).
  Every VNX profile fails at 20 % dropout with 1 % errors (0-3/10). DNA-RS recovered every trial in this grid.
* **Costs:**
  * Strands are 256 nt, against 144 nt for DNA-RS: 78 % longer, so a longer synthesis.
  * Decode peak RSS is 63 MiB, against 16 MiB for DNA-RS.
  * DNA-RS needed 61 s median decode against 0.9 s for VNX-DNA. Given the timing caveat, treat these times as indicative only.
* **Against DNA-Fountain-medium**, `hd-l256-i4` is Wilson-separated above it in 4 cells and below it in none. DNA Fountain
  returned wrong data with exit 0 in 47 of 100 trials.
* Column parity (`hd-l280-i4-c1`) did not help on this 19 kB archive. It scored 86 against 87 for `hd-l280-i4`, at 0.9909 bit/nt.

## Secondary grid, about 0.5 bit/nt (`report_r05.md`)

Errors 0.5 % and 1 %, dropout 10/20/30 %, seeds 1-10.

| participant | bits/nt | exact | FS |
|---|---|---|---|
| vnx-hd-l184-i2-r05 | 0.5002 | 60/60 | 0 |
| vnx-hd-l232-i2-r05 | 0.4996 | 60/60 | 0 |
| dna-fountain-low | 0.5027 | 52/60 | 8 |
| dna-rs-low (incomplete) | 0.4994 | 14/14 | 0 |

There is no Wilson separation in any cell. DNA-RS-low is incomplete. Its decoder needed up to 610 s per trial, and two
chunks hit the 3000 s cap, so it has only 14 of 60 trials (0.5 % errors, 10 % and 20 % dropout). Those trials cannot
support a conclusion about DNA-RS-low. The secondary grid is reported only. No profile is shipped from it.

## Limitations

* The grid has 10 seeds per cell, one input (19 kB of random bytes) and one coverage (10).
* Dropout is i.i.d., with no synthesis, PCR or sequencing bias.
* The VNX adapter does not enable the V6 opt-in recovery.
* VNX-DNA receives raw reads and the others receive `BasicSet` reads.
* The rate is matched at 19 kB only. The fixed container costs more on smaller files (see the B0 README).
* Profiles were tuned to this archive size with `archive.compression none`.
* At other sizes, the planner and fixed overheads change the dropout threshold. Measure on the target size before
  relying on these numbers.
* This is not the published ETH protocol, and none of these numbers enter `records.json`.
