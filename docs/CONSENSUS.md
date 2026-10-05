# Clustering and consensus

The read-processing chain turns a pile of noisy, duplicated, shuffled reads into one best estimate per designed
strand, and says so when it is unsure.

```
raw reads ─▶ vnx-dna reads (validate/filter) ─▶ vnx-dna cluster ─▶ vnx-dna consensus ─▶ vnx-dna decode
```

## Clustering (`vnx-dna cluster`)

Order-independent, and without all-against-all comparison:

| step | reads | cost | reliability |
|---|---|---|---|
| 1. verified address | full-length reads whose frame CRC passes (forward, reverse-complemented, or after inner RS) | O(1) per read | exact: a wrong address needs a CRC collision (≈ 2⁻³²) |
| 2. tentative address | reads that fail validation but whose descrambled 11-byte header is plausible and carries an archive tag seen on verified reads | O(1) per read | proposal only; consensus and the CRC decide |
| 3. second chance by similarity | reads of *weak* groups (a single tentative read) and reads with an unknown tag or no plausible header | one lookup per read in a sorted numpy index of minimizers (k = 12, window 8) of one representative per *strong* cluster (a verified read, or ≥ 2 reads); the read joins the cluster sharing the most minimizers, in either orientation, if that is ≥ 30 % of its minimizers and ≥ 4 | heuristic; a wrong join adds noise to one cluster, which consensus excludes or outvotes |
| 4. orphans | reads still unassigned | minimizer leader clustering among themselves (inverted index) | heuristic; orphan clusters have no address |

Records live in disk buckets keyed by a hash of the address, and the representative index is compact (two integer
arrays, searched with `searchsorted`), so memory stays bounded. Members of every cluster are sorted canonically (by
bases, then qualities), representatives are chosen after sorting, and orphans are processed in sorted order. **The
output therefore depends only on the multiset of reads, not on their order** (tested).

Why step 3 matters: at high error rates many headers are damaged. At 1 % indels per base, reassignment moved about a
third of the reads into their true clusters, cut the number of clusters from ~2.7 to ~1.1 per strand, and raised the
number of valid consensus strands from 146 to 475 of ~490 in one measured case (the development trace of this change is
summarised in [EXPERIMENTS.md](EXPERIMENTS.md#sweeps)).

**Complexity.** O(N) address lookups, plus O(N_weak × log index size) similarity lookups. The index is capped at
2,000,000 clusters, and the orphan list at 200,000 reads. Anything beyond is counted and handled as tentative or orphan
reads, never silently merged.

## Consensus (`vnx-dna consensus`)

Per cluster, in order of cost:

1. **Verified read.** If any read passes the frame CRC as it is, it *is* the strand (a wrong one needs a CRC
   collision), and it is written without voting.
2. **Corrected read.** Otherwise, up to three full-length reads (best mean quality first) are tried with the inner RS
   decoder. A correction that the CRC then verifies gives the strand.
3. **Iterative alignment consensus.** Otherwise:
   * the seed draft is the read closest to the strand length (best quality first);
   * **every** read of the cluster is aligned to the draft with banded edit distance ([SYNCHRONIZATION.md](SYNCHRONIZATION.md)),
     batched across up to 16,384 reads of many clusters at once, and each aligned base carries its own quality;
   * reads more than 15 % of L edits from the draft are excluded (a wrong member);
   * each draft position is voted over A, C, G, T and "deleted" (weight = base quality, at least 1; deletion 20;
     `N` contributes nothing), and each **insertion slot** between positions is voted too: a base is inserted when
     more than half of the aligned reads insert there. The draft can therefore grow as well as shrink;
   * the vote becomes the next draft, and a second round realigns every read to it (`rounds = 2`).

   In the final round, a position whose winner carries less than 60 % of the top-two weight (`--min-winner-share`)
   becomes `N`, and so does a single-read position with quality below 10.
4. **Check.** The result is test-parsed (CRC, then inner RS) and counted as CRC-valid, valid after inner RS, or
   unverified. If it fails and a member verifies on its own, that member is written instead (a safety net; rarely
   needed after step 1).

### Ambiguity is exposed, not invented

A consensus position is `N` whenever the evidence is split. The decoder turns `N` into an **erasure** for the inner
RS code, which corrects `e` errors and `f` erasures when `2e + f ≤ r`, so an honest `N` costs half as much as a wrong
base. Consensus never decides correctness: the CRC-32, the chunk SHA-256 and the object SHA-256 do. A wrong consensus
can only cost an erasure, never produce wrong output.

### Quality scores

FASTQ qualities from the simulator are informative by design (`--quality-model informative`: sequencing-error bases
get Phred 2–20 with probability 0.8). With real data, quality informativeness is a property of the platform and must
be measured, not assumed. `--quality-erasure-below Q` on `decode`/`recover` turns bases below Q into erasures without
consensus. That helps at coverage 1 and is harmful if qualities are uninformative (then every flagged base is a lost
erasure-budget slot).

### Quality-weighted pass-2 vote (opt-in, V6 Phase 4)

`vnx decode --consensus-weighting quality` (`DecodeOptions.consensus_weighting`, config `decode.consensus_weighting`)
weights each projected read base in the decoder's pass-2 address vote by its own Phred quality
(`recovery.consensus.consensus_quality_weighted`); groups whose reads carry no qualities use the count vote. The score
is Phred-interpreted, not calibrated. Pre-registered comparison (SIMULATED, `experiments/v6/phase4`): 0 false SUCCESS,
more symbols recovered by consensus, but no archive-level gain whose 95 % interval excludes 0, at about 1.3 times the
decode time. The default stays `count`.

## Statistics reported

`cluster`: reads verified / tentative / orphan, reads with an unknown tag, strong clusters, reads reassigned by
similarity, tentative-only and orphan clusters, cluster-size histogram. `consensus`: consensus from a verified read / a corrected read / alignment, CRC-valid, valid after inner RS, fallbacks,
unverified sequences written, reads aligned / excluded, ambiguous (N), inserted and deleted positions. Measured effects are in
[EXPERIMENTS.md](EXPERIMENTS.md).
