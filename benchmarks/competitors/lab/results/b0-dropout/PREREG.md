# b0-dropout pre-registration (written and committed before any pre-registered trial was run)

All channel results are SIMULATED (harness `ErrorGenerator`). Timing and RSS are MEASURED on a shared VPS.

## Question

Can VNX-DNA be configured, with existing format options only, to tolerate heavy strand dropout at about 1.0 bit/nt
(and secondarily about 0.5 bit/nt) while keeping the 0.5-1 % error tolerance of `s184`, on the protocol where the B0
profiles `s184`/`s280` recovered 0/3 at 10 % dropout while DNA-RS-medium and DNA-Fountain-medium recovered 3/3?

## Design choice (before simulation)

B0 profiles used outer code 64+8 (11 % parity, rows of 72) on 28-44 byte payloads with 4-12 bytes of inner parity.
The V6 analytic bounds (`vnxdna.codec.outer.plan`, `dropout_threshold`, exact overhead from `Geometry`) were evaluated for
strand lengths 148-376 nt and inner parity 0-12 bytes at a fixed rate of 1.0 bit/nt. Findings used for the choice:

* the fixed per-strand cost (14 bytes) falls with strand length, so longer strands free budget for outer parity;
* the planner picks single-stripe rows of 200-255 symbols (longer rows concentrate the loss count) and never column parity
  for this 19 kB archive (12 data groups at K=64 would be too few rows to repay a column-parity row);
* analytic i.i.d. loss thresholds (failure bound <= 1e-3): `s184` about 0.04, 232 nt/2 B inner parity 0.15, 280 nt/4 B 0.15.

A screen on a separate seed set (101-104, coverage 10; `configs/b0_dropout_screen*.json`, exploration only, not part of the
comparison) selected the finalists below. Profiles are in `adapters/vnx/profiles/` (`hd-lLLL-iII`: strand nt LLL, inner parity
bytes II; outer K+M in the file). Tuned so that bits/nt at 19 kB is 0.996-1.002.

## Pre-registered comparison

* Input `random/19kB` (19,456 B), coverage 10, error composition 53/45/2, harness `ErrorGenerator`, `NoClustering` for VNX
  (raw reads) and `BasicSet` for the public codecs, as in B0 sweep C. Step limit 900 s.
* Cells: dropout 0, 5, 10, 15, 20 % x errors 0.5, 1 % = 10 cells; seeds 1-10 (B0 used 1-3, included) = 100 trials per participant.
* Participants (about 1.0 bit/nt): VNX `s184` (B0 baseline), `hd-l208-i2`, `hd-l232-i2`, `hd-l256-i4`, `hd-l280-i4`,
  `hd-l280-i4-c1` (adds column parity 1 per stripe, to test it), DNA-RS-medium, DNA-Fountain-medium (rerun on the same cells).
* Secondary (about 0.5 bit/nt), run only if time remains after the main grid: errors 0.5/1 %, dropout 10/20/30 %,
  seeds 1-10; VNX `hd-l232-i2-r05`, `hd-l184-i2-r05`, DNA-RS-low, DNA-Fountain-low (their actual bits/nt are reported;
  if they are not within 10 % of 0.5 the comparison is labelled unmatched).
* Metrics per cell: exact (SHA-256 equal) count with Wilson 95 % CI, false-SUCCESS count (exit 0 with differing output),
  nt/byte and bits/nt (exact, from the written strands), median and max decode seconds, decode peak RSS.

## Decision rules (fixed in advance)

1. VNX false-SUCCESS must be 0; any non-zero count stops the work and is reported as a bug.
2. "VNX beats X in a cell": Wilson lower bound of VNX > Wilson upper bound of X. "Loses": the reverse. Otherwise "no
   separation". Pooled statements use the pooled counts. No claim of superiority beyond these cells and this protocol.
3. A profile is the "lead profile" if it has the highest total exact count over the 100 trials among the VNX finalists
   (ties: shorter strand). It is added as a named redundancy profile `high-dropout` only if (a) its bits/nt is within
   0.995-1.005 at 19 kB, (b) it has false-SUCCESS 0, (c) it is at least `s184` in every cell and strictly better
   (non-overlapping CIs) in at least one, and (d) it recovers at least 8/10 in each cell with dropout <= 10 %.
4. Losses are reported plainly (cells, costs: longer strands, memory, decode time).

## Known limitations of this protocol

3-10 seeds per cell, one input, one coverage, iid dropout, no synthesis/PCR bias; the VNX adapter does not enable V6 opt-in
recovery; all profiles use the same archive container (about 7 % fixed overhead at 19 kB).
Trial files and provenance: `results/b0-dropout/`.
