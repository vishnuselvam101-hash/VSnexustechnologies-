# EXP-SIM-1: do the migrated channel models reproduce? (SIMULATED)

**Evidence class: SIMULATED.** Software-generated strands and software channel models; no DNA was synthesised, stored,
amplified or sequenced. Question (V6_ARCHITECTURE §9, Phase 3): does the staged simulator `vnxdna.simulation.engine`
reproduce the current simulator byte for byte with the 14 Phase 1 models, read both as `vnx.channel-model/0` (the files in
`experiments/v6/channel/models`) and as the shipped `/1` conversions (`src/vnxdna/simulation/models`)?

- Code: commit `54ce9bc` (clean tree; `results.json` → `git.dirty: false`), vnxdna 6.0.0.dev0, simulator 1.0.0.
- Command: `PYTHONPATH=src python experiments/v6/phase3/EXP-SIM-1/run.py` (config: `config.json`; 132.5 s wall, one
  process, on the shared development VPS).
- Grid: 14 models × 3 strand files × 5 seeds (9100–9104) = 210 cells. Strand files: 2,500 random 120-nt strands (three
  simulator batches), 300 random 80-nt strands, and the 820-strand, 313-nt `tests/fixtures/v6_0/max-recovery` encoder
  output. Each cell is simulated three times: by the reference (the Phase 1 composer
  `experiments/v6/channel/channel.py`: `vnxdna.simulation.loss` + `vnxdna.simulation.channel`), and by the engine with
  the /0 file and with the shipped /1 model.
- Metric: SHA-256 of each read file (FASTQ).

## Result (`results.json`)

| Criterion | Result |
|---|---|
| cells with three identical read-file SHA-256 | **210 / 210** |
| simulations | 630 (829,611,638 bytes of reference FASTQ) |
| verdict (`pass`) | **true** |

The same property is a regression test (`tests/simulation/test_sim_compat.py`, smaller files and seeds, also with 3
workers) together with byte identity of old `ChannelConfig` JSON against `vnxdna.simulation.channel.simulate_file`.

## Limits

Byte identity is a statement about software reproducibility only. The 14 models are synthetic stress profiles and are not
fitted to any platform (`experiments/v6/channel/README.md`).
