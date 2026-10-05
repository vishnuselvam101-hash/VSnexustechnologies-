"""Provider interface (spec §9.2): ``DNAWriter``, ``DNAReader``, ``DNAProvider`` and the value types they exchange.

A *provider* stands between VNX-DNA and a laboratory: it takes strands (an export package) to synthesis and storage, and
brings reads (an import package) back for the decoder. In 6.x the only implementation is
:class:`vnxdna.providers.reference.ReferenceSimulatorProvider`, which does everything in software and labels every
package SIMULATED. No synthesis or sequencing vendor adapter exists (V11); no DNA has been synthesised, stored or
sequenced by VNX-DNA.

Semantics (normative, spec §9.2): ``prepare`` is pure and deterministic; ``write`` and ``retrieve`` may take days
physically and return a receipt or a package; ``read`` streams reads to decode stage D0; no operation changes archive
bytes; every package records the provider ``name``, ``version`` and ``interface``. A capability violation in ``prepare``
raises ``CONFIGURATION_ERROR`` (code ``VENDOR_MAX_LENGTH``, ``ARCHIVE_TAG_COLLISION``, …) and publishes nothing; a
provider failure raises ``PROVIDER_ERROR`` (exit 10), never ``INSUFFICIENT_REDUNDANCY``.
"""
from __future__ import annotations

import builtins
import hashlib
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from vnxdna.archive.container import open_container
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.core.version import codec_id
from vnxdna.dnaenc.layout import PROFILES
from vnxdna.dnaenc.strandio import ReadBatch

INTERFACE = "vnx.provider/1"
#: maximum strand length a provider accepts unless configured otherwise (spec §9.3; common oligo-pool vendor limit)
DEFAULT_MAX_STRAND_NT = 350

#: status of the interface (spec §9.1), kept here so that docs and tests read one source
STATUS = (
    {"item": "DNAWriter, DNAReader, DNAProvider protocols", "interface_specified": "yes", "interface_implemented": "yes",
     "provider_integration_tested": "software only"},
    {"item": "ReferenceSimulatorProvider", "interface_specified": "yes", "interface_implemented": "yes",
     "provider_integration_tested": "software only, SIMULATED"},
    {"item": "Export and import package manifests", "interface_specified": "yes", "interface_implemented": "yes",
     "provider_integration_tested": "software only"},
    {"item": "Physical record schema and validator (vnxdna.physical)", "interface_specified": "yes",
     "interface_implemented": "yes", "provider_integration_tested": "no physical run has occurred"},
    {"item": "Any synthesis or sequencing vendor adapter", "interface_specified": "no", "interface_implemented": "no",
     "provider_integration_tested": "no (V11)"},
)

StrandSource = str | os.PathLike


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ================================================================================================================ values
@dataclass(frozen=True)
class PrimerPair:
    """Primer flanks (spec §3.6): SPECIFIED, NOT IMPLEMENTED (V7). Accepted by the signature, refused by ``prepare``."""

    forward: str
    reverse: str
    primer_id: int | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider accepts. ``None`` = not limited by this provider. ``max_strand_nt`` is configurable."""

    max_strand_nt: int = DEFAULT_MAX_STRAND_NT
    min_strand_nt: int = 0
    alphabet: str = "ACGT"
    max_pool_strands: int | None = None
    gc_min_percent: int | None = None
    gc_max_percent: int | None = None
    max_homopolymer: int | None = None
    primers_supported: bool = False

    def validate(self) -> "ProviderCapabilities":
        if not isinstance(self.max_strand_nt, int) or isinstance(self.max_strand_nt, bool) or self.max_strand_nt < 1:
            raise VNXConfigurationError("max_strand_nt must be a positive integer", details={"max_strand_nt": self.max_strand_nt})
        if not isinstance(self.min_strand_nt, int) or not 0 <= self.min_strand_nt <= self.max_strand_nt:
            raise VNXConfigurationError("min_strand_nt must be in 0..max_strand_nt")
        if self.alphabet != "ACGT":
            raise VNXConfigurationError("only the ACGT alphabet is supported")
        for name in ("max_pool_strands", "gc_min_percent", "gc_max_percent", "max_homopolymer"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                raise VNXConfigurationError(f"{name} must be a non-negative integer or null")
        if self.gc_min_percent is not None and self.gc_max_percent is not None and \
                not self.gc_min_percent <= self.gc_max_percent <= 100:
            raise VNXConfigurationError("require gc_min_percent <= gc_max_percent <= 100")
        return self

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ArchiveRef:
    """One encoded archive as an export package describes it (spec §9.3 ``archives[]``). Built from the container and
    the encoder report by :meth:`from_encode`; the container itself is never put in a package (it may be confidential)."""

    archive_id: str
    container_sha256: str
    container_size: int
    encrypted: bool
    codec: str
    frame_version: int
    superblock_version: int
    strand_profile: str
    layout: dict
    outer: dict
    pool_tag: int
    strand_count: int
    strands_sha256: str
    redundancy_profile: str | None = None
    primer_id: int | None = None

    @classmethod
    def from_encode(cls, container: str | os.PathLike, report: dict, *, strand_profile: str | None = None,
                    redundancy_profile: str | None = None) -> "ArchiveRef":
        """``report`` is the encoder report (``sdk.encode(...).body`` or ``vnx.result/1["result"]``)."""
        if not isinstance(report, dict):
            raise VNXConfigurationError("the encoder report must be a JSON object")
        c = open_container(container)
        aid = c.archive_id.hex()
        if report.get("archive_id") != aid:
            raise VNXConfigurationError("the encoder report belongs to another archive",
                                        details={"container_archive_id": aid, "report_archive_id": report.get("archive_id")})
        size = Path(container).stat().st_size
        if report.get("container_bytes") != size:
            raise VNXConfigurationError("the encoder report's container size differs from the container",
                                        details={"container_bytes": size, "report": report.get("container_bytes")})
        lay = report.get("layout") or {}
        lay4 = {"P": lay.get("payload_bytes"), "r": lay.get("inner_parity"), "marker_period": lay.get("marker_period"),
                "marker_len": lay.get("marker_len")}
        oc = report.get("outer_code") or {}
        v6 = report.get("outer_v6")
        if v6:
            outer = {"name": oc.get("name"), "K": v6["K"], "M": v6["M"], "D": v6["D"], "Mc": v6["Mc"], "order": v6["order"]}
            sbv = int(v6["superblock_version"])
        else:
            outer = {"name": oc.get("name"), "K": oc.get("data_symbols"), "M": oc.get("parity_symbols"), "D": 1, "Mc": 0,
                     "order": "sequential"}
            sbv = 1
        matches = sorted(n for n, (pl, k, m) in PROFILES.items()
                         if (pl.payload_bytes, pl.inner_parity, pl.marker_period, pl.marker_len) == tuple(lay4.values()))
        if strand_profile is None:
            exact = [n for n in matches if PROFILES[n][1:] == (outer["K"], outer["M"])]
            if len(exact) != 1:
                raise VNXConfigurationError("cannot tell the strand profile from the report; pass strand_profile",
                                            details={"candidates": matches})
            strand_profile = exact[0]
        elif strand_profile not in matches:
            raise VNXConfigurationError(f"strand profile {strand_profile!r} does not have this archive's layout",
                                        details={"layout": lay4, "profiles_with_this_layout": matches})
        tag = int.from_bytes(c.archive_id[:2], "big")
        if report.get("archive_tag") not in (None, f"{tag:04x}"):
            raise VNXConfigurationError("the encoder report's archive tag differs from the archive ID")
        sha = report.get("file_sha256")
        if not (isinstance(sha, str) and len(sha) == 64):
            raise VNXConfigurationError("the encoder report has no strand-file SHA-256 (file_sha256)")
        return cls(archive_id=aid, container_sha256=sha256_file(container), container_size=size, encrypted=c.encrypted,
                   codec=codec_id(4, sbv, str(outer["name"])), frame_version=4, superblock_version=sbv,
                   strand_profile=strand_profile, layout=lay4, outer=outer, pool_tag=tag,
                   strand_count=int(report["strands"]), strands_sha256=sha, redundancy_profile=redundancy_profile)


@dataclass(frozen=True)
class PoolRef:
    """A pool known to a provider: the stand-in for one tube (or plate well) holding the strands of an export package."""

    pool_id: str
    provider: str
    export_package_id: str
    strands_sha256: str
    archives: tuple = ()        # ({"archive_id", "pool_tag", "primer_id", "strand_count", "first_strand"}, ...)

    def to_dict(self) -> dict:
        return {"pool_id": self.pool_id, "provider": self.provider, "export_package_id": self.export_package_id,
                "strands_sha256": self.strands_sha256, "archives": [dict(a) for a in self.archives]}


@dataclass(frozen=True)
class Selection:
    """Which part of a pool to retrieve. ``primer_id`` None = the whole pool (random access by primer is V7)."""

    primer_id: int | None = None


@dataclass(frozen=True)
class SequencingRequest:
    """How to sequence a retrieved pool. For the reference simulator: a named channel model (or a model file or
    document), an explicit seed and an optional coverage that overrides the model's mean coverage."""

    model: str | os.PathLike | dict = "illumina-like"
    seed: int = 0
    coverage: float | None = None
    workers: int = 1             # simulation workers; the reads do not depend on it


@dataclass(frozen=True)
class WriteReceipt:
    pool: PoolRef
    receipt: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExportPackage:
    """A VNX Export Package directory (``<package_id>.vnxexp``) and its verified manifest."""

    path: Path
    manifest: dict

    @property
    def package_id(self) -> str:
        return self.manifest["package_id"]

    @property
    def strands_path(self) -> Path:
        return self.path / next(f["name"] for f in self.manifest["files"] if f["role"] == "strands")

    @property
    def evidence_class(self) -> str:
        return self.manifest["evidence_class"]


@dataclass(frozen=True)
class ImportPackage:
    """A VNX Import Package directory (``<package_id>.vnximp``) and its verified manifest."""

    path: Path
    manifest: dict

    @property
    def package_id(self) -> str:
        return self.manifest["package_id"]

    @property
    def read_files(self) -> list[Path]:
        return [self.path / f["name"] for f in self.manifest["files"]]

    @property
    def evidence_class(self) -> str:
        return self.manifest["evidence_class"]


# ============================================================================================================= protocols
@runtime_checkable
class DNAWriter(Protocol):
    def prepare(self, strands: StrandSource, *, archive: ArchiveRef, profile: str, primers: PrimerPair | None = None,
                pool_tag: int | None = None) -> ExportPackage: ...

    def write(self, package: ExportPackage) -> WriteReceipt: ...


@runtime_checkable
class DNAReader(Protocol):
    def retrieve(self, pool: PoolRef, *, selection: Selection | None = None,
                 sequencing: SequencingRequest) -> ImportPackage: ...

    def read(self, package: ImportPackage) -> Iterator[ReadBatch]: ...


@runtime_checkable
class DNAProvider(DNAWriter, DNAReader, Protocol):
    name: str
    version: str
    interface: Literal["vnx.provider/1"]

    def capabilities(self) -> ProviderCapabilities: ...

    def list(self) -> list[PoolRef]: ...

    def search(self, *, archive_id: bytes | str | None = None, pool_tag: int | None = None,
               primer_id: int | None = None) -> builtins.list[PoolRef]: ...

    def close(self) -> None: ...


__all__ = ["INTERFACE", "DEFAULT_MAX_STRAND_NT", "STATUS", "StrandSource", "PrimerPair", "ProviderCapabilities",
           "ArchiveRef", "PoolRef", "Selection", "SequencingRequest", "WriteReceipt", "ExportPackage", "ImportPackage",
           "DNAWriter", "DNAReader", "DNAProvider", "sha256_file"]
