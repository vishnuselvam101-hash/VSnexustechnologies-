# A-CONS: full-template consensus polish vs reference consensus (EXPERIMENTAL, SIMULATED)

All results are SIMULATED (unfitted nanopore-like channel, V6 stress profile; not fitted to any platform; no DNA was
synthesised or sequenced). Pre-registration: `PREREGISTRATION.md` (committed before the held-out decodes, code `bff39a9`).

## Change under test
`ClusterConfig.consensus_template="full"` (opt-in; default `"wildcard"` is the unchanged reference path). A-LOSS showed
the first irreversible loss is per-cluster consensus → inner RS, with about 90 % of wrongly decided bases equal to the
true base one position away. The reference rounds align reads to a template whose undecided frame positions are cost-0
wildcards, so an indel can slide anywhere between two markers. The polish keeps a base at every frame position and edits
the template to lower the reads' summed alignment cost: exact single-edit costs (one forward/backward pass per read),
length-preserving shift moves inside each marker-delimited segment (markers fixed; they anchor every alignment), each
scored by exact realignment. The decision is the unchanged certain-call vote against the polished template. No quality
values, no RS/erasure-policy change, no format, addressing or clustering change.

## Results (`results/summary.json`; per case and arm in `results/{frozen,heldout}.jsonl`)
Held-out seeds 82060-82099, 20,000 B archive, 679 data strands, 40 seeds per coverage, means per seed:

| cov | arm | >=2 reads | clustered | consensus ok | data frames | EXACT (SHA-256) | Wilson 95 % | false success | false frames | decode s | peak RSS MiB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 3 | old | 480.6 | 459.2 | 123.6 | 123.6 | 0/40 | 0–0.088 | 0 | 0 | 0.96 | 93 |
| 3 | new | 480.6 | 459.2 | 294.7 | 294.7 | 0/40 | 0–0.088 | 0 | 0 | 16.9 | 339 |
| 5 | old | 593.0 | 577.7 | 279.6 | 279.6 | 0/40 | 0–0.088 | 0 | 0 | 1.6 | 121 |
| 5 | new | 593.0 | 577.7 | 463.0 | 463.0 | 0/40 | 0–0.088 | 0 | 0 | 24.8 | 333 |
| 10 | old | 661.4 | 656.6 | 513.0 | 513.0 | 0/40 | 0–0.088 | 0 | 0 | 2.7 | 196 |
| 10 | new | 661.4 | 656.7 | 618.1 | 618.1 | 40/40 | 0.912–1 | 0 | 0 | 42.0 | 327 |

NEW >= OLD in data frames on 40/40 paired seeds at every coverage; the pre-registered decision rule holds at cov 3, 5
and 10 (frames), and archive recovery improves at cov 10 only. Frozen corpus (seeds 82040-82045, 12 cases): same
direction, cov 10 EXACT 4/4 vs 0/4, cov 3/5 0 EXACT in both arms, 0 false frames.

"clustered" differs by 0.1 at cov 10 because the funnel credits a strand to a cluster after peeling; the clustering
stage itself is unchanged.

## Limits
- cov 5 and cov 3 still do not decode an archive: remaining losses are consensus erasures beyond the inner budget and
  strands with < 2 reads (cov 3: ~200 of 679 structurally).
- Decode is about 15x slower (pure-Python forward/backward for the polish) and peak RSS about 330 MiB; the native
  kernel computes only certain calls, not the edit costs.
- Simulated, unfitted channel; no claim for real nanopore data.

## Reproduce
    PYTHONPATH=src python experiments/v7/a-cons/run.py frozen  --jobs 4
    PYTHONPATH=src python experiments/v7/a-cons/run.py heldout --jobs 4
    python experiments/v7/a-cons/summarise.py
