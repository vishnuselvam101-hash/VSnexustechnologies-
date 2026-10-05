# Interoperability

Status: V6 Phase 7. **Nothing in this document is physically validated.**
No DNA has been synthesised, stored or sequenced by VNX-DNA. The provider interface has been tested in software only,
against the reference simulator, and every channel result is SIMULATED.

Labels used here: **VERIFIED** = checked by a committed software test; **SIMULATED** = produced by a software channel
model; **THEORETICAL** = specified, not implemented; **PHYSICAL** = from real DNA (nothing in VNX-DNA is).

Normative source: `docs/spec/VNX-DNA-SPEC-V6.md` §8 (standards), §9 (provider interface), §3.9 (pool composition),
§10 (error codes). The laboratory file formats are in `docs/LAB_INTERFACE.md`; the DNA Data Storage Alliance mapping is
in `docs/DDSA_MAPPING.md`.

## 1. Status

| Item | Interface specified | Interface implemented | Provider integration tested |
|---|---|---|---|
| `DNAWriter`, `DNAReader`, `DNAProvider` protocols | yes | yes (`vnxdna.providers.base`) | software only |
| `ReferenceSimulatorProvider` | yes | yes (`vnxdna.providers.reference`) | software only, SIMULATED |
| Export and import packages (`vnx.export-package/1`, `vnx.import-package/1`) | yes | yes (`vnxdna.providers.packages`) | software only |
| Physical record schema and validator | yes | yes (`vnxdna.physical`) | no physical run has occurred |
| Any synthesis or sequencing vendor adapter | no | no | no (V11) |
| DDSA Sector Zero / Sector One | mapping only | no; layouts to be aligned (V9 / DDSA membership) | no |
| SNIA Swordfish DNA resources | no | no | no |

The same table is `vnxdna.providers.STATUS`, and a test reads it. "Interface implemented" means that the code exists and
its tests pass. "Provider integration tested: software only" means that no external provider, laboratory or instrument
has been connected to it.

## 2. Provider interface (spec §9.2)

```python
from vnxdna import sdk
from vnxdna.providers import ArchiveRef, ReferenceSimulatorProvider, SequencingRequest

enc = sdk.encode("a.vnx", "strands.fasta", dna=sdk.DNAOptions(profile="v4-balanced"))
ref = ArchiveRef.from_encode("a.vnx", enc.body)                    # archive ID, container SHA-256, codec, geometry
p = ReferenceSimulatorProvider("lab", models_dir="experiments/v6/channel/models")
exp = p.prepare("strands.fasta", archive=ref, profile="v4-balanced")   # export package; all checks
pool = p.write(exp).pool                                           # "tube": a directory
imp = p.retrieve(pool, sequencing=SequencingRequest("illumina-like", seed=7, coverage=10))   # import package, SIMULATED
n = sum(batch.count for batch in p.read(imp))                      # verified reads, streamed to decode stage D0
res = sdk.decode(imp.read_files[0], "recovered.vnx")               # SUCCESS / PARTIAL / FAILURE
```

| Operation | Protocol | Reference simulator |
|---|---|---|
| `prepare(strands, *, archive, profile, primers=None, pool_tag=None)` | `DNAWriter` | builds an export package; pure and deterministic; a failed check publishes nothing. `prepare_pool([(strands, archive), …])` builds a pool |
| `write(package)` | `DNAWriter` | re-verifies the package and copies its strands into `<dir>/pools/<pool_id>/` (the stand-in for a tube) with their SHA-256 |
| `retrieve(pool, *, selection=None, sequencing)` | `DNAReader` | re-checks the tube's SHA-256, applies a named channel model (strand loss, then coverage, per-read errors and qualities) with an explicit seed and coverage, writes an import package |
| `read(package)` | `DNAReader` | re-verifies the import package, then streams its reads as `ReadBatch` objects |
| `capabilities()`, `list()`, `search(archive_id=, pool_tag=, primer_id=)`, `close()` | `DNAProvider` | vendor limits; the pools in the provider directory; filters; after `close` every call fails with `PROVIDER_ERROR` |

`ReferenceSimulatorProvider` is the only provider. There are no stubs or imitations of real laboratory providers.

Guarantees (VERIFIED by `tests/providers/test_providers.py`):

- **Round trip.** `prepare → write → retrieve → read → decode` gives SUCCESS and the original container SHA-256 for
  `v4-balanced` (superblock 1, `illumina-like` model) and for the V6 stripe profile `maximum-recovery` (v4-archival layout,
  superblock 2, column parity 2 per stripe of 8, interleaved order; `dropout-5` model, which removes strands). SIMULATED.
- **Determinism.** With 1 and with 4 workers (encode, simulation and decode) the export manifests, import manifests, read
  files and recovered containers are byte-identical. An import package is a function of (strand file SHA-256,
  model@version, seed, coverage, selection).
- **Labels.** Every export package, import package and write receipt the reference simulator produces says `SIMULATED`
  and carries the statement that no DNA was synthesised, stored or sequenced. Import packages record the model name,
  version, schema, SHA-256 and seed.
- **No operation changes archive bytes.** The container is never copied into a package: only its SHA-256 and size are
  recorded, because the container may be confidential.

## 3. Checks before anything is published

| Check | Rule | Failure (exit) |
|---|---|---|
| `vendor-max-length` | longest strand ≤ `ProviderCapabilities.max_strand_nt` (default **350 nt**, configurable) | `VENDOR_MAX_LENGTH` (7). VERIFIED: a 351-nt strand with a 350-nt limit is refused; 350 nt passes |
| `vendor-min-length`, pool size | shortest strand ≥ `min_strand_nt`; strand count ≤ `max_pool_strands` | `CONFIGURATION_ERROR` (7) |
| `alphabet`, `constraints` | ACGT only; the archive's GC and homopolymer constraints; optional vendor GC / homopolymer limits | `CONSTRAINT_ERROR` (7) |
| `strands-match-archive` | the strand file is the one the encoder reported (SHA-256, count, archive tag in the labels) | `CONFIGURATION_ERROR` (7) |
| `pool-tag-unique` | **pool-composition rule** (spec §3.9): two archives with equal frame tags and equal primer IDs are refused unless their container SHA-256s are equal (then the archive is included once) | `ARCHIVE_TAG_COLLISION` (7). VERIFIED with two different archives that share one derived tag |

Not implemented, and refused rather than ignored: primer flanks (`primers`, spec §3.6, V7) and assigned pool tags for
frame 4 (frame-4 tags are derived from the archive ID; assigned tags need frame 6, V7).

## 4. Errors

- Capability or composition violations in `prepare` are `CONFIGURATION_ERROR` (exit 7) with codes `VENDOR_MAX_LENGTH`,
  `ARCHIVE_TAG_COLLISION`, `CONSTRAINT_ERROR` or `CONFIGURATION_ERROR`.
- A provider failure (unknown pool, a stored strand file that no longer matches its SHA-256, a closed provider, an empty
  selection) is **`PROVIDER_ERROR`, exit 10** (`VNXProviderError`; founder decision 3). It is never reported as
  `INSUFFICIENT_REDUNDANCY`: a failed retrieval says nothing about the archive's redundancy. The CLI error handler maps
  it to exit 10 (VERIFIED).
- A damaged or edited package is `INTEGRITY_ERROR` (exit 1, a file or the package ID does not match) or `FORMAT_ERROR`
  (exit 3, unsafe file name, schema violation). A newer manifest schema is `SCHEMA_UNSUPPORTED` (exit 6).

## 5. Channel models

`retrieve` reads the existing model files (`experiments/v6/channel/models/*.json`: name, version, classification
SIMULATED, `loss`, `channel`), which carry no `schema` key and are therefore `vnx.channel-model/0` (spec §4.5). A model
can also be given as a file path or as a JSON document. A model with another schema is refused with
`SCHEMA_UNSUPPORTED`; reading `vnx.channel-model/1` follows the V6 Phase 3 simulation work. The models are stress
settings, not fitted to any platform.

## 6. Standards

- **DNA Data Storage Alliance** Sector Zero / Sector One: mapping table only, layouts to be aligned; no bytes are
  produced (`docs/DDSA_MAPPING.md`).
- **SNIA Swordfish** DNA working draft: the operations above map onto its "DNA process" resources; names and schemas to
  be aligned; nothing defined in 6.x.
- **JPEG DNA** (ISO/IEC 25508-1): carried as an ordinary file in a VNX archive; no conformance claim.

## 7. Limits

- Software only. The reference simulator models synthesis, storage and sequencing as a channel; it is not evidence of how
  real synthesis or sequencing would behave.
- One read layout (single-end FASTQ, uncompressed). Random access by primer (`Selection.primer_id`) is wired through the
  manifests but no archive carries a primer ID before V7.
- The conversion of an import package plus its decode report into a physical record (spec §9.3, last bullet) is not
  implemented yet; records are written by hand and checked with `python -m vnxdna.physical check`.
