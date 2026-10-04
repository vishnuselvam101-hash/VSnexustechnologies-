# VNX-DNA V5 — Phase 3 optimisation: deferred per-read recovery (Gate B)

All channel results are **SIMULATED** (V4 channel simulator and harness-built reads). No DNA was synthesised or
sequenced. Timings come from one machine (Intel Xeon Gold 6240, 8 CPUs) and will differ elsewhere.

## 1. Summary

**Result (all SIMULATED).** Phase 3/4 per-read smart and soft recovery now runs after a cheap pass, and only on reads
that can still change the result. A read is searched only if its header names a missing address in a group that is
not yet decodable. If any group stays undecodable, every remaining read is searched, so the search never does less
than Phase 3/4. The recovery algorithm itself is unchanged. The old behaviour is kept as `recovery_schedule="eager"`.
The new default is `"deferred"`.

* **Verified archive recovery per unit time** (P3O-EXP-01: 4 channels × 5 coverages × 2 seeds, 8 workers, same read
  files for both schedules): V5-hard **155 → 581 verified archives per hour (×3.7)**, V5-soft-auto **147 → 669
  (×4.6)**. Total wall time 626 s → 167 s and 737 s → 162 s.
* **Same verified outcome in all 80 cell/decoder pairs.** Same status, same recovered-group fraction, same output
  SHA-256. **0 false SUCCESS** in 160 decodes.
* **Where the work is saved:** wherever the cheap pass already makes the groups decodable. On the mixed channel at
  coverage 5, smart attempts fall from 5,298 to 94 and the decode from 30.6 s to 3.0 s. On the indel-heavy channel at
  coverage 10 they fall from 7,285 to 125 (25.8 s → 2.7 s). CPU time falls 14 % (V5-hard) and 31 % (V5-soft-auto)
  in total, and by up to 6× in those cells.
* **What the control showed (P3O-EXP-02):** a large part of Phase 3's slowness was **load balance, not wasted
  work**. Phase 3 with 256-read pass-1 batches is already 3.8× faster in wall time than with the default 8,192. The
  deferred schedule is faster still in total (93 s vs 100 s vs 379 s) and uses the least CPU (393 vs 551 vs 488 CPU
  seconds). It is **slower than the well-balanced eager schedule where nothing can be skipped**: failing archives at
  coverage 1 (2.4 s vs 1.6 s) and the substitution-heavy channel (15.0 s vs 13.6 s at coverage 10).
* **Clean channels:** no change (0 reads reach the stage; ±0.04 s).
* Tests: 16 new (`tests/v5/test_recovery_schedule.py`), full suite **1013 / 1013** (997 existing + 16 new) on the final
  commit `d41530d`.

## 2. The bottleneck, measured

The Phase 4 report attributed the 25–35× slowdown of Phase 3 on noisy channels to smart recovery running on every
read that V4 fails, including reads of addresses that other reads already recover. The stored stage timings of
P4-EXP-08 (32 KiB archive, 8 workers) confirm where the time goes:

| channel, coverage 10 | V4 total | V5-hard total | V5-hard pass 1 | V5-hard pass 2 | reads | smart attempts |
|---|---|---|---|---|---|---|
| indel 0.5 % + 0.5 % + sub 1 % | 1.4 s | 39.0 s | **38.7 s** | 0.0 s | 10,880 | 10,433 |
| indel 0.25 % + 0.25 % + sub 4 % | 1.6 s | 49.1 s | **45.7 s** | 2.9 s | 10,880 | 14,711 |

Pass 1 is the whole cost. Smart recovery was attempted about once per read; more than once on the second channel,
because a read that fails in both orientations is searched in both. On the first channel V4 alone decodes the archive,
so all of that work was unnecessary.

A control experiment (P3O-EXP-02, §6) found a second, independent cause. Pass 1 hands reads to the workers in batches
of `batch_reads` = 8,192. With smart recovery inside pass 1, an archive of about 10,000 reads keeps only two of eight
workers busy. Part of the measured slowdown was this load imbalance, not wasted work. Both causes are reported
separately below.

## 3. Design

The recovery algorithm is unchanged: the deferred stage calls the same per-read function (`_process` → `_try`, with
smart and soft recovery on) that pass 1 called in Phase 3/4. What changes is *when* it runs and *on which reads*.

```
pass 1 (cheap)      fast path + sync path, both orientations, V4 rules only       ── parallel, streaming
                    accepted → verified frames; failed but aligned → pending (raw read kept)
                    failed, aligned, no readable header → unaddressed (kept)
deferred stage      round S  pending reads whose header says superblock (always)
                    superblock → expected addresses → group state
                    round A  TARGETED reads (header reading, or its one-byte snap, is a NEEDED address)
                    group state again
                    round B  only if a group is still INCOMPLETE (or no superblock): every read not yet tried,
                             including unaddressed reads
pass 2 (unchanged)  duplicates → V4 consensus → smart consensus → soft consensus → outer decode → SHA-256
```

### 3.1 Explicit state (`decoder._deferred_recovery`, `_group_state`)

| state | definition |
|---|---|
| **recovered address** | at least one verified frame (inner RS + CRC-32 + version/kind checks) whose payload survives duplicate resolution (strict majority of identical verified copies; ties dropped, as in pass 2) |
| **missing address** | expected by the superblock (`codec.symbols_for(k)` symbols per group), not recovered |
| **decodable group** | its recovered symbols, plus the symbols pass 2's **V4 consensus vote** will recover, satisfy pass 2's outer decode (`_group_decodable`: ≥ k distinct symbols for the MDS Cauchy-RS code, a trial decode for LT) |
| **incomplete group** | not decodable |
| **needed address** | missing, in an incomplete group |
| **insufficient consensus** | a needed address whose pending reads exist but whose V4 vote does not verify. These reads are targeted in round A |
| **targeted read** | pending, and either header reading (projected or raw prefix), or its unique one-byte snap, is a needed address (the same snapping rule as pass 2) |
| **skipped read** | never tried: every reading points at a recovered address or a decodable group, and round B was not needed |
| **unaddressed read** | aligned and failed, with no readable header. In Phase 3/4 it was searched in pass 1 and then dropped. Here it is kept for round B |
| **unrecoverable address** | still missing after pass 2. Its group fails and is reported in `failed_groups` (PARTIAL/FAILURE semantics unchanged) |

### 3.2 Why skipping is safe

* **A decodable group stays decodable.** The V4 vote counted in the group state is computed exactly as pass 2
  computes it: same known symbols, same missing set (which affects snapping), same pending reads, with consumed ones
  removed. Pass 2 only adds smart and soft consensus on top, so every group called decodable here decodes in pass 2.
  Random-access decodes (`select`) use other missing sets in pass 2, so for them only single verified reads count.
  Pass 2 can only add to that.
* **Nothing is left untried while data is missing.** If any group is still incomplete after round A, round B tries
  every remaining read, including unaddressed ones and reads whose header points elsewhere (a header can be wrong).
  A failing archive therefore gets at least the per-read search that Phase 3/4 gave it.
* **Frames are filed by verified address.** A recovered frame goes to the spill bucket of its *verified* group, not
  its tentative one, and its pending record is removed. This is exactly what happens to a read accepted in pass 1, so
  it cannot vote again in a consensus, and duplicates cannot pose as missing frames.
* **Verification is unchanged.** Every accepted frame still passes inner RS, CRC-32 and the metadata checks, and the
  container still has to match the superblock's SHA-256 before anything is published.

### 3.3 Behavioural differences from the eager schedule (reported, not hidden)

1. **Orientation.** The deferred stage searches each pending read in its stored orientation (the better-scoring one).
   If that fails at high cost, it also searches the reverse complement, as `_process` always does. Phase 3/4 searched
   the forward orientation first, then the reverse complement of high-cost reads. So for a read whose reverse
   complement scored better at low cost, the forward orientation is no longer searched. In the wrong orientation a
   read is effectively a random word, and passes RS + CRC-32 only with the probability Phase 3 bounds (≤ 10⁻⁹ per
   read). So this removes work, not recoveries. In every experiment the verified outcome
   is identical (§5–6).
2. **Redundant per-read decodes are skipped.** Reads of addresses that are already recovered, or of groups already
   decodable, are no longer smart-decoded. They stay pending, as any failed read does. In pass 2 they can join a V4
   vote for an address that is missing but not needed, in a group that is already decodable. That cannot change the
   group's decoded data.
3. **Report fields.** `reads.pending` counts pass-1 pending reads (before the stage). A new
   `report["recovery_schedule"]` records the stage. `recovery_schedule="eager"` restores the Phase 3/4 behaviour
   exactly (it is the "before" configuration in every experiment).

### 3.4 Parallelism

The stage runs its rounds on the decode's worker pool, in tasks of about four per worker and at most 256 reads.
Per-read results do not depend on the chunking (checked: identical reports for 1 and 3 workers). The first version
used fixed 256-read tasks; P3O-EXP-02 showed that a small round A then used only two workers. Those first results are
kept in `experiments/v5/phase3opt/superseded-fixed-chunks/` (§7).

## 4. Tests

`tests/v5/test_recovery_schedule.py`, 16 tests, built on a real archive's strands with controlled damage. Each
damaged read is first checked to fail the V4 paths and to be accepted by eager smart recovery (or soft, for test 9).

| mission item | test | what it checks |
|---|---|---|
| 1 all addresses recovered by the cheap pass | `test_all_addresses_recovered_by_cheap_pass_means_zero_smart_work` | 12 smart-recoverable damaged duplicates: 0 reads tried, 0 smart attempts, SUCCESS = eager |
| 2 one missing address | `test_one_missing_address_is_the_only_one_searched` | round A: 1 read, 1 address; 6 damaged duplicates elsewhere skipped; SUCCESS, output SHA-256 = input (8) |
| 3 several missing | `test_several_missing_addresses_only_those_are_searched` | round A: 3 reads, 3 addresses, 3 recovered, no round B |
| 4 duplicates | tests 1 and 2 | damaged duplicates of recovered addresses never trigger smart recovery |
| 5 wrong / ambiguous reads | `test_wrong_and_ambiguous_reads_stay_rejected` | two targeted junk reads (valid header, random payload) are tried and rejected; only the genuine read is accepted |
| 6 address snapping | `test_snapped_address_lands_in_the_bucket_of_its_verified_group` | the only copy has a header error in the group byte; 3 forced spill buckets; snapped, recovered, filed in its verified group's bucket; SUCCESS |
| 6 (unit) | `test_spill_routes_verified_frames_by_verified_group` | `Spill.append_acc` routes by group mod B |
| 7 partial archive | `test_partial_archive_keeps_missing_addresses_missing` | M + 2 symbols without reads: group 0 fails in both schedules, round B runs, nothing synthesised |
| 8 full archive | tests 2 and channel test | extracted output SHA-256 = input |
| 9 soft decoding | `test_soft_decoding_opportunities_are_not_bypassed` | the only copy is soft-recoverable only: hard fails, `soft_decoding="auto"` succeeds in both schedules |
| 10 determinism | `test_repeated_and_parallel_decodes_are_identical` | identical reports for repeat runs and for 1 vs 3 workers |
| 11 adversarial false success | `test_verified_but_wrong_frame_cannot_produce_false_success` | a genuinely RS + CRC-valid frame with a wrong payload for the needed address: both schedules raise `VNXIntegrityError`, nothing written |
| cheap consensus counts | `test_cheap_v4_consensus_vote_counts_before_any_smart_work` | two damaged copies: the V4 vote completes the group, 0 smart work; eager smart-decodes both; same output |
| equivalence on channels | `test_simulated_channels_eager_and_deferred_agree` (2 channels × smart / smart + soft, 3 buckets) | same status, groups, container hash |
| options / CLI / V4 | `test_option_validation_and_default`, `test_cli_flag`, `test_v4_mode_is_untouched` | default `deferred`; `--recovery-schedule`; V4 mode has no schedule |

Full suite on the final commit `d41530d`: **1013 / 1013 pass** (11 min 26 s): 997 existing + 16 new. By area: V2 137,
V3 194, V4 122, V5 290 (native/reference equivalence 94 + native 22, Phase 3 indel 77, Phase 4 soft 79, terminology 2,
schedule 16), unit 163, integration 62, CLI 29, adversarial 11, property 5. Every existing Phase 3/4 decoder test now
runs under the new default schedule.

## 5. P3O-EXP-01: before (eager) vs after (deferred)

32 KiB archive (14 groups, input seed 4801), the V4 channel with fixed coverage 1/2/3/5/10, channel seeds 44000 and
44010, 8 workers, each decode in a fresh process. Both schedules decode the **same read file**. "Same outcome"
means the same status, recovered-group fraction and output SHA-256.

Per cell (mean of 2 seeds; "reads tried / skipped" are the deferred stage's counts; peak RSS = decode process +
largest worker):

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

Totals (all 20 cells × 2 seeds):

| decoder | verified SUCCESS eager / deferred | total wall s eager / deferred | verified archives per hour eager / deferred | CPU s eager / deferred | cells with different outcome | false SUCCESS |
|---|---|---|---|---|---|---|
| V5-hard | 27 / 27 | 625.9 / 167.3 | 155.3 / 581.1 | 774.6 / 666.5 | 0 | 0 of 80 |
| V5-soft-auto | 30 / 30 | 737.3 / 161.5 | 146.5 / 668.8 | 901.8 / 620.8 | 0 | 0 of 80 |

Where the time goes now (V5-hard; "groups decodable after cheap pass" per seed, of 14):

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

Reading the tables:

* **Clean:** no read fails, so the stage does nothing. The deferred and eager times are the same to within noise.
* **Indel-heavy and mixed, coverage ≥ 3** (or ≥ 2 on indel-heavy): the cheap pass, together with the V4 consensus vote
  counted in the group state, makes every group decodable (or all but a few). Most reads are skipped, and the
  speed-up is 3–11×.
* **Coverage 1 and the substitution-heavy channel:** groups remain incomplete, so round B runs and the stage tries
  every read. Smart attempts are the same as eager (e.g. 14,692 vs 14,677). The 2–3× speed-up there comes from load
  balance (§6), not from saved work.
* **Substitution-heavy, coverage 10, V5-soft-auto:** the cheap pass leaves 9–10 of 14 groups decodable. Round A
  targets the rest, but one group still needs round B. Smart attempts still fall 14,519 → 1,502 because the soft path
  recovers the needed addresses early. Speed-up ×7.1.
* **Pass 2 is now the largest cost in some cells** (indel-heavy coverage 5: 4.3 s of 6.0 s; mixed coverage 10:
  4.1 s of 6.5 s). Reads that were skipped stay pending, and pass 2's smart/soft consensus still runs for missing
  addresses in groups that are already decodable. This is unchanged Phase 3 pass-2 behaviour (§10).

## 6. P3O-EXP-02: saved work vs load balance (control)

To separate the two causes of §2, this control adds eager recovery with `batch_reads = 256`: the Phase 3 schedule,
but with pass-1 tasks as small as the deferred stage's.

V5-hard on 3 noisy channels × coverage 1/3/10 × 2 seeds, 8 workers (mean of 2 seeds per cell):

| channel | cov | V5-hard eager: wall s / CPU s / SUCCESS | V5-hard eager batch256: wall s / CPU s / SUCCESS | V5-hard deferred: wall s / CPU s / SUCCESS | same outcome (all three) |
|---|---|---|---|---|---|
| indel-heavy (0.5%+0.5%, sub 0.1%) | 1 | 4.0 / 6 / 0 | 1.6 / 8 / 0 | 2.4 / 12 / 0 | yes |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 3 | 11.7 / 13 / 2 | 3.5 / 17 / 2 | 2.0 / 6 / 2 | yes |
| indel-heavy (0.5%+0.5%, sub 0.1%) | 10 | 25.1 / 36 / 2 | 5.6 / 40 / 2 | 2.5 / 9 / 2 | yes |
| mixed (0.5%+0.5%, sub 1%) | 1 | 6.2 / 8 / 0 | 2.3 / 10 / 0 | 3.1 / 15 / 0 | yes |
| mixed (0.5%+0.5%, sub 1%) | 3 | 18.1 / 20 / 2 | 4.5 / 24 / 2 | 3.5 / 12 / 2 | yes |
| mixed (0.5%+0.5%, sub 1%) | 10 | 41.9 / 58 / 2 | 8.6 / 61 / 2 | 6.5 / 14 / 2 | yes |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 1 | 6.9 / 9 / 0 | 2.3 / 11 / 0 | 2.6 / 15 / 0 | yes |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 3 | 24.1 / 26 / 0 | 8.0 / 30 / 0 | 9.1 / 34 / 0 | yes |
| mixed, substitution-heavy (0.25%+0.25%, sub 4%) | 10 | 51.7 / 70 / 1 | 13.6 / 74 / 1 | 15.0 / 79 / 1 | yes |
| **total** | | **379 / 488 / 9** | **100 / 551 / 9** | **93 / 393 / 9** | |

* **Load balance:** eager with 256-read batches cuts total wall time 379 s → 100 s (×3.8) and *raises* CPU time
  (488 → 551 s, more task and process overhead). So most of Phase 3's wall-clock slowdown on these 32 KiB archives
  (≈ 10,000 reads, two pass-1 batches) was idle workers.
* **Saved work:** where groups complete early, deferred uses a fraction of the CPU of either eager variant: 9 vs
  36–40 CPU s (indel-heavy coverage 10), 14 vs 58–61 (mixed coverage 10). Its wall time is lowest there too.
* **Where deferred loses:** when every read must be tried (coverage 1; substitution-heavy), deferred needs more CPU
  than eager-256: 12–15 vs 8–11 CPU s at coverage 1, 79 vs 74 at substitution-heavy coverage 10. It is also slower in
  wall time: 2.4–3.1 s vs 1.6–2.3 s at coverage 1, and 9.1–15.0 s vs 8.0–13.6 s on the substitution-heavy channel. The
  stage repeats the cheap V4 attempts on every pending read before searching it, re-aligns it, starts its own worker
  pool, and runs rounds one after another. This overhead was not profiled further.
* All three configurations give the same verified outcome in all 9 cells.

## 7. History: the first version

The first Gate B version (commit `12c0ddf`, then `cab9233`) used fixed 256-read tasks in the deferred stage. Its
P3O-EXP-01 run gave 158 → 446 (V5-hard) and 149 → 491 (V5-soft-auto) verified archives per hour, also with 0
outcome differences and 0 false SUCCESS. On failing coverage-1 archives it was 10–15 % *slower* than eager, and P3O-EXP-02
showed why: a round of about 450 reads made only two tasks, so only two of eight workers ran. Commit `d41530d` sizes
tasks to the round (about four per worker, at most 256 reads), and every number in this report comes from that
commit. The first run's results and logs are kept unchanged in `experiments/v5/phase3opt/superseded-fixed-chunks/`
(provenance `12c0ddf` / `cab9233`, clean). The adaptive chunking raised the deferred CPU time in the control slightly
(337 → 393 CPU s in total) while cutting wall time 125 s → 93 s.

## 8. Memory

Peak RSS (decode process + largest worker) is the same or lower with the deferred schedule in 30 of 40 cell/decoder
pairs, and at most 2 MB higher in 5 more. It is lower by up to 96 MB where eager ran soft decoding on many reads (V5-soft-auto, substitution-heavy coverage 10:
337 → 241 MB). It is higher by at most 16 MB (substitution-heavy coverage 10, V5-hard: 227 → 243 MB; mixed coverage 10: 226 →
237 MB). Pending records already carried the raw read in smart/soft mode. The stage adds one boolean pair per pending record, the
needed-address set, and the unaddressed reads, which are now kept until the stage (they were dropped after pass 1 before). The stage
works one spill bucket at a time, like pass 2.

## 9. Correctness and integrity

| check | result |
|---|---|
| false archive SUCCESS | **0** of 160 decodes (P3O-EXP-01) and 0 of 54 (P3O-EXP-02); every SUCCESS verified by the output SHA-256 |
| outcome differences eager vs deferred | **0** of 80 cell/decoder pairs (P3O-EXP-01), 0 of 9 cells × 3 configurations (P3O-EXP-02) |
| false / ambiguous / wrong-but-verified frames | unchanged code path (the per-read recovery is the Phase 3/4 function); Phase 3 adversarial (13) and Phase 4 soft (79) tests pass; Gate A re-verified Phase 4 integrity on the committed code |
| verified-but-wrong frame for a needed address | fails closed in both schedules (`VNXIntegrityError`, nothing written) |
| determinism | identical reports for repeat decodes and for 1 vs 3 workers |
| V4 mode | untouched: no deferred stage runs unless smart recovery or soft decoding is on (`test_v4_mode_is_untouched`; V2/V3/V4 suites green) |
| native/reference equivalence | 94 + 22 tests pass (the aligner is not changed) |
| defaults | `indel_recovery="segment"` and `soft_decoding="off"` (V4) are unchanged; `recovery_schedule` matters only when one of them is enabled |

## 10. Known failures and limitations

* **SIMULATED only;** one machine; 32 KiB archives (14 groups) with 8 workers. Larger archives have more pass-1 batches,
  so the load-balance share of the gain will be smaller and the saved-work share is what remains. That was not measured
  here.
* **No speed-up where nothing can be skipped.** On failing or nearly failing archives (coverage 1, substitution-heavy),
  deferred tries every read. It is then slower than a well-balanced eager schedule (1.5×–1.1× in wall time, more CPU),
  though still faster than the default eager schedule.
* **Pass 2 still does unnecessary work.** Its smart/soft consensus runs for missing addresses in groups that are already
  decodable (up to 4.3 s of a 6.0 s decode). Restricting it would change pass-2 behaviour and was left out of this
  gate.
* **Random access (`select`)** uses the conservative group state (single verified reads only), so it may search more
  reads than strictly needed.
* **Several archives in one read pool:** round S searches every read whose header says superblock, so all archives'
  superblocks get the per-read search before the schedule decides. A superblock read whose header is corrupt beyond a
  readable "superblock" kind is searched only in round B. That could make a second archive's superblock undecodable in a
  case where Phase 3/4 would have found it and reported "several archives". Not tested.
* **Orientation:** the forward orientation of a read whose reverse complement scored better is no longer searched
  (§3.3). No outcome changed in any experiment.
* **Not changed:** pass-1 `batch_reads` (8,192) stays the default for the eager schedule, so the eager schedule keeps
  its load-balance problem on small archives.

## 11. Reproducibility

| item | value |
|---|---|
| commit under test | `d41530d` (clean; recorded in both `results.json`) |
| V4 control | `v4.0.0` = `a358ae8` |
| environment | Python 3.12.3, NumPy 2.5.3, gcc 13.3.0, native aligner (pip-built extension), Intel Xeon Gold 6240, 8 CPUs |
| P3O-EXP-01 | `python experiments/v5/phase3opt/exp_schedule.py --seeds 2 --workers 8` (30 min) |
| P3O-EXP-02 | `python experiments/v5/phase3opt/exp_schedule.py --control --seeds 2 --workers 8` (10 min) |
| seeds | input 4801 (32 KiB random), channel 44000 + 10·k, fixed coverage |
| tables | `python experiments/v5/phase3opt/summarize.py`, `summarize_control.py` → `summary.md`, `summary-control.md` |
| suite | `PATH=.venv/bin:$PATH .venv/bin/python -m pytest -p no:cacheprovider -o addopts="" -q` → `log-suite.txt` |

Each `results.json` records the configuration and its SHA-256, the SHA-256 of every read file, and the provenance.
Results directories: `experiments/v5/phase3opt/P3O-EXP-01-schedule/`, `P3O-EXP-02-parallelism-control/`.

## 12. Acceptance

| requirement | status |
|---|---|
| two-stage flow: cheap pass, then smart only where the cheap pass is insufficient | **met** (§3) |
| explicit state: missing / incomplete / insufficient consensus / unrecoverable / recovered | **met** (§3.1) |
| already-complete addresses are not reprocessed | **met**: skipped reads 0 → up to 5,744 per decode; tested (items 1, 4) |
| V4 / Phase 1 / Phase 2 / Phase 3 / Phase 4 behaviour preserved | **met** for verified outcomes (0 differences in 89 comparisons; full suite green). The work done per read differs as stated in §3.3 |
| deterministic, SHA-256 integrity, fail-closed, PARTIAL semantics, snapping, outer code | **met** (§9, tests 6, 7, 10, 11) |
| 11 mandatory regression tests | **met** (§4) |
| performance experiment: clean / indel-heavy / mixed × coverage 1/3/5/10, same inputs | **met** (P3O-EXP-01, plus coverage 2 and a substitution-heavy channel) |
| primary metric improved: verified archive recovery per unit time | **met**: ×3.7 (V5-hard), ×4.6 (V5-soft-auto) against the Phase 3/4 schedule |
| no Phase 5 work | **met** |
