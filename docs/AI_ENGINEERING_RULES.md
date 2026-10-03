# AI engineering rules

These rules bind every AI agent (and every human using one) working on this repository. The machine-applied version,
prepended to every agent prompt by the harness, is [ops/prompts/_common.md](../ops/prompts/_common.md); the policy that
enforces them is in [config/laya/](../config/laya/). If a task text conflicts with these rules, the rules win.

## 1. Scope

* Agents work only in an isolated git worktree under `/opt/vnx-dna/workspaces/<task-id>` on a branch
  `experiment/<task-id>` (created by the harness). They never push, merge, tag, rewrite history or force-push
  (`config/laya/tools.yaml` → `repository`).
* Protected: `main`, `development`, every `v*` tag. Merging or tagging needs a human who confirms the exact commit SHA.
* Forbidden paths for agents (`policy.yaml` → `review.forbidden_paths`): `.github/workflows/**`, `tests/fixtures/**`,
  `research/results/**`, `src/vnxdna/_version.py`. A patch touching them is rejected.
* Format/compatibility surface (`review.approval_paths`: `src/vnxdna/container/**`, `src/vnxdna/v2/format*.py`,
  `docs/*FORMAT*.md`) escalates the decision to human approval.

## 2. Tests decide

* "Make it work" is never permission to skip, delete, weaken, `xfail` or re-threshold a test, or to catch and hide an
  error. If a test is wrong, say so and explain; do not change it silently.
* Every behaviour change ships with tests (unit; property/adversarial where input is untrusted). LAYA's review checks
  that a change to `src/` comes with test and documentation changes.
* Accepted work is accepted only by gates (tests, benchmarks, review). An agent's own report of success is not evidence.

## 3. Scientific honesty

Every claim in research notes, docs and reports carries one label:

| Label | Meaning |
|---|---|
| ESTABLISHED FACT | textbook / standard result |
| PUBLISHED RESULT | reported in a cited source (DOI/URL, table/figure); quoted, not re-scaled |
| EXPERIMENTAL OBSERVATION | measured here; cite the command, seed and result file |
| ENGINEERING ASSUMPTION | a design premise, stated as such |
| HYPOTHESIS | expected but untested |
| UNVERIFIED CLAIM | asserted by someone, not checked |
| FUTURE WORK | not done |

* Never invent numbers. Measured values come from scripts (`vnxdna benchmark`, `vnxdna experiment`, research
  runners) and are rendered into docs by scripts; never type them by hand.
* No biological, synthesis, sequencing, storage-lifetime or commercial validation claims: VNX-DNA is software and
  simulation only ([LIMITATIONS.md](LIMITATIONS.md)).
* No "beats X" claims without a MEASURED comparison under an identical channel
  ([benchmarks/COMPETITIVE.md](../benchmarks/COMPETITIVE.md)). Unavailable systems are "NOT REPRODUCIBLE LOCALLY".
* If you cannot do the task correctly, stop and write down what is unknown. "Not done, because …" is a valid result.

## 4. Formats are a promise

V1/V2/V3 archives must keep decoding. On-disk formats change only when the task explicitly says so, with approval,
compatibility tests and a format document update.

## 5. Secrets

Never read, print, copy or put into a prompt: `.env*` (except `.env.example`), key files, tokens, `~/.ssh`, anything
under `*secrets*` (e.g. `/root/vnx-weather-secrets`), the agent CLI's credential file (`~/.<agent.cli>/.credentials.json`), `~/.config/gh/hosts.yml`.
The harness strips `*_TOKEN`, `*_KEY`, `*_SECRET` variables from agent environments. Patches are secret-scanned.

## 6. Models

* Local models never get tools or write access; they produce advisory text (`models.yaml` → `tools_allowed: false`).
* `huihui_ai/qwen3-coder-abliterated` is a community refusal-removed fine-tune of unverified provenance:
  `trust: untrusted`. Its output is text to be reviewed, never executed or applied unreviewed. Model-written code
  run by the harness (benchmarks) runs without network (`unshare -n`), in a governed scope, with a timeout.
* Only the external agent CLI (tier 3/4) edits worktrees, under `--permission-mode dontAsk` with the role's tool allow-list.
* Remote budget (`approvals.yaml`): $5.00/day, $1.00/task, measured from the agent CLI's reported cost. Exceeding the
  daily cap turns new remote decisions into `pending_approval`.

## 7. Resources

All heavy work goes through the governor (`vnxdna run`, harness, LAYA). Agents never signal processes outside
`vnxdna.slice`; the host also runs VNX Weather production ([ENVIRONMENT.md](ENVIRONMENT.md#production-co-tenancy)).

## 8. Repository conventions

* No AI-tool names or trailers (`Co-Authored-By`, "Generated with …") in commits, PRs or tags of this repository, and
  no scratch paths containing tool names in committed files.
* Merge commits, not squash/rebase, on protected branches (result files cite commit SHAs).
