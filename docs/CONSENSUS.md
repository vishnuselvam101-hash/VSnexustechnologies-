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
| verified address | full-length reads whose frame CRC passes, forward, reverse-complemented or after inner RS | O(1) per read | exact: a wrong address needs a CRC collision (≈ 2⁻³²) |
| tentative address | reads that fail validation but whose descrambled 11-byte header is plausible and carries an archive tag seen on verified reads | O(1) per read | proposal only; consensus and the CRC decide |
| orphans | the rest (header damaged) | one minimizer-index lookup per read against cluster leaders (k = 12, window 8; join when ≥ 30 % of the read's minimizers hit one leader) | heuristic; orphan clusters have no address |

Clusters are grouped on disk into buckets by a hash of the address, so memory is bounded by one bucket. The output
is JSON Lines ([V2_FORMAT.md §4.3](V2_FORMAT.md#43-cluster-file-json-lines-vnx-clusters-1)) with forward-oriented reads
and quality strings. Shuffling the input changes only the order of clusters and of reads inside them. The consensus
vote does not depend on order, so decoding gives the same result (tested).

**Complexity.** O(N) for addressed reads. For orphans, O(N_orphan × leaders sharing a minimizer), which is small in
practice because unrelated strands rarely share 12-mers. The orphan list is capped (default 200,000 reads), and
excess orphans are counted and dropped, never silently merged.

## Consensus (`vnx-dna consensus`)

Per cluster:

1. **Draft**: quality-weighted per-position vote over reads of the strand length L. Without any, the read with the
   length closest to L, trimmed or `N`-padded.
2. **Alignment** of other-length reads to the draft ([SYNCHRONIZATION.md](SYNCHRONIZATION.md)), batched across up to
   16,384 reads of many clusters at once. Reads more than 15 % of L edits from the draft are excluded (for example a
   tentative member with a wrong address).
3. **Vote** over A, C, G, T and "deleted". Weight = Phred quality (at least 1) for full-length reads, the read's mean
   quality for aligned reads, 20 for a deletion. `N` contributes nothing.
4. **Call**: the winner must carry ≥ 60 % of the top-two weight (`--min-winner-share`). Otherwise the base is `N`.
   A single-read cluster also gets `N` where the base quality is below 10.
5. **Check**: the consensus is test-parsed (CRC, then inner RS). If it fails and the cluster contains a read that
   verified on its own, that read is written instead (counted as `fallback_to_verified_read`).

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

## Statistics reported

`cluster`: reads verified / tentative / orphan, foreign-tag tentatives dropped, orphan clusters, cluster-size
histogram. `consensus`: CRC-valid consensus, valid after inner RS, fallbacks, unverified sequences written,
reads aligned / excluded, inserted bases dropped, ambiguous (N) and deleted positions. Measured effects are in
[EXPERIMENTS.md](EXPERIMENTS.md).
