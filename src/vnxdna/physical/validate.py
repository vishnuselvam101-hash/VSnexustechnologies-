"""Validate a VNX-DNA physical-validation run record, or write an empty template.

    python -m vnxdna.physical check record.json
    python -m vnxdna.physical template [--out record.template.json]

(Moved from ``experiments/v6/physical/validate.py`` in V6 Phase 7; that path still works and runs this module.)

No physical experiment has been performed by VNX-DNA. This tool only checks the structure and internal consistency
of a record that a future lab partner (or a software test) would fill in. A passing check does not show that the
data are genuine; it shows that the record is well formed, internally consistent and carries the evidence fields
its evidence classification requires.

Exit status of ``check``: 0 = VALID (complete and consistent), 2 = INVALID (schema or rule violation),
3 = INCOMPLETE (well formed and consistent so far, but required fields are empty, e.g. the template).

The schema check is a small dependency-free implementation of the JSON Schema 2020-12 keywords the schemas use
(type, enum, const, pattern, minimum, maximum, minLength, minItems, required, properties, additionalProperties,
items, $ref, anyOf). If the ``jsonschema`` package is installed it can be used to cross-check (see tests).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().with_name("schemas")
PUBLIC_DATA = "PUBLIC-DATA-DERIVED"
CLASSES = ("REAL PHYSICAL RESULT", "SIMULATED RESULT", "SYNTHETIC SOFTWARE TEST", PUBLIC_DATA)
#: a public dataset's paper reference: a DOI (``10.<registrant>/<suffix>``) or the word ``unpublished``
DOI_OR_UNPUBLISHED = re.compile(r"^(10\.[0-9]{4,9}/\S+|unpublished)$")
NO_PROVIDER = "none (simulation)"
STATEMENT = ("No DNA has been synthesised, stored or sequenced by VNX-DNA. This record is the interface for a future "
             "laboratory partnership; only a record classified REAL PHYSICAL RESULT with the required evidence may describe physical data.")
NOT_APPLICABLE_WITHOUT_PHYSICAL = {"$.storage.temperature_c", "$.storage.relative_humidity_percent"}
PLACEHOLDERS = {"", "todo", "tbd", "n/a", "na", "unknown", "placeholder", "xxx", "none"}


# ------------------------------------------------------------------------------------------------ schema validation
def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text())


def _resolve(ref: str, base: dict) -> tuple[dict, dict]:
    file, _, frag = ref.partition("#")
    root = load_schema(file) if file else base
    node = root
    for part in [p for p in frag.split("/") if p]:
        node = node[part]
    return node, root


def _type_ok(v, t: str) -> bool:
    return {"object": isinstance(v, dict), "array": isinstance(v, list), "string": isinstance(v, str), "null": v is None,
            "boolean": isinstance(v, bool), "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool)}[t]


def schema_errors(value, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    root = root or schema
    if "$ref" in schema:
        node, nroot = _resolve(schema["$ref"], root)
        return schema_errors(value, node, nroot, path)
    errs: list[str] = []
    if "anyOf" in schema:
        if not any(not schema_errors(value, s, root, path) for s in schema["anyOf"]):
            sub = [schema_errors(value, s, root, path) for s in schema["anyOf"]]
            return [f"{path}: value {value!r} matches none of the allowed alternatives ({'; '.join(e[0] for e in sub if e)})"]
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not one of {schema['enum']}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            return errs + [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errs.append(f"{path}: missing required key {k!r}")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errs += schema_errors(v, props[k], root, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected key {k!r}")
    if isinstance(value, list) and "minItems" in schema and len(value) < schema["minItems"]:
        errs.append(f"{path}: fewer than {schema['minItems']} items")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errs += schema_errors(v, schema["items"], root, f"{path}[{i}]")
    return errs


# ------------------------------------------------------------------------------------------------ completeness
def _deref(schema: dict, root: dict) -> tuple[dict, dict]:
    while "$ref" in schema:
        schema, root = _resolve(schema["$ref"], root)
    return schema, root


def missing_fields(value, schema: dict, root: dict, path: str = "$") -> list[str]:
    """Required fields (per the schema's ``required``) that are null/empty; optional fields are only walked if present."""
    schema, root = _deref(schema, root)
    if isinstance(value, dict) and schema.get("properties"):
        out: list[str] = []
        req = set(schema.get("required", []))
        for k, sub in schema["properties"].items():
            v = value.get(k)
            if v is None or v == []:
                if k in req and k != "statement":
                    out.append(f"{path}.{k}")
            else:
                out += missing_fields(v, sub, root, f"{path}.{k}")
        return out
    if isinstance(value, list):
        items = schema.get("items")
        out = []
        for i, v in enumerate(value):
            out += missing_fields(v, items, root, f"{path}[{i}]") if items else []
        return out
    return []


# ------------------------------------------------------------------------------------------------ helpers
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _d(s):
    try:
        return date.fromisoformat(s) if isinstance(s, str) else None
    except ValueError:
        return None


def _g(rec, *keys):
    for k in keys:
        if not isinstance(rec, dict):
            return None
        rec = rec.get(k)
    return rec


def _filled(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() not in PLACEHOLDERS
    if isinstance(v, (list, dict)):
        return bool(v)
    return True


def _resolve_path(p: str, base: Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else base / q


# ------------------------------------------------------------------------------------------------ cross-field rules
def rule_errors(rec: dict, base: Path) -> list[str]:
    e: list[str] = []
    cls = rec.get("evidence_classification")

    # dates: synthesis <= delivery <= storage start <= storage end <= sequencing run; duration consistent
    seq = [("synthesis.synthesis_date", _g(rec, "synthesis", "synthesis_date")),
           ("synthesis.delivery_date", _g(rec, "synthesis", "delivery_date")),
           ("storage.start_date", _g(rec, "storage", "start_date")),
           ("storage.end_date", _g(rec, "storage", "end_date")),
           ("sequencing.run_date", _g(rec, "sequencing", "run_date"))]
    parsed = []
    for name, v in seq:
        if v is None:
            continue
        d = _d(v)
        if d is None:
            if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                e.append(f"{name}: {v!r} is not a real calendar date")
            continue
        parsed.append((name, d))
    for (n1, d1), (n2, d2) in zip(parsed, parsed[1:]):
        if d2 < d1:
            e.append(f"dates out of order: {n2} ({d2}) is before {n1} ({d1})")
    s, t, dur = _d(_g(rec, "storage", "start_date")), _d(_g(rec, "storage", "end_date")), _g(rec, "storage", "duration_days")
    if s and t and isinstance(dur, int) and dur != (t - s).days:
        e.append(f"storage.duration_days is {dur} but end_date - start_date is {(t - s).days} days")

    # FASTQ files: checksum/size/read count vs the file when a path is given
    seqn = rec.get("sequencing") or {}
    files = seqn.get("fastq_files") or []
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            continue
        p = f.get("path")
        if p:
            fp = _resolve_path(p, base)
            if not fp.is_file():
                e.append(f"sequencing.fastq_files[{i}].path {p!r} not found")
            else:
                if f.get("file_name") and Path(p).name != f["file_name"]:
                    e.append(f"sequencing.fastq_files[{i}].file_name {f['file_name']!r} differs from the path's file name")
                if f.get("sha256") and sha256_file(fp) != f["sha256"]:
                    e.append(f"sequencing.fastq_files[{i}].sha256 does not match the file {fp.name}")
                if isinstance(f.get("size_bytes"), int) and fp.stat().st_size != f["size_bytes"]:
                    e.append(f"sequencing.fastq_files[{i}].size_bytes {f['size_bytes']} != actual {fp.stat().st_size}")
                if isinstance(f.get("read_count"), int) and fp.suffix.lower() in (".fastq", ".fq"):
                    n = fp.read_bytes().count(b"\n") // 4
                    if n != f["read_count"]:
                        e.append(f"sequencing.fastq_files[{i}].read_count {f['read_count']} != {n} reads in the plain FASTQ")
    counts = [f.get("read_count") for f in files if isinstance(f, dict)]
    total = _g(rec, "sequencing", "read_counts", "total")
    if files and all(isinstance(c, int) for c in counts) and isinstance(total, int) and sum(counts) != total:
        e.append(f"sequencing.read_counts.total {total} != sum of FASTQ read_count {sum(counts)}")
    passing = _g(rec, "sequencing", "read_counts", "passing_filter")
    if isinstance(total, int) and isinstance(passing, int) and passing > total:
        e.append("sequencing.read_counts.passing_filter exceeds total")
    layout = seqn.get("layout")
    if layout == "single" and len(files) > 1:
        e.append("sequencing.layout is single but more than one FASTQ file is listed")
    if layout == "paired" and files and len(files) < 2:
        e.append("sequencing.layout is paired but fewer than two FASTQ files are listed")
    of = _g(rec, "synthesis", "ordered_fasta") or {}
    if of.get("path"):
        fp = _resolve_path(of["path"], base)
        if not fp.is_file():
            e.append(f"synthesis.ordered_fasta.path {of['path']!r} not found")
        elif of.get("sha256") and sha256_file(fp) != of["sha256"]:
            e.append("synthesis.ordered_fasta.sha256 does not match the file")
    syn = rec.get("synthesis") or {}
    if isinstance(of.get("strand_count"), int) and isinstance(syn.get("strand_count"), int) and of["strand_count"] != syn["strand_count"]:
        e.append("synthesis.strand_count differs from synthesis.ordered_fasta.strand_count")
    lib, libs = _g(rec, "sample", "library_id"), syn.get("library_ids")
    if lib and libs and lib not in libs:
        e.append(f"sample.library_id {lib!r} is not among synthesis.library_ids")

    # decode report checksum when the report file is present next to the record
    dec = rec.get("decode") or {}
    if dec.get("report_file") and dec.get("report_sha256"):
        rp = _resolve_path(dec["report_file"], base)
        if rp.is_file() and sha256_file(rp) != dec["report_sha256"]:
            e.append("decode.report_sha256 does not match decode.report_file")

    # decode SHA comparison vs the stated verification result
    res = rec.get("result") or {}
    rs, ex, vr = res.get("recovered_sha256"), res.get("expected_sha256"), res.get("verification_result")
    fe, fr = res.get("files_expected"), res.get("files_recovered")
    if vr == "SUCCESS":
        if not (rs and ex):
            e.append("result: SUCCESS requires both recovered_sha256 and expected_sha256")
        elif rs != ex:
            e.append("result: verification_result is SUCCESS but recovered_sha256 != expected_sha256")
    elif vr in ("PARTIAL", "FAILURE") and rs and ex and rs == ex:
        e.append(f"result: recovered_sha256 == expected_sha256 but verification_result is {vr}")
    if isinstance(fe, int) and isinstance(fr, int):
        if fr > fe:
            e.append("result.files_recovered exceeds files_expected")
        if vr == "SUCCESS" and fr != fe:
            e.append("result: SUCCESS requires files_recovered == files_expected")
        if vr == "PARTIAL" and fr >= fe:
            e.append("result: PARTIAL requires files_recovered < files_expected")

    # evidence classification
    if cls == "REAL PHYSICAL RESULT":
        need = {"synthesis.provider_name": _g(rec, "synthesis", "provider_name"), "synthesis.order_id": syn.get("order_id"),
                "synthesis.synthesis_date": syn.get("synthesis_date"), "synthesis.ordered_fasta.sha256": of.get("sha256"),
                "sequencing.provider_name": seqn.get("provider_name"), "sequencing.run_id": seqn.get("run_id"),
                "sequencing.platform": seqn.get("platform"), "sequencing.run_date": seqn.get("run_date"),
                "sequencing.fastq_files": files, "attestations.synthesis.performed_by": _g(rec, "attestations", "synthesis", "performed_by"),
                "attestations.sequencing.performed_by": _g(rec, "attestations", "sequencing", "performed_by"),
                "attestations.signed_off_by.name": _g(rec, "attestations", "signed_off_by", "name"),
                "attestations.raw_data_location": _g(rec, "attestations", "raw_data_location")}
        for k, v in need.items():
            if not _filled(v):
                e.append(f"REAL PHYSICAL RESULT requires {k} (missing, empty or placeholder)")
        for i, f in enumerate(files):
            if not (isinstance(f, dict) and f.get("sha256")):
                e.append(f"REAL PHYSICAL RESULT requires a SHA-256 for sequencing.fastq_files[{i}]")
        for k in ("synthesis.provider_name", "sequencing.provider_name"):
            if str(need[k] or "").strip().lower() == NO_PROVIDER:
                e.append(f"REAL PHYSICAL RESULT cannot have {k} = {NO_PROVIDER!r}")
    elif cls == PUBLIC_DATA:
        e += public_data_errors(rec.get("public_data"), base)
    elif cls in ("SIMULATED RESULT", "SYNTHETIC SOFTWARE TEST"):
        for k, v in (("synthesis.provider_name", syn.get("provider_name")), ("sequencing.provider_name", seqn.get("provider_name"))):
            if v is not None and v != NO_PROVIDER:
                e.append(f"{cls} must have {k} = {NO_PROVIDER!r} (found {v!r}); only REAL PHYSICAL RESULT may name a provider")
        if syn.get("order_id") not in (None, NO_PROVIDER):
            e.append(f"{cls} must not carry a synthesis order ID")
    return e


def public_data_errors(pd, base: Path) -> list[str]:
    """PUBLIC-DATA-DERIVED (dataset registry, research gate 6): a dataset accession, the SHA-256 of every downloaded
    file, and the paper's DOI or ``unpublished``. A file's SHA-256 is checked against it when its ``path`` is reachable."""
    if not isinstance(pd, dict):
        return [f"{PUBLIC_DATA} requires public_data (dataset accession, SHA-256 of the downloaded files, DOI or 'unpublished')"]
    e: list[str] = []
    if not _filled(pd.get("accession")):
        e.append(f"{PUBLIC_DATA} requires public_data.accession (missing, empty or placeholder)")
    doi = pd.get("doi")
    if not (isinstance(doi, str) and DOI_OR_UNPUBLISHED.match(doi)):
        e.append(f"{PUBLIC_DATA} requires public_data.doi: a DOI (10.xxxx/...) or 'unpublished' (found {doi!r})")
    files = pd.get("files")
    if not isinstance(files, list) or not files:
        e.append(f"{PUBLIC_DATA} requires public_data.files: at least one downloaded file with its SHA-256")
        return e
    for i, f in enumerate(files):
        if not (isinstance(f, dict) and isinstance(f.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", f["sha256"])):
            e.append(f"{PUBLIC_DATA} requires a SHA-256 for public_data.files[{i}]")
            continue
        if f.get("path"):
            fp = _resolve_path(f["path"], base)
            if not fp.is_file():
                e.append(f"public_data.files[{i}].path {f['path']!r} not found")
            elif sha256_file(fp) != f["sha256"]:
                e.append(f"public_data.files[{i}].sha256 does not match the file {fp.name}")
    return e


# ------------------------------------------------------------------------------------------------ top level
def validate_record(rec, base: Path | None = None) -> dict:
    base = base or Path.cwd()
    schema = load_schema("record.schema.json")
    errs = schema_errors(rec, schema)
    rules, missing = [], []
    if not errs:
        rules = rule_errors(rec, base)
        missing = missing_fields(rec, schema, schema)
        if rec.get("evidence_classification") in ("SIMULATED RESULT", "SYNTHETIC SOFTWARE TEST", PUBLIC_DATA):
            # no physical storage took place at VNX-DNA (public data were stored by another group, if at all), so these
            # two measurements are not applicable
            missing = [m for m in missing if m not in NOT_APPLICABLE_WITHOUT_PHYSICAL]
        if rec.get("evidence_classification") is None:
            missing = sorted(set(missing) | {"$.evidence_classification"})
    status = "INVALID" if errs or rules else ("INCOMPLETE" if missing else "VALID")
    return {"status": status, "evidence_classification": rec.get("evidence_classification") if isinstance(rec, dict) else None,
            "schema_errors": errs, "rule_errors": rules, "missing_fields": missing, "statement": STATEMENT}


def template_from_schema(schema: dict, root: dict | None = None):
    root = root or schema
    schema, root = _deref(schema, root)
    if "const" in schema:
        return schema["const"]
    if schema.get("type") == "object" and "properties" in schema:
        return {k: template_from_schema(v, root) for k, v in schema["properties"].items()}
    return None


def make_template() -> dict:
    t = template_from_schema(load_schema("record.schema.json"))
    t["statement"] = STATEMENT
    return t


__all__ = ["CLASSES", "PUBLIC_DATA", "NO_PROVIDER", "STATEMENT", "SCHEMA_DIR", "DOI_OR_UNPUBLISHED", "load_schema",
           "schema_errors", "missing_fields", "rule_errors", "public_data_errors", "validate_record", "make_template",
           "template_from_schema", "sha256_file", "main"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate a record")
    c.add_argument("record")
    t = sub.add_parser("template", help="write an empty template")
    t.add_argument("--out", help="output file (default: stdout)")
    a = ap.parse_args(argv)
    if a.cmd == "template":
        text = json.dumps(make_template(), indent=2) + "\n"
        if a.out:
            Path(a.out).write_text(text)
        else:
            sys.stdout.write(text)
        return 0
    p = Path(a.record)
    try:
        rec = json.loads(p.read_text())
    except (OSError, ValueError) as ex:
        print(json.dumps({"status": "INVALID", "schema_errors": [f"cannot read record: {ex}"]}, indent=2))
        return 2
    r = validate_record(rec, p.resolve().parent)
    print(json.dumps(r, indent=2))
    return {"VALID": 0, "INVALID": 2, "INCOMPLETE": 3}[r["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
