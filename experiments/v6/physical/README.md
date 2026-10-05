# Physical validation interface (V6 Phase 1, item 21)

**No DNA has been synthesised, stored or sequenced by VNX-DNA.** Nothing in this directory is a physical result. This is the
*interface* (record schemas, a validator and a template) for a possible future laboratory partnership (V7/V11 roadmap
candidates, not commitments). The only complete example here is a **SYNTHETIC SOFTWARE TEST**: a software round trip through
the SIMULATED illumina-like channel model.

## Contents

| Path | What |
|---|---|
| `schema/*.schema.json` | link to `src/vnxdna/physical/schemas/` (package data of `vnxdna.physical` since V6 Phase 7): JSON Schema (draft 2020-12): `record` (top level, evidence classification) referencing `synthesis`, `sample`, `storage`, `sequencing`, `decode`, `result`, `attestations`, plus `common` (SHA-256, date, commit formats) |
| `validate.py` | old path of `python -m vnxdna.physical`, kept working: `check <record.json>` (schema + cross-field rules) and `template [--out file]` |
| `examples/synthetic_software_test/` | a filled SYNTHETIC SOFTWARE TEST record with its strands, simulated reads, and decode report; regenerate with `examples/make_synthetic_example.py` |

## Use

```
python experiments/v6/physical/validate.py template --out run.json     # empty template: validates as INCOMPLETE
python experiments/v6/physical/validate.py check run.json              # exit 0 VALID, 2 INVALID, 3 INCOMPLETE
python experiments/v6/physical/validate.py check experiments/v6/physical/examples/synthetic_software_test/record.json
```

The validator has no third-party dependency. Tests: `vnx-lab test tests/v6/physical`.

## Evidence classes

`REAL PHYSICAL RESULT`, `SIMULATED RESULT`, `SYNTHETIC SOFTWARE TEST`, `PUBLIC-DATA-DERIVED` (needs a dataset accession,
the SHA-256 of every downloaded file and a DOI or `unpublished`). A record may carry `REAL PHYSICAL RESULT` only if synthesis
evidence (provider, order ID, date, SHA-256 of the ordered strand FASTA), sequencing evidence (provider, platform, run ID, date)
and a SHA-256 for every FASTQ file are all present, together with attestations. The other two classes must name
`none (simulation)` as provider and carry no order ID.

## Limits

- The validator checks structure and internal consistency. It cannot establish that a record describes a genuine experiment;
  that rests on the raw data and the signed attestations it points to.
- Checksums are verified against files only when a `path` is given and the file is reachable.
- The example's commit field is the repository HEAD at generation time and its `working_tree_dirty` flag is recorded as found.
- The example's dates are the day it was generated, not laboratory dates. Its Q30 and mean Q are computed from simulated quality strings.
