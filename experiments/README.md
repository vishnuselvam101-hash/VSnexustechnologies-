# V4 experiments

Each directory is a reproducible experiment: `config.json` (complete definition, inputs generated from seeds),
`environment.json`, `input.sha256`, `seed.json`, `results.json` (every trial) and a `README.md` generated from them
(`python research/v4/write_experiment_readmes.py`). All tables together: [docs/V4_RESULTS.md](../docs/V4_RESULTS.md)
(`python research/v4/render_v4_results.py`).

All experiments are **SIMULATED** (software strands, configurable channel). None is physical evidence.

| id | question |
|---|---|
| EXP-0001 | recovery vs substitution rate (coverage 1 and 5) |
| EXP-0002 | recovery vs insertion rate (coverage 1 and 5) |
| EXP-0003 | recovery vs deletion rate (coverage 1 and 5) |
| EXP-0004 | recovery vs strand dropout for 20 % and 50 % parity |
| EXP-0005 | recovery and decode cost vs coverage 1–100× under heavy mixed errors (consensus) |
| EXP-0006 | combined substitution + insertion + deletion + dropout at five severities |
| EXP-0007 | Cauchy RS vs GF(2) fountain codes at equal redundancy (erasure-only) |
| EXP-0008 | indels at coverage 1: no markers vs two marker layouts |
| EXP-0009 | marker period/length design study (success vs nucleotide cost) |
| EXP-0010 | V3 vs V4 under the identical channel and seeds |
| EXP-0011 | 1/2/4/8-worker scaling |
| EXP-0012 | memory and throughput, 1 MiB – 1 GiB |
| EXP-0013 | homopolymer-dependent indels, GC-dependent coverage, uneven coverage |
| EXP-0014 | constraint screening: violations before/after, cost, recoverability |
| EXP-0015 | single-core stage throughput |
| EXP-0016 | outer codes in the full DNA pipeline under dropout at equal redundancy |

```bash
vnx experiment run experiments/EXP-0001-substitution/config.json
vnx experiment reproduce experiments/EXP-0001-substitution
```
