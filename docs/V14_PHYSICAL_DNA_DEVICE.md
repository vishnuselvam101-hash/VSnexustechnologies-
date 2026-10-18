# V14 physical DNA device (design)

Phase 11 design document (master directive §17, §17.1, §17.2; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P11). It
designs a DNA storage device from researched, real parts and processes, on top of the P7 physical-DNA lab interface
`vnx.dna-lab/1` ([V14_MEDIA_ADAPTERS.md](V14_MEDIA_ADAPTERS.md) §5) and the external controller of
[V14_EXTERNAL_CONTROLLER.md](V14_EXTERNAL_CONTROLLER.md).

**Status: design only. This device does not exist. No part was bought, no DNA was synthesised, stored, retrieved or
sequenced, and no adapter has talked to any instrument or provider.** VNX-DNA's evidence reaches categories A–D
(codec, software device, electronic backend, simulated channel); E (real DNA operations) and F (physical device) are
empty. No price, part number or capability below is ours: each is quoted from the source in §8 (tag `[Pn]`), or
marked "not verified". Prices are list prices as published on the vendor page on 2026-10-18 (USD), not quotes.

## 1. What is, and is not, possible today

* No currently sold benchtop writer we found writes data-scale oligo pools: DNA Script SYNTAX makes 96 oligos of
  15–120 nt per run [P1]; Kilobaser is a single-column desktop synthesizer (recommended up to 50 nt per a third-party
  guide) [P2]; Evonetix's chip platform has no commercial launch we could find [P4]. Data-scale writing is
  **outsourced** to oligo-pool vendors (Twist up to 300 nt; IDT oPools 40–350 nt; GenScript 20–170 nt) [P5][P6][P7].
* The one published end-to-end automated system wrote and read the 5-byte message "HELLO" with a write-to-read
  latency of about 21 h (synthesis ≈ 8.4 h, cleave/deprotect 12 h, preparation 30 min, nanopore read + decode 6 min),
  on a custom synthesizer and a MinION; the authors put the device at about $10k and call it "not yet commercially
  viable" [P20].
* Therefore the "device" is a **hybrid**: an electronic controller that is always online and holds the electronic
  region, plus an orchestrated lab workflow (partly external services) for the DNA tier. An ordinary disk controller
  cannot drive synthesis or sequencing (directive §17); the lab workflow is reached only through `vnx.dna-lab/1`.

## 2. Architecture

```
 host ── USB MSC (BOT) ──► controller board (Pi 4 class, C1)                         ELECTRONIC, always online
                            vnxd: VNX-IO, Volume, Cache, VNX-RAM, fallback, sealing
                            electronic backend (SSD): boot LUN, journal, cache, staged archives
                            media adapter: dna-lab (real provider adapter: NOT BUILT)
                                   │  vnx.dna-lab/1 (jobs, samples, deliveries; network, not USB)
                                   ▼
          ┌──────────────── lab orchestration host (custom software) ─────────────────┐
          │ synthesis orders ─► oligo-pool vendor (external service)  │ basecalling   │
          │ pool catalogue (location, preservation, aliquots)         │ (GPU host,    │
          │ retrieval protocol ─► liquid handler + thermocycler       │  Dorado)      │
          │ sequencing run ─► MinION (MinKNOW API)                     │               │
          └───────────────────────────────────────────────────────────┴───────────────┘
                     physical tier: dried / encapsulated pools in a labelled store (LAB)
```

Data path for an archive write: host writes to the electronic LUN (durable on SSD, P8 rules) → archive job (management
command) seals and encodes extents to strands (codec, existing) → `submit_synthesis` = an oligo-pool order file for the
vendor (the order is placed by a person through the vendor's portal; we model no vendor API) → pool received,
quantified, stored, `PoolRecord` filled by the operator → optional verification read. Read: `submit_retrieval`
(PCR with the extent's primer pair, or whole pool) → library prep → `submit_sequencing` → POD5 → Dorado → FASTQ →
`LabMedia::get` decodes and checks SHA-256 → staged to the electronic backend → exported read-only (controller §5.10).

## 3. Components and classification

Classes (P11 acceptance): **buildable-now** = real parts or existing V14 software, assembled without new engineering
beyond configuration; **custom** = needs new engineering (software, protocols, hardware integration) or a
research prototype; **lab-only** = needs a wet lab, trained operators, consumables or an external service; it cannot
be an automatic part of a device today. Mode (directive §17): automatic / lab equipment / external service / open
research.

| # | component (§17 split) | researched part or process | class | mode | sources |
|---|---|---|---|---|---|
| 1 | host | any computer with a USB host port and a stock `usb-storage` driver | buildable-now | automatic | S8 (controller doc) |
| 2 | host-facing controller | Raspberry Pi 4 Model B as USB MSC BOT gadget (stage C1) | buildable-now | automatic | controller doc §3–§4 |
| 3 | electronic working memory + electronic region | board RAM (VNX-RAM budget) + SSD for journal, cache, boot LUN, staged archives | buildable-now | automatic | boot doc §7.2; directive §17.1 |
| 4 | firmware / OS | Raspberry Pi OS or Linux + signed `boot.img` (optional), A/B tryboot | buildable-now (secure boot is an irreversible option) | automatic | boot doc §3–§6 |
| 5 | VNX software | `vnxd` + Python codec/sealing workers (exists, evidence A–D) | buildable-now | automatic | V14_MEDIA_ADAPTERS, V14_DEVICE_INTERFACE |
| 6 | codec engine on the controller CPU | ARM64 build of the codec and `vnxd` | custom (Pi acceptance is P10 and needs real hardware) | automatic | master plan P10 |
| 7 | lab adapter | a real provider adapter for `vnx.dna-lab/1` + media contract v2 with a physical-DNA durability class | custom | automatic (software) | V14_MEDIA_ADAPTERS §5 |
| 8 | synthesis, data scale | outsourced oligo pools: Twist (≤300 nt, error "up to 1:3000"), IDT oPools (40–350 nt), GenScript (20–170 nt, 3–5 business days) | lab-only | external service | P5, P6, P7 |
| 9 | synthesis, on site | DNA Script SYNTAX STX-200: 96 oligos/run, 15–120 nt, ≈0.35 % average error per base, ≈21 h for 96×120 nt; no programmatic control documented | lab-only | lab equipment | P1 |
| 10 | synthesis, on site (small) | Kilobaser One-XT desktop synthesizer (vendor: "in less than 2h"; one oligo per run per third-party guide) | lab-only | lab equipment | P2 |
| 11 | synthesis, automated in-device | custom phosphoramidite synthesizer as in Takahashi et al. 2019 | custom | open research | P20 |
| 12 | gene-scale synthesis services (not used for data) | Telesis Bio BioXp (gene assembly kits), Ansa (up to 900 bp direct, 50 kb clonal) | lab-only | external service | P3, P8 |
| 13 | preservation, standard | dried pools in labelled tubes, location/condition recorded by the lab (`PoolRecord`) | lab-only | lab equipment | V14_MEDIA_ADAPTERS §5 |
| 14 | preservation, encapsulated | Imagene DNAshell (ambient storage of dried DNA by encapsulation; capacity and lifetime not stated) | lab-only | external product | P15 |
| 15 | preservation, silica | silica encapsulation (Grass et al. 2015) | lab-only | open research / lab process | P17 |
| 16 | preservation, managed | Biomemory (DNA Card with 50/100/150-year retention offers; "DNA Shield" service, early access; Catalog assets acquired 2026-03) | lab-only | external service (availability not verified) | P16 |
| 17 | archive management | pool catalogue, aliquot accounting, lifecycle (`PoolRecord`, `PoolLifecycle`; exists in the simulator) | buildable-now (software) | automatic | V14_MEDIA_ADAPTERS §5 |
| 18 | archive self-description | SNIA *DNA Data Storage Sector Zero* v1.0 (vendor + codec identity), Sector One (metadata) | custom (not implemented) | automatic (software) | P21 |
| 19 | retrieval, random access | PCR with per-file primers (Organick et al. 2018: 35 files, >200 MB, each recovered individually) | lab-only | lab equipment | P19 |
| 20 | retrieval automation | Opentrons Flex (from $26,400; Thermocycler, Magnetic Block, Heater-Shaker, Temperature modules) or OT-2 (from $15,950), Python Protocol API; HTTP robot server | custom (protocols to write and validate) on lab-only hardware | lab equipment | P11, P12, P13, P14 |
| 21 | retrieval, digital microfluidics | dehydrated spots + DMF retrieval (Newman et al. 2019, custom OpenDrop-based board); PurpleDrop open DMF platform | custom | open research | P18, P22 |
| 22 | library preparation | nanopore library kit (kit not selected in this phase) | lab-only | lab equipment | not researched |
| 23 | sequencing | Oxford Nanopore MinION Mk1D (USB-C to a computer; $3,150 device + support; Mk1D Pack $5,150 with 5 flow cells; R10.4.1 flow cells; 10–35 °C) | lab-only (consumables, wet sample) | lab equipment | P9 |
| 24 | sequencing control | MinKNOW API (gRPC/protobuf; start/stop protocols, acquisition, device info) | custom (integration) | automatic (software) | P10 |
| 25 | sequencing, alternative | Illumina MiSeq i100 (≈4–24 h, up to 2×500 bp; no API documented); iSeq 100 ordering closed 2025-09-30 | lab-only | lab equipment / external service | P23, P24 |
| 26 | basecalling | Dorado (POD5 in; NVIDIA GPU on Linux/Windows x86_64, Jetson Orin/Thor, DGX Spark, Apple Silicon; CPU-only not stated) on a separate GPU host | buildable-now | automatic | P25 |
| 27 | ECC / reconstruction / verification | VNX-DNA codec (RS inner/outer, indel recovery), `LabMedia::get` SHA-256 check | buildable-now (software exists; no real-read validation: evidence E empty) | automatic | V14_MEDIA_ADAPTERS |
| 28 | power / thermal / fault | controller UPS/hold-up (not selected), instrument operating ranges (MinION 10–35 °C), equipment fault mapping (`EquipmentError`) | custom | automatic + lab | P9; V14_MEDIA_ADAPTERS §5 |
| 29 | enclosure / serviceability | controller enclosure; lab bench layout; sample store | custom | lab | — (not researched) |

**Counts (29 rows): buildable-now 8** (rows 1, 2, 3, 4, 5, 17, 26, 27); **custom 9** (rows 6, 7, 11, 18, 20, 21,
24, 28, 29); **lab-only 12** (rows 8, 9, 10, 12, 13, 14, 15, 16, 19, 22, 23, 25). Row 20 is custom software on lab-only
hardware and is counted as custom. Every wet step (synthesis, preservation, retrieval, library prep, sequencing) is
lab-only: none of them can be an automatic part of a device today.

Published prices found (no others are quoted): MinION Mk1D $3,150; Mk1D Pack $5,150; Opentrons Flex from $26,400
(Flex NGS Workstation from $55,100); OT-2 from $15,950 [P9][P13][P14]. Twist, IDT, GenScript, DNA Script, Kilobaser,
Illumina, Imagene and Biomemory publish no price on the pages read. A BOM total is therefore **not** given.

## 4. Mapping onto `vnx.dna-lab/1`

| interface (P7) | real counterpart | gap |
|---|---|---|
| `LabCapabilities` (strand length, alphabet, order size) | vendor limits: Twist ≤300 nt, IDT 40–350, GenScript 20–170 [P5–P7]; SYNTAX 15–120 [P1] | per-provider capability files; codec strand length must fit the provider (current VNX strands are within these ranges — to be checked per profile) |
| `submit_synthesis` | an order file uploaded through the vendor portal by a person | no vendor API modelled; status updates entered by the operator |
| `PoolRecord` / `PoolLifecycle` | lab freezer/store records; DNAshell or silica as `preservation.method` | operator entry; no sensor |
| `submit_retrieval` (`PrimerPair`) | PCR random access [P19], automated by an Opentrons protocol [P11] | protocol written + wet-validated (lab) |
| `submit_sequencing` + `JobState` | MinKNOW run states via the MinKNOW API [P10] | a gRPC client in the lab host (custom) |
| `read_data` (`pod5`) | MinION output POD5 → Dorado basecalling [P25] → FASTQ | `LabMedia` refuses `pod5` today; a basecalling step on the GPU host turns it into `fastq` |
| `EquipmentError` | MinKNOW stop reasons (device error, temperature, disk space) | mapping table per instrument |
| `durability` | — | **media contract v2** with a physical-DNA class; `LabMedia` refuses `simulated: false` until then |

## 5. Electronic metadata region (directive §17.1)

Kept on the controller's SSD, never on DNA: volume journal, extent SHA-256s, backend binding marker, sealing key
check, pool catalogue, job ids outstanding at the lab (to reconcile at start, P7 §6), boot LUN. In DNA, per archive:
the sealed extents, plus a self-describing header (Sector Zero/One style [P21], not implemented) so that the archive
can be decoded without the controller. Consistency rule: an archive is "in DNA" only after a verification read decoded
it to the recorded SHA-256; until then management reports it as `ordered`/`stored`, never as durable. Security: data is
sealed before it leaves the controller, so vendors and labs see ciphertext (V14_SECURITY_MODEL §1); the key is never
in DNA. If a host ever boots from the device, the boot data came from the electronic region, and documents say so.

## 6. Constraints that shape the design

* **Latency**: days for outsourced synthesis (GenScript 3–5 business days [P7]); ≈21 h per synthesis run on SYNTAX
  for 120-mers [P1]; ≈4–24 h per MiSeq i100 run [P24]; the only automated end-to-end precedent took ≈21 h for 5 bytes
  [P20]. The DNA tier is archival; it never serves block reads synchronously (controller §5.10).
* **Error profile**: synthesis errors (SYNTAX ≈0.35 %/base average [P1]; Twist "up to 1:3000" [P5]; IDT "<1 in 2000",
  snippet [P6]) and nanopore indels are what the VNX codec is built for in simulation; no real-read validation exists.
* **Consumables**: flow cells and reagents bound how many reads a pool allows (`aliquots_left`).
* **Biosafety/biosecurity**: synthesis vendors screen orders; codec output must avoid flagged sequences (not
  researched in this phase).

## 7. Path to a first physical test (proposal, needs founder decision)

1. External-service pilot (lab-only): one small archive as an oligo pool from a vendor; sequencing as a service or on a
   MinION in a partner lab; decode the delivered FASTQ with the existing codec → first category E result.
2. Lab host software (custom): MinKNOW client, Dorado step, operator UI for orders and pool records; media contract
   v2.
3. Controller C1 assembled (buildable-now) with the boot volume and an electronic data LUN; DNA archive jobs from
   management.
4. Only after 1–3 work end to end and are reported with evidence may a document call the device "built", and only for
   what was actually shown.

## 8. Sources

Read 2026-10-18. F = fetched; S = search snippet only.

| tag | source | supports | read |
|---|---|---|---|
| P1 | DNA Script, SYNTAX System product information sheet: https://www.dnascript.com/wp-content/uploads/2026/05/CUS-18-PIS-US-DIGITAL.pdf | STX-200; 96 oligos/run, 15–120 nt; ≥200 pmol; 0.35 % average error/base; ≈8/13/21 h for 30/60/120 nt; no API stated; no price | F |
| P2 | Kilobaser: https://kilobaser.com/ ; Excedr buyer's guide: https://www.excedr.com/blog/featured-manufacturer-kilobaser ; distributor: https://www.govsci.com/product-detail/Kilobaser/KB1CASTD/EA/ | "in less than 2h"; one oligo per run, ≤50 nt recommended; cartridge ≈150 bases | F (vendor) / S (others) |
| P3 | Telesis Bio: https://telesisbio.com/ ; SEC 8-K exhibit (Nasdaq delisting 2024): https://www.sec.gov/Archives/edgar/data/1850079/000095017024105164/tbio-ex99_1.htm | BioXp gene assembly; company status | F / S |
| P4 | BioSpace, Evonetix platform at Imperial College: https://www.biospace.com/evonetix-places-first-dna-synthesis-development-platform-at-imperial-college-london | development platform; no launch found (vendor site unreachable) | S |
| P5 | Twist Bioscience, oligo pools: https://www.twistbioscience.com/products/oligopools | up to 300 nt; error up to 1:3000; uniformity | F |
| P6 | IDT oPools: https://eu.idtdna.com/pages/products/custom-dna-rna/dna-oligos/custom-dna-oligos/opools-oligo-pools | 40–350 nt; pmol scales; "<1 in 2000" | S (page redirect loop) |
| P7 | GenScript oligo pools: https://www.genscript.com/oligo-pools.html | 20–170 nt; up to 8M oligos per chip; 3–5 business days | F |
| P8 | Ansa Biotechnologies: https://www.ansabio.com/ | direct synthesis up to 900 bp; 50 kb clonal | F |
| P9 | Oxford Nanopore, MinION: https://nanoporetech.com/products/sequence/minion ; Flongle spec: https://nanoporetech.com/document/requirements/flongle-spec | Mk1D USB-C, R10.4.1, 10–35 °C, prices $3,150 / $5,150; Flongle compatibility list does not include Mk1D | F |
| P10 | Oxford Nanopore, `minknow_api`: https://github.com/nanoporetech/minknow_api | gRPC API covering the UI's functions; multi-position start from MinKNOW 6.3 | F |
| P11 | Opentrons Python Protocol API v2: https://docs.opentrons.com/v2/ | Flex and OT-2 control; modules | F |
| P12 | Opentrons robot server: https://github.com/Opentrons/opentrons/tree/edge/robot-server | HTTP API "endpoints for executing protocols, controlling the hardware" (port/version details unverified) | F |
| P13 | Opentrons Flex: https://opentrons.com/products/flex | from $26,400; NGS Workstation from $55,100; modules; not IVD/GMP | F |
| P14 | Opentrons OT-2: https://opentrons.com/products/ot-2-robot | from $15,950; modules | F |
| P15 | Imagene: https://www.imagene.eu/ | DNAshell encapsulation for ambient storage of dried DNA | F |
| P16 | Biomemory: https://biomemory.com/ ; StorageNewsletter 2026-03-10: https://www.storagenewsletter.com/2026/03/10/biomemory-acquires-the-assets-of-catalog-boosting-its-ambitions-in-dna-data-storage-and-computing/ | DNA Shield service, early access; DNA Card retention offers; Catalog assets acquired | F |
| P17 | Grass et al., Angew. Chem. Int. Ed. 54(8):2552–2555 (2015), doi:10.1002/anie.201411378 (record: https://mediatum.ub.tum.de/1597107) | silica encapsulation of DNA with error-correcting codes | S |
| P18 | Newman et al., Nat Commun 10:1706 (2019), doi:10.1038/s41467-019-09517-y: https://pmc.ncbi.nlm.nih.gov/articles/PMC6461645/ | dehydrated spots, DMF retrieval (≈77–79 % mean recovery), custom OpenDrop-based board | F |
| P19 | Organick et al., Nat Biotechnol 36:242–248 (2018), doi:10.1038/nbt.4079: https://www.microsoft.com/en-us/research/publication/random-access-in-large-scale-dna-data-storage/ | PCR random access, 35 files, >200 MB | S |
| P20 | Takahashi et al., Sci Rep 9:4998 (2019), doi:10.1038/s41598-019-41228-8: https://pmc.ncbi.nlm.nih.gov/articles/PMC6428863 | end-to-end automation, "HELLO", ≈21 h, ≈$10k device, "not yet commercially viable" | F |
| P21 | SNIA, DNA Data Storage Sector Zero v1.0: https://snia.org/standards/technology-standards-software/standards-portfolio/dna-data-storage-sector-zero ; 2024 review/2025 plans: https://snia.org/sites/default/files/Preview/2025/DNA-Data-Storage-2024-Review-and-2025-Plans.pdf | Sector Zero (vendor/codec identity), Sector One (metadata); no device-level spec found | S (403) |
| P22 | PurpleDrop documentation: https://purpledrop.readthedocs.io/ | open DMF platform (firmware, Python driver); research status | S |
| P23 | Illumina iSeq 100: https://www.illumina.com/systems/sequencing-platforms/iseq.html | ordering closed 2025-09-30; support to 2029-12-31 | F |
| P24 | Illumina MiSeq i100: https://www.illumina.com/systems/sequencing-platforms/miseq-i100.html | ≈4–24 h; up to 2×500 bp; no API documented | F |
| P25 | Oxford Nanopore, Dorado: https://github.com/nanoporetech/dorado | POD5 input; supported GPU/ARM platforms; CPU-only not stated | F |
