# V15 M0: scope lock decisions (2026-10-19)

Basis: docs/V15_FINALIZATION_PLAN.md §11 (Q1–Q13). Decision authority (founder, 2026-10-19): the lead engineer decides
engineering questions; money, anything external in the founder's name, legal/guardian matters and release tags stay with
the founder. Budget: ₹0 (no purchases). Every decision below can be revisited at a milestone gate.

## Decided (engineering)

| Q | decision | reason |
|---|---|---|
| Q1 molecular random access in the frozen format | **Yes, as an opt-in frame 6 profile** (primer flanks; evaluate the `v10-oligo300` route first). The default profile keeps software-only random access. | Random access (CC-5) and the per-subsystem DNA pools (§5B) need chemical addressing; opt-in keeps the frozen default dense and compatible. |
| Q2 journal MAC | **Keyed MAC for encrypted volumes (release gate); optional keyed mode for unencrypted volumes; anchored counter against tail rollback in the defence profile only.** | Closes O-1 where it matters without taxing every write. |
| Q5 HEDGES/Schifra licence | **Internal measurement only**; results are published only after a licence review (external/legal step → founder). | Keeps CC-2 moving without a licence risk. |
| Q6 wait for external validation? | **No.** Ship with LR-5 marked pending if no outside party has run it. | External calendar time is unbounded. |
| Q7 support policy | **Draft:** security fixes 5 years after V15, bug fixes 2 years, 12 months' EOL notice; maintainer = the founder's company. | Placeholder for the founder to confirm (organisational). |
| Q8 adopt a winning published indel technique | **Yes, if it wins at equal overhead with CIs, before the M3 freeze.** | The freeze is permanent; the best method should be in it. |
| Q10 frontier scope | **In V15:** F8 tiering, F7 Kani (bounded), F6 soft decoding, AP-16/AP-17 (offline security, offline-only build), DA track, LR track. **Simulation-only studies:** F2 composite letters, F4 adaptive sampling, F3 enzymatic model. **Deferred:** F5 similarity search. Experimental profiles may exist outside the frozen core. | Software-only, ₹0, highest value to the DNA-only drive and the defence profile. |
| Q12 post-quantum | **Yes: algorithm identifiers in the frozen format (crypto agility); hybrid ML-KEM/ML-DSA (or SLH-DSA) implemented once a vetted library is chosen.** | Long-lived archives; must be decided before the M3 freeze. |
| Q13 archive-platform scope | **In V15:** AP-1 WORM/snapshots, AP-3 key escrow, AP-4 crypto agility, AP-5 provenance, AP-6 monitoring, AP-7 refresh planning, AP-8 cost planner, AP-14 partial retrieval, AP-16/17/18. **Later:** AP-2 multi-site (needs sites), AP-9 S3 gateway, AP-10 re-encoding, AP-11 plugin SDK (after FZ-2), AP-12 GPU (gated), AP-13 learned priors, AP-15 authentication study. | Fits the ₹0, DNA-only, defence-first direction. |

## Founder-only (not decided here)

| Q | status |
|---|---|
| Q3 wet-lab budget and go | **On hold (₹0).** Route: an academic partner lab; the software side (order export, lab interface) is ready. |
| Q4 hardware budget (Pi, SSD, sequencer) | **On hold (₹0).** Everything runs in simulation/emulation until a sponsor or partner exists. |
| Q9 DNA Data Storage Alliance / SNIA membership | **Founder decision** (external, may cost money). Spec alignment work proceeds from public documents. |
| Q11 patent-attorney review before publishing | **Founder + guardian decision.** No V15 method or spec is published before this is decided. |
| Q7 support-policy numbers | Draft above; the founder confirms. |

## M1 starts now

CC-1 (decode speed: the serial pass-1 bottleneck behind the missed 5x/3x targets) on branch `work/v15-cc1`, with the
V14 benchmark suite (`experiments/v14/bench/p6e-channels`) as the measuring stick and the P12/benchmark findings as
inputs (retry-rung early exit when pass 1 shows capacity exceeded; per-read bookkeeping cost).
