# VNX-DNA research automation: plan

Status: **PLAN, nothing built.** Written 2026-10-05 on `work/research-gate` (base 081697b). This plan **extends** the
existing workforce in `/root/vnx-dna-ai` and the intel sweep in `/root/vnx-dna-company/vnxco/intel.py`; it adds no
second queue, no second knowledge index and no agent dispatcher.

Inputs: the research gate of 2026-10-05 in `research/competitive-2026-10-05/` (cited as `[00]`, `[10]`,
`[20]`, `[30]`, `[40]`), the workforce documents (`ARCHITECTURE.md`, `RESEARCH_PIPELINE.md`, `TOOL_REGISTRY.md`,
`JOBS.md`, `WORKFLOWS.md`, `WORKFORCE_HARDENING_PLAN.md`), the scheduled jobs (`openclaw cron list`, read on
2026-10-05) and the LAYA router configuration (`/root/vnx-dna-env/config/laya/`).

Terms used below:

- **script**: deterministic program, no model;
- **local model**: LAYA tier 1 (`tiny-local`, a small local model already installed), used only for short summaries and
  classification hints whose output is marked UNTRUSTED;
- **remote model job**: LAYA tier 3/4 (`strong-remote` / `specialist-research`), the single remote model provider the
  founder has approved; no other remote provider is used;
- **scheduler**: the existing scheduled-job layer (`openclaw cron`), times in Asia/Kolkata.

---

## 1. Founder rules this plan keeps

1. One approved remote model provider only; no other remote provider, no second router.
2. **No autonomous code-changing dispatcher.** Every automated step either collects data, writes reports and ledger
   lines, or adds *tracking* records to `vnx-jobs`. No automated step edits code, commits, pushes or starts an agent on
   a code task. Code work starts only in a session the founder starts (`ARCHITECTURE.md`, design decision 3).
3. Scheduled remote-model jobs stay read-only on code and git (the wording already used by `vnx-weekly-intel`).
4. Deterministic first: collection, de-duplication, detection, rendering and notification are scripts.
5. Never invent links or numbers; a source is `verified: true` only after its URL or DOI was opened and title, authors
   and year matched (`RESEARCH_PIPELINE.md`, rules 1–3).
6. Reports go to the founder after each completed process, not on a timer; the weekly digest is the end of the weekly
   process.

---

## 2. What exists today (2026-10-05)

| Piece | What it does | Gap |
|---|---|---|
| `vnx-weekly-intel` (Sun 09:00, remote model job, read-only) | searches the past week's papers and company news, opens each link, appends verified sources to `kb/sources.jsonl`, writes `VNX-Vault/Intel/weekly-DATE.md` | no structured input: it searches from scratch each week; no event types; no GitHub, patents, standards, datasets |
| `vnx-daily-kb` (06:30, command) | `vnx-kb build`: FTS5 index over repo docs, vault (incl. `Intel/`), workforce docs, `kb/sources.jsonl` (162 documents on its last run) | indexes only; no collection |
| `vnx-morning-brief` (07:30, remote model job, read-only) | status brief from the health report, `vnx-lab status`, `vnx-jobs list` | does not read intel |
| `vnx-daily-health` (06:45, command) | git, tests, deps, security, fuzz, jobs, system | does not check research collectors |
| `vnx-weekly-roadmap` (Mon 08:00, remote model job) | progress vs plan; may add job records | — |
| `vnxco/intel.py` | keyless sweep: Google News RSS (4 queries), GDELT, OpenAlex (5 queries), Google News per company (12 names); de-duplicates by URL into `vnx-dna-company/state/harness.db` table `intel`; appends `VNX-Vault/Intel/<date>.md` | **not scheduled**: it was called by the previous harness, removed 2026-10-04; `harness.db` holds 28 items, the newest seen on 2026-10-04. Its company list still uses "Atlas Data Storage" (renamed AtlasBase) and "Catalog Technologies" (assets acquired by Biomemory) `[30 §1]`. Crossref is named in its docstring but has no collector |
| `kb/sources.jsonl` | verified-source ledger | 4 lines; hardening item H5.3 targets ≥ 50 verified sources |
| `vnx-jobs` | tracking-only queue; types include `research`, `competitive-intelligence`, `benchmark`, `scientific-review` | — |
| Research gate raw files | 179 GitHub repositories and 8 "no public code found" records `[10]`, `[20]`; 38 organisations with 53 verified patent records `[30]`; 38 profiled datasets and 28 further candidate accessions `[40]` | not machine-watched |

---

## 3. Architecture

```
 (A) collectors (scripts) ── daily: papers, GitHub watched repos, company news (intel.py)
                          └─ weekly: patents, standards pages, datasets, org scans, citation watch, conferences
        │  observations → research/state.sqlite (obs, seen, cursors, failures)
 (B) detector (script) ── rules R-xx per event type, against research/watchlist.jsonl and the ledgers
        │  candidate events → research/events.jsonl (status: candidate)
 (C) triage (local model, optional) ── 2-sentence summary + category hint per candidate, marked UNTRUSTED
        │
 (D) verification (remote model job = vnx-weekly-intel, read-only on code)
        │  opens every candidate, confirms or rejects it, appends verified sources to kb/sources.jsonl,
        │  writes Intel/weekly-DATE.md, adds tracking jobs within caps (§8)
 (E) notify (script) ── renders the digest from events.jsonl and sends it to Telegram (§10)
 (F) audit log (script) ── every write above appends one line to research/audit.log (§11)
```

One new deterministic tool, `vnx-watch`, in `/root/vnx-dna-ai/tools/` (on PATH like the other tools, listed in
`TOOL_REGISTRY.md`):

| Command | Does |
|---|---|
| `vnx-watch seed --from <raw dir>` | builds or updates `research/watchlist.jsonl` from the research-gate JSON files (§5.3) |
| `vnx-watch collect --daily \| --weekly [--only <collector>] [--budget 10m]` | runs collectors; news, papers and company items are collected by **calling `vnxco.intel.sweep()`** unchanged and importing the new rows of `harness.db`, so the existing sweep keeps writing `VNX-Vault/Intel/<date>.md` |
| `vnx-watch detect` | applies the detection rules (§4) and appends candidate events |
| `vnx-watch triage [--max 60]` | local-model summaries via the LAYA router (§6) |
| `vnx-watch decide <EV-id> --status confirmed\|rejected\|duplicate --why "…" [--source S-…] [--job N] --by <actor>` | the only way an event's status changes; used by the weekly job and by the founder |
| `vnx-watch digest --daily \| --weekly` | renders `research/digests/<kind>-DATE.md` and copies the weekly one into `VNX-Vault/Intel/` so `vnx-kb` indexes it |
| `vnx-watch notify --daily \| --weekly [--dry-run]` | sends the rendered digest (§10) |
| `vnx-watch status [--json]` | last run per collector, consecutive failures, pending candidates by age; read by `vnx-daily-health` |
| `vnx-watch verify-log` | checks the audit-log hash chain (§11) |

The only change outside `/root/vnx-dna-ai` is in `vnxco/intel.py`: its hard-coded `COMPANIES` list is replaced by the
`company` entries of the watch list (R0, with the founder's approval because it is another repository).

---

## 4. Sources, cadence and event detection

### 4.1 Collectors

| Channel | Collector (script) | Cadence | Notes |
|---|---|---|---|
| Papers: OpenAlex | existing `intel.openalex` (5 queries) via `intel.sweep()` | daily | |
| Papers: arXiv | `export.arxiv.org/api/query`, keyword queries over cs.IT, q-bio.GN, cs.ET, cs.DS; ≤ 1 request / 3 s | daily | |
| Papers: bioRxiv | `api.biorxiv.org/details/biorxiv/<from>/<to>`, filtered locally by the relevance regex already in `intel.py` | daily | |
| Papers: Crossref | `api.crossref.org/works?query=…&filter=from-pub-date:` with a contact `mailto` header; also used to fill DOIs for `records.json` entries marked `doi: null` | weekly | |
| Citation watch | OpenAlex `filter=cites:<work id>` for anchor works: Gimpel 2026 (10.1038/s41467-026-70548-3), Gimpel 2023 dt4dds (10.1038/s41467-023-41729-1), HEDGES (10.1073/pnas.2004821117), DNA-Aeon (10.1038/s41467-023-36297-3), DNA Fountain (10.1126/science.aaj2038), Organick 2018 (10.1038/nbt.4079) `[10 Sources]`, `[20 §10]` | weekly | papers citing these are high-signal |
| GitHub: watched repos | `gh api repos/<o>/<r>` and `…/commits?per_page=1`, `…/releases?per_page=5`: HEAD SHA, release tags, licence SPDX, archived flag, stars | daily for tier 1, weekly for tier 2–3 | authenticated `gh` (≤ 500 requests/day) |
| GitHub: watched orgs and users | `gh api orgs/<o>/repos` / `users/<u>/repos` sorted by created: e.g. `dna-storage`, `uwmisl`, `fml-ethz`, `umr-ds`, `BGI-SynBio`, `HaolingZHANG`, `MLI-lab`, `HKU-BAL`, `Guanjinqu`, `TJU-QiGe`, `microsoft` (keyword filter), `atlas-data-storage`, `atlasds` `[10]`, `[20 §0]`, `[30]` | weekly | |
| GitHub: search | `gh api search/repositories` for topics `dna-storage`, `dna-data-storage`, `molecular-storage` and keyword queries, `created:>` / `pushed:>` last 8 days | weekly | search is rate-limited (the gate hit the limit once `[10]`) |
| Patents | Google Patents query endpoint (the one used by the gate `[30 Method]`): assignee queries for watched companies and text queries ("DNA data storage", "nucleotide storage codec", "oligonucleotide data encoding"); CPC filters taken from the CPC fields of the 53 seeded patents (extracted in R0, not assumed); ≤ 1 request / 10 s, ≤ 50 queries/week | weekly | endpoint is unofficial; may block (§12) |
| Company announcements | existing `intel.gnews` per company and GDELT via `intel.sweep()`; plus company newsroom RSS or page-hash watch for pages listed in the watch list | daily (news), weekly (page hash) | |
| Standards | page-hash watch: DNA Data Storage Alliance / SNIA pages (direct fetch returns HTTP 403; use the Wayback `archive.org/wayback/available` snapshot and LoC mirrors as the gate did `[30 "Source limitation"]`), SNIA Swordfish DNA working draft, `jpeg.org` news and `iso.org/standard/90579.html` (JPEG DNA, ISO/IEC 25508-1; detect stage change, DIS reached Apr 2026 `[30 §3]`), IEEE standards search for "DNA storage" (none found by the gate) | weekly | |
| Conferences | `conference` entries in the watch list (venue, OpenAlex source ID, DBLP stream, CFP and event dates), e.g. ISIT, ITW, DNA Computing and Molecular Programming, ICRC, Designing Storage Architectures (LoC), SNIA SDC; new proceedings matched via OpenAlex source filter + relevance regex; CFP dates within 30 days are listed in the weekly digest | weekly | dates are entered by a person, never guessed |
| Datasets | Zenodo `/api/records?q=…&sort=mostrecent`; ENA portal API (`search?result=study&query=…`); NCBI E-utilities `esearch` on bioproject and sra (≤ 1 request/s without a key); figshare `/v2/articles/search`; plus run-count/size checks on watched accessions `[40]` | weekly | |

### 4.2 Event types and detection rules

All rules are deterministic. "Relevance regex" is `intel.RELEVANT` from `intel.py`, extended in R1 with the method
taxonomy below. Severity decides routing (§7, §10).

Method taxonomy (for NEW ALGORITHM): indel / insertion-deletion code, marker or watermark code, HEDGES-like hash code,
fountain / LT / Raptor, LDPC, polar, convolutional / trellis / BCJR, Reed-Solomon / product code, constrained code
(GC, homopolymer, run-length), trace reconstruction, clustering of reads, consensus, soft-decision decoding, basecaller
integration, composite letters.

| Event | Rule ID | Fires when | Severity |
|---|---|---|---|
| **NEW COMPETITOR** | R-COMP-1 | an organisation **not** in the watch list appears in ≥ 2 independent company/news items in 30 days that pass the relevance regex and contain a funding, launch, product, partnership or acquisition keyword | MEDIUM |
| | R-COMP-2 | a GitHub org/user not in the watch list owns a new repo that passes R-REPO-1 and whose profile names a company | LOW |
| **NEW REPOSITORY** | R-REPO-1 | repo not in the watch list, created or first seen in the window, name/description/topics pass the relevance regex, and (licence present **or** ≥ 5 stars **or** owner is a watched org) | LOW; MEDIUM if owner is watched or licence is OSI-approved and it has a code language |
| | R-REPO-2 | watched repo changes **licence SPDX**, becomes archived, or publishes a release | HIGH if the repo is a benchmark participant or channel tool (`VNX_BENCHMARK_PLAN.md` §8), else MEDIUM |
| **NEW PAPER** | R-PAP-1 | DOI or arXiv ID not in `kb/sources.jsonl`, not in the `seen` table, title or abstract passes the relevance regex | LOW |
| | R-PAP-2 | as R-PAP-1 and it cites an anchor work (citation watch) or its authors' affiliation matches a watched organisation | MEDIUM |
| **NEW ALGORITHM** | R-ALG-1 | a NEW PAPER or NEW REPOSITORY whose title/abstract/README matches ≥ 1 taxonomy term **and** a codec/decoder phrase ("we propose", "decoder", "code construction", "achieves"); confirmed only by the remote model job | MEDIUM; HIGH after confirmation if it reports results on the dt4dds channel or the ETH protocol |
| **NEW BENCHMARK** | R-BEN-1 | paper or repo matching benchmark / comparison / "we compare … codecs" / simulator-validation phrases, or a new commit or release of a watched harness (`fml-ethz/dt4dds-benchmark`, `AAnzel/UNACORM`, `fml-ethz/dt4dds`) | HIGH if it includes any codec in the benchmark participant list, else MEDIUM |
| **NEW PATENT** | R-PAT-1 | publication number not in the patent ledger, matching a watched assignee or a text query | MEDIUM; HIGH if the assignee's threat level is HIGH in the gate (AtlasBase, Biomemory incl. Catalog) `[30 §4]` |
| | R-PAT-2 | a watched application changes status (granted, withdrawn) | MEDIUM |
| **NEW COMMERCIAL PRODUCT** | R-PROD-1 | a watched company's news or page-hash change contains product / launch / service / available / pricing / early-access keywords | HIGH for HIGH-threat companies, else MEDIUM; confirmed only on a primary source (company page or press release) |
| **NEW STANDARD** | R-STD-1 | watched standard page hash changes and the diff contains specification / draft / release / ballot / stage keywords, or the ISO stage code changes | HIGH |
| **NEW DATASET** | R-DS-1 | accession or DOI not in the dataset registry, passes the relevance regex, from ENA/SRA/Zenodo/figshare | LOW; MEDIUM if a design/reference FASTA is attached or the record cites a peer-reviewed paper |
| | R-DS-2 | a watched dataset gains runs, files or a new version | LOW |

De-duplication keys: DOI (lower-case), arXiv ID without version, `owner/repo` (lower-case), patent publication number,
accession, normalised URL. An item matching an existing key is attached to the existing event instead of creating a
new one. A paper and its code repository found in the same week are linked as one event with two identifiers.

---

## 5. Research database

Extends existing stores; nothing is moved.

### 5.1 Stores and paths

| Path | Format | Role | New? |
|---|---|---|---|
| `/root/vnx-dna-ai/kb/sources.jsonl` | JSONL, append-only | **verified-source ledger** (unchanged schema: id, title, authors, year, venue, doi, url, accessed, verified, access, claims, used_in). Two optional fields are added: `event_ids` (list) and `kind` (`paper` / `repo` / `patent` / `dataset` / `standard` / `company`) | extended |
| `/root/vnx-dna-ai/kb/kb.sqlite` | FTS5 | search index; picks up weekly digests through `VNX-Vault/Intel/` (already indexed as kind `intel`) | unchanged |
| `/root/vnx-dna-company/state/harness.db` table `intel` | SQLite | raw news/paper/company items written by `intel.py` | unchanged; read by `vnx-watch` |
| `/root/vnx-dna-ai/research/watchlist.jsonl` | JSONL, one entity per line, git-tracked | **watch list**: what is monitored and its last known state | new |
| `/root/vnx-dna-ai/research/events.jsonl` | JSONL, append-only | event log; a status change is a new line with the same `event_id` (latest line wins) | new |
| `/root/vnx-dna-ai/research/state.sqlite` | SQLite (WAL) | operational state: `obs`, `seen`, `cursors`, `failures`, `ratelimit` | new |
| `/root/vnx-dna-ai/research/digests/` | Markdown | `daily-DATE.md`, `weekly-DATE.md`, `candidates-DATE.md` | new |
| `/root/vnx-dna-ai/research/audit.log` | JSONL, append-only, hash-chained | audit log (§11) | new |
| `/root/vnx-dna-ai/research/seed/` | JSON | seed manifest: source file paths and SHA-256 of the gate files used | new |

### 5.2 Watch-list schema (`research/watchlist.jsonl`)

```json
{
  "id": "W-REPO-0001",
  "kind": "repo | org | company | patent | dataset | standard | venue | query | negative",
  "name": "MW55/DNA-Aeon",
  "aliases": [],
  "identifiers": {"github": "MW55/DNA-Aeon", "doi": null, "accession": null, "patent": null,
                  "url": "https://github.com/MW55/DNA-Aeon", "feed": null, "openalex": null},
  "tier": 1,
  "threat": null,
  "licence": {"spdx": "MIT", "note": "NOREC4DNA submodule AGPL-3.0"},
  "benchmark": {"participant": true, "phase": "B0"},
  "last_known": {"sha": "6e33bb6fc4", "date": "2025-01-14", "stars": 20, "licence": "MIT", "archived": false,
                 "stage": null, "size": null, "page_sha256": null},
  "cadence": "daily | weekly | monthly",
  "status": "active | retired",
  "sources": ["research/competitive-2026-10-05/20-repos-cluster2.json"],
  "added": {"date": "2026-10-05", "by": "vnx-watch seed"},
  "notes": ""
}
```

Field rules: `kind` and `identifiers` are required; exactly one primary identifier per kind (repo → `github`,
patent → `patent`, dataset → `accession` or `doi`, standard/company/venue → `url`); `threat` uses the gate's levels
(HIGH / MEDIUM / LOW-MEDIUM / LOW / NONE); `negative` entries hold the gate's "NO PUBLIC CODE FOUND" findings and are
re-checked monthly.

### 5.3 Seeding (R0, deterministic)

`vnx-watch seed --from research/competitive-2026-10-05` reads only the JSON files and records their SHA-256
in `research/seed/`:

| Source | Becomes | Expected count (2026-10-05) | Tier rule |
|---|---|---|---|
| `10-repos-cluster1.json` + `20-repos-cluster2.json` | `repo` entries, de-duplicated by `owner/repo` | 179 | tier 1: benchmark participants and channel tools; tier 2: audit depth in-depth/deep/medium; tier 3: light/shallow |
| same files, "NO PUBLIC CODE FOUND" records | `negative` entries | 8 | monthly |
| same files, note record | not imported | 1 | — |
| `30-companies.json` | `company` entries (with `threat` from `threat_to_vnx.level`) and `org` entries from `github_accounts` | 38 companies; 21 GitHub account strings to parse | tier 1 for HIGH/MEDIUM threat |
| `30-companies.json` `patents_found` | `patent` entries | 53 | tier 1 if assignee threat HIGH |
| `40-datasets.json` `datasets` + `other_candidates_not_profiled` | `dataset` entries | 38 + 28 | tier by the gate's priority (1–2 → tier 1) |
| standards in `[30 §3]` | `standard` entries: Sector Zero v1.0, Sector One v1.0, Stability Evaluation Method v1.0, Codecs white paper v1.0, Technology Review v1.0, Swordfish DNA working draft, JPEG DNA ISO/IEC 25508-1, DDSA newsletter page | 8 (entered by hand with URLs from `[30 §3]`) | tier 1 |
| venues | `venue` entries entered by a person | ~8 | tier 2 |

The seed is idempotent: a second run with the same files changes nothing; a later gate updates `last_known` and adds
entries without deleting any (retirement is a separate, logged `status: retired`).

### 5.4 Event record (`research/events.jsonl`)

```json
{
  "event_id": "EV-20261011-003",
  "type": "NEW BENCHMARK",
  "rule": "R-BEN-1",
  "severity": "HIGH",
  "detected_at": "2026-10-11T06:05:12+05:30",
  "entity": "W-REPO-0042 | null",
  "identifiers": {"doi": null, "arxiv": "2610.01234", "github": null},
  "title": "…", "urls": ["…"],
  "evidence_label": "peer-reviewed | preprint | company claim | repository README | unverified",
  "status": "candidate | confirmed | rejected | duplicate | pending-founder",
  "triage": {"summary": "…", "category_hint": "…", "tier": 1, "untrusted": true},
  "verification": {"by": "vnx-weekly-intel run <id>", "opened_url": "…", "matched": {"title": true, "authors": true, "year": true},
                   "source_id": "S-2026-0xx"},
  "job_id": null,
  "telegram": {"sent": false, "message_id": null},
  "decided_by": "rule | weekly-intel | founder",
  "why": "…"
}
```

---

## 6. Who does what: script, local model, remote model, founder

| Step | Actor | Why |
|---|---|---|
| Collect (all sources), rate limiting, retries | script | deterministic, cheap, testable offline with recorded responses |
| De-duplicate, detect, assign severity | script | rules in §4.2; reproducible |
| Licence-change and page-hash diffs | script | exact comparisons |
| Short summary (≤ 2 sentences) and category hint per candidate | **local model** through LAYA (`vnxdna laya run --type log_summary` / `classification`, the task types already in `policy.yaml`), ≤ 60 items/day, 20 s timeout each | saves remote-model time; output is stored with `untrusted: true`, never sets `verified`, never confirms an event, never creates a job; if the local model is unavailable the digest shows titles only |
| Open each candidate, check title/authors/year, read the abstract or README, confirm or reject, write ledger lines | **remote model job** `vnx-weekly-intel` | needs judgement and reading; already scheduled and read-only on code |
| Map a confirmed NEW ALGORITHM or NEW BENCHMARK to VNX-DNA (vnx-scientific-research steps QUESTION … MAP TO VNX-DNA) | remote model job, monthly or on founder request | stops before DESIGN EXPERIMENT; implementation needs a founder-started session |
| Add tracking jobs within caps (§8) | remote model job `vnx-weekly-intel`; reviewed by `vnx-weekly-roadmap` | tracking only |
| Render digests, send Telegram | script | the message text is built from `events.jsonl`, not written freely by a model |

### 6.1 Founder approval points

1. Any new scheduled job or change to an existing job's command or prompt (R0–R2 installs).
2. The edit to `vnxco/intel.py` (another repository).
3. Any job above P4, and any job of type `coding`, created from an event.
4. Adding a tool to the benchmark participant list or changing its licence rule (`VNX_BENCHMARK_PLAN.md` §8, §10).
5. Raising an organisation to threat HIGH or adding a NEW COMPETITOR to tier 1.
6. Any use of intel in external material (investor dossier, deck, website) and any correction of existing material
   (the gate already lists corrections for the dossier `[30 §7]`).
7. Any contact with an external organisation.
8. Token or secret changes (GitHub PAT scope, an optional NCBI API key).
9. Dataset downloads above 5 GB triggered by a NEW DATASET event.

Events waiting for the founder have `status: pending-founder` and appear in every weekly digest until decided
(`vnx-watch decide … --by founder`).

---

## 7. Cadence and attachment to existing scheduled jobs

| When | Existing job | Change | Runs |
|---|---|---|---|
| daily 06:30 | **`vnx-daily-kb`** (command) | argv becomes `sh -lc "vnx-watch collect --daily --budget 10m; vnx-watch detect; vnx-watch triage --max 60; vnx-watch digest --daily; vnx-watch notify --daily; vnx-kb build"`. Each `vnx-watch` step logs its own failure and the index is still rebuilt (`;`, not `&&`) | daily collectors, detection, local-model triage, HIGH-only alert, then the existing index build |
| daily 06:45 | **`vnx-daily-health`** (command) | adds one section from `vnx-watch status --json`: last run per collector, consecutive failures, candidates older than 14 days | — |
| daily 07:30 | **`vnx-morning-brief`** (remote model job) | prompt gains one line: "read `research/digests/daily-$(date +%F).md` and list HIGH events under 'Decisions needed' if any; do not repeat MEDIUM/LOW" | — |
| Sunday 06:00 | **new** command job `vnx-weekly-watch` (founder approval) | `vnx-watch collect --weekly --budget 40m; vnx-watch detect; vnx-watch triage --max 120; vnx-watch digest --weekly --candidates-only` | patents, standards, datasets, org scans, GitHub search, Crossref, citation watch, conferences. Kept separate from `vnx-daily-kb` so a 40-minute network run does not delay the index or hide its failures. It ends before the 09:00 verification. Alternative if no new job is wanted: the same commands inside Sunday's `vnx-daily-kb` run, with that job's timeout raised to 60 min |
| Sunday 09:00 | **`vnx-weekly-intel`** (remote model job, read-only) | prompt starts from `research/digests/candidates-DATE.md` instead of searching from scratch: verify each candidate in severity order (cap 40), `vnx-watch decide` each one, append verified sources to `kb/sources.jsonl` with `event_ids`, add tracking jobs within the caps of §8, write `Intel/weekly-DATE.md`, and finish with `vnx-watch digest --weekly && vnx-watch notify --weekly`. Its existing free search for the week's papers stays as a recall check (§13, R3) | verification and weekly report |
| Monday 08:00 | **`vnx-weekly-roadmap`** (remote model job) | prompt gains: "review jobs tagged `[EV-…]` added since last Monday; merge duplicates; propose priority changes up to P4; list anything above P4 as a founder decision" | — |
| first Sunday of the month | inside `vnx-weekly-watch` | `--monthly`: re-check `negative` entries, licence re-scan of every benchmark participant and channel tool, retire watch entries with no activity for 12 months (logged, reversible) | — |

Heavy-window rule: no collector runs between 01:15 and 04:00 or on Saturday 04:00–05:00 (`WORKFLOWS.md`), and none runs
while a lab timing cell is running (`VNX_BENCHMARK_PLAN.md` §12).

---

## 8. From events to `vnx-jobs`

`vnx-jobs` stays tracking-only. Jobs are added with `vnx-jobs add "<description> [EV-<id>]" --type … --priority …
--version … --validation "…"`; the `[EV-…]` tag is the de-duplication key (`vnx-jobs list --all | grep EV-<id>` before
adding) and the job ID is written back with `vnx-watch decide <EV-id> … --job <N>`.

| Confirmed event | Job type | Default priority | Validation text (example) |
|---|---|---|---|
| NEW PAPER (MEDIUM) | `research` | P5 | "source in kb/sources.jsonl verified; one paragraph on relevance to VNX-DNA in Intel/weekly-DATE.md" |
| NEW ALGORITHM | `research` | P4 | "method summarised with locator; mapped to the VNX-DNA component it would affect; THEORETICAL label; experiment proposal or 'not applicable' with reason" |
| NEW BENCHMARK including a participant codec | `benchmark` | P4, depends on the B1 job | "protocol documented; decision whether to add a `protocol: true` record to benchmarks/competitors/records.json (reviewed)" |
| NEW REPOSITORY, runnable codec, OSI licence | `benchmark` | P5 | "licence read in full; adapter feasibility noted; founder decision on participant list" |
| R-REPO-2 licence change of a participant | `benchmark` | P4 | "new licence read; VNX_BENCHMARK_PLAN §8/§10 updated or unchanged with reason" |
| NEW PATENT (HIGH) | `competitive-intelligence` | P4 | "claims summary from the patent page; flagged for IP counsel; no novelty statement made" |
| NEW COMMERCIAL PRODUCT | `competitive-intelligence` | P4 | "primary source opened; competitor note updated with date and link" |
| NEW STANDARD | `scientific-review` | P4 | "document opened; impact on archive format / Sector Zero-One interoperability noted" |
| NEW DATASET (MEDIUM) | `research` | P5, depends on job #19 (R-03) | "licence and reference FASTA availability checked; added to datasets registry or rejected with reason" |
| NEW COMPETITOR | `competitive-intelligence` | P5 | "organisation profiled with sources; threat level proposed (founder decides)" |

Caps: at most 5 new jobs per week from events, none above P4 and none of type `coding` without the founder; events over
the cap stay `confirmed` with `job_id: null` and are listed in the weekly digest as "not queued (cap)".

---

## 9. Data flow example

1. Sunday 06:00 `vnx-weekly-watch` finds a new commit on `fml-ethz/dt4dds-benchmark` adding a codec wrapper
   → R-BEN-1 → candidate `EV-…`, severity HIGH (harness of the benchmark plan).
2. The local model adds a two-sentence summary (UNTRUSTED).
3. Sunday 09:00 `vnx-weekly-intel` opens the commit and the README, confirms, appends a source line, adds job
   "Review dt4dds-benchmark change … [EV-…]" type `benchmark` P4, and runs the weekly digest and notify.
4. Monday 08:00 `vnx-weekly-roadmap` checks the job against the backlog.
5. A founder-started session does the work; the event is never acted on automatically.

---

## 10. Telegram reporting

Sent by `vnx-watch notify` with
`openclaw message send --channel telegram --target <chat-id> --message "<text>"` (`--dry-run` in tests). The message
ID is stored in the event record. Text is rendered from `events.jsonl`; limit 3 500 characters (Telegram's limit is
4 096); overflow is cut at a line boundary with "… N more in Intel/weekly-DATE.md".

Weekly (end of `vnx-weekly-intel`):

```
VNX research watch — week ending 2026-10-11
Events: 4 confirmed · 9 rejected · 2 pending · 1 waiting for you
HIGH
• NEW BENCHMARK — fml-ethz/dt4dds-benchmark: new codec wrapper (repository) <url> → job #61 P4
MEDIUM
• NEW PAPER — "<title>" (preprint, arXiv 2610.01234) <url>
• NEW PATENT — <number> "<title>" (<assignee>) <url>
WAITING FOR YOU
• EV-20261004-002 NEW COMPETITOR — <organisation>: raise to tier 1?
Sources failing: SNIA pages (HTTP 403, checked via archive snapshot)
Ledger: +6 verified sources (total 58) · Jobs added: 2 (cap 5)
Report: VNX-Vault/Intel/weekly-2026-10-11.md
```

Daily (end of the `vnx-daily-kb` run): sent **only if** there is at least one HIGH candidate; one message per day:

```
VNX research watch — HIGH candidate(s), 2026-10-06 (not yet verified)
• R-REPO-2 licence change — umr-ds/NOREC4DNA: AGPL-3.0 → <new> <url>
Verification: Sunday 09:00 run, or ask for it now.
```

Every line carries the event type, the evidence label or "not yet verified", and a real link. No line states a number
that is not in a verified source.

---

## 11. Audit log

`research/audit.log`, JSON lines, append-only:

```json
{"ts": "2026-10-11T09:14:03+05:30", "actor": "vnx-weekly-intel run <id> | vnx-watch | founder",
 "action": "decide | seed | collect | detect | notify | job-add | retire",
 "target": "EV-20261011-003", "before": "candidate", "after": "confirmed",
 "detail": {"source_id": "S-2026-031", "job_id": 61}, "prev_sha256": "<hash of previous line>"}
```

- Every write by `vnx-watch` and every `decide` appends a line; `vnx-watch verify-log` checks the chain; a broken chain
  is a health-report FAIL.
- Scheduler run history (`openclaw cron runs --id <job>`) is the second record for scheduled jobs.
- The weekly digest lists the audit-log line count and the chain status.

---

## 12. Failure recovery

| Failure | Handling |
|---|---|
| One source fails (network, 5xx, parse error) | isolated per collector; the others continue; failure appended to `state.sqlite.failures`; the collector's cursor advances **only on success**, so the missed window is fetched on the next run |
| HTTP 429 / rate limit | back-off 6 s / 30 s / 90 s (the pattern already in `intel.gdelt`), then record failure |
| Source blocks automated access (403, e.g. SNIA) | marked `manual` in the watch list; archive-snapshot fallback; listed in the weekly digest under "Sources failing" |
| Same source fails 3 runs in a row | `vnx-daily-health` reports WARN; weekly digest lists it |
| Unofficial endpoint changes format (Google Patents) | parser failure → failure record, no events; R1 test fixtures detect format drift; a person updates the parser |
| Local model unavailable or slow | triage skipped for that run; digest shows titles only |
| `vnx-weekly-intel` fails or times out | candidates stay `candidate`; next week verifies them first; candidates older than 14 days are flagged in health and digest. Long verification is split into steps under the scheduler's no-output limits (hardening item H1.10) |
| Corrupted JSONL line | every line is schema-validated on write; a reader skips and quarantines an invalid line into `research/quarantine/` and reports it |
| `state.sqlite` damaged | it holds only operational state; rebuild from `events.jsonl`, the watch list and a re-fetch window of 30 days. `vnx-weekly-watch` copies `research/` to `/root/backups/vnx-research/` each week (newest 8 kept); the existing scheduler backup does not cover `/root/vnx-dna-ai` |
| Telegram send fails | retried once; the digest file remains the record; the failure is shown in the next digest |
| Duplicate job risk on re-run | `[EV-…]` tag check before `vnx-jobs add` |

No failure is hidden: a digest always states which collectors did not run.

---

## 13. Resource limits

| Resource | Limit |
|---|---|
| Wall time | daily collection ≤ 10 min; weekly ≤ 40 min (`--budget`; collectors stop cleanly and resume next run) |
| CPU / memory | every `vnx-watch` command runs under `vnxdna.slice` with MemoryMax 1 GiB and CPUQuota 100 % |
| Network politeness | arXiv ≤ 1 req / 3 s; GitHub authenticated ≤ 500 req/day; NCBI ≤ 1 req/s; Google Patents ≤ 1 req / 10 s and ≤ 50 queries/week; GDELT ≤ 1 req / 5 s (existing); a `User-Agent` naming the project |
| Disk | observation payloads kept 90 days, then compressed; `research/` capped at 200 MB with a health WARN above 150 MB |
| Local model | ≤ 60 items daily, ≤ 120 weekly, 20 s timeout each; only if LAYA reports free RAM for tier 1; never in the 01:15–04:00 window |
| Remote model | only the existing weekly jobs (and an optional monthly mapping run); ≤ 40 candidates verified per week in severity order; the rest carried over |
| Jobs | ≤ 5 per week from events (§8) |

---

## 14. Implementation plan

Each stage is one `vnx-jobs` entry (type `research`, P4 unless the founder raises it), built in its own `vnx-task`
worktree of the workforce repository, reviewed, and activated only after the founder approves the scheduler change.

### R0 — watch list, seed, daily collectors (no model)

Scope: `vnx-watch seed`, `collect --daily` (intel.py call + arXiv + bioRxiv + GitHub watched repos), `detect` for
NEW PAPER and NEW REPOSITORY, `status`, `audit.log`; `intel.py` reads companies from the watch list.

Acceptance tests:

1. Seed counts equal the counts computed from the gate files: 179 repos, 8 negative, 38 companies, 53 patents,
   38 + 28 datasets; the seed manifest records each file's SHA-256; a second seed run changes nothing.
2. Every watch-list and event line validates against its JSON schema.
3. Offline tests with recorded HTTP responses for each collector: a planted new arXiv entry and a planted new repo each
   produce exactly one candidate of the right type; a known item produces none.
4. Failure injection: one collector raising leaves the others complete and its cursor unchanged.
5. Measured daily run ≤ 10 min; `vnx-daily-kb` still ends `ok` with the index rebuilt.
6. `vnx-watch verify-log` passes after the test run.

Effort: 3–4 sessions.

### R1 — all event types, weekly collectors, triage, Telegram

Scope: patents, standards, datasets, orgs, GitHub search, Crossref, citation watch, conferences; rules R-COMP … R-DS;
local-model triage; `digest`; `notify`; the new `vnx-weekly-watch` job.

Acceptance tests:

1. One positive and one negative fixture per rule ID; all pass.
2. Triage with the LAYA router stopped: pipeline completes, digest has titles only, no exception.
3. Triage output always carries `untrusted: true`; a test asserts that no code path sets `verified` or `status:
   confirmed` from triage output.
4. `notify --dry-run` renders under 3 500 characters for a 50-event fixture; one real weekly message is delivered and
   its message ID recorded.
5. Rate-limiter tests: request spacing per host meets §13.

Effort: 5–7 sessions.

### R2 — verification loop and jobs

Scope: prompt changes to `vnx-weekly-intel`, `vnx-morning-brief`, `vnx-weekly-roadmap`; health section in
`vnx-daily-health`; job creation with caps and tags; ledger `event_ids`.

Acceptance tests (one full Sunday cycle, then a re-run):

1. Every candidate ends `confirmed`, `rejected`, `duplicate` or `pending-founder`, each with a `why`.
2. Every `confirmed` event has a `kb/sources.jsonl` line with `verified: true`, URL, access date, and the event ID.
3. Jobs added ≤ 5, none above P4, none of type `coding`; a re-run adds none (tag check).
4. `vnx-claims check` on `Intel/weekly-DATE.md` passes; no number in it lacks a verified source.
5. Audit chain verifies; scheduler run history shows `ok` for all four jobs touched.

Effort: 2–3 sessions plus one observed weekly cycle.

### R3 — operation and measurement (4 weeks)

Scope: run unchanged for 4 weeks; measure; tune thresholds through reviewed changes only.

Acceptance tests:

1. 4 weekly digests delivered (message IDs recorded) and 4 weekly reports written.
2. Precision per event type reported (confirmed / all candidates); rules with precision below 10 % are reviewed.
3. Recall check: each week the weekly job's free search (kept from today's prompt) lists items it found that the
   collectors missed; misses are reported and either added as rules or explained.
4. Drills run and documented: network off, one 403 source, local model down, corrupted `state.sqlite` rebuilt,
   scheduler job failure.
5. Hardening item H5.3 progress reported (verified sources in the ledger; target ≥ 50).

Effort: 1 session per week of review.

---

## 15. Open questions for the founder

| # | Question |
|---|---|
| Q1 | Approve the new Sunday 06:00 command job `vnx-weekly-watch`, or run the weekly collectors inside Sunday's `vnx-daily-kb` (§7)? |
| Q2 | Approve the one-file change in `vnxco/intel.py` (company list from the watch list)? |
| Q3 | Daily Telegram alert for HIGH candidates before verification, or weekly digest only? |
| Q4 | Job caps: 5 per week, none above P4 — acceptable? |
| Q5 | Use the unofficial Google Patents endpoint weekly (≤ 50 queries), or limit patents to a monthly manual check? |
| Q6 | Optional NCBI API key for dataset searches (stored as a secret, not in plain configuration)? |
