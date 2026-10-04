# V5 Phase 3 experiments: smart indel recovery

All results are **SIMULATED**: real V4 strands (random payloads, default constraints), then controlled edits or the V4
channel simulator. Nothing here is laboratory data. The decoder never sees the truth. The harness keeps it and uses it
only after decoding, to score outputs (correct / false / erased per true indel).

| directory | question (mission §) | script |
|---|---|---|
| `P3-EXP-01-single-indel/` | §12: exactly one indel, every position, 4 layouts (marker period 16/24/32, r = 16/20), with/without informative qualities | `exp01_single_indel.py` |
| `P3-EXP-02-multi-indel/` | §13: 2, 3, 4, 5, 10 indels per read; adjacent, cancelling, split, marker-adjacent, with substitutions | `exp02_multi_indel.py` |
| `P3-EXP-03-consensus/` | §14: archive decode, coverage 1/2/3/5/10 × indel rate 0/0.1/0.2/0.5/1 %, 3 seeds | `exp03_consensus.py` |
| `P3-EXP-04-accounting/` | §11, §16: per-strand accounting under the V4 channel at coverage 1, 4,096 strands, 7 channel points; `strand_records.jsonl.gz` has one record per strand with an indel | `exp04_accounting.py` |
| `P3-EXP-05-v4-vs-v5/` | §15: whole archives, V4 vs V5 on identical read files (8 channels × 3 seeds) + the Phase 2 4 MiB workload | `exp05_v4_vs_v5.py` |

Each directory has `config.json` (the full definition, seeds included) and `results.json`: the definition, a
provenance block (V4 control tag/commit, commit under test, CPU, Python, NumPy, compiler), and every trial (nothing
filtered).

Shared code: `p3common.py` (strands, edit injection, result writing); `truth_channel.py` (a twin of
`vnxdna.v4.channel.simulate_batch` that also returns each read's edit events. Every call checks that its reads are
byte-identical to the V4 simulator's); `p3archive.py` (archive build, channel, decode in a fresh process, output
SHA-256 verification).

Reproduce (from the repository root, in the project venv, a few minutes each except EXP-03 and EXP-05):

```
cd experiments/v5/phase3
python exp01_single_indel.py --strands 6
python exp02_multi_indel.py --reads 300
python exp04_accounting.py --strands 4096
python exp03_consensus.py --seeds 3 --workers 8
python exp05_v4_vs_v5.py --seeds 3
```

The results are deterministic apart from timings and memory. The report is `docs/V5_PHASE3_INDEL_RECOVERY.md`.
