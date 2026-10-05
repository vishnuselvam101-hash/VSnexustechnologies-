"""VNX Export Package and VNX Import Package (spec §9.3): the files that cross the boundary to a laboratory.

    VNX archive → strands → **export package** (strands.fasta, order.csv, manifest.json, SHA256SUMS) → laboratory
    (synthesis, storage, retrieval, sequencing) → sequencing data → **import package** (reads/*.fastq, manifest.json,
    SHA256SUMS) → VNX decoder

Both manifests are machine-readable JSON with JSON Schemas (``vnx.export-package/1``, ``vnx.import-package/1`` in
``vnxdna/core/schemas``). Each package is content-addressed: ``package_id`` is the SHA-256 of the canonical manifest
without that field, and every file is listed with its SHA-256 and size. Loading a package re-checks all of it.

Export checks (all before anything is published; a failed check publishes nothing):

* ``vendor-max-length``: the longest strand against the provider's ``max_strand_nt`` (default 350 nt, configurable)
  → ``VENDOR_MAX_LENGTH``; also ``min_strand_nt`` and ``max_pool_strands``;
* ``alphabet`` (ACGT only) and ``constraints`` (the archive's GC / homopolymer configuration and any vendor GC /
  homopolymer limit) → ``CONSTRAINT_ERROR``;
* ``strands-match-archive``: the strand file is the one the encoder reported (SHA-256 and count);
* ``pool-tag-unique``: the pool-composition rule (spec §3.9) → ``ARCHIVE_TAG_COLLISION``.

No package says anything about physical DNA: nothing produced by VNX-DNA has been synthesised, stored or sequenced.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path

from vnxdna.core.version import SPEC_VERSION, __version__
from vnxdna.core.errors import (VNXConfigurationError, VNXConstraintError, VNXFormatError, VNXIntegrityError,
                                VNXOutputError, VNXUnsupportedVersionError)
from vnxdna.core.schema import load as load_schema
from vnxdna.dnaenc.constraints import ConstraintConfig, diagnose, iter_fasta
from vnxdna.physical.validate import schema_errors
from vnxdna.providers.base import (INTERFACE, ArchiveRef, ExportPackage, ImportPackage, ProviderCapabilities,
                                   sha256_file)

EXPORT_SCHEMA = "vnx.export-package/1"
IMPORT_SCHEMA = "vnx.import-package/1"
EVIDENCE_CLASSES = ("SIMULATED", "SYNTHETIC SOFTWARE TEST", "PUBLIC-DATA-DERIVED", "REAL PHYSICAL RESULT")
STATEMENT = ("No DNA has been synthesised, stored or sequenced by VNX-DNA. This package is a software artefact of the "
             "VNX-DNA provider interface; only an import package classified REAL PHYSICAL RESULT, with the provider, order "
             "and run evidence the physical-record rules require, may describe physical data.")
MAX_MANIFEST_BYTES = 16 << 20
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DOI = re.compile(r"^(10\.[0-9]{4,9}/\S+|unpublished)$")


# =============================================================================================================== helpers
def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _package_id(manifest: dict) -> str:
    import hashlib
    return hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != "package_id"})).hexdigest()


def _sums_text(files: list[dict], manifest_sha: str) -> str:
    rows = sorted([(f["sha256"], f["name"]) for f in files] + [(manifest_sha, "manifest.json")], key=lambda r: r[1])
    return "".join(f"{s}  {n}\n" for s, n in rows)


def _write_manifest(directory: Path, manifest: dict) -> None:
    manifest["package_id"] = _package_id(manifest)
    data = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    (directory / "manifest.json").write_bytes(data)
    import hashlib
    (directory / "SHA256SUMS").write_text(_sums_text(manifest["files"], hashlib.sha256(data).hexdigest()))


def _publish(tmp: Path, final: Path) -> Path:
    """Move a finished package directory into place. An existing directory of the same content-addressed name is
    accepted only if it verifies (idempotent re-export); anything else there is refused."""
    if final.exists() or final.is_symlink():
        shutil.rmtree(tmp, ignore_errors=True)
        if final.is_symlink() or not final.is_dir():
            raise VNXOutputError(f"{final} exists and is not a package directory")
        return final
    os.replace(tmp, final)
    return final


def _schema_check(manifest, schema_id: str, where: Path) -> None:
    if not isinstance(manifest, dict):
        raise VNXFormatError(f"{where}: the manifest is not a JSON object", stage="package")
    sid = manifest.get("schema")
    family = schema_id.split("/")[0]
    if isinstance(sid, str) and sid.startswith(family + "/") and sid != schema_id:
        raise VNXUnsupportedVersionError(f"{where}: unsupported manifest schema {sid!r} (this software reads {schema_id})",
                                         stage="package", code="SCHEMA_UNSUPPORTED")
    errs = schema_errors(manifest, load_schema(schema_id))
    if errs:
        raise VNXFormatError(f"{where}: manifest does not match {schema_id}: {errs[0]}", stage="package",
                             details={"errors": errs[:20]})


def _read_manifest(directory: Path) -> dict:
    p = directory / "manifest.json"
    try:
        if p.is_symlink() or not p.is_file():
            raise VNXFormatError(f"{directory}: no manifest.json", stage="package")
        if p.stat().st_size > MAX_MANIFEST_BYTES:
            raise VNXFormatError(f"{p}: manifest larger than {MAX_MANIFEST_BYTES} bytes", stage="package")
        return json.loads(p.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise VNXFormatError(f"{p}: cannot read the manifest: {error}", stage="package") from None


def _verify_files(directory: Path, manifest: dict) -> None:
    """Every listed file exists inside the package (no links, no path escape) and matches its SHA-256 and size; the
    package ID and SHA256SUMS agree with the manifest."""
    if manifest["package_id"] != _package_id(manifest):
        raise VNXIntegrityError(f"{directory}: package_id does not match the manifest content", stage="package")
    root = directory.resolve()
    names = set()
    for f in manifest["files"]:
        name = f["name"]
        parts = name.split("/")
        if name in names or any(not _SAFE_NAME.match(x) for x in parts) or len(parts) > 2:
            raise VNXFormatError(f"{directory}: unsafe or duplicate file name {name!r}", stage="package")
        names.add(name)
        p = directory / name
        if p.is_symlink() or not p.is_file() or root not in p.resolve().parents:
            raise VNXFormatError(f"{directory}: listed file {name!r} is missing or not a regular file", stage="package")
        if p.stat().st_size != f["bytes"] or sha256_file(p) != f["sha256"]:
            raise VNXIntegrityError(f"{directory}: {name} does not match its recorded SHA-256 / size", stage="package",
                                    details={"file": name})
    sums = directory / "SHA256SUMS"
    if sums.is_symlink() or not sums.is_file():
        raise VNXFormatError(f"{directory}: no SHA256SUMS", stage="package")
    expected = _sums_text(manifest["files"], sha256_file(directory / "manifest.json"))
    if sums.read_text(encoding="ascii", errors="replace") != expected:
        raise VNXIntegrityError(f"{directory}: SHA256SUMS does not match the manifest", stage="package")


# ======================================================================================================== export package
def _scan_strands(path: Path, archive: ArchiveRef, caps: ProviderCapabilities, constraints: ConstraintConfig,
                  out, first: int) -> dict:
    """Stream one archive's strands into the pool file, collecting lengths and constraint diagnostics."""
    n, lo, hi = 0, None, 0
    longest = shortest = None
    bad_alpha: list[str] = []
    violations: dict[str, int] = {}
    vendor_bad = 0
    tag = f"{archive.pool_tag:04x}"
    foreign = 0
    for name, seq in iter_fasta(path):
        n += 1
        L = len(seq)
        if lo is None or L < lo:
            lo, shortest = L, name
        if L > hi:
            hi, longest = L, name
        if L > caps.max_strand_nt:          # the first offender is enough: nothing will be published
            raise VNXConfigurationError(
                f"strand {name!r} is {L} nt; the provider accepts at most {caps.max_strand_nt} nt", stage="export",
                code="VENDOR_MAX_LENGTH", details={"limit": caps.max_strand_nt, "value": L, "strand": name,
                                                   "archive_id": archive.archive_id})
        if set(seq) - set("ACGT"):
            if len(bad_alpha) < 5:
                bad_alpha.append(name)
            continue
        d = diagnose(seq, constraints)
        for r in d["violations"]:
            violations[r] = violations.get(r, 0) + 1
        if (caps.gc_min_percent is not None and d["gc_percent"] < caps.gc_min_percent) or \
                (caps.gc_max_percent is not None and d["gc_percent"] > caps.gc_max_percent) or \
                (caps.max_homopolymer is not None and d["max_homopolymer"] > caps.max_homopolymer):
            vendor_bad += 1
        parts = name.split("|")
        if len(parts) >= 2 and parts[0].startswith("vnx") and parts[1] != tag:
            foreign += 1
        out.write(f">{name}\n{seq}\n")
    if n == 0:
        raise VNXConfigurationError(f"{path}: no strands", stage="export")
    return {"count": n, "min": lo, "max": hi, "longest": longest, "shortest": shortest, "bad_alphabet": bad_alpha,
            "violations": violations, "vendor_bad": vendor_bad, "foreign_tag": foreign, "first": first}


def pool_composition(archives: Sequence[ArchiveRef]) -> tuple[list[int], dict]:
    """Spec §3.9: refuse two archives with equal frame tags and equal primer IDs unless their containers are identical
    (then the archive is included once). Returns (indices to keep, check)."""
    seen: dict[tuple, int] = {}
    keep: list[int] = []
    duplicates = []
    for i, a in enumerate(archives):
        key = (a.pool_tag, a.primer_id)
        if key in seen:
            j = seen[key]
            if archives[j].container_sha256 == a.container_sha256:
                duplicates.append([j, i])
                continue
            raise VNXConfigurationError(
                f"archives {j} and {i} share frame tag {a.pool_tag:#06x} and primer ID {a.primer_id} but are different "
                "containers; their strands could not be told apart in one pool", stage="export",
                code="ARCHIVE_TAG_COLLISION",
                details={"pool_tag": a.pool_tag, "primer_id": a.primer_id, "archives": [archives[j].archive_id, a.archive_id],
                         "container_sha256": [archives[j].container_sha256, a.container_sha256]},
                hint="re-encode one archive under another primer partition (V7) or put the archives in separate pools")
        seen[key] = i
        keep.append(i)
    return keep, {"id": "pool-tag-unique", "status": "PASS",
                  "details": {"archives": len(keep), "identical_duplicates_included_once": duplicates} if duplicates else None}


def build_export_package(items: Sequence[tuple[str | os.PathLike, ArchiveRef]], out_dir: str | os.PathLike, *,
                         provider: dict, capabilities: ProviderCapabilities | None = None,
                         constraints: ConstraintConfig | None = None, evidence_class: str = "SYNTHETIC SOFTWARE TEST",
                         ) -> ExportPackage:
    """Build ``<out_dir>/<package_id>.vnxexp`` from (strand file, archive) pairs; more than one pair is a pool.

    ``provider`` is the target ``{"name", "version", "interface"}``. Deterministic: equal inputs give a byte-identical
    package (no timestamps, no paths). Every check runs before the package is published; a failure publishes nothing."""
    caps = (capabilities or ProviderCapabilities()).validate()
    cfg = (constraints or ConstraintConfig()).validate()
    if evidence_class not in EVIDENCE_CLASSES:
        raise VNXConfigurationError(f"evidence_class must be one of {EVIDENCE_CLASSES}")
    if evidence_class == "REAL PHYSICAL RESULT":
        raise VNXConfigurationError("an export package is an order, not a result; it cannot be REAL PHYSICAL RESULT")
    if not items:
        raise VNXConfigurationError("an export package needs at least one archive", stage="export")
    if provider.get("interface") != INTERFACE:
        raise VNXConfigurationError(f"provider interface must be {INTERFACE}")
    archives = [a for _, a in items]
    for a in archives:
        if not isinstance(a, ArchiveRef):
            raise VNXConfigurationError("each item must be (strand file, ArchiveRef)")
    keep, pool_check = pool_composition(archives)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".vnxexp-", dir=out_dir))
    try:
        entries, scans = [], []
        first = 0
        with open(tmp / "strands.fasta", "w", encoding="ascii", newline="\n") as out:
            for i in keep:
                path, a = Path(items[i][0]), items[i][1]
                if sha256_file(path) != a.strands_sha256:
                    raise VNXConfigurationError(f"{path} is not the strand file the encoder reported for archive "
                                                f"{a.archive_id} (SHA-256 differs)", stage="export",
                                                details={"archive_id": a.archive_id})
                s = _scan_strands(path, a, caps, cfg, out, first)
                if s["count"] != a.strand_count:
                    raise VNXConfigurationError(f"{path}: {s['count']} strands, the encoder reported {a.strand_count}",
                                                stage="export")
                if s["foreign_tag"]:
                    raise VNXConfigurationError(f"{path}: {s['foreign_tag']} strand label(s) carry another archive tag",
                                                stage="export")
                first += s["count"]
                scans.append(s)
                entries.append({"archive_id": a.archive_id, "container_sha256": a.container_sha256,
                                "container_size": a.container_size, "encrypted": a.encrypted, "codec": a.codec,
                                "frame_version": a.frame_version, "superblock_version": a.superblock_version,
                                "strand_profile": a.strand_profile, "redundancy_profile": a.redundancy_profile,
                                "layout": dict(a.layout), "outer": dict(a.outer), "pool_tag": a.pool_tag,
                                "primer_id": a.primer_id, "primers": None, "strand_count": s["count"],
                                "strand_nt": {"min": s["min"], "max": s["max"]}, "strands_sha256": a.strands_sha256,
                                "first_strand": s["first"]})
        total = first
        lo = min(s["min"] for s in scans)
        hi = max(s["max"] for s in scans)
        if lo < caps.min_strand_nt:
            raise VNXConfigurationError(f"a strand is {lo} nt; the provider needs at least {caps.min_strand_nt} nt",
                                        stage="export", code="CONFIGURATION_ERROR")
        if caps.max_pool_strands is not None and total > caps.max_pool_strands:
            raise VNXConfigurationError(f"{total} strands; the provider accepts at most {caps.max_pool_strands} per pool",
                                        stage="export")
        bad = [n for s in scans for n in s["bad_alphabet"]]
        if bad:
            raise VNXConstraintError(f"strands with symbols other than ACGT: {bad[:5]}", stage="export")
        violations: dict[str, int] = {}
        for s in scans:
            for k, v in s["violations"].items():
                violations[k] = violations.get(k, 0) + v
        vendor_bad = sum(s["vendor_bad"] for s in scans)
        if violations or vendor_bad:
            raise VNXConstraintError(f"strands violate the sequence constraints: {violations or ''}"
                                     f"{' and ' if violations and vendor_bad else ''}"
                                     f"{f'{vendor_bad} outside the provider GC/homopolymer limits' if vendor_bad else ''}",
                                     stage="export", details={"violations": violations, "vendor_limit_violations": vendor_bad})
        with open(tmp / "strands.fasta", encoding="ascii") as src, \
                open(tmp / "order.csv", "w", encoding="ascii", newline="") as dst:
            w = csv.writer(dst, lineterminator="\n")
            w.writerow(["name", "sequence"])
            name = None
            for line in src:
                line = line.rstrip("\n")
                if line.startswith(">"):
                    name = line[1:]
                else:
                    w.writerow([name, line])
        checks = [{"id": "vendor-max-length", "status": "PASS", "limit": caps.max_strand_nt, "value": hi},
                  {"id": "vendor-min-length", "status": "PASS", "limit": caps.min_strand_nt, "value": lo},
                  {"id": "alphabet", "status": "PASS", "config": {"alphabet": caps.alphabet}},
                  {"id": "constraints", "status": "PASS", "config": cfg.to_dict(),
                   "details": {"vendor_gc_percent": [caps.gc_min_percent, caps.gc_max_percent],
                               "vendor_max_homopolymer": caps.max_homopolymer}},
                  {"id": "strands-match-archive", "status": "PASS", "value": total},
                  {k: v for k, v in pool_check.items() if v is not None or k != "details"}]
        files = [{"name": n, "role": r, "sha256": sha256_file(tmp / n), "bytes": (tmp / n).stat().st_size}
                 for n, r in (("strands.fasta", "strands"), ("order.csv", "order"))]
        manifest = {"schema": EXPORT_SCHEMA, "package_id": "", "created_by": {"software": __version__, "spec": SPEC_VERSION},
                    "provider_target": {"name": provider["name"], "version": provider["version"], "interface": INTERFACE},
                    "evidence_class": evidence_class, "statement": STATEMENT, "vendor_constraints": caps.to_dict(),
                    "archives": entries, "checks": checks, "files": files}
        _write_manifest(tmp, manifest)
        _schema_check(manifest, EXPORT_SCHEMA, tmp)
        final = _publish(tmp, out_dir / f"{manifest['package_id']}.vnxexp")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return load_export_package(final)


def load_export_package(path: str | os.PathLike) -> ExportPackage:
    """Open and fully verify an export package (schema, package ID, every file's SHA-256 and size, SHA256SUMS)."""
    d = Path(path)
    if d.is_symlink() or not d.is_dir():
        raise VNXFormatError(f"{d}: not an export package directory", stage="package")
    m = _read_manifest(d)
    _schema_check(m, EXPORT_SCHEMA, d)
    _verify_files(d, m)
    if sum(1 for f in m["files"] if f["role"] == "strands") != 1:
        raise VNXFormatError(f"{d}: an export package has exactly one strands file", stage="package")
    return ExportPackage(d, m)


# ======================================================================================================== import package
def _count_fastq(path: Path) -> tuple[int, int | None]:
    """(reads, read length if all equal else None) of a plain FASTQ file."""
    n, lengths = 0, set[int]()
    with open(path, "rb") as f:
        for i, line in enumerate(f):
            if i % 4 == 1:
                n += 1
                if len(lengths) < 2:
                    lengths.add(len(line.rstrip(b"\r\n")))
    return n, (next(iter(lengths)) if len(lengths) == 1 else None)


def _import_rules(m: dict, where) -> None:
    cls = m["evidence_class"]
    seq = m["sequencing"]
    if cls == "SIMULATED" and m["simulation"] is None:
        raise VNXFormatError(f"{where}: a SIMULATED import package must record its simulation (model, version, seed)",
                             stage="package")
    if cls != "SIMULATED" and m["simulation"] is not None:
        raise VNXFormatError(f"{where}: only a SIMULATED import package may carry a simulation block", stage="package")
    if cls == "REAL PHYSICAL RESULT":
        missing = [k for k in ("platform", "run_id") if not seq.get(k)]
        if missing or m["provider"]["name"] == "reference-simulator":
            raise VNXFormatError(f"{where}: REAL PHYSICAL RESULT needs a real provider and sequencing {missing}",
                                 stage="package")
    if cls == "PUBLIC-DATA-DERIVED":
        ds = m["dataset"]
        if not ds or not ds.get("accession") or not _DOI.match(str(ds.get("doi"))) or not ds.get("files"):
            raise VNXFormatError(f"{where}: PUBLIC-DATA-DERIVED needs dataset accession, file SHA-256s and a DOI or "
                                 "'unpublished'", stage="package")
    elif m["dataset"] is not None:
        raise VNXFormatError(f"{where}: only a PUBLIC-DATA-DERIVED import package may name a public dataset",
                             stage="package")


def build_import_package(reads: Sequence[str | os.PathLike], out_dir: str | os.PathLike, *, provider: dict,
                         evidence_class: str, export_package_id: str | None, sequencing: dict | None = None,
                         simulation: dict | None = None, dataset: dict | None = None,
                         primer_id: int | None = None, move: bool = False) -> ImportPackage:
    """Build ``<out_dir>/<package_id>.vnximp`` from FASTQ files (a laboratory's or a simulator's).

    ``sequencing`` = ``{"platform", "instrument", "run_id", "run_date", "read_length", "layout", "primers_trimmed"}``
    (missing keys are null / "single" / false). The class rules: SIMULATED needs ``simulation``; REAL PHYSICAL RESULT
    needs a provider other than the simulator, ``platform`` and ``run_id``; PUBLIC-DATA-DERIVED needs ``dataset``.
    ``move`` moves the read files instead of copying them (for files the caller just wrote)."""
    if evidence_class not in EVIDENCE_CLASSES:
        raise VNXConfigurationError(f"evidence_class must be one of {EVIDENCE_CLASSES}")
    if not reads:
        raise VNXConfigurationError("an import package needs at least one read file")
    if provider.get("interface") != INTERFACE:
        raise VNXConfigurationError(f"provider interface must be {INTERFACE}")
    seq = {"platform": None, "instrument": None, "run_id": None, "run_date": None, "read_length": None,
           "layout": "single", "primers_trimmed": False, **(sequencing or {})}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".vnximp-", dir=out_dir))
    try:
        (tmp / "reads").mkdir()
        files: list[dict] = []
        lengths: set[int | None] = set()
        roles = ["reads"] if len(reads) == 1 else [f"reads-r{i + 1}" for i in range(len(reads))]
        if len(reads) > 2:
            raise VNXConfigurationError("at most two read files (single or paired layout)")
        for i, r in enumerate(reads):
            src = Path(r)
            if not src.is_file():
                raise VNXConfigurationError(f"read file not found: {src}")
            name = f"reads/r{i + 1}.fastq"
            (shutil.move if move else shutil.copyfile)(str(src), str(tmp / name))
            n, L = _count_fastq(tmp / name)
            lengths.add(L)
            files.append({"name": name, "role": roles[i], "sha256": sha256_file(tmp / name),
                          "bytes": (tmp / name).stat().st_size, "reads": n})
        if seq["read_length"] is None and len(lengths) == 1 and None not in lengths:
            seq["read_length"] = next(iter(lengths)) or None
        manifest = {"schema": IMPORT_SCHEMA, "package_id": "", "export_package_id": export_package_id,
                    "provider": {"name": provider["name"], "version": provider["version"], "interface": INTERFACE},
                    "evidence_class": evidence_class, "statement": STATEMENT, "simulation": simulation,
                    "sequencing": seq, "selection": {"primer_id": primer_id}, "dataset": dataset, "files": files}
        _schema_check(manifest | {"package_id": "0" * 64}, IMPORT_SCHEMA, tmp)
        _import_rules(manifest, tmp)
        _write_manifest(tmp, manifest)
        final = _publish(tmp, out_dir / f"{manifest['package_id']}.vnximp")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return load_import_package(final)


def load_import_package(path: str | os.PathLike) -> ImportPackage:
    """Open and fully verify an import package (schema, class rules, package ID, file hashes, SHA256SUMS)."""
    d = Path(path)
    if d.is_symlink() or not d.is_dir():
        raise VNXFormatError(f"{d}: not an import package directory", stage="package")
    m = _read_manifest(d)
    _schema_check(m, IMPORT_SCHEMA, d)
    _import_rules(m, d)
    _verify_files(d, m)
    return ImportPackage(d, m)


def order_rows(package: ExportPackage) -> list[tuple[str, str]]:
    """The (name, sequence) rows of the package's order sheet."""
    text = (package.path / "order.csv").read_text(encoding="ascii")
    rows = list(csv.reader(io.StringIO(text)))
    return [(a, b) for a, b in rows[1:]]


__all__ = ["EXPORT_SCHEMA", "IMPORT_SCHEMA", "EVIDENCE_CLASSES", "STATEMENT", "canonical", "pool_composition",
           "build_export_package", "load_export_package", "build_import_package", "load_import_package", "order_rows"]
