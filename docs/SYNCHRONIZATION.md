# Synchronization (insertions and deletions)

**Synchronization is not error correction.** Reed–Solomon codes, both the inner per-strand code and the outer Cauchy
code, correct substitutions and erasures at *known positions*. An insertion or deletion shifts every later base of
a read, so every later symbol is wrong. No Reed–Solomon decoder repairs that by itself, and VNX-DNA never claims
otherwise.

VNX-DNA therefore handles indels in dedicated synchronization layers *before* error correction:

```
raw reads ─▶ [ multi-read synchronization: cluster → align → vote ] ─▶ strand-length consensus (N where unsure)
          └▶ [ single-read synchronization: RS-assisted realignment / burst resynchronisation (opt-in) ]
                                   │
                                   ▼
             inner RS (substitutions + N erasures) ─▶ CRC-32 ─▶ outer erasure code ─▶ chunk SHA-256
```

| layer | handles | does not handle | module |
|---|---|---|---|
| address anchoring | locating the strand a read belongs to, even when the read has indels after its 44-nt header | reads with indels inside the header (these become orphans, clustered by minimizers) | `vnxdna.v2.cluster` |
| multi-read alignment | indels in individual reads when other reads of the same strand exist (coverage > 1) | indels shared by *every* read of a strand (for example a synthesis error in all molecules) | `vnxdna.v2.consensus`, `vnxdna.v2.align` |
| single-read realignment | exactly one indel (or two or three of the same net direction, within the hypothesis budget) plus at most ⌊(r−1)/2⌋ other byte errors | indel pairs that keep the length (+1 −1: the read reaches the inner code as a full-length read with a shifted segment and is usually rejected) | `vnxdna.v2.sync.repair_read` |
| burst resynchronisation (V3) | one contiguous run of L lost or extra bases, with ⌈(L + b − 1)/b⌉ + 2e ≤ r | several bursts in one read | `vnxdna.v2.sync.repair_burst` |
| block boundaries | limiting any residual damage to one strand (fixed-length frames) and one ECC group | | frame format 5 |

## Multi-read synchronization (the main defence)

1. **Anchoring.** Every strand starts with an 11-byte header (tag, stripe, shard) inside the scrambled frame. A read
   whose CRC passes is anchored exactly. A read that fails (typically because of an indel *after* the header) is
   anchored tentatively by descrambling its header. The tentative anchor must carry an archive tag that verified
   reads also carry.
   Reads whose header is damaged get a second chance by minimizer similarity to the cluster representatives.
2. **Alignment.** Every read of the cluster is aligned to the current draft with banded global edit distance (band 12
   by default, so a net indel of up to 12 bases). The alignment projects each read onto the draft's positions (a base
   with its quality, or a gap for a deletion) and records the bases it inserts between positions.
3. **Voting** at each position over {A, C, G, T, deleted}, weighted by base quality, and at each insertion slot (a
   base is inserted when most reads insert there). The vote becomes the next draft, and all reads are realigned to it
   once more. In the final round the winner needs at least 60 % of the top-two weight, otherwise the position becomes
   `N`. Clusters that contain a read passing the CRC (directly or after inner RS) skip alignment entirely.

Why this works: independent indels hit different positions in different reads, so after alignment each strand position
has a majority of correct evidence. Measured recovery rates versus indel rate, with and without this layer, are in
[CHANNEL_MODEL.md](CHANNEL_MODEL.md#measured-recovery) and [EXPERIMENTS.md](EXPERIMENTS.md).

## Single-read realignment (opt-in: `--experimental-indel-repair`)

For a read `d` bases off the strand length (1 ≤ |d| ≤ `--max-indel`, at most 3), every hypothesis "the indels are in
frame bytes j₁ ≤ … ≤ j|d|" restores the length at those bytes, flags them as erasures, and runs the inner RS decoder.
It is accepted only if the CRC-32 passes. One hypothesis matches a single-indel read exactly, so recovery is
guaranteed when the rest of the read has at most ⌊(r − 1)/2⌋ byte errors. Two indels cost O(F²) and three O(F³)
hypotheses. The search stops after `max_indel_candidates` hypotheses per orientation (API parameter, default 4,096),
so it is exhaustive only while the hypothesis count fits the budget. With the balanced profile, F = 63 frame bytes:
63 hypotheses for one indel, 2,016 for two, 43,680 for three. False acceptance requires an RS miscorrection *and* a
CRC collision (≈ 2⁻³² per hypothesis), and chunk SHA-256 checks downstream catch it in any case.

Worst-case cost per read: every hypothesis is decoded in up to two passes (with the flagged erasures, then errors
only) and in both orientations, so 4 × (number of hypotheses) decodes. VNX-DNA 2.0's documentation said "up to 2·F";
that was an undercount (V2 audit D8). **V3** decodes each read's hypotheses in vectorised blocks of 2,048
(`vnxdna.ecc.rs_batch`) instead of one `reedsolo` call each, and returns the first accepted hypothesis in the same
enumeration order as V2, so results are unchanged. Reads that also contain `N` are now repaired too; VNX-DNA 2.0
skipped them (audit D7).

## Burst resynchronisation (V3, opt-in: `--burst-repair N`)

A contiguous run of L lost bases (or L extra bases) shifts everything after it, exactly like L separate indels, but
it needs only **F hypotheses**, whatever L is: "the run starts inside frame byte j". For each j, VNX-DNA inserts L
erasure symbols at nucleotide j·b (or removes L bases there). That restores the alignment of everything after the
run. The ⌈(L + b − 1)/b⌉ bytes starting at j, which can still be wrong, are flagged as erasures, and the inner RS
decodes. A hypothesis is accepted only if the CRC-32 passes.

**Guarantee:** one burst of L ≤ N bases in a read is recovered when ⌈(L + b − 1)/b⌉ + 2e ≤ r (e = other byte errors;
b = nucleotides per byte, 4 for 2bit). For the balanced profile (r = 8) that means bursts of up to 29 nt with no
other error. Tested for L = 1…29, deletions and insertions, both orientations (`tests/v3/test_v3_features.py`).
Burst repair runs after indel repair, only for reads whose length is off by 1…N.

## V3 measurements

Single-read indel repair, V2 vs V3, on the same reads (exhaustive search budget of 100,000 hypotheses per
orientation):

<!-- BEGIN GENERATED: v3-indel -->
*(generated by `research/v3/render_v3_tables.py` from `research/results/v3/indel-*.json`)*

Geometry 2bit P=40 r=8 (balanced), 40 reads per row, seed 7, search budget 100,000 hypotheses per orientation; software simulation.

| indels (direction) | extra substitutions | V2 correct / wrong / failed | V2 ms per read | V3 correct / wrong / failed | V3 ms per read |
|---|---|---|---|---|---|
| 1 (same) | 0 | 40 / 0 / 0 of 40 | 13.59 | 40 / 0 / 0 of 40 | 8.94 |
| 1 (same) | 3 | 40 / 0 / 0 of 40 | 15.26 | 40 / 0 / 0 of 40 | 8.81 |
| 2 (same) | 0 | 40 / 0 / 0 of 40 | 422.25 | 40 / 0 / 0 of 40 | 145.69 |
| 2 (same) | 2 | 40 / 0 / 0 of 40 | 386.64 | 40 / 0 / 0 of 40 | 145.45 |
| 3 (same) | 0 | 40 / 0 / 0 of 40 | 9496.16 | 40 / 0 / 0 of 40 | 1707.6 |
| 2 (mixed) | 0 | 14 / 0 / 0 of 14 | 357.63 | 14 / 0 / 0 of 14 | 139.49 |
<!-- END GENERATED: v3-indel -->

Burst and indel recovery through the whole decoder at coverage 1 (repairs off vs on) is in
[ERROR_MODEL.md](ERROR_MODEL.md#v3-sweep-at-coverage-1-every-read-of-every-strand-once-shuffled).

## What is not implemented

* **Periodic markers / watermark codes / VT codes.** Frame format 5 carries no in-strand sync markers. (V3's burst
  resynchronisation needs none: it uses the CRC and the inner code as the synchronisation test.) With coverage
  > 1, alignment between reads resynchronizes more cheaply than markers would, and markers cost density at every
  coverage. A marker-based frame would be a new frame format (a new required feature), not a reinterpretation of format 5.
* **Indels common to all reads of a strand.** If every molecule carries the same indel (a synthesis error in the
  species), consensus faithfully reproduces it and the strand fails its CRC. It then becomes an erasure, and the
  outer code recovers it, within its guarantee of M strands per group. The simulator's synthesis errors are
  independent per molecule, so this case appears only through dropout-like loss.
