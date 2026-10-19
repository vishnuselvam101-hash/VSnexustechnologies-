# V15 finalization plan

**Status: FINAL (M0 2026-10-19; see V15_M0_DECISIONS.md) (V14 phase P13, 2026-10-18; revised the same day with the lead's additions: core codec track,
frontier track, long-term-readability objective, competitive position and IP).** Written from `build/v14` a32cbf5 before the P12 acceptance review.
P12 may add items (every criterion it marks FAIL or INCOMPLETE becomes a V15 item or an explicit, documented
non-goal). The V14 lead finalizes this plan after P12. Nothing in it is implemented, and nothing was run or measured
to write it. Every effort figure is an estimate.

Sources: master directive §23 (`docs/V14_MASTER_DIRECTIVE_2026-10-18.md`), brief §12 (`docs/V14_BRIEF_2026-10-18.md`),
master plan row P13 (`docs/V14_MASTER_PLAN.md`), `docs/V14_STATE.md` ("Inputs for P13"), the V14 phase reports and
their open items, `docs/V14_HARDENING.md` (open items O-1 to O-11), `docs/V14_SCHEDULER.md` (branch `work/v14-p6c`, not
yet merged at a32cbf5), `experiments/v14/bench/p6e-channels/README.md`, `docs/VNX_BENCHMARK_PLAN.md`, and the literature
in §13.

## 0. What V15 is

V15 is the **final planned major version**. Its headline objective is long-term readability (§1): maximising the
chance that a future reader can recover the data. It finishes the core codec (§3), adds only the frontier items the
founder keeps (§5, Q10), freezes the formats and the APIs,
removes the known format-level weaknesses in one batch, and produces evidence: reproducible builds, compatibility and
migration, corruption/fuzz/crash/fault validation, a fair comparison with published DNA-storage codes, independent
reproduction where someone outside the project agrees to do it, and a final statement that separates software evidence
from physical-DNA evidence. After V15 only maintenance releases are planned. Maintenance fixes may still be needed;
this plan does not promise that they will not be (brief §12).

### 0.1 Evidence ladder and evidence categories

Every item names the highest rung its method can reach. No V15 document may claim more.

| rung | meaning here |
|---|---|
| THEORETICAL | specified, derived or proven on paper; nothing run |
| SIMULATED | run against the channel simulator or a software medium; outcomes are simulator outcomes |
| MEASURED | timed or observed on a named host from a committed results file (speed, memory, device behaviour) |
| REPRODUCED | the same result regenerated from the committed commands on a second host or by a second person, within the stated intervals |
| EXTERNALLY VALIDATED | reproduced or reviewed by a party outside VSNexus, who says so in writing |

Evidence categories (directive §23): A software codec; B software storage device; C electronic-backend persistence;
D simulated DNA channel; E actual synthesis/preservation/retrieval/sequencing; F complete physical device. Passing one
never implies a later one. **V15 as planned claims A–D only**, except for the one pool of CC-5 step 5 if the founder
funds it (category E for that pool only). F needs hardware and work the project does not have (§9, Q3, Q4). A channel model calibrated on published wet-lab data is still category D
and still SIMULATED.

### 0.2 Item template

Tracks: §1 long-term readability (headline, release acceptance), §2 format/API freeze, §3 CORE CODEC, §4 supporting
research, §5 FRONTIER, §5A archive platform, §6 platform, §7 evidence/release, §8 competitive position and IP.

Each item below has: **Goal**, **Method**, **Evidence target**, **Acceptance**, **Risks**, **Starts from** (V14 file
or module), **Depends on**. IDs: `LR` long-term readability, `FZ` format/API freeze, `CC` core codec, `RS` supporting research, `F` frontier, `AP` archive platform,
`PL` platform, `QA` evidence and release.

## 1. Long-term readability track (headline objective and release acceptance)

**Objective.** The founder's end goal for V15, the final version, is software and archives that can still be read
"after 100 years". This plan phrases it the only way the project's evidence rules allow: **V15 maximises the chance
that a future reader, without VNX software, VSNexus or any of today's tools, can recover the data.** No lifetime is
guaranteed or claimed. Nothing about DNA longevity is measured by this project (§0.1); CC-5's order, if funded, would
measure decoding of one fresh pool, not longevity.

Design principles, taken from long-lived format practice: a format is more likely to stay readable when it is
openly disclosed, widely adopted, transparent (readable with simple tools), self-documenting, free of external
dependencies, unencumbered by patents and free of technical protection mechanisms (the seven sustainability factors of
the Library of Congress); when an archival profile restricts a general format to features that are safe to preserve
(PDF/A, ISO 19005); and when every later revision must keep reading every earlier file (the FITS practice, "once FITS,
always FITS"; the FITS 4.0 standard is maintained by the IAU FITS Working Group). The tension between "open" and the
company's advantage is addressed in §8.

### LR-1 Permanent format freeze: normative spec, golden archives, compatibility rule

- **Goal.** One exact, normative specification of every byte and base V15 writes, golden archives that pin it, and a
  rule binding every later version.
- **Method.** Consolidate `docs/spec/VNX-DNA-SPEC-V6.md`, the VNX4 spec, `docs/V14_CONTRACTS.md` and the FZ items into
  one V15 specification with RFC 2119 keywords, test vectors inline, and no reference to source code as the definition.
  Golden archives (FZ-4) for every frozen profile, each with its strands, a simulated read set, the expected bytes and
  SHA-256. **Compatibility rule** (normative, in the spec): every later VNX-DNA version MUST decode every V15 archive to
  the same bytes; extensions only through the declared `required_features`/extension fields; a reader MUST refuse,
  never ignore, a required feature it does not know; a V15 reader is archived with every later release (QA-6).
  The freeze happens once (FZ-1..FZ-4) and is the last breaking change.
- **Evidence target.** THEORETICAL (the spec) + MEASURED (golden tests pass in CI).
- **Acceptance.** Every golden archive decodes bit-exactly with the production decoder **and** the LR-3 reference
  decoder; a spec review finds no behaviour defined only by code; the compatibility rule is in the spec and in
  `docs/RELEASE_PROCESS.md`.
- **Risks.** Freezing a weakness forever (mitigation: CC-2/CC-4/RS-3 run before the freeze, M1 before M2).
- **Starts from.** `docs/spec/`, `docs/V14_CONTRACTS.md`, `docs/INTEROPERABILITY.md`, FZ-1..FZ-4.
- **Depends on.** FZ-1..FZ-4; CC-4 and CC-5 decisions that change the format (frame 6, Q1).

### LR-2 (= F1) Self-describing bootstrap layer, aligned with DNA Data Storage Sector Zero

- **Goal.** A pool carries what a reader needs to start decoding, so decoding does not depend on any surviving
  software or company.
- **Method.** (1) Implement the SNIA **DNA Data Storage Sector Zero** v1.0 (2023, DNA Data Storage Alliance / SNIA
  DARS working group: basic vendor and codec information at a known place, leading to Sector One archive metadata)
  and **Sector One** for VNX pools, per the open mapping in `docs/DDSA_MAPPING.md` (today: "VNX-DNA 6.x produces no
  Sector Zero or Sector One bytes or bases"). Vendor and codec IDs need a DDSA allocation (Q9). For encrypted archives,
  Sector One carries no names, sizes or hashes (`docs/DDSA_MAPPING.md`). (2) A "bootstrap" strand set: the LR-1
  specification itself (compressed text, or a pointer plus a short decoding primer if size forbids) encoded with the
  simplest frozen profile and heavy outer redundancy, addressable by its own primer pair (CC-5) or Sector Zero entry.
  Its cost in strands is reported and is optional per archive.
- **What is new vs. the state of the art (modest).** Sector Zero/One are existing standards; VNX-DNA would be one more
  implementation. We did not find a published system that stores its own decoding specification in the same pool
  (search on 2026-10-18; this is not a novelty claim).
- **Evidence target.** SIMULATED (bootstrap strands survive the named channels and decode); THEORETICAL for the
  standards mapping.
- **Acceptance.** A reader given only the Sector Zero bases and the published SNIA specs can identify the codec and find
  Sector One; the embedded spec decodes on every named channel at its profile's threshold; DDSA mapping table rows all
  "aligned" or explained.
- **Risks.** SNIA spec licence terms (internal business use; reproduction rules) and allocation process; the embedded
  spec costs pool space. Physical behaviour unmeasured.
- **Starts from.** `docs/DDSA_MAPPING.md`, `docs/spec/VNX-DNA-SPEC-V6.md` §8.1, `src/vnxdna/dnaenc/superblock.py`,
  `src/vnxdna/providers/` (export package).
- **Depends on.** LR-1; Q9 (DDSA membership/allocation). Lab/money: no for the software; yes for physical evidence.

### LR-3 Minimal, dependency-free reference decoder

- **Goal.** A decoder short enough to read and re-implement, with no third-party dependencies, so the spec can be
  checked against running code that a future reader can also port.
- **Method.** One self-contained source (plain C99 or Python standard library only; the language is chosen at M0),
  favouring clarity over speed: read parsing, inner RS, consensus, outer code, container, verification. Cryptographic
  primitives for encrypted archives are named by their standards (AES-GCM, NIST SP 800-38D; scrypt, RFC 7914) and
  either included as small reference implementations or documented as the only external requirement.
  Differential tests against the production decoder on every golden archive and on a simulated read corpus.
- **Evidence target.** MEASURED (equivalence); EXTERNALLY VALIDATED through LR-5.
- **Acceptance.** Bit-identical output (or the same typed refusal) to the production decoder on the whole corpus; no
  imports outside the language's standard library; documented line count and runtime; never returns bytes whose
  SHA-256 does not match.
- **Risks.** It is slow on large pools (acceptable; documented); a second implementation can drift (golden tests).
- **Starts from.** `src/vnxdna/v4/` and `src/vnxdna/recovery/` (Python reference paths), `src/vnxdna/ecc/`,
  `src/vnxdna/container/`.
- **Depends on.** LR-1.

### LR-4 Physical preservation guidance

- **Goal.** Sourced guidance for whoever stores VNX pools: storage conditions, redundancy and geographic copies,
  periodic test reads, documentation kept with the samples.
- **Method.** A literature-based guide with the bioinformatics specialist: DNA decays mainly by hydrolysis and
  oxidation, and dry, cold, oxygen-excluded, encapsulated storage slows it (Matange, Tuck & Keung 2021 review). Silica
  encapsulation (Grass et al. 2015): its multi-century-to-million-year figures are an **extrapolation** from accelerated
  ageing at 60–70 °C over weeks via Arrhenius kinetics, **not a measurement** of long storage; the guide says so.
  Recommendations are stated as practices (several independent aliquots, at least two sites, a written read-test
  schedule that spends a stated aliquot budget, Sector Zero/LR-2 bootstrap in every copy, paper/stable-medium copy of the
  spec stored with the samples), never as a lifetime.
- **What is new (modest).** Nothing new in preservation science; the contribution is tying the guide to the codec's
  redundancy settings via RS-3's decay model (SIMULATED).
- **Evidence target.** THEORETICAL (literature) + SIMULATED (decay-model sweeps). Any VNX-specific longevity statement
  needs category E work.
- **Acceptance.** Every statement cited; no lifetime promised; claims audit clean.
- **Starts from.** RS-3 decay model; `docs/LAB_INTERFACE.md`; QA-5 protocol.
- **Depends on.** RS-3. Lab/money: none for the guide.

### LR-5 External validation: decode from the spec alone

- **Goal.** An independent party, given only the V15 spec, golden strand files and simulated read sets (and, if CC-5
  happens, real reads), writes its own decoder and recovers the bytes.
- **Method.** Clean-room protocol: the party receives no VNX code; they report every ambiguity in the spec; the spec is
  fixed and the exercise repeated until it passes. Internal dry run first: a team member or agent that has not seen the
  code (REPRODUCED-level only).
- **Evidence target.** EXTERNALLY VALIDATED if an outside party completes it and says so in writing; otherwise reported
  as pending.
- **Acceptance.** SHA-256 match on the assigned archives; the ambiguity log is closed.
- **Risks.** Finding a willing party (researchers already in contact are candidates; founder's call, Q6); time.
- **Depends on.** LR-1, LR-3 (for the dry run).

### LR-6 Standards alignment (DNA Data Storage Alliance)

- **Goal.** VNX archives follow the DDSA/SNIA specifications that exist at the freeze, and the project tracks new ones.
- **Method.** Implement LR-2; compare the V15 spec with each published DDSA specification and record conformance per
  clause; join the SNIA DNA Data Storage technical work if the founder approves the membership cost (Q9).
- **Evidence target.** THEORETICAL (conformance table); MEASURED where a conformance test exists.
- **Acceptance.** Conformance table complete; deviations justified.
- **Starts from.** `docs/DDSA_MAPPING.md`, `docs/INTEROPERABILITY.md`.

### Release acceptance for V15 (headline)

V15.0.0 is accepted for release only when: LR-1 done (spec + golden archives + compatibility rule); LR-2 implemented
(or explicitly waived by the founder); LR-3 bit-exact on the whole golden corpus; LR-4 published; LR-5 internal dry
run passed and the external run reported (done, pending or declined; Q6 decides whether the release waits); LR-6 table
complete; plus the QA gates (§7). The release notes state the result as "maximises the chance of future recovery",
with the evidence rungs, and no lifetime.

## 2. Format and API freeze (one breaking batch)

The rule: every breaking change V15 needs is collected here and lands **once**, behind new format/contract version
numbers, with V14 data still readable (`vnx-dna-format-compat` rules: versioned formats, golden fixtures,
cross-version decode tests). After the freeze, a format change is a new major version, which this plan does not
foresee.

### FZ-1 Volume journal integrity: keyed MAC (hardening O-1)

- **Goal.** A party with write access to an encrypted volume cannot reorder, replay or drop commit records without
  the open refusing the volume.
- **Method.** Journal format v2 (Python `volume/journal.py` and Rust `vnxd` `journal.rs`/`record.rs`; one spec, both
  implementations). Each commit record carries an HMAC-SHA-256 (RFC 2104) over the record bytes **and the previous
  record's MAC** (a hash chain, so dropping or reordering a middle record breaks the chain). The MAC key is derived
  from the volume key with HKDF (RFC 5869) under a distinct label, never the data key itself. In addition, the extent
  number and version are bound into the sealed extent as AES-GCM associated data, so a swapped extent file fails
  authentication even if the journal is replaced wholesale. The CRC-32 stays as the cheap torn-write detector. The
  chain cannot detect the loss of the **newest** records (rollback of the tail) on its own; V15 decides between
  (a) documenting it as a limit, and (b) an anchored commit counter kept outside the volume directory (key store or
  the `vnxd` state file), which costs one extra durable write per commit. Unencrypted volumes keep CRC-only integrity
  unless the founder chooses a keyed mode for them too (Q2).
- **Evidence target.** THEORETICAL (the security argument) + SIMULATED/MEASURED (tests and the commit-latency cost,
  category B on C).
- **Acceptance.** Swap, replay, drop-middle, truncate and cross-volume-splice attacks are refused in both
  implementations (tests mirror the 6F O-1 description); a V14 volume opens read-only and migrates (FZ-4); the
  every-offset bit-flip and cut tests of `docs/V14_VOLUME_RELIABILITY.md` still pass; commit latency regression is
  reported with a CI against V14.
- **Risks.** Key handling for volumes opened without a key (scrub, stat) must not weaken; two implementations of one
  MAC format can drift (mitigation: shared golden journals).
- **Starts from.** `src/vnxdna/volume/journal.py`, `src/vnxdna/volume/volume.py`, `src/vnxdna/native/rust/vnxd/src/journal.rs`,
  `record.rs`, `seal.rs`; `docs/V14_HARDENING.md` O-1; `docs/V14_VOLUME_RELIABILITY.md`.
- **Depends on.** Q2 (founder: keyed mode for unencrypted volumes); FZ-4.

### FZ-2 Media contract v2 (versioned additions)

- **Goal.** Remove the P7 limits that need a contract change, as one versioned addition; v1 adapters keep working.
- **Method.** `vnx.media/2` adds optional capabilities negotiated through `info()`: (1) `stored_digest(at)` returning
  the SHA-256 recorded at commit, so an ambiguous put retry no longer needs a full simulated sequencing and decode
  (P7 report, `docs/V14_MEDIA_ADAPTERS.md` §6); (2) a persistent job ledger for lab adapters (`list_jobs`,
  `adopt`/`destroy` at start) for unowned-job reconciliation (§6 "Unowned lab jobs"; `vnx.dna-lab/1` becomes `/2`);
  (3) primer selection (`primer_selection: true`) once CC-5 lands in the simulator. A v1 adapter reports none of these,
  and callers keep the v1 path. The digest is an **unverified claim** from the medium: `ResilientMedia` may use it only
  to decide "retry vs. done", and every read still verifies the decoded SHA-256 (`codec::verify_decoded`).
- **Evidence target.** SIMULATED (DNA adapters) and MEASURED (cost of an ambiguous retry, before/after).
- **Acceptance.** The v1 conformance suite passes unchanged against every adapter; a v2 conformance suite covers the
  new calls; a lying `stored_digest` never yields wrong data (fault test); lab-sim restart with outstanding jobs
  reconciles every job (the simulator gains a mode where jobs advance without polling).
- **Risks.** A real lab may not offer a digest or a job list; the capability stays optional.
- **Starts from.** `src/vnxdna/native/rust/vnxd/src/media.rs` (`MediaAdapter`, `conformance`), `lab.rs`, `dnasim.rs`,
  `fallback.rs`, `src/vnxdna/device/contracts.py`; `docs/V14_MEDIA_ADAPTERS.md` §5–§6; P7 report review table.
- **Depends on.** CC-5 for item (3) only.

### FZ-3 Other breaking changes batched with the freeze (inventory)

Collected so far; P12 may add more. Each is accepted or rejected explicitly at the freeze review.

| change | why | starts from |
|---|---|---|
| strand frame 6 + optional primer flanks (superblock 3) | molecular random access within the 350-nt vendor limit needs short strands with primers; frame 4 cannot carry them (spec §3.5–§3.7). Only if the founder chooses molecular random access (Q1) | `docs/spec/VNX-DNA-SPEC-V6.md` §3.5–§3.8; `src/vnxdna/dnaenc/layout.py`, `frame4.py`; `src/vnxdna/providers/base.py` `PrimerPair` |
| version strings and metadata consistency | release `v10.0.0` shipped `6.0.0.dev0`; V14 still reports `13.0.0` | `src/vnxdna/_version.py`, `CITATION.cff`, CLI |
| KDF/cipher parameters frozen and recorded in the container header (scrypt cost, `cryptography` floor) | open founder decisions since V11 and P4 (pin `>=44,<47` vs advisories fixed in 48.0.1) | `docs/V14_PHASE4_REPORT.md`, `src/vnxdna/secure/` |
| device/SDK/HTTP API surface marked stable (names, error codes, JSON schemas) | API freeze (brief §12) | `docs/V14_CONTRACTS.md`, `src/vnxdna/core/schemas/`, `src/vnxdna/sdk/` |
| SDK/CLI volume moved onto the V14 block volume (P8 Stage B note) | one volume format, not two | `docs/V14_DEVICE_INTERFACE.md` §12, P9 outcome |

Not format changes, fixed in the same milestone because they touch the same code: O-2 (ctypes width checks, pattern
`engine._checked_arrays`), O-3 (hash the input once, decode from the hashed bytes or re-check after decode).

### FZ-4 Compatibility and migration evidence

- **Goal.** Every archive, volume and strand file written by a released version (V4 to V14) decodes under V15, or is
  refused with a typed error; V14 volumes migrate to journal v2.
- **Method.** Golden fixtures per released format version (container, superblocks 1–3, frame 4/6, `.vxs`, `.vxidx`,
  stream manifests, volumes, journals) committed with hashes; a cross-version matrix (writer version x reader version)
  run in CI; `vnx volume migrate` (copy-on-migrate, never in place; the old volume stays until verified).
- **Evidence target.** MEASURED (pass/fail matrix, category A–C), REPRODUCED in QA-1's second build.
- **Acceptance.** Matrix 100 % pass or typed refusal as specified; migration interrupted at every step leaves the old
  volume openable (crash test); no fixture regenerated silently (hash check).
- **Risks.** Old fixtures may not exist for every release; regenerate from tagged builds only.
- **Starts from.** existing golden/compat tests under `tests/` (V4/V5 archives), `src/vnxdna/volume/`, `vnxd` `volume.rs`.
- **Depends on.** FZ-1, FZ-2, FZ-3 decided.

## 3. CORE CODEC track (ordered)

The lead set this order on 2026-10-18 (the founder asked to "evolve the software to the core" and delegated the
order). Items run in this order where they compete for the same people or host; CC-1 and the CC-2 harness can overlap.

A note on real data, stated once for the whole track: the public DNA-storage sequencing datasets encode **other
codecs' data**. They cannot validate VNX-DNA decoding; they only provide error models, and that use was made in V7/V8
(`docs/DNA_STORAGE_DATASET_REGISTRY.md`). A VNX-DNA decode of real reads needs VNX-DNA strands synthesised and
sequenced, which is CC-5's wet-lab order. Until then every CC result is SIMULATED (recovery) or MEASURED (speed, memory
on a named host).

### CC-1 Decode speed: remove the serial-stage bottleneck

- **Goal.** Reach the V14 targets that remain open (brief §10): 4 MiB indel decode at 1 worker ≥ 5x the V12 baseline,
  clean decode at 4 workers ≥ 3x, each with the CI lower bound clearing the tier. P6 workstreams B and C reduced CPU
  time, but wall time stayed bounded by the serial stages (P6B/P6C results on `work/v14-p6b` / `work/v14-p6c`; numbers
  in their reports, not repeated here).
- **Method.** Profile first (py-spy flamegraph + the per-stage counters, `vnx-dna-profiling`), on the V12 baseline
  (`build/v12` 6bf7749) and the V14 head, same host, governed. Rank the serial stages by wall share (Amdahl: the
  achievable speedup is bounded by 1 / serial share, so the bound is computed before any work). Then, per stage in
  rank order: algorithmic fix first (avoid repeated whole-pool passes, pipeline stages so pass 1 overlaps I/O and
  pass 2, parallelise per-extent/per-bucket work that is independent by construction), native code only where the
  profile shows interpreter overhead dominates, and every native change with bit-exact equivalence to the Python
  reference, golden hashes and sanitizer runs (`vnx-dna-native-safety`). Each change is a BASELINE → CHANGE →
  BENCHMARK → VERIFY → COMPARE step (`vnx-benchmarking`); a change that does not clear its A/A band is reverted.
- **Evidence target.** MEASURED (one host, CIs), REPRODUCED in QA-3 on a second host.
- **Acceptance.** Byte-identical outputs and reports to the previous head on the full benchmark suite and the channel
  campaign; 0 false successes; the tier is claimed only when the CI lower bound clears it; if a target is missed, the
  remaining serial share is quantified and reported (brief §10 F).
- **Risks.** The serial share may be inherent (e.g. ordered output, superblock discovery); then the honest result is a
  documented ceiling. Native code widens the audited surface.
- **Starts from.** `src/vnxdna/pipeline/decode.py`, `src/vnxdna/pipeline/stream.py`, `src/vnxdna/recovery/outer.py`,
  `recovery/pass1.py`, `recovery/engine.py`, `recovery/spill.py`, the Rust/C engine under `src/vnxdna/native/`;
  `experiments/v14/bench/p6a-pool/`, `experiments/v14/bench/p6e-channels/`; P6B/P6C reports.
- **Depends on.** P6B/P6C merged; RS-4 shares the pass-1 code (coordinate, do not run both on the host at once).

### CC-2 Indel benchmark against HEDGES-class and marker/watermark codes, same channel

- **Goal.** A fair, preregistered answer to "how does VNX-DNA's indel path compare with published indel-correcting
  codes?" on the **same** simulated channel: rate at equal recovery, overhead (nt per input byte, including side
  information), decode time and memory. No "indel-immune" or "better than" wording unless the measured intervals say so.
- **Method.**
  1. Participants: VNX-DNA in its three indel modes (markers → erasures, multi-read alignment, single-read
     realignment); HEDGES (Press et al. 2020) through `whpress/hedges` as a separate process, internal measurement
     only (licence note in `docs/VNX_BENCHMARK_PLAN.md` §8); DNA-Aeon (Welzel et al. 2023); DNA-RS (Heckel/Grass)
     as the no-indel-code baseline; a marker code with an LDPC/RS outer code (Ratzer 2005; Davey & MacKay 2001 for the
     watermark variant; multi-read decoding per Maarouf et al. 2023). No maintained public marker-code codec was found,
     so the marker code is **our own reference implementation**: it is validated first by reproducing one published
     curve within its stated tolerance; until then its numbers are reported as "implementation not validated" and not
     compared.
  2. Channel: the VNX channel models plus an i.i.d. IDS sweep (insertion, deletion, substitution grids of
     `docs/VNX_BENCHMARK_PLAN.md` §6.8, incl. the ETH composition 53 % sub / 45 % del / 2 % ins) at fixed coverage, and
     the dt4dds digital twin (Gimpel et al. 2023) as an external channel run as a separate process.
  3. Metrics: threshold at 95 % success (logistic fit, ≥ 30 trials per point, bootstrap CI), `FALSE_SUCCESS` next to
     every threshold, nt/byte, decode s/MB and peak RSS on one governed host.
  4. The full ETH codec-benchmark protocol (Gimpel et al. 2026) re-run with VNX-DNA as an added codec, to obtain a
     DIRECTLY COMPARABLE label against its six published codecs.
- **Evidence target.** SIMULATED (recovery) + MEASURED (speed/memory on the named host). REPRODUCED if QA-3 re-runs it.
- **Acceptance.** Preregistration (workloads, seeds, rules) committed before the first run, as in `p6e-channels`;
  every participant pinned by commit; results JSON + README per run; a results table where each comparison carries its
  CI and the label rules of `docs/VNX_BENCHMARK_PLAN.md` §11; a limitations section. Any outcome is acceptable,
  including VNX-DNA losing; the item fails only if the comparison is unfair or untraceable. **Adoption rule:** if a
  published technique beats VNX-DNA's indel path at equal overhead (CI clears it), CC-4 evaluates adopting it, from
  the paper (re-implemented, never copied across an incompatible licence), before the freeze (Q8).
- **Risks.** HEDGES decode speed (≈ 1.1 KB/s in the research gate's demo) makes large workloads hit the 1-hour limit:
  use small inputs and report TIMEOUT as an outcome. Parameter tuning bias: use each code's published parameters, and
  give every participant the same tuning budget. Licences (Schifra in HEDGES; AGPL NOREC4DNA in DNA-Aeon): process
  boundary only.
- **Starts from.** `docs/VNX_BENCHMARK_PLAN.md` (adapter contract, participants, metrics, fairness); 
  `experiments/v14/bench/p6e-channels/` (preregistration and verdict machinery); `src/vnxdna/simulation/`;
  `src/vnxdna/sync/indel.py`, `docs/INDEL_ENGINE.md`, `docs/INDEL_RESEARCH.md`; `benchmarks/competitors/records.json`.
- **Depends on.** RS-3 for the PCR/decay channels (the IDS sweep can start before); founder Q5 (licence posture).

### CC-3 Nanopore 10 MB EXPLICIT_FAILURE: root cause and fix

- **Goal.** The 10 MB case on the nanopore-like channel (seed 93100) decodes, or fails for a documented reason that
  is not a budget artefact. Open since V11: "the clustering stage stops on its candidate-pair budget and recovers
  nothing; no wrong data is returned" (`docs/V11_COMPLETION_REPORT.md`; still open in `docs/V12_COMPLETION_REPORT.md`).
- **Method.** Two unmerged lines of work exist and are evaluated first, not redone: branch `work/nano-10mb` (job 86,
  5e38cd0, 2026-10-12: over the candidate budget the bucket cap is lowered instead of dropping the stage; its commit
  message reports the 10 MB dev seed 93100 and held-out seed 93110 decoding exactly, SIMULATED) and branch
  `work/nanopore-f2` (decoder preset F, outer-assisted recovery of short rows; held-out evaluations: preset F 19/20 vs
  preset E 2/20, 0 false successes, SIMULATED). Steps: (1) reproduce the failure on the V14 head with
  `vnx-dna-decoder-debug` (minimal reproducer, stage-by-stage); (2) rebase job 86 onto build/v14 and re-run its
  preregistered held-out seeds plus new held-out seeds committed before the run; (3) decide preset F on its evidence;
  (4) scaling sweep 1 / 4 / 10 / 32 MB on the nanopore-like model (`experiments/v6/channel/models/nanopore-like.json`,
  fitted in V7/V8; `docs/research/V7_NANOPORE_DECODING_RESEARCH.md`) to find the next limit.
- **Evidence target.** SIMULATED (the channel is a fitted model, not real reads; see the track note).
- **Acceptance.** The held-out seeds decode with the correct bytes, 0 false successes on every seed, no regression on
  the p6e suite, memory and time reported; a test pins the 10 MB case (or a scaled proxy that exercises the budget path).
- **Risks.** The fix may move the failure to a larger size; that is reported as the new limit. The branches are old
  (V10/V11 bases) and touch clustering code changed since.
- **Starts from.** `src/vnxdna/recovery/cluster/` (budget, bucket cap), `recovery/outer_list.py`; branches
  `work/nano-10mb`, `work/nanopore-f2`; `experiments/v11/` (mem/queue README).
- **Depends on.** CC-1 profiling (shared host time); none on format.

### CC-4 Density: channel-adaptive redundancy

- **Goal.** More bits per base at equal recovery, by choosing inner/outer redundancy and marker spacing per channel
  instead of per fixed profile.
- **Method.** Build on CC-2 (where the indel path loses or wins at equal overhead) and CC-3 (the nanopore limit).
  (1) Compute, for each named channel model, the recovery threshold as a function of overhead for the existing profiles
  (`src/vnxdna/v6/profiles.py`, `dnaenc/layout.py` `PROFILES`) and a grid of new parameter sets (inner parity, outer
  K/M, column parity, marker period), using the CC-2 threshold method. (2) Compare against an information-theoretic
  reference for the same channel where one exists (e.g. achievable rates for IDS channels with multiple reads, Maarouf
  et al. 2023), stated as THEORETICAL. (3) Select per-channel profiles on the Pareto front; the channel estimator
  (`simulation/fit/`) recommends a profile from pilot reads. (4) If CC-2 shows a published technique (e.g. a
  HEDGES-style inner code or marker code) wins at equal overhead, its adoption is evaluated here as a new profile or
  frame option; that is a format change and must make the FZ-3 freeze or wait for a version after V15 (Q8).
- **Evidence target.** SIMULATED; THEORETICAL for the bounds.
- **Acceptance.** For each channel, nt per byte at 95 % success with CI, against the current default profile; any
  claimed gain clears its CI; new profiles get golden vectors and cross-version decode tests; 0 false successes.
- **Risks.** Gains on simulated channels may not transfer to real reads (CC-5 is the only check). Profile sprawl:
  keep a small registry.
- **Starts from.** `src/vnxdna/v6/profiles.py`, `src/vnxdna/dnaenc/layout.py`, `src/vnxdna/recovery/planner.py`,
  `src/vnxdna/simulation/fit/`; `docs/V9_COMPLETION_REPORT.md` (adaptive recovery); CC-2 and CC-3 results.
- **Depends on.** CC-2, CC-3; FZ-3 (if it adds a frame option).

### CC-5 Primer-based physical random access

- **Goal.** One file can be PCR-amplified out of a mixed pool. Today random access is software-only (`.vxidx`,
  `docs/RANDOM_ACCESS.md`); primer flanks are specified, not implemented (spec §3.6), `PrimerPair` is refused by
  `prepare`, and the simulated lab refuses primer selection (`lab.rs`).
- **Method.**
  1. **Primer library design.** Generate candidate 20-nt primers and filter: Tm window by the unified nearest-neighbour
     model (SantaLucia 1998), GC 40–60 %, no runs > 3, 3'-end stability, self-/hetero-dimer and hairpin screens
     (Primer3 core, Untergasser et al. 2012), pairwise orthogonality (no shared stretch > k with any other primer or
     reverse complement; Xu et al. 2009's construction for large orthogonal sets), and **payload collision**: no primer
     within Hamming/edit distance d of any 20-mer window of the encoded bodies (the check Organick et al. 2018 apply).
  2. **Addressing scheme.** Compare one pair per file (Organick et al. 2018; Yazdi et al. 2015), nested addresses
     (Tomek et al. 2019) and combinatorial primer pairs (Winston et al. 2022) for address count vs. strand overhead.
  3. **Layout.** Implement frame 6 + superblock 3 with flanks (FZ-3), constraint screening over the full strand.
  4. **Simulation.** Add selective amplification to the simulator: per-pair efficiency from primer–template mismatch
     count, off-target amplification of near-matches, and RS-3's PCR bias; then decode one file from a pool of many.
  5. **First small wet-lab order (needs founder budget and go, Q3).** A small pool (e.g. a few files, some thousand
     strands, with primers) ordered from a synthesis vendor through the existing order export (`providers/` export
     package, `docs/LAB_INTERFACE.md`, the P7 `vnx.dna-lab/1` interface), PCR-selected and sequenced by a service
     provider, then decoded by VNX-DNA, with QA-5's protocol and the physical-validation record schema
     (`src/vnxdna/physical/`). **This is the only route in this plan to MEASURED evidence that VNX-DNA decodes real
     DNA.** Public datasets cannot substitute (they encode other codecs' data; track note above).
- **Evidence target.** THEORETICAL (library properties) + SIMULATED (selection). With step 5: MEASURED (category E, for
  that one pool, vendor and sequencer only). Without step 5, wet-lab selectivity is **not** claimed.
- **Acceptance.** A committed primer library of N pairs (N set at the freeze; ≥ 100 as a working target) with every
  filter's report; a simulated pool of ≥ 50 files from which each file is retrieved and decoded with off-target strands
  present; overhead reported (nt and % of strand) at the vendor length limit; the bioinformatics specialist reviews the
  thermodynamic assumptions. With step 5: the decoded bytes match SHA-256, or the failure is reported with its stage;
  raw reads deposited with the results.
- **Risks.** The simulator's mismatch-efficiency model is an assumption, not a measurement; real primer behaviour
  differs (secondary structure, polymerase). Frame 6 is the largest code change in V15 and a format change; if the
  founder declines it (Q1), CC-5 reduces to the design study (steps 1–2) and the limitation is documented.
- **Starts from.** `docs/spec/VNX-DNA-SPEC-V6.md` §3.5–§3.8; `src/vnxdna/providers/base.py` (`PrimerPair`),
  `providers/reference.py`; `src/vnxdna/native/rust/vnxd/src/lab.rs` (`primer_selection`, `PrimerPair` selector);
  `src/vnxdna/dnaenc/layout.py`; `docs/RANDOM_ACCESS.md`; `docs/LAB_INTERFACE.md`.
- **Depends on.** FZ-3 (frame 6 decision), RS-3 (PCR model), founder Q1; step 5: founder Q3 (budget, go).

## 4. Supporting research (SIMULATED unless stated)

### RS-3 PCR, chimera and decay-aware channel models

- **Goal.** The simulator covers the molecular effects the V14 suite does not: per-cycle PCR amplification bias and
  stochasticity, template-switching chimeras, and time/temperature-dependent decay (strand breaks and depurination).
- **Method.** New versioned mechanisms in the staged simulator (common random numbers preserved: each new mechanism
  draws from its own stage generator, so existing models give byte-identical reads):
  - PCR as a per-cycle branching process with sequence-dependent efficiency (Chen et al. 2020 identify synthesis and
    PCR as the main bias sources; Kebschull & Zador 2015 show PCR stochasticity dominates skew);
  - chimeras by template switching at a per-cycle rate on homologous stretches (Kebschull & Zador 2015 find them rare
    and concentrated at low copy number: the model must reproduce that, not inflate it);
  - decay as first-order strand-break kinetics with Arrhenius temperature dependence (Grass et al. 2015), producing
    fragments rather than whole-strand loss (Gimpel et al. 2024 show decoding fragmented DNA is the hard case);
  - calibration: parameters from the published fits of the dt4dds digital twin (Gimpel et al. 2023, 40 sequencing
    experiments) and Heckel et al. 2019; cross-check summary statistics (coverage distribution, error-position profile)
    against dt4dds runs on the same strands. MESA (Schwarz et al. 2020) and DeSP (Yuan et al. 2022) are cross-checks
    only.
  - New named models, e.g. `pcr-30cycles`, `chimera-low`, `decay-aged-<t>` under `experiments/v6/channel/models/`.
- **Evidence target.** SIMULATED. "Calibrated to published parameters" is stated as such; never "validated".
- **Acceptance.** Each mechanism has a unit test of its statistics (e.g. coverage dispersion after n cycles within
  tolerance of the analytic branching-process value); the existing models' reads are byte-identical (regression);
  summary statistics within stated tolerance of dt4dds on a shared input; one p6e-style benchmark row per new model.
- **Risks.** Over-parameterisation without data; mitigated by using only published parameters and reporting
  sensitivity sweeps. dt4dds is GPL-3.0: process boundary only.
- **Starts from.** `src/vnxdna/simulation/engine.py` (staged simulator; already has amplification duplicates, PCR
  substitutions, storage damage/breakage), `simulation/model.py`, `errormodels.py`, `registry.py`, `simulation/fit/`;
  `experiments/v6/channel/models/`; skill `vnx-channel-simulation`.
- **Depends on.** none (first research item to start).

### RS-4 Scheduler follow-up: a faster provably-safe skip rule, or why not

- **Goal.** Decide, with evidence, whether a skip rule exists that is provably safe (zero false skips by argument, not
  only by campaign) and at least as fast as the V13 rule on random indel pools. V14 result (`docs/V14_SCHEDULER.md`
  §9): the strand rule has 0 false skips in 3.39 M reads but is 25–33 % slower than the V13 rule on indel workloads
  (ratio 0.701 [0.700, 0.718] on indel-w1) and costs more than attempting every read; the default stays `auto` (the
  V13 rule plus one strand retry rung).
- **Method.** (1) Profile where the strand rule's cost goes (full DP attempts: 2.6x the V13 rule's). (2) Candidate:
  an edit-distance certificate, i.e. skip a read when a banded alignment to a verified frame stays under t, where t is
  below half the minimum Levenshtein distance between distinct valid frames of the same archive. The open question is
  that bound: RS Hamming distance does not imply Levenshtein distance, so it must be derived or computed for the frame-4
  layout with markers (the ecc-mathematician's task). (3) Candidate: reuse the alignment the certificate computes as
  the decode attempt itself (no second DP), which removes the duplicate work. (4) If no bound exists, write the negative
  result: why header/window evidence cannot certify identity on structured data (§5 of the scheduler doc) and what the
  safe rule costs.
- **Evidence target.** THEORETICAL (the bound and the safety argument) + SIMULATED (campaign) + MEASURED (A/B).
- **Acceptance.** Either (a) a proof reviewed by a second agent, 0 false skips on the 28-run campaign, and an A/B whose
  CI lower bound is ≥ 1.0 against the V13 rule on indel-w1, after which it may become the default; or (b) a committed
  negative-result note and `auto` stays. Time-box: 2 weeks; (b) is an acceptable outcome.
- **Risks.** The bound may be too small to skip anything useful; a fast rule may only be probabilistically safe (then
  it stays opt-in and is labelled so).
- **Starts from.** `docs/V14_SCHEDULER.md` (work/v14-p6c), `src/vnxdna/native/rust/.../sched.rs`, `pass1.rs`,
  `src/vnxdna/pipeline/decode.py`, `src/vnxdna/recovery/options.py`, `experiments/v14/bench/p6c-scheduler/campaign.py`.
- **Depends on.** P6C merged into build/v14.

## 5. FRONTIER track (ordered)

The founder asked for VNX-DNA to be "as futuristic as possible"; the lead set this order. Frontier items must not
delay the long-term-readability release acceptance (§1): anything that would change the frozen format is either an
opt-in, explicitly experimental extension outside the frozen core, or a study (Q10). Each item says what is genuinely
new against the state of the art, kept modest and sourced, and whether it needs lab work or money.

### F8 Automatic hot/cold tiering (electronic cache/volume → DNA media)

- **Goal.** Cold extents of a V14 volume migrate automatically to DNA media and are recalled on demand, with the same
  verify-before-return rule.
- **Method.** A policy engine in `vnxd`: per-extent access statistics (cache hit/miss, age), a cost model (write cost,
  recall latency in hours to days for DNA, aliquot budget), batched migration into a DNA pool through the media adapter,
  a stub/catalogue entry left on the electronic volume, recall as an asynchronous job (lab adapter, FZ-2 job ledger),
  never evicting the electronic copy before the DNA copy has been read back and verified (dna-sim) or the lab reports
  completion (dna-lab). Workload traces: synthetic plus public block-I/O traces if a licence allows.
- **What is new (modest).** DNA has been proposed as a new archival tier of the storage hierarchy (DNA Data Storage
  Alliance white paper, 2021). The contribution would be an open implementation behind a real block volume with a
  verified recall path; we did not find a published open implementation (search 2026-10-18; not a novelty claim).
- **Evidence target.** SIMULATED (DNA media are the simulator/lab-sim) + MEASURED (policy overhead, electronic side).
- **Acceptance.** No data loss or wrong data in crash/fault campaigns across migrate/recall; policy decisions
  deterministic for a trace; recall correctness 100 % on the simulator at the profile's operating point.
- **Risks.** Real DNA write/recall costs dominate; the policy is only as good as its cost model.
- **Starts from.** `vnxd` `cache.rs`, `volume.rs`, `balance.rs`, `fallback.rs`, `media.rs`, `lab.rs`, `dnasim.rs`;
  `docs/V14_RAM_AND_CACHE.md`, `docs/V14_MEDIA_ADAPTERS.md`.
- **Depends on.** FZ-2 (job ledger). Lab/money: no for the software; a real DNA tier needs lab and money.

### F1 Self-describing archives / Sector Zero bootstrap

Defined once as **LR-2** (§1); it is part of the release acceptance.

### F7 Formal verification of the "never returns wrong bytes" invariant (Rust/native, bounded scope)

- **Goal.** Machine-checked evidence, within stated bounds, that the Rust read paths return data only after the
  digest check, and that parsers of untrusted input cannot panic or go out of bounds.
- **Method.** Kani (bounded model checking for Rust; VanHattum et al. 2022) harnesses for: (1) the read/verify gate
  (`codec::verify_decoded` and every path that returns bytes to a caller), with the hash abstracted as an
  uninterpreted function, proving "Ok(bytes) ⇒ digest(bytes) = recorded digest" on all paths; (2) journal/record and
  NBD request parsing for absence of panics and arithmetic overflow up to a stated input size; (3) small pure kernels.
  C kernels: CBMC-style bounded checks only where harnesses are cheap. The realistic scope excludes proving the RS
  decoder or consensus correct: the invariant rests on the verify gate plus SHA-256 collision resistance (an
  assumption, stated).
- **What is new (modest).** Not a new verification technique; applying an existing model checker to a DNA codec's
  safety gate. We did not find published formal verification of a DNA-storage decoder (not a novelty claim).
- **Evidence target.** THEORETICAL (machine-checked, bounded; the bound is part of every statement).
- **Acceptance.** Harnesses in CI; each proof's bound and assumptions written next to it; any counterexample becomes a
  regression test and a fix.
- **Risks.** State explosion; unsupported Rust features; proofs about stubs not matching the real hash. Lab/money: no.
- **Starts from.** `src/vnxdna/native/rust/vnxd/src/codec.rs`, `record.rs`, `journal.rs`, `proto.rs`, `nbd.rs`.
- **Depends on.** FZ-1 (journal v2 is what gets verified).

### F6 Signal-level / quality-aware soft decoding

- **Goal.** Use more of the sequencer's information than hard base calls: per-base qualities today, basecaller
  posteriors or raw-signal likelihoods next.
- **Method.** Extend the V5 soft path (`src/vnxdna/v5/soft/`, `src/vnxdna/recovery/soft/`) from quality-weighted symbols
  to basecaller-posterior input, following basecaller–decoder integration (Chandak et al. 2020) and the faster trellis
  decoders of Volkel et al. 2025. The MEASURED route uses **public raw nanopore signals**: Volkel et al. deposited raw
  FAST5/FASTQ (Zenodo 10.5281/zenodo.11985454 and 10.5281/zenodo.12014515). Honest limit: those molecules carry
  HEDGES-encoded data, not VNX strands, so real signals can measure the **signal-to-posterior front end** (and
  reproduce a published soft decoder), not a VNX-DNA decode; VNX end-to-end stays SIMULATED until CC-5 sequences VNX
  strands on a nanopore with raw signal kept.
- **What is new (modest).** Soft-input decoding for DNA storage is published (above); the contribution would be soft
  input into VNX-DNA's marker/RS/outer pipeline with the same verify gate.
- **Evidence target.** SIMULATED (VNX end-to-end) + MEASURED (front end on public signals).
- **Acceptance.** On simulated channels, threshold gain at equal overhead with CI; on the public signals, reproduction
  of the paper's front-end metric within tolerance; 0 false successes.
- **Risks.** Basecaller/model licence and version churn; signal formats (FAST5 vs POD5). Lab/money: no for the public
  route; yes for VNX strands.
- **Starts from.** `src/vnxdna/v5/soft/`, `src/vnxdna/recovery/soft/`, `docs/research/V7_NANOPORE_DECODING_RESEARCH.md`.
- **Depends on.** CC-3 (nanopore path), CC-5 for VNX real signals.

### F2 Composite DNA letters

- **Goal.** Quantify, in simulation, whether composite letters (mixtures of the four bases at a position in set
  ratios; Anavy et al. 2019) would let VNX-DNA write more bits per synthesis cycle at acceptable read cost.
- **Method.** A composite-letter layer in the simulator (per-position multinomial sampling of the mixture, synthesis
  ratio error, coverage-dependent ratio estimation) and an experimental decoder that estimates letters from read
  clusters and hands symbol reliabilities to the outer code. Report bits per cycle vs. reads needed at 95 % success.
- **What is new (modest).** The method is published (Anavy et al. 2019); the contribution would be its combination
  with VNX-DNA's outer code and channel models, in simulation.
- **Evidence target.** SIMULATED. MEASURED needs mixture synthesis and sequencing (vendor capability, money).
- **Acceptance.** Simulator statistics match the analytic multinomial model; results with CIs; clearly labelled as an
  experimental profile outside the frozen core (Q10).
- **Risks.** It is a format change; real ratio errors are unknown to us. Lab/money: yes for any MEASURED result.
- **Starts from.** `src/vnxdna/simulation/engine.py`, `src/vnxdna/dnaenc/`, `src/vnxdna/recovery/consensus.py`.
- **Depends on.** RS-3; Q10.

### F4 Nanopore adaptive sampling ("Read Until") as PCR-free random access

- **Goal.** Select one file's molecules during nanopore sequencing by rejecting others in real time, without PCR.
- **Method.** Read Until (Loose, Malla & Stout 2016) ejects molecules whose prefix does not match a target; Sokolovskii,
  Ramirez-Garcia & Heinis (2024, bioRxiv preprint) apply adaptive sampling to PCR-free random access in DNA storage.
  Work: a file-identifying prefix in the strand layout (shared with CC-5's primer/address region), a simulator stage for
  the decision (decision latency in bases, ejected-prefix reads, enrichment), and a prefix classifier fast enough for
  the decision window.
- **What is new (modest).** The approach is published; the contribution would be the layout co-design with VNX
  addressing and its simulated enrichment/cost trade-off.
- **Evidence target.** SIMULATED. MEASURED needs a nanopore sequencer, flow cells and a synthesised VNX pool (money,
  lab).
- **Acceptance.** Simulated enrichment and decode success vs. reads with CIs; a sensitivity sweep on decision latency.
- **Risks.** Real decision latency and ejection efficiency differ by device and software version. Lab/money: yes for
  MEASURED.
- **Starts from.** `src/vnxdna/simulation/engine.py`, `src/vnxdna/dnaenc/layout.py`, CC-5 design.
- **Depends on.** CC-5 (layout), FZ-3 (if the prefix changes the frozen layout).

### F3 Enzymatic-synthesis channel model

- **Goal.** A named channel model for template-independent enzymatic (TdT) synthesis, so VNX-DNA's profiles can be
  evaluated against it.
- **Method.** Lee et al. 2019 used TdT with apyrase-limited extension, producing homopolymer runs of uncontrolled length
  per intended base transition (so information is in transitions, and run deletions are the dominant error); Verardo
  et al. 2023 report multiplex enzymatic synthesis with single-base resolution. Model: a run-length/transition channel
  plus deletion-heavy per-base errors; parameters fitted to deposited read data where it exists (data availability is
  checked first), otherwise set from the papers' summary statistics and labelled "parameters from published summaries,
  not fitted". Photolithographic synthesis errors (Gimpel et al. 2024) are a separate, cited model.
- **What is new (modest).** A versioned, reusable model in an open simulator; not new biology.
- **Evidence target.** SIMULATED.
- **Acceptance.** Model statistics reproduce the papers' reported distributions within tolerance; one p6e-style row.
- **Risks.** Enzymatic chemistry is changing quickly; published profiles may not match commercial instruments.
  Lab/money: no.
- **Starts from.** `src/vnxdna/simulation/errormodels.py`, `simulation/model.py`, `experiments/v6/channel/models/`.
- **Depends on.** RS-3 (shared simulator stage work).

### F5 Molecular content / similarity search

- **Goal.** A design study: could VNX archives support content-based retrieval in the molecule, as Bee et al. (2021)
  showed for images with hybridisation-probe queries and learned sequence encodings?
- **Method.** Literature study; a simulation of probe–target hybridisation yield for a learned encoding of feature
  vectors; how such encodings would coexist with VNX strands (separate index strands referencing archive addresses).
- **What is new (modest).** Published concept; any contribution would be the integration with a VNX index.
- **Evidence target.** THEORETICAL / SIMULATED. MEASURED needs wet-lab hybridisation and extraction (money, lab).
- **Acceptance.** A written study with a go/no-go recommendation; no product claim.
- **Risks.** Large effort for an uncertain product fit; lowest priority.
- **Starts from.** `docs/RANDOM_ACCESS.md`, `.vxidx` index code.
- **Depends on.** CC-5 (physical access model).

## 5A. ARCHIVE PLATFORM and NEXT-GENERATION track (added by the lead, 2026-10-19)

Features a long-lived archival system needs that the tracks above do not cover. All sit in the evolving layer: none
changes the frozen DNA format except where marked "freeze-relevant", which must be decided at M3. Citations marked †
are to be verified before the plan is final. Order is by value to the long-term readability goal, then cost.

### AP-1 Snapshots and write-once (WORM) retention
- **Goal.** Point-in-time snapshots of a volume and a retention mode in which committed data cannot be overwritten or
  deleted before its retention date, for archives that must be provably unchanged.
- **Method.** Snapshots as immutable journal checkpoints over generation-referenced extents (V14 never reuses a
  generation); a retention policy enforced in VNX-IO and the volume, recorded in the sealed metadata; refusal paths
  tested; a compliance mapping against a published WORM requirement (e.g. SEC Rule 17a-4(f) †) as a study, no
  compliance claim.
- **Evidence target.** MEASURED on electronic backends; SIMULATED over DNA media.
- **Acceptance.** Snapshot read-back byte-identical; every overwrite/delete/truncate path on a retained extent refused
  with a typed error; retention survives crash, restart and reopen (fault-injection tests).
- **Risks.** Interaction with tiering (F8) and replication (AP-2); clock trust for retention dates.
- **Starts from.** `vnxd/src/volume.rs`, `journal.rs`, `io.rs`; `src/vnxdna/volume/`.
- **Depends on.** FZ-1 (keyed journal) for tamper evidence.

### AP-2 Replication and erasure coding across physical pools
- **Goal.** Copies or erasure-coded shares of an archive in several physical places (freezers, sites, vendors) so the
  loss of one site loses nothing.
- **Method.** A placement layer above the media adapters: N-way replication and k-of-n erasure coding across pools
  (reusing the GF(256) Cauchy RS code), placement policy, per-site health, repair by re-synthesis from surviving
  shares; a simulated multi-site loss campaign.
- **Evidence target.** SIMULATED (DNA sites); MEASURED over electronic backends.
- **Acceptance.** Any declared number of site losses recovered byte-identically; never wrong bytes when more sites are
  lost than the code tolerates (explicit failure).
- **Risks.** Synthesis cost multiplies with redundancy; physical logistics are out of software scope.
- **Starts from.** `vnxd/src/media.rs`, `src/vnxdna/ecc/` (Cauchy RS), P7 adapters.
- **Depends on.** FZ-2 (media contract v2).

### AP-3 Key management: rotation, escrow, recovery
- **Goal.** No sealed archive becomes unreadable because one controller or one person is lost (P11 open question).
- **Method.** Key hierarchy (archive keys wrapped by rotating key-encryption keys); rotation without re-encoding DNA;
  offline escrow with threshold secret sharing (Shamir 1979, "How to share a secret", CACM) so k of n custodians can
  recover; recovery drill tests.
- **Evidence target.** MEASURED (software).
- **Acceptance.** Rotation, escrow and k-of-n recovery round-trip on test volumes; fewer than k shares reveal nothing
  (property tests); audit log of every key event.
- **Risks.** Custodian process is organisational, not software; mistakes are unrecoverable by design.
- **Starts from.** `vnxd/src/seal.rs`, `docs/V14_SECURITY_MODEL.md`, `docs/V14_BOOT_ARCHITECTURE.md`.
- **Depends on.** AP-4 crypto agility decision.

### AP-4 Post-quantum and crypto-agile protection for long-lived archives (freeze-relevant)
- **Goal.** Archives meant to be read decades from now should not depend only on algorithms a future quantum computer
  could break (key exchange/signatures; symmetric AES-256 is generally considered adequate with doubled margins †).
- **Method.** Algorithm identifiers in the sealed format (crypto agility); hybrid key wrapping with ML-KEM (NIST FIPS
  203, 2024) and signatures with ML-DSA (FIPS 204) or SLH-DSA (FIPS 205); a re-wrap path when algorithms age; library
  choice by licence and maturity.
- **Evidence target.** MEASURED (software, test vectors).
- **Acceptance.** NIST test vectors pass; old and new wrap formats both open; downgrade refused.
- **Risks.** Library maturity in Rust/Python; larger keys and signatures in metadata.
- **Starts from.** `docs/V14_SECURITY_MODEL.md`, `vnxd/src/seal.rs`, `src/vnxdna/` container crypto.
- **Depends on.** M3 freeze (algorithm IDs must be in the frozen format).

### AP-5 Signed provenance and chain of custody
- **Goal.** Prove what was stored, by whom and when, and track the physical sample from synthesis to retrieval.
- **Method.** Signed manifests (AP-4 signatures) over the existing SHA-256/Merkle roots; an append-only custody log of
  physical events (synthesis order, shipment, storage location, retrieval) linked to the P7 lab interface records.
- **Evidence target.** MEASURED (software).
- **Acceptance.** Any change to data, manifest or custody log is detected; verification works offline from the spec.
- **Risks.** Physical custody entries are only as true as the people entering them.
- **Starts from.** `vnxd/src/lab.rs`, `audit.rs`, manifest/Merkle code.
- **Depends on.** AP-4.

### AP-6 Monitoring, health and observability
- **Goal.** Operators see problems before data is at risk.
- **Method.** Metrics (capacity, I/O latency, cache hit rate, fallback state, scrub results, media/pool health, decode
  margins) exported in a standard text format (Prometheus exposition †); health checks and alert rules; the web
  console shows them.
- **Evidence target.** MEASURED.
- **Acceptance.** Every V14 fallback/degraded state and scrub failure appears as a metric and an alert in tests.
- **Risks.** Low.
- **Starts from.** vnxd diagnostics (P8 health), `web/` console.
- **Depends on.** none.

### AP-7 Pool health forecasting and refresh planning
- **Goal.** Re-synthesise (refresh) a pool before decay makes it unrecoverable, instead of discovering loss.
- **Method.** Combine RS-3 decay models with the outer-code margin measured at each sampling read; forecast the time
  at which recovery margin falls below a threshold; schedule refresh; a "digital twin" simulation of a pool over time.
- **Evidence target.** SIMULATED; MEASURED only with real aged samples.
- **Acceptance.** On simulated decay trajectories, the forecast triggers refresh before any loss in the campaign.
- **Risks.** Decay models are extrapolations (see LR-4); forecasts must carry uncertainty.
- **Starts from.** RS-3 models, scrub code.
- **Depends on.** RS-3, AP-2.

### AP-8 Synthesis-cost planner, deduplication and compression before synthesis
- **Goal.** Synthesis is the dominant cost; store fewer bases for the same data.
- **Method.** Content-defined chunking deduplication across archives; compression choice per content type before
  encoding; a planner that reports strands, bases and an estimated cost per vendor profile from published price
  inputs the operator supplies (no hard-coded prices).
- **Evidence target.** MEASURED (software counts).
- **Acceptance.** Byte-identical round trip; dedup never merges different content (hash-verified); plan output matches
  the encoder's actual strand counts.
- **Risks.** Dedup across tenants leaks information; must be per-tenant/opt-in.
- **Starts from.** `pipeline/encode.py`, `.vxidx` index.
- **Depends on.** none.

### AP-9 S3-compatible object gateway
- **Goal.** Let existing tools and applications use VNX through the de-facto object-storage API.
- **Method.** An S3-compatible subset (buckets, put/get/list/delete, multipart) over VNX volumes/tiering, with object
  lock mapped to AP-1; conformance against an open S3 test suite †.
- **Evidence target.** MEASURED.
- **Acceptance.** The chosen test-suite subset passes; integrity verified end to end.
- **Risks.** API surface is large; scope to a documented subset.
- **Starts from.** `src/vnxdna/sdk/`, vnxd service protocol, cloud API.
- **Depends on.** AP-1 (object lock), F8.

### AP-10 Automated re-encoding (migration) to newer codec profiles
- **Goal.** When a better codec profile exists, move archives to it on refresh, while every frozen-format archive stays
  readable.
- **Method.** Decode-verify-re-encode pipeline triggered by refresh (AP-7) or policy; old and new copies kept until the
  new one is verified.
- **Evidence target.** MEASURED (software), SIMULATED (DNA).
- **Acceptance.** No data deleted before the new copy verifies; compatibility matrix (FZ-4) green.
- **Starts from.** FZ-4, pipeline code. **Depends on.** M3 freeze.

### AP-11 Plugin SDK for third-party codecs and media adapters
- **Goal.** Others can add sequencers, synthesis vendors, storage hardware or codecs without forking.
- **Method.** Stable, versioned plugin interfaces (media contract v2, codec IPC), a conformance suite they must pass,
  and sandboxing (process isolation, resource limits) for untrusted plugins.
- **Evidence target.** MEASURED.
- **Acceptance.** An example external plugin passes conformance; a malicious plugin cannot return unverified bytes as
  success.
- **Starts from.** `media::conformance`, `worker.rs`, `vnx.codec-ipc/1`. **Depends on.** FZ-2.

### AP-12 GPU acceleration (gated)
- **Goal.** Faster alignment/consensus for large pools.
- **Method.** Only if CC-1 profiling shows alignment/consensus dominates after CPU work; a GPU kernel with bit-exact
  equivalence tests against the CPU reference; CPU remains the reference path.
- **Evidence target.** MEASURED. **Acceptance.** Bit-exact outputs; reproducible end-to-end gain with CIs.
- **Risks.** Hardware dependency; no gain if serial stages dominate. **Starts from.** native align/cluster kernels.
- **Depends on.** CC-1.

### AP-13 Learned error models assisting the classical decoder (fail-closed)
- **Goal.** Use machine-learned channel/error priors to raise recovery at low coverage, without ever trusting the model
  for correctness.
- **Method.** A learned model supplies soft priors (per-position error likelihoods) to the existing soft path (F6); the
  outer code, SHA-256 and Merkle checks stay the only acceptance; trained on simulator + public real error data.
- **Evidence target.** SIMULATED; MEASURED with public raw signals (F6 route).
- **Acceptance.** Recovery gain with CIs on held-out channels; 0 false successes; deterministic inference.
- **Risks.** Overfitting to the simulator; model provenance and reproducibility. **Starts from.** V5 soft path, F6.
- **Depends on.** F6.

### AP-14 Progressive and partial retrieval
- **Goal.** Read part of a file (a range or a preview) without decoding the whole archive.
- **Method.** Extend range reads (`.vxidx`, stream extents) so a requested byte range maps to the minimal set of
  extents and strands; physical side via CC-5/F4.
- **Evidence target.** MEASURED (software), SIMULATED (physical selection).
- **Acceptance.** Range output byte-identical to the slice of a full decode; strands touched reported.
- **Starts from.** `pipeline/stream.py`, `.vxidx`. **Depends on.** CC-5 for physical selection.

### AP-15 Molecular authentication of physical pools (research)
- **Goal.** Detect a counterfeit or substituted DNA sample.
- **Method.** Literature study of DNA watermarking/authentication schemes †; design of keyed marker strands verified
  at retrieval; simulation.
- **Evidence target.** THEORETICAL / SIMULATED; MEASURED needs wet lab.
- **Acceptance.** A study with a go/no-go recommendation. **Risks.** Low priority; uncertain value.
- **Starts from.** AP-5, P7 lab interface. **Depends on.** AP-5.

### AP-16 Integrated 24/7 offline security monitor (added 2026-10-19, founder)
- **Goal.** A device that protects itself continuously with no network: integrity, access and tamper monitoring.
- **Method.** An always-on monitor inside vnxd/the controller: file and metadata integrity checks against signed
  manifests (AP-5), audit of every access (existing `audit.rs`), anomaly rules (unusual read volume, repeated auth
  failures, unexpected processes or devices), tamper-evident hash-chained log, configurable lock-down (read-only,
  key wipe on policy); secure boot and measured state from `docs/V14_BOOT_ARCHITECTURE.md`.
- **Evidence target.** MEASURED (software, fault and attack-injection tests).
- **Acceptance.** Every injected tamper/attack scenario in a declared campaign is detected and logged; no false
  lock-down in the normal-use campaign; works with networking disabled.
- **Risks.** False positives; physical attacks are out of software scope. **Starts from.** `vnxd/src/audit.rs`,
  `authz.rs`, AP-5, AP-6. **Depends on.** AP-4, AP-5.

### AP-17 Offline-only device build (design rule, added 2026-10-19, founder)
- **Goal.** The device build and its runtime need no network at any time (defence use).
- **Method.** Remove/forbid network dependencies from the device profile (build from vendored sources; no telemetry;
  no online key/licence checks); updates as signed offline packages (USB), verified before install; a CI check that
  fails on any network call in the device profile (e.g. run the test suite in a network namespace with no interfaces).
- **Acceptance.** Full device test suite passes with networking disabled; signed-update install/rollback tested.
- **Starts from.** `docs/V14_BUILD_AND_DEPLOYMENT.md`, `deploy/systemd/`. **Depends on.** AP-4 (signatures).

### AP-18 Real-error replay from public sequencing data (added 2026-10-19, founder)
- **Goal.** Validate VNX decoding against the errors of real sequencers, without a wet lab.
- **Method.** Re-download public DNA-storage read sets with published reference designs (e.g. the datasets of
  `docs/DNA_STORAGE_DATASET_REGISTRY.md`); align reads to their references to extract per-read error events
  (substitutions, insertions, deletions, dropouts, coverage); replay exactly those events onto VNX strands and decode.
  Downloads and long runs on Kaggle CPU sessions (no GPU quota).
- **Evidence target.** SIMULATED with real error data (not MEASURED: no VNX strand was synthesised).
- **Acceptance.** Replay reproduces the dataset's measured error statistics; VNX decode results with CIs and 0 false
  successes. **Starts from.** V7/V8 dataset tooling. **Depends on.** RS-3.

## 5B. FINAL PRODUCT: the VNX-DNA Drive (founder vision, 2026-10-19)

A portable unit that a computer sees as an ordinary external disk reporting DNA capacity, doing encode, store,
retrieve, decode and protection itself, fully offline.

| part | V15 (software, ₹0) | after V15 (needs partner/sponsor hardware) |
|---|---|---|
| controller | P11 C1 design: Raspberry Pi as a USB Mass Storage gadget over the P8 device; `--low-memory` profile (P10) | custom board (P11 C2/C3) |
| speed | DNA speed (synthesis hours, sequencing minutes or more); no microsecond claim (see design decision below; RT-1 research) | — |
| capacity reporting | device reports the DNA tier capacity as the disk size | — |
| DNA write/read | simulated DNA tier (P7 dnasim) + lab interface (P7 `vnx.dna-lab/1`) | electrode-array synthesis and nanopore sequencing modules (research-grade today), integrated box |
| third-party machines | AP-11 plugin SDK + media contract: other synthesisers/sequencers plug in | vendor integrations |
| security | AP-16 24/7 offline monitor, AP-4 post-quantum, AP-1 WORM, AP-3 escrow, secure boot | hardware tamper sensors |
| network | AP-17 offline-only build, signed USB updates | — |

**Design decision (founder, 2026-10-19, final): DNA-only drive.** No RAM modules, no SSD, flash or disk: DNA is the
only storage and memory of the product. The only electronics are those that cannot be molecular today: the host link
(USB), the control processor (with only its built-in volatile scratch memory), and the electrical parts of the
synthesis and sequencing modules. Consequences stated plainly: every write and read goes through chemistry (hours to
write, minutes or more to read); a write is acknowledged only when committed to DNA; no microsecond-class latency is
claimed for the product. The earlier hybrid (RAM + SSD tier) mode is dropped from the product; electronic tiers may be
used only as development/test fixtures, never as product storage. Microsecond-class access becomes a research goal
(RT-1).

**Molecular partition architecture (founder, 2026-10-19).** Every subsystem has its own DNA pool, physically
compartmented (separate chambers/cartridges) and chemically addressable (an orthogonal primer set per pool, CC-5):

| pool | contents | behaviour | V14 software it maps to |
|---|---|---|---|
| storage | user data | write-once, read-rare; standard redundancy | volume extents, codec |
| VNX-RAM | working state, hot data | highest write rate; small; updates are new versions (DNA is not rewritable in place) | VNX-RAM pool, cache |
| VNX-IO journal | write-ahead log for crash safety | append-only, small, frequent | journal, VNX-IO |
| metadata/index | extent map, versions, file map | small, critical: highest redundancy | volume map, `.vxidx` |
| security | sealed keys, audit log, chain of custody | append-only, tamper-evident | VNX-Secure, `audit.rs`, AP-5 |
| bootstrap | Sector Zero decoding instructions | write-once, maximum redundancy | LR-2 |

Software method: one media-adapter instance per pool with its own ECC profile and capacity accounting; generation
references never reused (already a V14 invariant) model write-once molecules; stale versions are tracked and
garbage-collected logically; physical erasure (e.g. enzymatic degradation of stale strands) is a research item under
RT-2. Acceptance in V15: the full stack runs over six simulated pools (P7 dnasim), with per-pool fault injection
showing that loss/damage in one pool is contained and recovered as declared (e.g. metadata rebuilt from storage-pool
headers, journal replay after a crash).

**DNA-only adaptation of the V14 subsystems (DA track, founder 2026-10-19).** Every operation is a long-running job
(synthesis hours, retrieval minutes or more), so each subsystem becomes planned, batched, tracked and fail-safe:

| id | subsystem | V15 change | starts from |
|---|---|---|---|
| DA-1 | VNX-IO → job engine | writes/reads as tracked jobs: states (queued, synthesising, committed, retrieving, verified), progress, hour-scale deadlines, cancellation, host-visible status | `vnxd/src/io.rs`, P8 device |
| DA-2 | VNX-RAM → RAM-pool manager | small hot pool, versioned updates, stale-version tracking, compaction into a fresh pool | `ram.rs`, `volume.rs` |
| DA-3 | cache → predictive pre-retrieval | access-pattern prediction, pre-sequencing of likely reads (RT-1) | `cache.rs` |
| DA-4 | load balancer → molecular scheduler | fair sharing of synthesiser slots, sequencer flow cells, reagents and cost; priority retrieval | `balance.rs` |
| DA-5 | fallback → per-pool health | pool damage rebuilt from other pools (metadata from storage headers, journal replay); synthesis down → read-only; sequencing down → retrieval blocked with status; never wrong bytes | `fallback.rs`, `fault.rs` |
| DA-6 | volume/journal → journal pool + group commit | journal in its own pool; many writes per synthesis run | `journal.rs`, `volume.rs` |
| DA-7 | security → per-pool keys | per-pool sealing keys, audit log in the security pool, tamper checks on retrieval | `seal.rs`, `audit.rs`, AP-16 |
| DA-8 | synthesis batch planner (new) | pack pending writes into runs minimising time and reagents | P7 lab interface |
| DA-9 | retrieval planner (new) | choose pools and primer sets; retrieve only what is needed | CC-5, `.vxidx` |
| DA-10 | consumables manager (new) | reagents/flow cells/cartridges inventory, warnings, refuse writes that cannot complete | P7 `lab.rs` |
| DA-11 | pool lifecycle manager (new) | ageing, decay forecast, refresh scheduling | AP-7, RS-3 |
| DA-12 | GC / compaction (new) | logical removal of stale versions; physical erasure under RT-2 | DA-2 |
| DA-13 | instrument drivers (new) | real synthesiser/sequencer drivers through AP-11, simulator until hardware exists | AP-11, P7 |
| DA-14 | drive digital twin (new) | whole-drive simulation: six pools, instruments, failures, timing; the V15 test bed | P7 dnasim, P9 matrix |

Acceptance: the P9-style end-to-end matrix re-run against the digital twin (DA-14) in DNA-only mode, including
per-pool damage, instrument failure, consumable exhaustion, power loss mid-job and crash replay; 0 wrong bytes or
false successes. All software, ₹0.

**Research track RT-2: molecular memory and computation.** Move control state and computation into molecules as the
science matures: DNA-based rewritable memory and DNA logic (strand-displacement) circuits, starting from
`docs/V14_MOLECULAR_MEMORY.md`; literature survey first (sources verified at M0), simulation next; THEORETICAL /
SIMULATED until a wet-lab partner exists. Never promised.

**Research track RT-1: reducing molecular-tier latency** (pursued continuously, never promised): predictive
pre-retrieval of likely-needed data into the electronic tiers; read parallelism (many pores/sensors) and parallel
electrode synthesis; in-situ reading without library preparation; each integrated through the AP-11 plugin SDK when
real hardware exists, with measured latency reported honestly.

## 5B-G. GRAND TARGET: DNA replacing RAM and SSD (founder, 2026-10-19)

The specialised long-term target of VNX is DNA as a replacement for electronic storage and memory. It is pursued as a
measured ladder, never claimed early:

| rung | replaces | condition to claim it | status |
|---|---|---|---|
| R1 | archive tape / cold storage | reliable write + read with long retention, MEASURED with real DNA | closest: V15 + first wet-lab result (CC-5) |
| R2 | cold SSD / backup drive | retrieval in minutes; cost per GB competitive | needs faster, cheaper synthesis and sequencing |
| R3 | warm SSD (daily files) | seconds-level reads; rewrite cycles via molecular erase | needs RT-1 in-situ reading, RT-2 erasure |
| R4 | RAM | nanosecond-class rewritable molecular memory | frontier research (RT-2); no known chemistry today |

**Gap dashboard (every release from V15):** the DNA-only drive (digital twin until hardware exists, then hardware)
and a reference SSD and RAM measured on the same workloads: read/write latency, throughput, cost per GB, rewrite
cycles, energy at rest and active, retention. Reported as ratios with their sources; the gap is tracked, never
rounded away. VNX software is kept ready to exploit each hardware improvement immediately (AP-11 drivers, DA track).

## 5C. EXTREME TARGETS (founder, 2026-10-19: "push the limits to the extreme")

Ambition is expressed as measured records against published work or theoretical limits, never as unsupported
superlatives. Each target reports the number, the comparator, the method and the confidence interval.

| id | target | metric | comparator | builds on |
|---|---|---|---|---|
| X1 | density near capacity | net bits/base at a declared recovery rate, and the gap to the channel's capacity bound | published codecs on the same channel (CC-2) | CC-4, ECC math |
| X2 | robustness envelope | highest substitution/indel/dropout rates and lowest coverage still decoded (rate at ≥ declared recovery) | HEDGES, DNA-Aeon, DNA-RS, marker codes (CC-2) | CC-2, RS-3 |
| X3 | decode throughput | MB/s per core and scaling efficiency to N cores, end-to-end | the same decoders on the same host | CC-1, AP-12 |
| X4 | false-success bound | upper confidence bound on P(wrong bytes returned) from very large adversarial campaigns | — (absolute bound) | E suite, QA-2 |
| X5 | proven safety | formally verified "never returns unverified bytes" for the scoped core | — | F7 |
| X6 | scale | decode time linear in pool size at constant memory up to the largest simulated pool feasible | V14 linear pool decode | workstream A |
| X7 | DNA latency | measured write/read latency per stage of the DNA-only drive (synthesis, retrieval, sequencing, decode), driven down by RT-1 | — | RT-1, P8 device |

## 5D. HARDWARE and OS tracks (founder, 2026-10-19)

**Controller boards (cheapest first).** Full VNX (Linux, ARM64; P10) on a Raspberry Pi Zero 2 W (512 MB, `--low-memory`
profile ~96 MB, USB gadget) as the minimum target, Pi 4/5 as the standard one; other 64-bit ARM Linux boards with
>= 512 MB and USB gadget support are candidates after testing. ESP32/Arduino-class microcontrollers cannot run VNX (RAM,
no Linux) and are used only as helpers on a serial link: temperature/humidity, tamper, pumps/valves/motors of the DNA
modules. Nothing has run on real hardware yet (PL-2).

| id | hardware track | what we design | first step at ₹0 |
|---|---|---|---|
| H1 | carrier board | our own PCB around a Raspberry Pi Compute Module: USB-C host link, power-loss hold-up, secure element, tamper, sensor/helper connectors, enclosure | spec + KiCad schematic + BOM checked against datasheets |
| H2 | fully custom controller board | own board with an off-the-shelf SoC, DDR layout, Linux bring-up | after H1; needs funding |
| H3 | VNX codec accelerator (FPGA) | Verilog for the hot paths (RS decode, alignment, consensus), bit-exact against the software reference | Verilog + Verilator/Yosys simulation of the GF(256) RS decoder, differential against `vnxdna` |
| H4 | VNX codec ASIC | the proven H3 design taped out on an open PDK | after H3; fabrication costs money |
| H5 | own DNA hardware | electrode synthesis, microfluidics, nanopore read electronics | research partnership (RT-1/RT-2) |

| id | OS track | what | first step |
|---|---|---|---|
| OS1 | VNX-OS | own minimal Linux distribution (Buildroot/Yocto): kernel + VNX only, no network stack, read-only root, secure boot (P11), A/B and signed USB updates (AP-17); boots straight into the drive | Buildroot ARM64 config, boot + VNX tests in QEMU |
| OS2 | verified core | keys, security monitor and audit on the seL4 microkernel; Linux drivers isolated | after OS1; ties to F7 |
| OS3 | own kernel | a VNX-specific kernel (e.g. Rust) | long-term research |

## 6. Platform items

### PL-1 ublk upgrade (P8 Stage A')

- **Goal.** A ublk block device beside NBD, only if it pays for its `unsafe` surface.
- **Method.** Gate first: the P9 integrated profile must show the NBD socket hop is a material share of request time
  next to the per-extent fsyncs (P8: throughput is fsync-bound). If not, PL-1 is closed as "not needed" with the
  profile as evidence. If yes: a std-only io_uring/ublk server per `docs/V14_DEVICE_INTERFACE.md` §11 (SQE128 control
  commands, `ADD_DEV`/`SET_PARAMS`/`START_DEV`/`STOP_DEV`/`DEL_DEV`, per-queue FETCH/COMMIT_AND_FETCH), serving the same
  `IoQueue`; `UBLK_F_USER_RECOVERY` for server death. Kernel reference: Linux ublk documentation
  (docs.kernel.org/block/ublk.html). Also from P8: teardown/attach tests that run without the kernel (a fake control
  layer), so CI covers them on hosts without `nbd`/`ublk_drv`.
- **Evidence target.** MEASURED (category B on C).
- **Acceptance.** Same conformance, fault and teardown tests as NBD Stage A; ASan/Miri-style review of every `unsafe`
  block; A/B vs NBD with CIs on the P8 workloads; a dead server never leaves a device that blocks the host (recovery or
  `DEL_DEV` tested); NBD stays the default unless the CI lower bound shows a gain.
- **Risks.** Kernel-version dependence; large audited unsafe surface; permission system may deny device creation
  (then record the blocker and the command, as V14 rules require).
- **Starts from.** `docs/V14_DEVICE_INTERFACE.md` §11–§13, `vnxd` `nbd.rs`, `nbdctl.rs`, `nbdsys.rs`, `io.rs`.
- **Depends on.** P9 profile.

### PL-2 Raspberry Pi hardware acceptance (needs real hardware)

- **Goal.** Turn P10's cross-build + qemu result (Pi acceptance INCOMPLETE by design) into an on-device acceptance.
- **Method.** On a Raspberry Pi 5 (8 GB) running 64-bit Raspberry Pi OS (and a Pi 4 if available): native build of
  the wheel and `vnxd`, full test suite, the P5/P8 benchmarks scaled down, NBD device attach on the Pi kernel (check
  the `nbd` module is shipped), power-cut and storage-fault tests on SD card and USB/NVMe SSD, thermal throttling
  logged during the benchmark.
- **Evidence target.** MEASURED on the device (categories A–C). Not F.
- **Acceptance.** Suite green on device (any skip explained); benchmark numbers with CIs and the throttling log;
  device tests pass or the blocker is recorded; results reproducible from the committed commands.
- **Risks.** Hardware not bought (Q4); SD-card wear and power behaviour vary per card (name the exact parts).
- **Starts from.** `docs/V14_LANGUAGE_AND_RUNTIME_STRATEGY.md` §2–§3, the P10 report and its qemu transcript.
- **Depends on.** P10 merged; hardware (Q4).

### PL-3 External-controller prototype

- **Goal.** Build the first prototype of whichever external-controller path P11's study recommends.
- **Method.** Follow `docs/V14_EXTERNAL_CONTROLLER.md` (written by P11 in parallel; this plan does not presume its
  conclusion). The prototype presents the VNX volume to a host through the recommended interface, using parts the study
  classifies as buildable-now; custom or lab-only parts stay on paper.
- **Evidence target.** MEASURED for the electronic prototype (categories B–C). Category F is never claimed: the
  prototype stores data electronically.
- **Acceptance.** The study's own acceptance list; every hardware statement sourced; host interoperability tested on
  at least two host OSes; a teardown and power-loss test.
- **Risks.** Depends on the study's outcome, parts lead time and cost.
- **Starts from.** `docs/V14_EXTERNAL_CONTROLLER.md`, `docs/V14_BOOT_ARCHITECTURE.md` (P11 deliverables).
- **Depends on.** P11 merged; PL-2 (if the controller is Pi-class); founder budget (Q4).

## 7. Evidence, review and release items (directive §23, brief §12)

### QA-1 Reproducible clean builds

- **Goal.** Two independent clean builds from the release commit produce bit-identical artifacts (sdist, wheels,
  `vnxd` binary), or every remaining difference is explained.
- **Method.** Pinned toolchains (Python, Rust, C compiler) recorded in a lock file; `SOURCE_DATE_EPOCH`, path
  remapping, sorted archive members (reproducible-builds.org practices); build in a fresh container on two hosts;
  `diffoscope` on differences; SHA-256 manifest + SBOM published with the release.
- **Evidence target.** REPRODUCED (two builders); EXTERNALLY VALIDATED only if an outside party rebuilds.
- **Acceptance.** Identical hashes, or a short explained-difference list with no code differences.
- **Starts from.** `pyproject.toml`, `src/vnxdna/_native_build.py`, the `vnxd` Cargo files, `docs/RELEASE_PROCESS.md`.
- **Depends on.** FZ freeze (artifacts of the final formats).

### QA-2 Correctness, corruption, fuzz, crash-recovery and fault validation on the frozen formats

- **Goal.** The full validation campaign of brief §12, run once on the frozen formats and kept as the release gate.
- **Method.** Fuzz harnesses for every parser of the frozen formats (container, superblock 3, frame 6, journal v2,
  media v2 messages, NBD/ublk request parsing) with ASan/UBSan; hypothesis round-trips; crash injection at every
  durable-write point (journal v2, migration); FaultMedia campaigns; the "decoder never returns wrong data" campaign
  across all channel models including RS-3's; security review of the crypto/MAC changes. Closes O-2 and O-3.
- **Evidence target.** MEASURED (campaign outcomes, category A–C) and SIMULATED (channel campaigns, D).
- **Acceptance.** 0 false successes; 0 sanitizer findings; every crash point recovers to a consistent prior state or
  refuses; test count never drops; all HARDENING items closed or explicitly accepted by the founder.
- **Starts from.** `docs/V14_HARDENING.md`, existing fuzz harnesses, `vnxd` `fault.rs`, skill `vnx-fuzzing`.
- **Depends on.** FZ-1..FZ-4.

### QA-3 Independent reproduction and review

- **Goal.** Key results reproduced by someone else; architecture and security reviewed by someone else.
- **Method.** A reproduction pack (container image digest, commands, expected result files with tolerances) for: the
  V14/V15 benchmark suite, CC-2's comparison, RS-4's campaign. Step 1 (REPRODUCED): a second host run by the team.
  Step 2 (EXTERNALLY VALIDATED): researchers or reviewers outside VSNexus, invited by the founder; their written
  statement is committed verbatim. Same for an independent architecture and security review.
- **Evidence target.** REPRODUCED (second host, in-house) is within the team's control; EXTERNALLY VALIDATED only if an outside party agrees and reports.
- **Acceptance.** Step 1 within the stated intervals; step 2 is reported as done, pending or declined; never implied.
- **Risks.** External lead time is outside the project's control; the release does not wait for it unless the founder
  says so (Q6).
- **Depends on.** QA-1, CC-2, RS-4.

### QA-4 Research-ready documentation and the final evidence statement

- **Goal.** Methods and architecture documents that an outside researcher can review; complete known-limitations and
  security sections; the **final software-vs-physical-DNA evidence statement** (what is A–D, what E/F would need).
- **Method.** Consolidate V4–V15 specs into one frozen specification; a methods document (codes, decoder, scheduler,
  channel models with citations); limitations and failure cases (incl. the dropout10 preregistration miss in
  `p6e-channels`, the torn-tail-as-zeros limit, the RS-4 outcome); claims audit with the claims checker.
- **Evidence target.** n/a (documentation); every claim inside carries its rung.
- **Acceptance.** Claims audit clean; every number traceable to a committed results file; evidence categories A–F in
  every report.
- **Starts from.** `docs/V14_ARCHITECTURE.md`, `docs/spec/`, `docs/V14_SECURITY_MODEL.md`,
  `docs/V14_FAILURE_AND_RECOVERY_MODEL.md`, skill `vnx-scientific-audit`.

### QA-5 Physical-media experimental protocol (category E, written not run)

- **Goal.** A protocol that a wet lab could run to test synthesis → storage → PCR (incl. CC-5 primer selection) →
  sequencing → decode with VNX-DNA, with the measurements it would produce and the claims each would allow.
- **Method.** Written with the bioinformatics specialist; vendor limits, pool size, primers, controls, sequencing depth,
  pre-registered success criteria, data deposition.
- **Evidence target.** THEORETICAL (a protocol). Running it is out of V15 scope unless a lab partner exists (Q3).
- **Starts from.** `docs/LAB_INTERFACE.md`, `docs/V6_PHYSICAL_VALIDATION_INTERFACE.md`, `src/vnxdna/physical/`.

### QA-6 Release, LTS and support policy for a final version

- **Goal.** A written policy so users know what "final" means.
- **Proposed policy (for the founder to decide, Q7).**
  - V15.0.0 is the last planned **major** version: frozen formats and stable APIs under semantic versioning
    (semver.org); 15.x releases are maintenance only (bug, security, compatibility with new Python/Rust/kernel
    versions). Maintenance may still be needed; nothing promises it will not be.
  - Support window: security fixes for a stated period (proposal: 3 years from 15.0.0), bug fixes for a shorter one
    (proposal: 18 months); end-of-life announced at least 6 months ahead.
  - Read compatibility is permanent for every released format: any future version that drops a reader must ship a
    standalone reader for it. A standalone, dependency-minimal V15 reader (decode only) is archived with each release.
  - Security disclosure contact and process in `docs/SECURITY.md`; advisories per fix.
  - Release checklist is **founder-controlled**: no merge to main, tag or publish without the founder's go on a named
    SHA (as in V14).
- **Evidence target.** n/a (policy).
- **Starts from.** `docs/RELEASE_PROCESS.md`, `docs/V11_RELEASE_GATES.md`, `docs/SECURITY.md`, skill `vnx-release`.

## 8. Competitive position and IP

There is a real tension: the long-term-readability objective (§1) **requires an open, normative specification** and a
reference decoder that anyone can re-implement (the Library of Congress factors favour disclosure and transparency).
Secrecy of the format therefore cannot be the advantage. The advantage has to come from:

- **evidence**: MEASURED and externally validated results (CC-5, LR-5, QA-3) that others can check;
- **the integrated platform**: block volume, tiering, media adapters, lab interface and codec working together;
- **method patents**, if counsel advises that specific methods qualify (below);
- **data and channel models**: fitted models, benchmark suites, any VNX wet-lab data;
- **standards participation** (LR-6);
- **execution speed**.

Comparisons with other systems follow the existing rule: no statement about a competitor's weakness without a source
(`docs/VNX_BENCHMARK_PLAN.md`; skill `vnx-competitive-intelligence`).

**Founder decision item (Q11): patent-attorney review before publication.** Before the V15 specification, any paper,
preprint, talk or public repository describes them, a patent attorney should review candidate methods, for example the
RS-4/V14 scheduler skip rules, the linear pool decode, composite-letter coding with the outer code (F2), the tiering
policy with verified recall (F8), the bootstrap layer (LR-2) and adaptive-sampling layout co-design (F4). Publishing
first can affect patentability: the lead's understanding is that many jurisdictions, including India, have no general
grace period for an inventor's own disclosure. That is not legal advice and this plan draws no legal conclusion; it
needs a qualified attorney's opinion, including on what has already been disclosed (public site, released tags,
outreach emails) and on freedom to operate for methods taken from the literature. The spec publication milestone
(M6) waits for this review unless the founder decides otherwise.

## 9. Out of scope for V15 (stated so that nothing implies it)

- Claiming any physical DNA result (category E) beyond what CC-5's order actually measures, and any physical device
  (category F).
- Any guaranteed lifetime or longevity figure for VNX archives.
- New codec families in the frozen core unless CC-2/CC-4 justify them before the freeze (M2); frontier codec ideas
  (F2) stay experimental and outside the frozen core.
- A hosted public service launch (founder decision outside this plan).

## 10. Milestones and effort

Estimates are focused engineering weeks for the agent team, founder review time excluded; ranges, not promises.
External lead times (vendors, reviewers, hardware, counsel) are calendar time on top.

| milestone | items | effort | gate |
|---|---|---|---|
| **M0** scope lock | finalize after P12; founder answers Q1–Q11; trim the frontier list | 3–5 days | founder approval of the final plan |
| **M1** core codec I + models | CC-1 (speed); RS-3 (PCR/chimera/decay); CC-2 harness, adapters, preregistration; CC-5 steps 1–2 (primer library, scheme); F3 (enzymatic model) | 6–9 weeks | CC-1 A/B with CIs; RS-3 regression byte-identical; CC-2 preregistration committed |
| **M2** core codec II | CC-2 full runs (incl. ETH protocol); CC-3 (nanopore 10 MB); RS-4 (time-boxed 2 weeks); CC-4 (density) | 6–9 weeks | results with CIs; adoption decisions for CC-4 made before the freeze |
| **M3** format + API freeze | FZ-1..FZ-4, O-2/O-3, frame 6 if Q1 = yes; LR-1 spec; LR-2 bootstrap layer | 6–9 weeks (4–6 without frame 6) | freeze review: every FZ-3 row decided; golden corpus committed; compatibility matrix green |
| **M4** readability | LR-3 reference decoder; LR-4 guidance; LR-6 conformance table; LR-5 internal dry run | 4–6 weeks | LR-3 bit-exact on the corpus; dry run passes |
| **M5** wet-lab order (only with founder budget + go) | CC-5 step 5: small synthesis + sequencing order through the export/lab interface, decode, report | 2–3 weeks of work; 6–12 weeks calendar (vendor) | the first MEASURED VNX-DNA decode, or a documented failure |
| **M6** frontier (software) | F8 tiering; F7 Kani harnesses; F6 soft decoding (public signals); F2/F4 simulations; F5 study | 10–18 weeks; parallel with M3–M5 where people allow | per-item acceptance; trims allowed at M0 |
| **M7** platform | PL-1 (gate first), PL-2 (hardware), PL-3 (per P11 study) | 1–10 weeks, hardware-bound | each acceptance or a recorded blocker |
| **M8** validation + release candidate | QA-1..QA-6; patent review done (Q11) before the spec is published; release acceptance of §1 | 4–6 weeks | full gate on the RC SHA; founder release go |
| **M9** external validation | LR-5 external run; QA-3 step 2 | calendar, unknown | statements committed or reported as pending (Q6) |
| **M10** archive platform (§5A) | AP-4 algorithm IDs decided at M3 (freeze-relevant); then AP-1, AP-3, AP-6, AP-8 (high value, low risk); AP-2, AP-5, AP-7, AP-9, AP-10, AP-11; gated AP-12, AP-13; studies AP-14 (with CC-5), AP-15 | 16–28 weeks; parallel with M3–M8 | per-item acceptance; trims allowed at M0 |

Total engineering: roughly **45–75 focused weeks** with every item; with two to three parallel streams, about **8–12
months of calendar time** to a release candidate, plus vendor, counsel, hardware and reviewer lead times. Without the
frontier track (M6) and PL-3: roughly 30–45 weeks. The largest swing factors: the frontier scope, frame 6 (Q1),
whether CC-1 reaches its targets, the PL-1 gate, the wet-lab order (Q3), and how many CC-2 participants run within
limits.

Critical path: CC-1/RS-3 → CC-2 → CC-4 adoption decision → FZ freeze + LR-1/LR-2 → LR-3 → QA-2 → QA-1 → patent
review → RC.

## 11. Open questions for the founder

- **Q1** Molecular random access in the frozen format: implement frame 6 + primer flanks (largest change; costs strand
  length/density; the opt-in `v10-oligo300` profile on `work/nanopore-f2` leaves room for two 20-nt primers under
  300 nt and is an alternative to evaluate), or keep random access software-only?
- **Q2** Journal MAC: keyed integrity for encrypted volumes only, or also a keyed mode for unencrypted volumes? Accept
  tail rollback as a documented limit, or pay one extra durable write per commit for an anchored counter?
- **Q3** Wet-lab budget and go for CC-5's first synthesis + sequencing order. This is the **only route to MEASURED
  evidence** that VNX-DNA decodes real DNA; without it every channel result stays SIMULATED.
- **Q4** Hardware budget: Raspberry Pi 5 (+ SSD/NVMe, SD cards, power-cut rig) for PL-2, parts for PL-3, and a nanopore
  sequencer + flow cells if F4/F6 should reach MEASURED.
- **Q5** Licence posture for CC-2: HEDGES bundles Schifra (non-commercial terms): internal measurement only, results
  publishable? (Q2 of `docs/VNX_BENCHMARK_PLAN.md`.)
- **Q6** Does the V15 release wait for external validation (LR-5, QA-3 step 2), or ship with it marked pending?
- **Q7** Support policy numbers (security window, bug-fix window, EOL notice) and who maintains after V15.
- **Q8** If CC-2 shows a published indel technique wins at equal overhead: adopt it into the frozen core before M3
  (delays the freeze), or document it and leave it out?
- **Q9** DNA Data Storage Alliance / SNIA: membership, Sector Zero vendor/codec ID allocation, licence terms of the
  specifications.
- **Q10** Frontier scope: which of F8, F7, F6, F2, F4, F3, F5 are in V15, and may experimental profiles (F2, F4) exist
  outside the frozen core after the final version?
- **Q11** Patent-attorney review of candidate methods before the spec, any paper or public repository (§8).
- **Q12** Post-quantum (AP-4): adopt hybrid ML-KEM/ML-DSA (or SLH-DSA) algorithm IDs in the frozen format at M3?
- **Q13** Archive platform scope (§5A): which AP items are in V15; AP-2/AP-7 also need a decision on physical sites.
- Carried over: scrypt cost (FZ-3; the `cryptography` pin was decided in V14: >=48.0.1,<51); any P12 FAIL/INCOMPLETE
  item becomes a V15 item or a documented non-goal.

## 12. Placeholders for P12

P12 adds rows here; the lead merges them into the tracks when finalizing.

| from P12 criterion | finding | proposed V15 item |
|---|---|---|
| 2 reproducible build | no clean-checkout build + test evidence; only the lab worktree was tested | reproducible clean-build test: fresh clone at the release SHA, clean venv, native + crate builds, full suite, the log committed |
| 14 benchmarks separated by backend | benchmark report §2 logical I/O, §3 cache, §5 electronic backend, §6 simulator end to end NOT MEASURED | `vnxd-io-bench` (JSON lines, one configuration per process) and a driver for §2/3/5/6 |
| 11 block device E2E | B05 kill-under-load left `/dev/nbdN` attached until b2e5e0e (test fd leak) | nbd-leak soak (repeated E2E + kill rows, 0 attached devices / mounts after) as a release gate |
| 12 filesystem tests | ext4 only, on this host | xfs, btrfs and f2fs over `/dev/nbdN` on disposable devices |
| 10 simulator + electronic backend | E2E rows M02/M04 run the DNA adapters on the TEST-ONLY `ToyCodec` | M02/M04 with the real codec worker |
| 15 ARM64 / Pi | Pi acceptance hardware-blocked; R24 is a SIMULATED budget | Pi native build + golden subset and R24-hw when hardware exists |
| 2/7 evidence | the release-gate suite log lived only in the lab results directory | commit the release-gate suite log under a tracked results path (`results/v<N>/release-gate/`) |
| 14 trade-offs | strand retry rung doubles time-to-FAILURE on undecodable inputs; ~2-5 % single-archive regression vs V13 at w4 | early exit when pass 1 shows capacity exceeded; profile the per-read bookkeeping on the V13-rule path |
| 9 security + integrity | O-1: unkeyed journal CRC-32 lets a writer reorder or roll back commits of an encrypted volume undetected | keyed journal MAC (or extent number/version bound into the sealed data) as the encrypted-volume release gate |

## 13. References

Checked against publisher, PubMed Central, standards-body or project records on 2026-10-18 unless marked †
(† = cited from the project's earlier research records or from standard references; recheck the DOI when finalizing).

Indel codes and benchmarks
- Press W.H., Hawkins J.A., Jones S.K. Jr, Schaub J.M., Finkelstein I.J. (2020). HEDGES error-correcting code for DNA
  storage corrects indels and allows sequence constraints. *PNAS* 117(31):18489–18496. doi:10.1073/pnas.2004821117.
- Davey M.C., MacKay D.J.C. (2001). Reliable communication over channels with insertions, deletions, and
  substitutions. *IEEE Trans. Inf. Theory* 47(2):687–698. doi:10.1109/18.910582. †
- Ratzer E.A. (2005). Marker codes for channels with insertions and deletions. *Annals of Telecommunications*
  60(1–2):29–44. doi:10.1007/BF03219806.
- Maarouf I., Lenz A., Welter L., Wachter-Zeh A., Rosnes E., Graell i Amat A. (2023). Concatenated codes for multiple
  reads of a DNA sequence. *IEEE Trans. Inf. Theory* 69(2):910–927. (Preprint arXiv:2111.14452.)
- Welzel M., Schwarz P.M., Löchel H.F., et al. (2023). DNA-Aeon provides flexible arithmetic coding for constraint
  adherence and error correction in DNA storage. *Nature Communications* 14. doi:10.1038/s41467-023-36297-3.
- Gimpel A.L., et al. (2026). Codec benchmark of DNA-Aeon, DNA Fountain, DNA-RS, Goldman, HEDGES, Yin-Yang (the ETH
  protocol). *Nature Communications* 17:3963. doi:10.1038/s41467-026-70548-3. † (project record
  `gimpel2026_codec_benchmark`)

Random access and primer design
- Organick L., Ang S.D., Chen Y.-J., et al. (2018). Random access in large-scale DNA data storage. *Nature
  Biotechnology* 36:242–248. doi:10.1038/nbt.4079. †
- Yazdi S.M.H.T., Yuan Y., Ma J., Zhao H., Milenkovic O. (2015). A rewritable, random-access DNA-based storage system.
  *Scientific Reports* 5:14138. doi:10.1038/srep14138. †
- Tomek K.J., Volkel K., Simpson A., Hass A.G., Indermaur E.W., Tuck J.M., Keung A.J. (2019). Driving the scalability
  of DNA-based information storage systems. *ACS Synthetic Biology* 8(6):1241–1248. (Preprint doi:10.1101/591594.)
- Winston C., Organick L., Ward D., Ceze L., Strauss K., Chen Y.-J. (2022). A combinatorial PCR method for efficient,
  selective oligo retrieval from complex oligo pools. *ACS Synthetic Biology*. (Preprint doi:10.1101/2021.08.25.457714.)
- SantaLucia J. Jr (1998). A unified view of polymer, dumbbell, and oligonucleotide DNA nearest-neighbor
  thermodynamics. *PNAS* 95(4):1460–1465. doi:10.1073/pnas.95.4.1460. †
- Untergasser A., Cutcutache I., Koressaar T., et al. (2012). Primer3 — new capabilities and interfaces. *Nucleic Acids
  Research* 40(15):e115. doi:10.1093/nar/gks596. †
- Xu Q., Schlabach M.R., Hannon G.J., Elledge S.J. (2009). Design of 240,000 orthogonal 25mer DNA barcode probes.
  *PNAS* 106(7):2289–2294. doi:10.1073/pnas.0812506106.

Channel models: PCR, chimeras, decay
- Chen Y.-J., Takahashi C.N., Organick L., et al. (2020). Quantifying molecular bias in DNA data storage. *Nature
  Communications* 11:3264. doi:10.1038/s41467-020-16958-3.
- Kebschull J.M., Zador A.M. (2015). Sources of PCR-induced distortions in high-throughput sequencing data sets.
  *Nucleic Acids Research* 43(21):e143. doi:10.1093/nar/gkv717.
- Gimpel A.L., Stark W.J., Heckel R., Grass R.N. (2023). A digital twin for DNA data storage based on comprehensive
  quantification of errors and biases. *Nature Communications* 14. doi:10.1038/s41467-023-41729-1.
- Gimpel A.L., Stark W.J., Heckel R., Grass R.N. (2024). Challenges for error-correction coding in DNA data storage:
  photolithographic synthesis and DNA decay. *Digital Discovery* 3(12):2497–2508. (RSC article d4dd00220b.)
- Grass R.N., Heckel R., Puddu M., Paunescu D., Stark W.J. (2015). Robust chemical preservation of digital information
  on DNA in silica with error-correcting codes. *Angewandte Chemie Int. Ed.* 54(8):2552–2555. doi:10.1002/anie.201411378.
- Heckel R., Mikutis G., Grass R.N. (2019). A characterization of the DNA data storage channel. *Scientific Reports*
  9:9663. doi:10.1038/s41598-019-45832-6. †
- Schwarz M., Welzel M., Kabdullayeva T., Becker A., Freisleben B., Heider D. (2020). MESA: automated assessment of
  synthetic DNA fragments and simulation of DNA synthesis, storage, sequencing and PCR errors. *Bioinformatics*
  36(11):3322–3326. doi:10.1093/bioinformatics/btaa140. †
- Yuan L., Xie Z., Wang Y., Wang X. (2022). DeSP: a systematic DNA storage error simulation pipeline. *BMC
  Bioinformatics* 23:185. doi:10.1186/s12859-022-04723-w. †

Systems, security and release practice
- Linux kernel documentation: Userspace block device driver (ublk). https://docs.kernel.org/block/ublk.html
- Krawczyk H., Bellare M., Canetti R. (1997). HMAC: Keyed-hashing for message authentication. RFC 2104.
- Krawczyk H., Eronen P. (2010). HMAC-based Extract-and-Expand Key Derivation Function (HKDF). RFC 5869.
- Reproducible Builds project: https://reproducible-builds.org/docs/ (SOURCE_DATE_EPOCH, deterministic archives).
- Semantic Versioning 2.0.0: https://semver.org/

Long-term readability and standards
- SNIA / DNA Data Storage Alliance (2023). DNA Data Storage Sector Zero, v1.0 (DARS working group), and the companion
  Sector One specification. https://www.snia.org/standards/technology-standards-software/standards-portfolio/dna-data-storage-sector-zero
  (Contents summarised from the SNIA page and press coverage; the 70-base layout figure in `docs/DDSA_MAPPING.md` is
  from secondary sources and must be checked against the PDF.)
- DNA Data Storage Alliance (2021). Preserving our Digital Legacy: An Introduction to DNA Data Storage (white paper).
  https://www.snia.org/educational-library/preserving-our-digital-legacy-introduction-dna-data-storage-2021
- Library of Congress. Sustainability of Digital Formats: sustainability factors (disclosure, adoption, transparency,
  self-documentation, external dependencies, impact of patents, technical protection mechanisms).
  https://www.loc.gov/preservation/digital/formats/
- ISO 19005 (PDF/A), document management — electronic document file format for long-term preservation (family of
  standards; described in the Library of Congress format entry). †
- IAU FITS Working Group. Definition of the Flexible Image Transport System (FITS), version 4.0 (approved 2016,
  finalized 2018). https://fits.gsfc.nasa.gov/fits_standard.html ("once FITS, always FITS": secondary source only.)
- Matange K., Tuck J.M., Keung A.J. (2021). DNA stability: a central design consideration for DNA data storage systems.
  *Nature Communications* 12. doi:10.1038/s41467-021-21587-5.
- NIST SP 800-38D (2007), GCM mode; Percival C., Josefsson S. (2016), The scrypt Password-Based Key Derivation
  Function, RFC 7914. †

Frontier items
- Anavy L., Vaknin I., Atar O., Amit R., Yakhini Z. (2019). Data storage in DNA with fewer synthesis cycles using
  composite DNA letters. *Nature Biotechnology*. doi:10.1038/s41587-019-0240-x.
- Bee C., Chen Y.-J., Queen M., Ward D., Liu X., Organick L., Seelig G., Strauss K., Ceze L. (2021). Molecular-level
  similarity search brings computing to DNA data storage. *Nature Communications* 12:4764. doi:10.1038/s41467-021-24991-z.
- Loose M., Malla S., Stout M. (2016). Real-time selective sequencing using nanopore technology. *Nature Methods*
  13(9):751–754. doi:10.1038/nmeth.3930.
- Sokolovskii R., Ramirez-Garcia R., Heinis T. (2024). Adaptive sampling in nanopore sequencing for PCR-free random
  access in DNA data storage. bioRxiv preprint (not peer-reviewed at the time of reading). doi:10.1101/2024.11.05.622081.
- Lee H.H., Kalhor R., Goela N., Bolot J., Church G.M. (2019). Terminator-free template-independent enzymatic DNA
  synthesis for digital information storage. *Nature Communications* 10. doi:10.1038/s41467-019-10258-1. (DOI and
  method checked; author list †.)
- Verardo D., et al. (2023). Multiplex enzymatic synthesis of DNA with single-base resolution. *Science Advances*
  9(27):eadi0263. (Error figures not yet read from the full text.)
- VanHattum A., Schwartz-Narbonne D., Chong N., Sampson A. (2022). Verifying dynamic trait objects in Rust. ICSE-SEIP
  2022 (Kani). Preprint: https://cs.wellesley.edu/~avh/dyn-trait-icse-seip-2022-preprint.pdf
- Chandak S., Neu J., Tatwawadi K., Mardia J., Lau B., Kubit M., Hulett R., Griffin P., Wootters M., Weissman T., Ji H.
  (2020). Overcoming high nanopore basecaller error rates for DNA storage via basecaller-decoder integration and
  convolutional codes. IEEE ICASSP 2020. (Preprint doi:10.1101/2019.12.20.871939; author list †.)
- Volkel K.D., Hook P.W., Keung A., Timp W., Tuck J.M. (2025). Nanopore decoding with speed and versatility for data
  storage. *Bioinformatics* 41(1):btaf006. doi:10.1093/bioinformatics/btaf006. Raw data: Zenodo
  doi:10.5281/zenodo.11985454 and doi:10.5281/zenodo.12014515.
