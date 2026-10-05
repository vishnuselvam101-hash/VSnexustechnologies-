# P4-EXP-01: failure taxonomy of the current decoder (SIMULATED)

**SIMULATED.** Software strands, the versioned channel models of `experiments/v6/channel` (plus derived models in
`models/`, each recording the model it was derived from), and the stock decoder. No DNA was synthesised, stored or
sequenced. Nothing here is biological validation.

## Provenance

| item | value |
|---|---|
| code | commit `2eac8e2` (work/v6-consensus; build/v6-sprint `a6a38f8` + the phase-4 harness and the opt-in, default-off consensus option); `dirty_tracked: false` (only this run's own output files untracked) |
| command | `PYTHONPATH=src nice -n 10 python experiments/v6/phase4/phase4.py run --config experiments/v6/phase4/P4-EXP-01-failure-taxonomy/config.json --jobs 3` |
| backends | align native, reads native, RS native (recorded in the header line of `trials.jsonl`) |
| seeds | trial seeds 41000-41019 (G4: 41000-41004); data seed 6201; decode workers 1 |
| files | `config.json`, `trials.jsonl` (header, 770 trial records, every one kept), `summary.json` (`phase4.py summarise`), `log.txt` |
| arms | `default` = stock `DecodeOptions`; `smart-auto` = existing opt-in V5 smart indel recovery + soft decoding `auto`, deferred schedule. G2 nanopore-like and G4 cells: `default` only (CPU budget) |

Stage classes (from the decode report, the pass-1 events and a ground-truth observer; definitions in the docstring of
`phase4.py`): `superblock` = refused before pass 2 (`NO_SUPERBLOCK`); `outer-loss` = a failed group lacks more than M
symbols whose strand had no alignable read (lost, zero coverage, every read beyond the band); `outer-address` = not
loss-limited, but the strands whose aligned reads never reached pass 2 under their address make it so;
`outer-consensus` = every failed group would decode if consensus had recovered the addresses that had pending reads.
False SUCCESS: **0 of 1,450 decodes**.

## Results: every cell, both arms (exact / trials, and the stage of every failure)

| cell | default | default failures by stage | smart-auto | smart-auto failures | median decode s (default / smart) |
|---|---|---|---|---|---|
| clean, dropout-5, dropout-10, illumina-like, mixed-mild, quality-degradation, substitution-heavy, uneven-coverage | 20/20 each | — | 20/20 each | — | 0.07-0.27 / 0.07-0.62 |
| burst-loss | 3/20 | outer-loss 17 | 3/20 | outer-loss 17 | 0.38 / 0.64 |
| dropout-20 | 0/20 | outer-loss 20 | 0/20 | outer-loss 20 | 0.16 / 0.36 |
| deletion-heavy | 0/20 | outer-consensus 20 | 20/20 | — | 0.43 / 3.73 |
| insertion-heavy | 0/20 | outer-consensus 20 | 20/20 | — | 0.44 / 3.91 |
| mixed-harsh | 19/20 | outer-consensus 1 | 20/20 | — | 0.70 / 3.03 |
| nanopore-like | 0/20 | superblock 20 | 0/20 | superblock 20 | 0.50 / 17.4 |
| mixed-mild cov 2 | 6/20 | outer-loss 7, outer-consensus 5, outer-address 2 | 12/20 | outer-loss 7, outer-consensus 1 | 0.07 / 0.24 |
| mixed-mild cov 3, 5, 30 | 20/20 each | — | 20/20 each | — | |
| substitution-heavy cov 2 | 0/20 | outer-address 13, outer-loss 5, outer-consensus 2 | 8/20 | outer-loss 6, outer-address 3, outer-consensus 3 | 0.10 / 1.16 |
| substitution-heavy cov 3 | 16/20 | outer-consensus 4 | 20/20 | — | 0.12 / 0.30 |
| substitution-heavy cov 5, 30 | 20/20 each | — | 20/20 each | — | |
| deletion-heavy cov 2 | 0/20 | superblock 12, outer-loss 8 | 0/20 | outer-loss 20 | 0.14 / 3.99 |
| deletion-heavy cov 3 | 0/20 | outer-address 12, superblock 5, outer-loss 3 | 0/20 | outer-address 16, outer-loss 4 | 0.20 / 5.70 |
| deletion-heavy cov 5 | 0/20 | outer-consensus 13, outer-address 7 | 0/20 | outer-consensus 20 | 0.30 / 9.48 |
| deletion-heavy cov 30 | 20/20 | — | 20/20 | — | 0.96 / 2.71 |
| nanopore-like cov 2, 3, 5, 30 | 0/20 each | superblock 20 each | not run | | |
| B0-like `s184` (no markers) cov 5, no dropout | 4/20 | outer-consensus 16 | 20/20 | — | 0.14 / 1.87 |
| B0-like `s184` cov 10, no dropout | 20/20 | — | 20/20 | — | |
| B0-like `s184` cov 5 and 10, 10 % dropout | 0/20 each | outer-loss 20 each | 0/20 each | outer-loss 20 each | |
| B0-like v4-balanced, cov 5 / 10, 0 / 10 % dropout | 20/20 each | — | 20/20 each | — | 0.13-0.24 / 0.20-0.36 |
| long archive 1 MiB mixed-mild | 5/5 | — | not run | | 5.1 |
| long archive 1 MiB nanopore-like | 0/5 | superblock 5 | not run | | 12.1 |

## Where it fails, with the numbers behind it

| question | data (this run unless stated) |
|---|---|
| Alignment: how many reads can be aligned at all? | Share of reads with abs(length − 313) ≤ 6 (the band): nanopore-like 0.36 (mean drift −8.0 nt), deletion-heavy 0.65 (−5.6), insertion-heavy 0.65 (+5.6), every other named model ≥ 0.995. Pass-1 verified reads: nanopore-like 0.004 %, deletion/insertion-heavy 9-10 %, mixed-harsh 11 %, substitution-heavy 68 %, mixed-mild 95 % |
| Where inside the frame consensus leaves undetected errors | Wrong (unerased) bytes of multi-read count-vote attempts, by 6-byte segment: segments 0-1 (header) 2-6 % each, segments 2-10 about 9-12 % each, last (5-byte) segment 3-4 %; by region (deletion-heavy): header 4 %, payload 65 %, CRC 7 %, inner parity 24 %. Headers are under-represented because reads are grouped by header |
| What the count vote leaves per multi-read attempt | deletion-heavy: 3.3 wrong + 12.4 erased bytes, 61 % within 2e + f ≤ r (r = 16); insertion-heavy 3.5 + 13.5, 54 %; mixed-harsh 3.6 + 14.6, 63 %. Erasures dominate, mostly whole indel segments |
| Ambiguity | split votes become erasures (threshold 0.6); a 1-vs-1 split always erases |
| Failure modes | (1) reads beyond the band → no superblock (nanopore-like, deletion-heavy at coverage ≤ 3); (2) strand loss above outer parity (dropout-20, burst-loss, B0-like `s184` with 10 % dropout: M/(K+M) = 8/72 = 11 % parity against 10 % i.i.d. loss plus zero-coverage strands); (3) addresses never reaching pass 2 at low coverage (outer-address); (4) consensus-limited groups (deletion/insertion-heavy at coverage 10, `s184` at coverage 5, substitution-heavy at coverage 3) |
| Interaction with RS / soft decisions | Every failure was detected (0 false SUCCESS in 1,450 decodes). The existing opt-in smart+soft path removes failure mode (4) in every cell where it was the only mode (deletion-heavy, insertion-heavy, mixed-harsh, substitution-heavy cov 3, `s184` cov 5: all 20/20) at 3-10 times the decode time; it does not touch modes (1)-(3) |
| Complexity | count vote O(m · L) per address; smart+soft median decode time up to 9.5 s against 0.30 s (deletion-heavy cov 5), 17.4 s against 0.50 s on nanopore-like where it gains nothing |
| B0 weakness (benchmark lab: VNX weaker than DNA-RS at 1 % errors near 1 bit/nt and at 10 % dropout) | Reproduced in simulation: `s184` (no markers) 4/20 at 1 % / coverage 5 (consensus-limited) and 0/20 at 10 % dropout (loss-limited, outer parity 11 %). The same channel on v4-balanced (markers, r = 16, M/(K+M) = 20 %) decodes 20/20 in all four cells, at a lower rate (0.74 against 0.99 bit/nt in B0). Smart+soft makes `s184` cov 5 20/20 |

## Limitations

The channel models are synthetic and i.i.d. apart from their explicit homopolymer, burst and GC terms; the qualities are
informative by construction where `quality_informative` > 0. 20 seeds per cell (5 for G4): a 20/20 cell has a Wilson
95 % interval of [0.84, 1]. Process peak-RSS values in `summary.json` are high-water marks of long-lived worker
processes, not per-trial peaks. Stage attribution uses ground truth unavailable to the decoder.
