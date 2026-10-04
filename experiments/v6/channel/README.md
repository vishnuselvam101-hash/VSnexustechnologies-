# Mixed-error channel simulator (V6 Phase 1, item 14)

**SIMULATED.** This directory is a *simulation* layer. Strands are software-generated, errors are drawn from software
random-number models, and nothing here is biological validation. No DNA was synthesised, stored or sequenced. The
"illumina-like" and "nanopore-like" models only follow publicly described qualitative characteristics (substitution-dominated
versus indel-dominated, homopolymer sensitivity, read orientation, quality) and are **not fitted to any measured platform**.

## What it is

A thin layer over existing code (no new error mechanics, nothing under `src/` changed):

1. strand loss: `vnxdna.v6.loss` (i.i.d. dropout and contiguous bursts in pool order)
2. per-read channel: `vnxdna.v4.channel` (coverage fixed/Poisson/negative-binomial, GC bias, substitutions, insertions,
   deletions, homopolymer multipliers, per-read deletion bursts, N calls, PCR-style duplicates, reverse complement, quality scores)
3. a provenance sidecar written next to every output

The output is a pure function of (input strands, model file, seed); it does not depend on the worker count.

## Models (`models/*.json`)

Each file is named, versioned (`version`), labelled `"classification": "SIMULATED"`, and lists **every** parameter of the loss and
channel layers (loading fails if one is missing or unknown). The model's SHA-256 (of the file bytes) is recorded in every sidecar.

clean, substitution-heavy, insertion-heavy, deletion-heavy, mixed-mild, mixed-harsh, dropout-5, dropout-10, dropout-20,
burst-loss, uneven-coverage (negative binomial + GC bias), quality-degradation, illumina-like, nanopore-like.

## Use

```
python experiments/v6/channel/simulate.py --list
python experiments/v6/channel/simulate.py --model mixed-mild --strands in.fasta --seed 7 --out reads.fastq
```

Writes `reads.fastq` and `reads.fastq.json`: classification and statement, seed, model name/version/SHA-256/full parameters,
coverage and quality model, input and output SHA-256, realised rates and raw event counts, generating git commit and dirty flags
(`dirty` includes untracked files, `dirty_tracked` does not), and `decoder` / `decode_result` (null here, filled by `evaluate.py`).
Input strands must be FASTA with equal lengths. A model may also be given as a path to a JSON file.

```
python experiments/v6/channel/evaluate.py --models all --seeds 20 --size 20000 --profile v4-balanced \
    --option strand_order='"interleaved"' --out results.jsonl
```

Encodes a random payload once, then per model and seed (trial seed = base seed + index): simulate, decode with the stock decoder,
compare the decoded container's SHA-256. Output is JSON lines (header, one record per trial, summary); every record says
`SIMULATED`. Outcomes: `exact`, `failed-detected`, `FALSE_SUCCESS` (SUCCESS with a different container; must be 0; makes the exit
status 2). The summary has Wilson 95 % intervals. Per trial: decode status, recovery statistics from the decode report,
encode/decode options, simulator and decode seconds, realised rates.

## Tests

`tools/vnx-lab test tests/v6/channel`: determinism across worker counts and repeats, realised versus configured rates per model
(5 sigma binomial tolerance plus a small relative allowance; checked both from simulator counters and from the written FASTQ), sidecar
completeness, CLI, and a small end-to-end evaluate run.

## Limitations

Errors are i.i.d. per base apart from the explicit homopolymer, burst and GC-bias terms; real error processes are context-dependent
and platform-specific. Parameters are stress settings. Results say how the software decoder behaves under these models, nothing more.
