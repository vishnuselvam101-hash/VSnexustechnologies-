# Phase 6 workstream E: realistic simulated-channel benchmark suite (SIMULATED)

SIMULATED: every input comes from the VNX-DNA channel simulator (random or synthetic payloads, archives built by the
V12 tree, strands and reads simulated in software). No DNA was synthesised, stored or sequenced; nothing in this
directory is physical-DNA evidence, and it must never be combined with physical-DNA results.

## Files

| file | what |
|---|---|
| `workloads.json` | the PREREGISTERED workload list (30 workloads), seeds, configurations, statistics, CI-extension and verdict rules. Committed (a81df8c) before any run; changes only as dated entries under `amendments` |
| `suite.py` | the harness: `validate`, `run`, `summary`, `repro` (one configuration on one prepared input) |
| `smoke/` | the harness-validation run at b69e70f (reduced sizes, contended host): `rows.jsonl`, `warmup.jsonl`, `prep.jsonl`, `environment.json`, `summary.json`, `run.log` |
| `../../../../tests/v14/test_p6e_suite.py` | tests of the manifest parser/validator, statistics, classification, summary and adversary transforms |

## Method (extends the V13/V14 harness, docs/V13_BENCHMARK_REPORT.md §1)

`suite.py` reuses the subprocess-per-tree step of `experiments/arch/compare.py` (extended to record the decoder's
stage timings and recovery counters), the process-tree RSS sampler of `experiments/v10/opt/bench.py` and the
bootstrap statistic of `experiments/v11/bench/bench.py` (tests/v14 checks the two agree). Per (workload, seed) the
input is prepared once: payload -> archive (V12 tree, `BASE_TREE`) -> strands (driver tree) -> strand loss + read
channel (driver tree; the plain `vnxdna.v4.channel` path of the V13 harness, or `experiments/v6/channel` composition
when a versioned model in `experiments/v6/channel/models` or strand loss is involved) -> optional adversary
transform. Every configuration decodes (or encodes) the same file:

- `v12` = `BASE_TREE` (build/v12 6bf7749, the tier denominator), `v13` = `V13_TREE` (v13.0.0 58171b8, the V13
  reference path), `v14` = the tree under test, `v14aa` = the tree under test again (the in-run A/A band);
- paired per seed, configuration order reversed on odd seeds, one discarded warm-up per configuration per workload;
- ratios v12/v14 (tier), v13/v14, v12/v13 and v14/v14aa (A/A), percentile bootstrap 95 % CI of the median ratio
  (10 000 resamples, seed 11; at n = 5 the min-max range); a ratio is "beyond noise" only when its CI does not
  overlap the A/A CI of the same workload; a tier needs the CI lower bound above the threshold and beyond noise;
- the preregistered CI-extension rule: when (hi - lo) / median of v12/v14, v13/v14 or v14/v14aa exceeds 0.25 on a
  timed workload, its extension seeds run automatically (n 5 -> 10, or 3 -> 6 for the large inputs);
- per run: size, seed, channel/loss parameters (model name, version, SHA-256), adversary parameters, workers,
  commit (`+dirty` when a tree has uncommitted changes under `src/`), wall seconds, decoder stage seconds, peak RSS
  (sampled process tree and the decoder's own figure), outcome, output SHA-256, `matches_input`, false success,
  foreign-archive output, engine rung, fallbacks, failed groups, conflicts, budget exhaustion, host load;
- correctness classes (`workloads.json` `method.expect`): false success = SUCCESS with output != input archive;
  CRASH (non-zero exit), TIMEOUT and ERROR:<type> are outcomes, never dropped; an input whose run misses its
  expectation, or whose configurations disagree, is kept with a `reproducer.json` in `--keep`.

## Workloads (preregistered; see `workloads.json` for every parameter)

- **targets** (docs/V14_BRIEF §10): `T-A-indel-4m-w1` (target A, 4 MiB indel decode, 1 worker; the V13 report's
  indel-w1 seeds and channel), `T-B-clean-4m-w4` (target B, clean coverage-3 decode, 4 workers), `T-E-encode-4m-w1`
  and `-w4` (target E, no encode regression);
- **channel**: indel-4m-w4 (V13 continuity), mixed sub/ins/del with homopolymer and burst errors, the `mixed-mild`
  and `mixed-harsh` models, 50 % and 100 % reverse-complemented reads, 5 % strand loss with negative-binomial +
  GC-biased coverage, the `dropout-10` model, 30 % duplicates with 85 % strand bias, low coverage (Poisson 2 and 1.5);
- **large**: 16 MiB indel (V13 continuity), 64 MiB substitution decode;
- **payload** (uncompressed, so the structure reaches the strands): zero-heavy (4 and 1 workers), repetitive,
  structured binary records, text;
- **adversarial pools** (1 MiB): 40 % reads of a foreign archive, a file truncated mid-record, 15 % junk records,
  2 % malformed records, PCR-jackpot duplicates, a poisoned consensus (a wrong majority of 12 copies with 12
  substitutions for 2 % of strands), an empty pool, strand loss beyond the code's capacity.

## Full measurement (for the integrated build/v14; one command)

Run on an otherwise idle host, no CPU quota (the V13 report's conditions), in its own systemd scope (NOT
`vnxdna.slice`: its TasksMax=256 starves OpenBLAS at interpreter start-up when the host is shared), ~12 GiB free under
`TMP` (the 64 MiB workload's read file is ~6.5 GB). The harness runs from any checkout that holds this directory
(`SUITE`); the tree under test is `V14` (the build/v14 checkout) and is used for the driver (strands, channel) and
the `v14`/`v14aa` decodes. `EXPECT_*_COMMIT` pins every tree: the run refuses a tree at another commit or with
uncommitted changes under `src/`. Native kernels and the engine must be built in all three trees (`python -m vnxdna.native`
-> `all_native: true`; rows record `stale_libraries`). OpenBLAS threads are left at their default, as in the V13 runs.

```bash
SUITE=/root/vnx-dna-lab/v14-e        # any checkout holding experiments/v14/bench/p6e-channels
V14=/root/vnx-dna-lab/v14            # build/v14 checkout under test
OUT=$SUITE/experiments/v14/bench/p6e-channels/full; TMP=/root/vnx-dna-lab/scratch-p6e
mkdir -p "$OUT" "$TMP" && cd "$SUITE" && \
systemd-run --scope --quiet --collect -p MemoryMax=24G -p TasksMax=8192 \
  env PATH=/root/vnx-dna-lab/.venv/bin:$PATH PYTHONPATH=$V14/src VNX_ARCH_TMP=$TMP \
      V14_TREE=$V14 EXPECT_V14_COMMIT=$(git -C $V14 rev-parse HEAD) \
      BASE_TREE=/root/vnx-dna-lab/v13-base EXPECT_V12_COMMIT=6bf7749 \
      V13_TREE=/root/vnx-dna-lab/v14-base EXPECT_V13_COMMIT=58171b8 \
  python experiments/v14/bench/p6e-channels/suite.py run --out "$OUT" --keep "$TMP/keep" 2>&1 | tee -a "$OUT/run.log" && \
PYTHONPATH=$V14/src /root/vnx-dna-lab/.venv/bin/python experiments/v14/bench/p6e-channels/suite.py summary --out "$OUT" \
  | tee "$OUT/summary.txt"
```

The run resumes where it stopped (rows already in `rows.jsonl` are skipped), so an interrupted run is re-started
with the same command. `--groups target` (or `--workloads a,b`) restricts it; the preregistered workload list is
not changed by doing so, and every workload not run is reported as NOT RUN.
Expected runtime: about 3.5 h without CI extensions, scaled from the smoke run's per-seed costs (sizes x seeds,
measured on a contended host, so an idle host should be faster); up to about 6.5 h if every timed workload triggers its
extension seeds. The 64 MiB workload is ~45 min of it. `--groups target` alone is ~15 min.

## Smoke run (harness validation, contended host)

`suite.py run --smoke` on 2026-10-18 at a81df8c (decoder code = b69e70f, build/v14 after Phase 6A; the
commit adds only `workloads.json`), V12 = 6bf7749, V13 = 58171b8, all kernels native in the three trees. Every
workload ran at min(size, 1 MiB) with 2 seeds and the four configurations (240 measured runs, 120 discarded
warm-ups). It ran in a systemd scope (MemoryMax 8 GiB, CPUQuota 400 %) while three other engineering jobs shared
the 8 vCPUs (load1 up to ~20). **Label: harness validation, contended host. No timing, ratio or RSS figure from this
run is a V14 result**; it only shows that the harness works and gives the A/A spread under these conditions. The
correctness observations below DO count.

Rows: `smoke/rows.jsonl` (`summary.json`, `summary.txt`, `prep.jsonl`, `warmup.jsonl`, `run.log`,
`environment.json`). Seed 98020 of `mixed-harsh-model-1m-w4` failed at interpreter start-up in all four
configurations (OpenBLAS could not create threads: the shared `vnxdna.slice` task limit, TasksMax 256, was
exhausted by other jobs); that workload was re-run on its own (`environment-rerun.json`) and the failed rows are
kept in `superseded-rows.jsonl`. The harness now retries such a host start-up failure up to 3 times and records each
attempt (`retried_env_crash`). A first smoke attempt lost its rows when another job reused the output directory;
its log is `run1-partial.log` (the same outcomes, plus one more such start-up failure, of V13 on
adv-malformed-records seed 98230, which decodes to the same `VNXFormatError` as the others when re-run).

Correctness (240 runs): **0 false successes, 0 foreign-archive outputs, 0 crashes or timeouts** of the code under
test. Every configuration agrees on outcome and output bytes on every input except one (below).

| observation | workloads | detail |
|---|---|---|
| fast engine falls back on structured payloads | payload-zeros (w1, w4), payload-repetitive, payload-text | V13 and V14 decode correctly, but only after the `stream` engine rung returns FAILURE and the ladder falls back to the `v13` rung (`engine.fallbacks = [stream: status FAILURE]`); decode RSS then ~490 MiB at w4 instead of ~100 MiB and most of the V14 speed-up is lost. Already present in v13.0.0. Random and structured-binary payloads take the `stream` rung. It also happens on a clean coverage-3 channel with an all-zero uncompressed archive (reproducer below) |
| V12 recovery limit | mixed-4m-w4 seed 98001 | V12 FAILURE, V13/V14 SUCCESS with the correct bytes (the only non-identical input; a V12 limitation, not a V14 one) |
| preregistered expectation missed in every tree | dropout10-model-4m-w4 (expect `success`) | 10 % strand loss + Poisson(5) coverage is at the outer code's capacity on 1 MiB inputs: all four configurations return FAILURE (no false success). Reported as a missed expectation, not amended |
| beyond capacity, refused | mixed-harsh-model, dropout-uneven, lowcov2, lowcov-edge, adv-loss-beyond-capacity | FAILURE in every tree (expect `no-false-success` / `explicit-failure`) |
| adversarial pools | adv-* | foreign 40 % mix: `VNXAddressError` refusal in every tree; truncated, malformed and empty pools: `VNXFormatError` refusal (the reader rejects the whole file at the first broken record); junk reads, PCR jackpot and poisoned consensus (wrong-majority copies): SUCCESS with the correct bytes |

**Update (finding 1):** addressed by Phase 6 workstream C (strand scheduler, branch work/v14-p6c 0f9c98e, pending
review and merge): per the workstream C report, zero payloads now stay on the `stream` engine (4 MiB, 4 workers: decode
RSS 489.8 -> 111.1 MiB, no fallback). Those figures are workstream C's, not re-measured here; the full run of this suite
on the integrated build/v14 measures all four structured-payload workloads.

Reproducer of the engine fallback (v13.0.0 and build/v14 before workstream C):

```python
from pathlib import Path
from vnxdna.v4 import archive as ar, encoder as en, decoder as de, channel as ch
w = Path("."); (w / "in.bin").write_bytes(bytes(1 << 20))
ar.build_archive([w / "in.bin"], w / "a.vnx", ar.ArchiveOptions(compression="none", dedup=False))
en.encode_container(w / "a.vnx", w / "s.fasta", en.DNAOptions())
ch.simulate_file(w / "s.fasta", w / "r.fastq", ch.ChannelConfig(seed=7, coverage=3))
r = de.decode_reads(w / "r.fastq", w / "o.vnx", de.DecodeOptions(workers=1))
print(r.status, r.report["engine"]["rung"], r.report["engine"]["fallbacks"])
# -> SUCCESS v13 [{'rung': 'stream', 'reason': 'status FAILURE', ...}]   (random bytes -> rung 'stream', no fallback)
```

or, through the suite: `suite.py repro --workload payload-zeros-4m-w1 --seed 98085 --smoke --config v14`.

A/A (v14/v14aa, the same tree twice, paired) under these contended conditions: per-workload median ratio 0.83-1.48
(median over workloads 1.03); the widest CIs are on the sub-second workloads (T-E-encode-4m-w1 [0.55, 1.50],
T-B-clean-4m-w4 [0.76, 1.39], adv-truncated [1.04, 1.77]). That spread is
the contended host's, and is why no smoke ratio is interpretable; the full run on an idle host measures its own
A/A band per workload and extends the seeds where the preregistered rule says so.
