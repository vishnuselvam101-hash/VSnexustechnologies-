# Channel model, synchronization research and measured recovery

## Simulator (`vnxdna.channel`, `vnx-dna simulate`)

This is a software model of synthesis → storage → sequencing. It is **not fitted to any real platform**. The stages
are applied in this order to the strand pool:

1. **Dropout** (molecule loss): each strand is lost with probability `dropout_rate`. `--exact-dropout` instead removes
   exactly `round(rate·N)` strands.
2. **Coverage** (sequencing depth): each surviving strand yields `coverage` reads (`fixed`) or Poisson(`coverage`)
   reads (`poisson`). Poisson coverage can give zero reads, which is *sequencing read loss* and is reported separately
   as `strands_with_zero_reads`.
3. **Burst** (per read, probability `burst_rate`): one contiguous run of U[min, max] bases is substituted, deleted, or
   mixed.
4. **Per-base errors**, independent: a substitution to one of the three other bases (uniform), a deletion, and an
   insertion of a uniform base after the position.
5. **Orientation:** a read is reverse-complemented with probability `reverse_complement_rate`.
6. **Order:** reads are shuffled if `shuffle` is set. The CLI shuffles by default.

All randomness comes from `numpy.random.Generator(PCG64(seed))`, drawn in a fixed order: strand-level draws first,
then per-base draws in blocks of ≤ 2²⁰ bases. The same input, configuration and seed produce byte-identical output
(tested).

**The report counts what happened**, not the configured rates:
- strands in, dropped and surviving; strands with zero reads;
- reads out, reads altered, reads reverse-complemented;
- substitutions, insertions, deletions, bursts and burst bases;
- observed per-base and dropout rates;
- a SHA-256 of the dropped strand indices.

`--events FILE` logs every event as JSON lines, with its read, source strand, position, and original/replacement
base. `simulate --report` adds provenance: version, git commit, Python, platform and library versions.

The error classes are kept distinct:

| class | where it arises | what handles it |
|---|---|---|
| substitution | per-base / burst | inner RS (≤ r/2 byte errors per strand); beyond that the strand becomes an erasure |
| insertion / deletion | per-base / burst | read-length mismatch → erasure; opt-in experimental realignment |
| unreadable base (N) | read files | byte erasure for the inner RS |
| molecular dropout / zero coverage | stages 1–2 | outer erasure code (≤ M per stripe) |
| erased shard (rejected read) | decoder | outer erasure code |
| duplicate / conflicting copies | coverage | validation + majority, tie → erasure |
| reordering / reverse complement | stages 5–6 | in-band addressing and an RC attempt per read |

## Synchronization and indels (research)

Reed–Solomon codes correct substitutions and erasures. **They do not correct insertions or deletions**, because an
indel shifts every later symbol. By default, a read whose length is not the strand length is discarded, and the outer
code treats its strand as an erasure. That is always safe.

`--experimental-indel-repair` (module `vnxdna.sync.indel`) adds **RS-assisted realignment**. For a read that is
d = ±1…3 bases off:
1. hypothesize that the indels fall in frame bytes j₁ ≤ … ≤ j_|d|;
2. insert a placeholder base, or delete one, at the start of each hypothesized byte;
3. mark those bytes as erasures and run the inner RS decoder;
4. accept the result only if the frame CRC-32 verifies.

If the true indel lies in byte j, only byte j is misaligned, so a single indel plus up to ⌊(r−1)/2⌋ substitutions is
always repaired (tested at many positions). Cost: O(F) inner decodes per read for |d| = 1 and O(F²) for |d| = 2,
where F = frame bytes. It is slow, so it is opt-in.

The experiment is honest about its limits:
- net-zero indel pairs keep the length and look like byte errors;
- two indels in different bytes need the O(F²) search;
- per-read realignment cannot use consensus across copies.

Considered and not implemented:
- **Synchronization markers / watermark codes:** they cost density and need a different frame format, which is a V2
  research item.
- **Consensus alignment across coverage:** needs multiple-sequence alignment, also V2.

The frame format (a fixed-length frame, a CRC check, and an erasure-tolerant outer code) lets any such method be added
as a new decoder stage without changing format 4.

## Measured recovery (generated)

The next section is written by `research/experiments/run_experiments.py` from real decode attempts. Each point is 10
seeds on a 60 kB half-random/half-text dataset with default settings unless stated. *Recovered* means the exact bytes
came back, verified by SHA-256. *Wrong data* counts runs that returned incorrect bytes. **It is 0 in every run**: every
failure was an explicit error, and the runs are reproducible from the commit and seeds recorded in
`research/results/channel_experiments.json`.
