# Nanopore regression corpus (DIAGNOSTIC / SIMULATED)

Deterministic cases for V7 item A nanopore-like decoding. Every case rebuilds its archive and read file from its seeds;
nothing here is real sequencing data. The channel is the unfitted V6 nanopore-like stress profile.

| path | content |
|---|---|
| `corpus/cases.json` | the cases: archive size, data seed, profile, full `ChannelConfig` (its `seed` is the trial seed), decoder arm, tier (`fast` / `slow`) |
| `nanofunnel.py` | build, simulate with per-read ground truth, decode, per-original-strand funnel with a reason for every lost strand, ORACLE address test |
| `reproduce.py` | regenerate `diagnostics/<id>.json` (and `expected/<id>.json` with `--update-expected`) from the seeds |
| `diagnostics/` | machine-readable failure artifact per case: funnel, loss reasons, cluster purity, ORACLE classes, every lost strand |
| `expected/` | the compact, deterministic part of each artifact that `test_nanopore_corpus.py` pins |

```bash
PYTHONPATH=src python tests/nanopore/reproduce.py --all --jobs 4          # every case
PYTHONPATH=src python tests/nanopore/reproduce.py nanopore-cov10-s82043    # one case
PYTHONPATH=src python -m pytest tests/nanopore                            # the regression test (slow cases marked slow)
```

Seeds come from the protocol §7 exploration range (82000-82099); the corpus uses 82040-82045. The ORACLE address test
supplies only each read's true source strand (no orientation, offsets, boundaries or bases) and is never a decoding or
acceptance result. A decoder change that alters a case must update `expected/` deliberately and say why.
