# Research pipeline

```
QUESTION → SEARCH → COLLECT SOURCES → EXTRACT CLAIMS → CLASSIFY EVIDENCE → IMPLEMENT EXPERIMENT → RUN TEST → COMPARE → DOCUMENT RESULT
```

| Step | Who / what | Output |
|---|---|---|
| Question | human or `vnxdna improve` proposal | one falsifiable question |
| Search, collect sources | RESEARCHER agent (tier 4: the external agent CLI with WebSearch/WebFetch/Read) via `vnxdna agent researcher "…"` | source list with DOI/URL |
| Extract claims, classify evidence | RESEARCHER | claims table with labels |
| Implement experiment | CODER / TEST ENGINEER | `experiments/<suite>.yaml` (+ code on an `experiment/*` branch) |
| Run test | `vnxdna experiment <suite>` (governed) | `/opt/vnx-dna/experiments/<suite>/<run>/` |
| Compare | BENCHMARK ENGINEER | baseline vs candidate, [benchmarks/COMPETITIVE.md](../benchmarks/COMPETITIVE.md) categories |
| Document result | DOCUMENTATION ENGINEER | `research/notes/<topic>.md`, docs updates |

LAYA rejects research output without evidence labels (`policy.yaml` → `evidence_labels_required`).

## Evidence labels

ESTABLISHED FACT · PUBLISHED RESULT (citation, table/figure) · EXPERIMENTAL OBSERVATION (command, seed, result file) ·
ENGINEERING ASSUMPTION · HYPOTHESIS · UNVERIFIED CLAIM · FUTURE WORK. See [AI_ENGINEERING_RULES.md](AI_ENGINEERING_RULES.md#3-scientific-honesty).

## Experiments

A suite is a YAML file of `trials` and/or a `matrix` (parameter lists × seeds) over the store constraints, the
sequencing channel and decode options, with optional `expect.rules`. Every trial records seed, input size, store
config, error model, reads, recovered/failed bytes, input/output SHA-256, runtime, peak RSS, CPU seconds and the outcome
(RECOVERED / FAILED_CLEAN / WRONG_OUTPUT). Shipped suites: `smoke`, `error-models`, `constraints`.

## Note template — `research/notes/<topic>.md`

```markdown
# <topic>
Question: …
Date, author/agent, commit: …

## Sources
1. <authors, title, venue, year, DOI/URL>

## Claims
| # | Claim | Label | Source / evidence |
|---|---|---|---|

## Reproduction plan
What is needed to reproduce locally (code, data, licence); or NOT REPRODUCIBLE LOCALLY and why.

## Experiment
Suite file, command, seeds, result directory.

## Result
EXPERIMENTAL OBSERVATION: … (numbers copied from the summary file, with its path)

## Open questions / FUTURE WORK
```

## Comparing with other systems

Follow [benchmarks/COMPETITIVE.md](../benchmarks/COMPETITIVE.md): MEASURED, PUBLISHED, ESTIMATED and UNKNOWN are
never mixed in one ranking; comparisons require an identical channel; third-party code is pinned, inspected and run
under the governor.
