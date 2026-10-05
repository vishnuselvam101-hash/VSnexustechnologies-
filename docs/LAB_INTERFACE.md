# Laboratory interface

Status: V6 Phase 7. **Nothing here is physically validated.** No DNA has been synthesised, stored or sequenced by
VNX-DNA, no laboratory has used these files, and no vendor adapter exists (V11). The interface has been exercised in
software only, with the reference simulator standing in for the laboratory (SIMULATED).

```
digital archive (VNX4 container)
  → strands (vnx encode)
  → VNX Export Package          <id>.vnxexp/   strands.fasta, order.csv, manifest.json, SHA256SUMS
  → laboratory writer           synthesis, storage                       [physical; never done by VNX-DNA]
  → physical DNA
  → sequencing data             FASTQ from the sequencer                 [physical; never done by VNX-DNA]
  → VNX Import Package          <id>.vnximp/   reads/r1.fastq, manifest.json, SHA256SUMS
  → decoder (vnx decode)        → verified container, decode report
  → physical record             vnxdna.physical (record_version "1")
```

Code: `vnxdna.providers.packages` (`build_export_package`, `load_export_package`, `build_import_package`,
`load_import_package`), `vnxdna.providers.reference` (the simulator), `vnxdna.physical` (records). Schemas:
`src/vnxdna/core/schemas/export-package.schema.json`, `import-package.schema.json` (JSON Schema 2020-12;
`vnxdna.core.schema.validate(manifest)`), `src/vnxdna/physical/schemas/`.

## 1. VNX Export Package (`vnx.export-package/1`)

What a laboratory receives to synthesise one pool.

| File | Content |
|---|---|
| `strands.fasta` | every strand of every archive in the pool, archive after archive, names as the encoder wrote them |
| `order.csv` | generic `name,sequence` order sheet, one row per strand (vendor-specific formats belong to V11 adapters) |
| `manifest.json` | the machine-readable description below |
| `SHA256SUMS` | `sha256  name` for `manifest.json` and every listed file (`sha256sum -c SHA256SUMS` checks it) |

Manifest fields:

- `package_id`: SHA-256 of the canonical manifest (sorted keys, no whitespace) without this field; the directory is
  named `<package_id>.vnxexp`. Equal inputs give a byte-identical package (no timestamps, no paths).
- `created_by`: `software` (VNX-DNA version) and `spec` (specification version).
- `provider_target`: `name`, `version`, `interface` (`vnx.provider/1`).
- `evidence_class` and `statement`: what the package is, and that no DNA has been synthesised by VNX-DNA. The reference
  simulator writes `SIMULATED`; an export package can never be `REAL PHYSICAL RESULT` (it is an order, not a result).
- `vendor_constraints`: the provider capabilities the package was checked against: `max_strand_nt` (default 350),
  `min_strand_nt`, `alphabet` (ACGT), `max_pool_strands`, `gc_min_percent`, `gc_max_percent`, `max_homopolymer`,
  `primers_supported`.
- `archives[]`, one per archive: `archive_id`, `container_sha256`, `container_size`, `encrypted`, `codec`
  (`f4-sb<n>-<outer>`), `frame_version`, `superblock_version`, `strand_profile`, `redundancy_profile`, `layout` (P, r,
  marker period and length), `outer` (name, K, M, D, Mc, order), `pool_tag` (the frame tag), `primer_id` and `primers`
  (null in 6.x), `strand_count`, `strand_nt` (min, max), `strands_sha256` (the encoder's strand file), `first_strand`
  (index in `strands.fasta`). The container itself is not included.
- `checks[]`: `vendor-max-length`, `vendor-min-length`, `alphabet`, `constraints`, `strands-match-archive`,
  `pool-tag-unique`, each with its limit, value or configuration. A package exists only if every check passed: a failure
  raises the error and leaves nothing in the output directory (`VENDOR_MAX_LENGTH`, `ARCHIVE_TAG_COLLISION`,
  `CONSTRAINT_ERROR`, `CONFIGURATION_ERROR`; see `docs/INTEROPERABILITY.md` §3).
- `files[]`: `name`, `role` (`strands`, `order`; `ddsa-sector-zero` and `ddsa-sector-one` are reserved and never
  written, see `docs/DDSA_MAPPING.md`), `sha256`, `bytes`.

Laboratory side (not performed by VNX-DNA): synthesise the sequences of `order.csv`, keep the order ID and the SHA-256 of
the FASTA that was ordered (it is `files[strands].sha256`), store the pool, and sequence it.

## 2. VNX Import Package (`vnx.import-package/1`)

What the decoder receives: the reads plus the metadata of how they were obtained.

| File | Content |
|---|---|
| `reads/r1.fastq` (and `reads/r2.fastq` for paired reads) | the reads, uncompressed FASTQ |
| `manifest.json` | the machine-readable description below |
| `SHA256SUMS` | as for the export package |

Manifest fields: `package_id` (as above; directory `<package_id>.vnximp`), `export_package_id` (the order it answers, or
null), `provider` (name, version, interface), `evidence_class`, `statement`, `simulation`, `sequencing` (`platform`,
`instrument`, `run_id`, `run_date`, `read_length`, `layout` single/paired, `primers_trimmed`), `selection`
(`primer_id`), `dataset`, `files[]` (`name`, `role`, `sha256`, `bytes`, `reads`).

Class rules (checked on build and on every load):

| `evidence_class` | Requires | Forbids |
|---|---|---|
| `SIMULATED` | `simulation`: model, model version, model schema, model SHA-256, seed, coverage, simulator software | `dataset` |
| `SYNTHETIC SOFTWARE TEST` | — | `simulation`, `dataset` |
| `PUBLIC-DATA-DERIVED` | `dataset`: accession, the SHA-256 of each downloaded file, DOI or `unpublished` | `simulation` |
| `REAL PHYSICAL RESULT` | a provider other than the reference simulator; `sequencing.platform` and `sequencing.run_id` | `simulation`, `dataset` |

A package passing these rules is well formed; that does not show the data are genuine. A real result additionally needs
the physical record with attestations (§4).

A laboratory's FASTQ becomes an import package with
`build_import_package([fastq], out_dir, provider={...}, evidence_class=..., export_package_id=..., sequencing={...})`.
Loading (`load_import_package`, also done by `ReferenceSimulatorProvider.read`) re-checks the schema, the class rules,
the package ID, every file's SHA-256 and size, `SHA256SUMS`, and refuses file names outside `reads/`, links and
duplicates.

## 3. Decoding

`vnx decode <package>/reads/r1.fastq -o recovered.vnx` (or `sdk.decode`). The decoder does not trust the manifest: it
finds the layout from the reads (spec §3.10) and verifies every frame, the superblock and the container SHA-256 before
publishing anything. The manifest's `container_sha256` lets a laboratory partner confirm the result without having the
container.

## 4. Physical record

The run record (`vnxdna.physical`, `record_version "1"`, `docs/V6_PHYSICAL_VALIDATION_INTERFACE.md`) is where a physical
run would be documented: synthesis (order ID, ordered-FASTA SHA-256), storage, sequencing (run ID, FASTQ SHA-256s),
decode, result and signed attestations. `python -m vnxdna.physical check record.json` reports VALID, INVALID or
INCOMPLETE. Evidence classes: `REAL PHYSICAL RESULT`, `SIMULATED RESULT`, `SYNTHETIC SOFTWARE TEST` and, since V6
Phase 7, `PUBLIC-DATA-DERIVED` (requires a dataset accession, the SHA-256 of the downloaded files and a DOI or
`unpublished`; `docs/DNA_STORAGE_DATASET_REGISTRY.md`). The only complete record in the repository is a SYNTHETIC
SOFTWARE TEST. The automatic conversion of an import package plus its decode report into a record is not implemented
yet.

## 5. Not implemented

- Primer flanks and primer-based random access (spec §3.6, V7): `prepare` refuses primers.
- Vendor adapters and vendor order formats (V11). `order.csv` is generic.
- DDSA Sector Zero / Sector One strands (layouts to be aligned; `docs/DDSA_MAPPING.md`).
- Compressed FASTQ in import packages, BAM input.
- Any physical validation: none has occurred.
