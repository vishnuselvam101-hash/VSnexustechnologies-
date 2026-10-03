# VNX-DNA — instructions for coding agents

Read first: [docs/AI_ENGINEERING_RULES.md](docs/AI_ENGINEERING_RULES.md) (binding),
[docs/ENVIRONMENT.md](docs/ENVIRONMENT.md), [docs/DEVELOPMENT_WORKFLOW.md](docs/DEVELOPMENT_WORKFLOW.md).

VNX-DNA is computational DNA data storage in Python: software and simulation only, not wet-lab validated.

## Layout

* `src/vnxdna/` — the library: `container/` (archive format), `dna/` (mapping, strands, constraints, reads), `ecc/`
  (Cauchy RS outer code, inner RS, engine), `storage/`, `sync/` (indel/burst resynchronisation), `v2/` (streaming
  format-5 pipeline), `v3/` (error sweeps), `legacy/` (read-only V0.1), `cli.py` (`vnx-dna`).
* `tests/` — `unit/`, `integration/`, `property/`, `adversarial/`, `cli/`, `v2/`, `v3/`, `fixtures/` (never edit).
* `research/` — runners and `results/` (generated; never edit by hand).
* `ops/vnxops/` — engineering environment (LAYA, harness, governor, benchmarks); `src/` never imports it.
* `config/laya/` — policy; `experiments/`, `benchmarks/` — suites; `docs/releases/` — generated baselines.

## Commands

```
/opt/vnx-dna/venv/bin/python -m pytest -q -m "not slow"   # pytest pythonpath = src, tests (pyproject)
/opt/vnx-dna/venv/bin/ruff check src tests research ops
vnxdna test fast | full | ops                              # governed
vnxdna run --class build -- <heavy command>                # anything heavy goes through the governor
vnxdna doctor
```

## Rules in short

* `v3.0.0` is the protected baseline ([docs/releases/V3_BASELINE.md](docs/releases/V3_BASELINE.md)); `main` and tags
  are never changed by agents. Work on `feature/*` / `experiment/*`.
* Tests decide; never weaken or skip them. Every change ships with tests and docs.
* Never invent or hand-type measured numbers; label claims; no physical-DNA or "beats X" claims.
* Keep V1/V2/V3 archives decodable; format changes need explicit approval.
* No secrets in prompts, logs or commits.
* No AI-tool names or trailers in commits, PRs or tags.
