# Synchronization (insertions and deletions)

**Synchronization is not error correction.** Reed–Solomon codes, both the inner per-strand code and the outer Cauchy
code, correct substitutions and erasures at *known positions*. An insertion or deletion shifts every later base of
a read, so every later symbol is wrong. No Reed–Solomon decoder repairs that by itself, and VNX-DNA never claims
otherwise.

VNX-DNA therefore handles indels in dedicated synchronization layers *before* error correction:

```
raw reads ─▶ [ multi-read synchronization: cluster → align → vote ] ─▶ strand-length consensus (N where unsure)
          └▶ [ single-read synchronization: RS-assisted realignment (opt-in) ]
                                   │
                                   ▼
             inner RS (substitutions + N erasures) ─▶ CRC-32 ─▶ outer erasure code ─▶ chunk SHA-256
```

| layer | handles | does not handle | module |
|---|---|---|---|
| address anchoring | locating the strand a read belongs to, even when the read has indels after its 44-nt header | reads with indels inside the header (these become orphans, clustered by minimizers) | `vnxdna.v2.cluster` |
| multi-read alignment | indels in individual reads when other reads of the same strand exist (coverage > 1) | indels shared by *every* read of a strand (for example a synthesis error in all molecules) | `vnxdna.v2.consensus`, `vnxdna.v2.align` |
| single-read realignment | exactly one indel (or two, at quadratic cost) plus at most ⌊(r−1)/2⌋ other byte errors, in a read of length L ± 1 | more indels; indel pairs that keep the length (handled as byte errors by the inner code) | `vnxdna.v2.sync` |
| block boundaries | limiting any residual damage to one strand (fixed-length frames) and one ECC group | | frame format 5 |

## Multi-read synchronization (the main defence)

1. **Anchoring.** Every strand starts with an 11-byte header (tag, stripe, shard) inside the scrambled frame. A read
   whose CRC passes is anchored exactly. A read that fails (typically because of an indel *after* the header) is
   anchored tentatively by descrambling its header. The tentative anchor must carry an archive tag that verified
   reads also carry.
2. **Alignment.** Reads of the cluster that do not have the strand length are aligned to the draft consensus with
   banded global edit distance (band 12 by default, so a net indel of up to 12 bases). The alignment projects each read
   onto the strand's L positions: a base, or a gap (deletion). Inserted bases have no strand position and are dropped.
3. **Voting** at each position over {A, C, G, T, deleted}, weighted by base quality; the winner needs at least 60 %
   of the top-two weight, otherwise the position becomes `N`.

Why this works: independent indels hit different positions in different reads, so after alignment each strand position
has a majority of correct evidence. Measured recovery rates versus indel rate, with and without this layer, are in
[CHANNEL_MODEL.md](CHANNEL_MODEL.md#measured-recovery) and [EXPERIMENTS.md](EXPERIMENTS.md).

## Single-read realignment (opt-in: `--experimental-indel-repair`)

For a read `d` bases off the strand length (1 ≤ |d| ≤ `--max-indel`), every hypothesis "the indels are in frame
bytes j₁ ≤ … ≤ j|d|" restores the length at those bytes, flags them as erasures, and runs the inner RS decoder. It is
accepted only if the CRC-32 passes. One hypothesis matches a single-indel read exactly, so recovery is guaranteed
when the rest of the read has at most ⌊(r − 1)/2⌋ byte errors. Two indels cost O(F²) hypotheses and are capped by
`max_candidates`. False acceptance requires an RS miscorrection *and* a CRC collision (≈ 2⁻³² per hypothesis), and
chunk SHA-256 checks downstream catch it in any case. It is useful at coverage 1, where there is nothing to align
against. It is slow (up to 2·F decodes per read), which is why it is opt-in.

## What is not implemented

* **Periodic markers / watermark codes / VT codes.** Frame format 5 carries no in-strand sync markers. With coverage
  > 1, alignment between reads resynchronizes more cheaply than markers would, and markers cost density at every
  coverage. A marker-based frame would be a new frame format (a new required feature), not a reinterpretation of format 5.
* **Indels common to all reads of a strand.** If every molecule carries the same indel (a synthesis error in the
  species), consensus faithfully reproduces it and the strand fails its CRC. It then becomes an erasure, and the
  outer code recovers it, within its guarantee of M strands per group. The simulator's synthesis errors are
  independent per molecule, so this case appears only through dropout-like loss.
