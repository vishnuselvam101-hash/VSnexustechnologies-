# V6 physical validation interface

No DNA has been synthesised, stored or sequenced by VNX-DNA, and no physical validation is claimed by this document or by V6.
This document describes the record format that a future laboratory partnership (V7/V11 roadmap candidates, not commitments)
would use to report a physical run against VNX-DNA, and the checks applied to such a record. All channel results in the
repository are SIMULATED.

Files: package `vnxdna.physical` (`src/vnxdna/physical/`: `schemas/`, `validate.py`; run as `python -m vnxdna.physical`),
moved there from `experiments/v6/physical/` in V6 Phase 7. The old script `experiments/v6/physical/validate.py` still works,
and `experiments/v6/physical/schema` is a link to the package schemas. README and example stay in `experiments/v6/physical/`.
Tests: `tests/v6/physical/` (unchanged) and `tests/physical/` (the package and the PUBLIC-DATA-DERIVED class).

## Workflow

1. **Order.** Encode an archive with a recorded VNX version/commit, profile and options, and write the strand FASTA.
   Record its SHA-256 (`synthesis.ordered_fasta.sha256`) and the VNX source in `synthesis.vnx_source`. Place the order with a
   provider and record provider, order ID, platform, pool/library/strand IDs, strand count and length, QC summary.
2. **Synthesis.** Record synthesis and delivery dates and the provider QC.
3. **Storage.** Record temperature, relative humidity, medium, encapsulation, start/end dates and duration, and the chain of
   custody in `sample`.
4. **Sequencing.** Record provider, platform, instrument, chemistry/kit, run ID, read length, single/paired layout, every FASTQ
   (file name, SHA-256, size, read count), read counts, %Q30, mean Q and any error estimates with their method.
5. **Decode.** Run the VNX decoder on the FASTQ. Record version/commit, decoder configuration, backend, workers, the command,
   and the SHA-256 of the decode report JSON.
6. **Verify.** Compare the SHA-256 of the recovered archive with the expected one. Record `SUCCESS`, `PARTIAL` or `FAILURE` and
   the number of files recovered.
7. **Record.** Assemble the record, add attestations (who performed each step, who signed off, where the raw data are), run
   `validate.py check`, and keep the raw data at the location named in the record.

## Record structure

`schemas/record.schema.json` references one schema per section.

| Section | Fields |
|---|---|
| top level | `record_version` ("1"), `record_id`, `description`, `evidence_classification`, `statement`, `public_data` (optional; `PUBLIC-DATA-DERIVED` only) |
| `public_data` | `registry_id`, `accession`, `doi` (a DOI or `unpublished`), `licence`, `files` (file name, URL, path, SHA-256, size), `downloaded_on` |
| `synthesis` | `provider_name`, `order_id`, `synthesis_platform`, `oligo_pool_id`, `library_ids`, `strand_ids`, `strand_count`, `strand_length_nt`, `synthesis_date`, `delivery_date`, `provider_qc`, `ordered_fasta` (name, SHA-256, strand count, path), `vnx_source` (version, commit, dirty flag, profile, encoder options, command) |
| `sample` | `sample_id`, `library_id`, `description`, `chain_of_custody` (step, from, to, date, performed_by) |
| `storage` | `temperature_c`, `relative_humidity_percent`, `medium`, `encapsulation`, `start_date`, `end_date`, `duration_days`, `notes` |
| `sequencing` | `provider_name`, `platform`, `instrument`, `chemistry_kit`, `run_id`, `run_date`, `read_length`, `layout`, `fastq_files` (file name, SHA-256, size, read count, role, optional path), `read_counts`, `quality` (%Q30, mean Q, error-rate estimate and method) |
| `decode` | `vnx_version`, `commit`, `working_tree_dirty`, `decoder_config`, `backend`, `workers`, `command`, `report_file`, `report_sha256`, `decode_status` |
| `result` | `recovered_sha256`, `expected_sha256`, `verification_result`, `files_expected`, `files_recovered`, `notes` |
| `attestations` | `synthesis`, `storage`, `sequencing`, `decode` (performed_by, organisation, date), `signed_off_by`, `raw_data_location` |

Fields are nullable so that an empty template is schema-valid. A field the schema lists as `required` counts as missing while it is
null or an empty list; a record with missing fields and no violations is reported `INCOMPLETE`.

## Evidence classes

| Class | Meaning | Provider fields |
|---|---|---|
| `REAL PHYSICAL RESULT` | data from a physical synthesis, storage and sequencing run | named provider, order ID |
| `SIMULATED RESULT` | outcome of a software channel simulation | `none (simulation)` |
| `SYNTHETIC SOFTWARE TEST` | a software test exercising the pipeline or this interface | `none (simulation)` |
| `PUBLIC-DATA-DERIVED` | derived from a public dataset produced by another group (`docs/DNA_STORAGE_DATASET_REGISTRY.md`) | as published by that group |

A record may carry `REAL PHYSICAL RESULT` only if it has all of: synthesis provider, order ID, synthesis date and ordered-FASTA
SHA-256; sequencing provider, platform, run ID, run date; at least one FASTQ file and a SHA-256 for every FASTQ file; synthesis
and sequencing attestations, sign-off and raw-data location. Placeholders such as "TBD" do not count.

A record may carry `PUBLIC-DATA-DERIVED` (added in V6 Phase 7, research gate item 6 of the dataset registry) only with
`public_data`: a dataset accession, at least one downloaded file with the SHA-256 of its bytes as downloaded, and the paper's
DOI or the word `unpublished`. When a file's `path` is reachable its SHA-256 is re-checked. Simulated reads from a model
fitted to public data stay `SIMULATED RESULT`; only results computed from the public data themselves are
`PUBLIC-DATA-DERIVED`. No such record exists yet.

## Validator (`vnxdna.physical.validate`)

`check` returns `VALID` (exit 0), `INVALID` (2) or `INCOMPLETE` (3). Rules beyond the schema:

- checksums: lowercase 64-hex SHA-256; commits 40-hex.
- dates: real calendar dates, ordered synthesis, delivery, storage start, storage end, sequencing run; `duration_days` equals end minus start.
- FASTQ: when `path` is given (absolute or relative to the record) the file must exist and match SHA-256 and size; plain `.fastq` read count is re-counted; the read-count total equals the sum over files; layout matches the number of files.
- decode comparison: `SUCCESS` requires recovered equal to expected SHA-256 and all files recovered; equal hashes cannot be `PARTIAL` or `FAILURE`; `PARTIAL` requires fewer files recovered than expected.
- the decode report SHA-256 is re-checked when the report file is next to the record; `sample.library_id` must be among `synthesis.library_ids`.
- classification rules as above.

Limits: the validator checks structure and internal consistency only. It cannot show that a record describes a genuine
experiment, and checksum verification covers only files that are reachable. The schema check is a small built-in implementation
of the keywords the schemas use; a test cross-checks it against the `jsonschema` package (part of the `[dev]` extra;
without it that one test is skipped).

## Example

`experiments/v6/physical/examples/synthetic_software_test/record.json` is classified `SYNTHETIC SOFTWARE TEST`. It was produced
by `make_synthetic_example.py`: a 200-byte random payload, profile `v4-balanced`, 66 strands of 313 nt, the SIMULATED
`illumina-like` model with seed 7, 679 reads, decoded with the V4 decoder. Its checksums are copied from the files in that
directory; recovered and expected SHA-256 are equal and the stated result is `SUCCESS`. Provider fields are
`none (simulation)`; storage temperature and humidity are null because no storage took place. The recorded commit is the repository
HEAD at generation time with the working-tree dirty flag as found (true). This example says nothing about behaviour on
physical DNA.
