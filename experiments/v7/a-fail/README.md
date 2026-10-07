# A-FAIL: failure taxonomy, coverage-5 failures and the coverage-3 conclusion (DIAGNOSTIC, SIMULATED)

Every non-EXACT decode of A-CONF (241 of 360; seeds 82100-82139) and of the A-PAR held-out run (42 of 120; seeds
82060-82099, v7-lowcov) was rebuilt and decoded again with the per-strand funnel and the ORACLE address test
(`diag.py`, commit aa62d61). **All 283 reruns reproduced the recorded outcome and data-frame count.** No decoder code
changed; nothing was tuned. Oracle results are ORACLE / DIAGNOSTIC, never decoding results.

| item | value |
|---|---|
| Commands | `PYTHONPATH=src python experiments/v7/a-fail/diag.py --jobs 4`; `… --source apar --jobs 2`; `… --summarise` |
| Run | 2026-10-06T14:30:48Z-15:13:52Z, shared 8-thread host, load average 1.2-4.5 |
| Raw | `results/failures.jsonl` (SHA-256 `0b712ab02728815e0de27f520c71071ce9896b18fe5fea7acd4eccaac82543f4`), `results/failures_apar.jsonl` (`ccc8329ae88298241c0babec81592d05544c9241f01f5bfdd4da42ecd72fecce`), `results/summary.json` |

## Method

Every non-EXACT decode is an EXPLICIT_FAILURE at the outer code (`terminal_stage` outer_ecc): at least one outer row
has more missing data symbols than its parity M (16 for v4-balanced, 48 for v7-lowcov). The root cause is therefore
**which losses fill the failing rows**. Each lost data strand gets the category of its first lost stage:

| category | funnel stage / reason |
|---|---|
| dropout | 0 reads of the strand in the pool |
| insufficient_reads | 1 read, or fewer than 2 stored reads (consensus needs 2) |
| clustering | unassigned, split, merged, or wrong cluster orientation |
| alignment | fewer than 2 reads inside the consensus band |
| addressing | the home cluster verified a different address |
| consensus_indel | consensus candidate beyond inner RS, wrong bases mainly shifted by one (indel placed wrong) |
| consensus_payload | consensus candidate beyond inner RS without the shift signature |
| frame | candidate within 2e+f ≤ r but no verified frame |

Input/read generation (reads SHA-256 reproduced in every rerun), archive reconstruction and final SHA mismatch
(0 FALSE SUCCESS, 0 false frames) have **0** failures. The deficit is Σ over rows of max(0, missing − M). "Rescued by
restoring only X" = every row would be within M if only category X's strands were recovered (counterfactual).

## Taxonomy (A-CONF). Share of missing strands inside the failing rows by category (in brackets: trials rescued by restoring only that category)

| cell (A-CONF) | failed | lost strands (mean) | failing rows (mean) | deficit (mean symbols) | dropout | insufficient_reads | clustering | alignment | addressing | consensus_indel | consensus_payload | frame | structural alone > M | oracle EXACT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cov3-old | 40/40 | 555.9 | 9.0 | 411.9 | 13.1 % (0) | 22.1 % (0) | 4.3 % (0) | 0.0 % (0) | 0.0 % (0) | 28.4 % (0) | 32.1 % (0) | 0.0 % (0) | 40 | 0 |
| cov3-phase1 | 40/40 | 385.6 | 8.95 | 241.6 | 18.9 % (0) | 31.8 % (0) | 6.2 % (0) | 0.0 % (0) | 0.0 % (0) | 34.2 % (0) | 8.8 % (0) | 0.0 % (0) | 40 | 0 |
| cov3-lowcov | 40/40 | 548.7 | 8.05 | 124.6 | 18.8 % (0) | 32.1 % (9) | 5.9 % (0) | 0.0 % (0) | 0.0 % (0) | 33.9 % (13) | 9.3 % (0) | 0.0 % (0) | 0 | 0 |
| cov5-old | 40/40 | 401.0 | 8.97 | 257.1 | 6.5 % (0) | 14.1 % (0) | 3.6 % (0) | 0.0 % (0) | 0.0 % (0) | 41.4 % (0) | 34.4 % (0) | 0.0 % (0) | 5 | 0 |
| cov5-phase1 | 40/40 | 218.2 | 8.03 | 77.4 | 12.1 % (0) | 25.8 % (0) | 6.6 % (0) | 0.0 % (0) | 0.0 % (0) | 43.2 % (2) | 12.3 % (0) | 0.0 % (0) | 5 | 0 |
| cov5-lowcov | 1/40 | 316.0 | 1.0 | 1.0 | 8.2 % (1) | 26.5 % (1) | 6.1 % (1) | 0.0 % (0) | 0.0 % (0) | 49.0 % (1) | 10.2 % (1) | 0.0 % (0) | 0 | 1 |
| cov10-old | 40/40 | 167.1 | 6.38 | 31.7 | 2.6 % (0) | 8.1 % (0) | 3.0 % (0) | 0.0 % (0) | 0.0 % (0) | 56.8 % (36) | 29.5 % (2) | 0.0 % (0) | 0 | 0 |

Findings:
1. **Old path, cov 10:** consensus_indel is 57 % of the losses in failing rows, and restoring only that category
   rescues 36/40 trials: the Phase 1 root cause, confirmed on fresh seeds.
2. **Phase 1 still loses most strands to indel placement** at cov 3 and 5 (34 % and 43 % of failing-row losses), now
   in clusters of few reads. Phase 1 fixes placement where reads outvote each other (cov 10); with 2-3 reads the
   placement is ambiguous.
3. **Clustering** is 4-7 % of losses and the oracle (true grouping) rescues 0 of 200 failed v4-balanced decodes (120 old, 80 Phase 1):
   clustering is not the limiting stage. **Addressing, alignment, frame: 0.**
4. No category is an implementation failure: every loss has a traced cause, every rerun reproduces, 0 false frames.

## Step 5: the coverage-5 v7-lowcov failures (82068, 82070 from A-PAR; 82132 from A-CONF)

| seed | failing rows | max row missing / M | deficit | losses in the failing row (indel, insufficient, dropout, payload, clustering) | rescued by restoring only | oracle |
|---|---|---|---|---|---|---|
| 82068 | 1 | 51 / 48 | 3 | 23, 14, 6, 6, 2 | indel, payload, dropout, insufficient | EXACT |
| 82070 | 1 | 51 / 48 | 3 | 20, 13, 7, 8, 3 | indel, payload, dropout, insufficient, clustering | EXPLICIT_FAILURE |
| 82132 | 1 | 49 / 48 | 1 | 24, 13, 4, 5, 3 | indel, payload, dropout, insufficient, clustering | EXACT |

Answers:
1. *Why 38-39/40 succeed:* v7-lowcov loses about 33 % of data strands at cov 5 (316-330 of 967), under its 42.9 %
   row budget (48/112); v4-balanced loses about 32 % against a 20 % budget and fails 40/40.
2. *Why 2/40 (and 1/40) fail:* one row's losses exceed 48 by 1-3 symbols. The three cases have **the same mechanism**:
   a binomial tail of an otherwise normal loss mix. With about 107 data symbols per row and p ≈ 0.33 per symbol, a row
   exceeds 48 with probability about 0.3 %, about 2-3 % per 9-row trial (THEORETICAL, independence assumed); observed
   3 of 80 trials.
3. Stochastic dropout: contributes (4-7 strands per failing row) but is not the cause on its own.
4. Clustering ambiguity: 2-3 strands per failing row; the oracle rescues 2 of 3 cases only because the deficit is 1-3.
5. Consensus ambiguity: the largest single share (indel 20-24, payload 5-8).
6. Multi-base indels: the simulator has few (D3), so this run cannot measure that sensitivity; real data has many.
7. Insufficient redundancy: yes at the margin — the deficit is 1-3 symbols in one row.
8. v7-lowcov does **not** recover information that Phase 1 loses: per-strand loss rates are the same (33 % vs 32 %);
   it tolerates more loss through redundancy (42.9 % vs 20 % per row), at +41.7 % strands.
9. New failure modes with v7-lowcov: none observed (0 false frames, no addressing/alignment/frame losses).

**Classification: STOCHASTIC** (row-level loss variance at the redundancy margin), not implementation-related.
Hypotheses for later, not implemented: (H-a) decoding 2-read clusters (largest remaining loss class) needs evidence
beyond a vote — e.g. quality-aware or soft decisions (item C); (H-b) a row interleave that spreads each row's
symbols over more independent clusters lowers the tail but does not change the mean.

## Step 6: coverage-3 conclusion

| measure (mean per trial) | v4-balanced + Phase 1 | v7-lowcov + Phase 1 |
|---|---|---|
| strands with ≤ 1 read, THEORETICAL (NB(3, size 4)) | 28.9 % | 28.9 % |
| row parity budget M/(K+M) | 20 % | 42.9 % |
| strands lost (observed) | 385.6 / 679 (57 %) | 548.7 / 967 (57 %) |
| structural losses (dropout + insufficient reads) alone exceed M | **40/40 trials** | **0/40** (A-PAR: 1/40) |
| deficit (symbols over budget) | 241.6 | 124.6 (A-PAR: 122.0) |
| rescued by restoring only insufficient_reads / consensus_indel | 0 / 0 | 9 / 13 (A-PAR: 10 / 12) |
| oracle (true clustering) archive EXACT | 0/40 | 0/40 |

Conclusion:
- **v4-balanced at cov 3: STRUCTURALLY INADEQUATE (C).** The strands that have fewer than 2 reads alone exceed the
  parity in every trial; no decoder that needs 2 reads per strand can succeed. Not an implementation defect.
- **v7-lowcov at cov 3: UNSUPPORTED, F = C + B.** Structural losses alone fit the budget (0/40 exceed it), but the
  remaining loss is indel placement in 2-3 read clusters (34 %) on top of the structural 51 %; restoring either one
  alone rescues only 9-13 of 40. Clustering is excluded (oracle 0/40), and the channel's error rate (E) drives the
  consensus share: real nanopore reads are harsher (D3), so real cov 3 would be worse.
- Coverage 3 is therefore **not supported** under this channel. No heuristic is added to hide it.
