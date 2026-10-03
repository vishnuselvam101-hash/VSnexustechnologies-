# Development workflow

All commands below are the `vnxdna` CLI (`/usr/local/bin/vnxdna`; it runs `ops/vnxops` from `$VNXDNA_REPO`, default
`/root/vnx-dna-env`, with `/opt/vnx-dna/venv`). Every ops command accepts `--json`. Library commands (`store`,
`encode`, `decode`, `verify`, `restore`, `inspect`, `simulate`, `sequence`, `benchmark scale`, `experiment run`, …)
are forwarded to the VNX-DNA CLI of the same checkout.

## Branches

| Branch | Use |
|---|---|
| `main` | protected; releases only; merge commits after explicit SHA confirmation |
| `development` | integration branch for V4+ (created at `v3.0.0`) |
| `feature/*` | human-led work, merged into `development` |
| `experiment/*` | one per agent task, created by the harness in `/opt/vnx-dna/workspaces/` |
| `release/*` | release candidates; `vnxdna release check` runs here |

Never force-push or rewrite published history. Tags `v*` are never moved.

## Start of day

```
vnxdna doctor            # every check PASS (warnings explained); --deep adds e2e, ops tests, bench smoke
vnxdna status            # one screen: resources, governor level, queue, git, last test/build gate
```

**V4 work starts only after `vnxdna doctor` has no FAIL and `docs/releases/V3_BASELINE.md` is recorded.**

## Start a piece of work

```
git -C /root/vnx-dna-env worktree add -b feature/<name> /root/vnx-dna-<name> development
cd /root/vnx-dna-<name>
VNXDNA_REPO=$PWD vnxdna test fast        # tests of this checkout
```

## Testing and heavy commands

```
vnxdna test fast | full | ops | all       # governed pytest; records reports/gates/tests.json
vnxdna e2e                                # small end-to-end run, SHA-256 + reproducibility
vnxdna run --class build --mem 2GiB -- <command…>   # any heavy command inside vnxdna.slice
vnxdna governor                           # level, active jobs, floors
```

Classes: `build`, `test`, `bench`, `experiment`, `agent`, `llm`, `misc` (`config/laya/resources.yaml`). A job that
cannot be admitted waits up to 600 s, then exits 75.

## Working through LAYA

```
vnxdna laya plan "implement streaming random access for V5"   # decision only (status planned / pending_approval)
vnxdna laya run  "run the full regression suite"              # decide and execute
vnxdna laya approve <decision-id> --by <name>                 # approve a high/critical-risk decision
vnxdna laya run  "<same text>" --decision-id <decision-id>    # execute the approved decision
vnxdna laya list                                              # recent decisions
vnxdna agent list                                             # roles, tiers, tools
vnxdna agent coder "fix the failing CLI test X"               # route to a role (its first task type)
vnxdna harness records                                        # execution records
vnxdna harness tool ruff                                      # one tier-0 tool under the harness
```

Accepted agent work stays on `experiment/<task-id>` with `changes.patch` in its artifacts directory; a human
reviews and merges it into a `feature/*` or `development` branch.

## Queue

```
vnxdna queue put "run the tests" [--type test_run]
vnxdna worker --max 5        # process up to 5 queued requests through LAYA
vnxdna queue                 # health and depth (local SQLite by default; --backend redis)
vnxdna queue selftest
```

## Experiments and benchmarks

```
vnxdna experiment --list
vnxdna experiment smoke | error-models | constraints
vnxdna benchmark --dna --suite smoke | standard | large
vnxdna benchmark --system [--full]    # re-measure hardware, rewrite hardware-capability.yaml
vnxdna models bench                   # re-measure local models + remote probe
```

See [BENCHMARK_METHODOLOGY.md](BENCHMARK_METHODOLOGY.md) and [RESEARCH_PIPELINE.md](RESEARCH_PIPELINE.md).

## Backups, security, self-improvement, release

```
vnxdna backup --label <why> /root/VSnexustechnologies- /root/vnx-dna-env   # before any major change
vnxdna backup --verify /opt/vnx-dna/backups/<dir>
vnxdna security                       # gitleaks, pip-audit, SBOM, permissions, subprocess audit
vnxdna improve                        # observations → reports/proposals/<date>.md (never edits code)
vnxdna release check --version V4     # acceptance gates → release/V4_ACCEPTANCE_REPORT.md
vnxdna validate [--only N]            # the 13 environment validation checks → reports/validation.json
vnxdna report                         # /opt/vnx-dna/reports/ENVIRONMENT_READY.md
```

## Commits

Small commits with tests and docs; no AI-tool names or trailers ([AI_ENGINEERING_RULES.md](AI_ENGINEERING_RULES.md)).
