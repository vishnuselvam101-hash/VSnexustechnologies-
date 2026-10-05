"""``vnxdna.physical``: the physical-validation record schemas and their validator (V6_ARCHITECTURE §3, layer 5).

No DNA has been synthesised, stored or sequenced by VNX-DNA. A *record* (``record_version "1"``) describes one run —
synthesis, sample, storage, sequencing, decode, result, attestations — and carries an evidence classification:
``REAL PHYSICAL RESULT``, ``SIMULATED RESULT``, ``SYNTHETIC SOFTWARE TEST`` or ``PUBLIC-DATA-DERIVED``. The validator
checks structure and internal consistency only; it cannot establish that a record describes a genuine experiment.

    from vnxdna import physical
    physical.validate_record(json.load(open("run.json")), base=Path("."))["status"]   # VALID | INVALID | INCOMPLETE

Moved from ``experiments/v6/physical/`` in V6 Phase 7; the old script path and ``schema/`` directory still work.
"""
from vnxdna.physical.validate import (CLASSES, DOI_OR_UNPUBLISHED, NO_PROVIDER, PUBLIC_DATA, SCHEMA_DIR, STATEMENT,
                                      load_schema, make_template, missing_fields, public_data_errors, rule_errors,
                                      schema_errors, sha256_file, template_from_schema, validate_record)

__all__ = ["CLASSES", "PUBLIC_DATA", "NO_PROVIDER", "STATEMENT", "SCHEMA_DIR", "DOI_OR_UNPUBLISHED", "load_schema",
           "schema_errors", "missing_fields", "rule_errors", "public_data_errors", "validate_record", "make_template",
           "template_from_schema", "sha256_file"]
