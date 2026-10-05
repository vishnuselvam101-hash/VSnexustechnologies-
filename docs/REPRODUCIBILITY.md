# Reproducibility

**V4** first; the **V3** procedures follow unchanged below the divider.

## V4 experiment directories

```
experiments/EXP-XXXX-name/
  README.md         purpose, method, results summary, limitations
  config.json       the complete definition: input generator (pattern, size, seed), codes/layout, channel, sweep, trials
  environment.json  CPU, logical CPUs, RAM, OS, Python, package versions, vnx version, git commit, UTC timestamp
  input.sha256      SHA-256 of the generated input
  seed.json         every seed in the configuration (trial seeds derive from the base seed: vnxdna.v4.sweep._seed)
  results.json      every trial (no filtering) + per-point aggregates
```

Inputs are generated deterministically, so a directory is self-contained:

```bash
vnx experiment run experiments/EXP-0001-substitution/config.json     # (re)writes environment/seed/input/results
vnx experiment reproduce experiments/EXP-0001-substitution           # re-runs and compares every deterministic field
```

`reproduce` compares outcomes, counts, read numbers, sizes and SHA-256s. It excludes timings and memory, which depend
on the machine. It exits 0 only if everything matches. Determinism holds for the same software version. A version
that changes encoding or simulation can legitimately change results; the git commit in `environment.json`
identifies the code.

**Manifests (V6).** `vnx experiment run` also writes `manifest.json`, a `vnx.experiment/1` manifest. It records the
experiment ID, source class, input hash, codec version, commit, simulator version, seed, parameters, hardware, workers
and result hash. `vnx channel simulate ... --manifest FILE` writes one for a single SIMULATED channel run.
`vnx experiment reproduce MANIFEST` re-runs a manifest and exits 0 only if the result hash matches. Format, validation
rules and exit codes: [CONFORMANCE.md](CONFORMANCE.md).

Benchmarks: `vnx benchmark --profile balanced --output bench.json` writes JSON (with environment and resolved
configuration) plus a Markdown table next to it. The V3 baseline is reproduced by
`python benchmarks/baseline/v3/run_v3_baseline.py`.

---

# V3 reproducibility — unchanged

Every number in the documentation is produced by a script from JSON in `research/results/`. None is typed by hand.
This page lists how to regenerate each one.

## Environment

```bash
git clone https://github.com/vishnuselvam101-hash/VSnexustechnologies-.git && cd VSnexustechnologies-
git checkout <commit>                   # the commit recorded in each result's "environment.git.commit"
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
vnx-dna version --json                  # records Python, platform and library versions
```

Linux, Python ≥ 3.12, CPU only. The dependencies are pinned by range in `pyproject.toml`; every result file records
the exact versions that produced it (`environment.libraries`), the machine, the CPU count and the Git commit with a
`dirty` flag.

## Tests

```bash
PATH=$PWD/.venv/bin:$PATH python -m pytest      # the README tests need vnx-dna on PATH
ruff check src tests research                   # lint (pyflakes rules)
python -m compileall -q src
```

## Determinism

| component | determinism |
|---|---|
| unencrypted archives | a pure function of the input bytes, the options and the stored name (content-derived archive ID, `created_at` null by default) |
| encrypted archives | random salt, archive ID and AEAD nonces by design; everything else deterministic |
| strands, DNA index | pure functions of the container |
| simulated channel | PCG64 seeded with `(seed, batch)`: the same strands, configuration and seed give byte-identical reads, whatever the worker count and input file format |
| experiments and sweeps | trial `t` uses seed `seed + t`; results are identical for any `--workers` (tested) |
| benchmarks (timings) | not deterministic: wall time, CPU time and RAM depend on the machine and its load; every result records the machine and the load average before the scale runs |

## Regenerating the V3 results

A VNX-DNA 2.0.0 interpreter is needed for the V2 baseline (tag `v2.0.0`):

```bash
git worktree add --detach ../vnx-v2 v2.0.0 && python3 -m venv ../vnx-v2/.venv && ../vnx-v2/.venv/bin/pip install -e ../vnx-v2
python research/v3/run_v3_research.py --out research/results/v3 --baseline-python ../vnx-v2/.venv/bin/python
python research/v3/render_v3_tables.py
```

| result file | produced by | rendered into |
|---|---|---|
| `scale-v2.json`, `scale-v3.json` | `vnx-dna benchmark scale --sizes 1MB,10MB,100MB,1GB` (V2 and V3) | BENCHMARKS.md, LARGE_FILES.md |
| `stages-v2.json`, `stages-v3.json` | `vnx-dna benchmark stages --sizes 100KB,1MB` | BENCHMARKS.md |
| `noisy-decode.json` | `research/v3/noisy_decode_bench.py` | BENCHMARKS.md |
| `indel-v2.json`, `indel-v3.json` | `research/v3/indel_repair_bench.py` | SYNCHRONIZATION.md |
| `sweep-cov1-repairs-off.json`, `sweep-cov1-repairs-on.json`, `sweep-cov5-consensus.json` | `vnx-dna simulate-errors` (sweeps listed in `run_v3_research.py`) | ERROR_MODEL.md |
| `v2-reads-v3.json` | `research/v3/check_v2_reads_v3.py` | COMPATIBILITY.md, V3_AUDIT.md |
| `run-meta.json` | versions, machine, start and end time, load averages | – |

`--only NAME …` reruns a subset (`scale stages noisy compat indel sweep1 sweep5`). `--large` adds a 5 GB V3 run.

The V2 results in `research/results/v2/` were produced by `research/v2/run_v2_research.py` at VNX-DNA 2.0.0rc4 and are
kept unchanged as the historical record; `research/v2/render_v2_tables.py` renders them.

## Compatibility fixtures

`tests/fixtures/v2_0/` was generated once by `research/v3/make_v2_fixtures.py` with the 2.0.0 release. Its
`SHA256SUMS.json` is checked by the test suite.

## What cannot be reproduced here

Physical results: no synthesis, sequencing or wet-lab measurement exists for this project.
