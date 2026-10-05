"""``ReferenceSimulatorProvider``: the only provider in 6.x, entirely in software (spec §9.2). SIMULATED.

It stands in for a laboratory so that the provider interface and the packages can be tested end to end:

* ``prepare`` builds a VNX Export Package (all checks, nothing published on failure);
* ``write`` copies the package's strand file into ``<directory>/pools/<pool_id>/`` — a directory standing in for a
  tube — and records its SHA-256;
* ``retrieve`` re-checks the tube's SHA-256 and passes the strands through a named, versioned channel model (strand
  loss, then coverage, per-read errors and qualities) with an explicit seed and coverage, and writes a VNX Import
  Package whose ``evidence_class`` is ``SIMULATED`` and which records the model name, version, schema, SHA-256 and seed;
* ``read`` verifies the import package and streams its reads to decode stage D0.

Deterministic: an import package is a function of (strand file SHA-256, model@version, seed, coverage, selection),
independent of the worker count. No DNA is synthesised, stored or sequenced; the channel models are stress settings,
not fitted to any platform.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path

from vnxdna.core.errors import (VNXConfigurationError, VNXError, VNXProviderError, VNXUnsupportedVersionError)
from vnxdna.core.version import __version__
from vnxdna.dnaenc.constraints import ConstraintConfig, iter_fasta
from vnxdna.dnaenc.strandio import ReadBatch, iter_batches
from vnxdna.providers.base import (INTERFACE, ArchiveRef, ExportPackage, ImportPackage, PoolRef, PrimerPair,
                                   ProviderCapabilities, Selection, SequencingRequest, WriteReceipt, sha256_file)
from vnxdna.providers.packages import build_export_package, build_import_package, load_export_package, load_import_package
from vnxdna.simulation.channel import ChannelConfig, simulate_file
from vnxdna.simulation.loss import LossConfig, apply_loss

NAME = "reference-simulator"
VERSION = "1.0.0"
#: the existing model files (``experiments/v6/channel/models``) carry no ``schema`` key: they are read as ``/0``
MODEL_SCHEMA_0 = "vnx.channel-model/0"
_POOL_ID = re.compile(r"^[0-9a-f]{64}$")


class ChannelModel:
    """A named, versioned channel model document (``vnx.channel-model/0``: name, version, classification SIMULATED,
    ``loss`` and ``channel`` sections). Every parameter must be explicit; the document's SHA-256 is recorded."""

    def __init__(self, doc: dict):
        if not isinstance(doc, dict):
            raise VNXConfigurationError("a channel model must be a JSON object")
        schema = doc.get("schema", MODEL_SCHEMA_0)
        if schema != MODEL_SCHEMA_0:
            raise VNXUnsupportedVersionError(f"channel model schema {schema!r} is not supported by this provider "
                                             f"(reads {MODEL_SCHEMA_0})", code="SCHEMA_UNSUPPORTED", stage="provider")
        for key in ("name", "version", "classification", "loss", "channel"):
            if key not in doc:
                raise VNXConfigurationError(f"channel model: missing key {key!r}")
        if doc["classification"] != "SIMULATED":
            raise VNXConfigurationError("channel model: classification must be SIMULATED")
        if not isinstance(doc["loss"], dict) or not isinstance(doc["channel"], dict):
            raise VNXConfigurationError("channel model: loss and channel must be objects")
        if "seed" in doc["channel"] or "seed" in doc["loss"]:
            raise VNXConfigurationError("channel model: the seed is given by the sequencing request, not the model")
        self.doc = doc
        self.name, self.version, self.schema = str(doc["name"]), str(doc["version"]), schema
        self.sha256 = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.channel_config(0, None)       # validates
        self.loss_config(0)

    def channel_config(self, seed: int, coverage: float | None) -> ChannelConfig:
        ch = dict(self.doc["channel"])
        if coverage is not None:
            ch["coverage"] = coverage
        return ChannelConfig.from_dict({**ch, "seed": seed})

    def loss_config(self, seed: int) -> LossConfig:
        try:
            return LossConfig(**self.doc["loss"], seed=seed).validate()
        except TypeError as error:
            raise VNXConfigurationError(f"channel model: invalid loss section: {error}") from None


class ReferenceSimulatorProvider:
    """The software reference provider. ``directory`` holds its exports, pools ("tubes") and imports.

    ``models_dir``: where named channel models are looked up (``<name>.json``); a request may also give a model file
    or a model document. ``capabilities``: the vendor limits to enforce (default: 350 nt maximum strand length)."""

    name = NAME
    version = VERSION
    interface = INTERFACE

    def __init__(self, directory: str | os.PathLike, *, models_dir: str | os.PathLike | None = None,
                 capabilities: ProviderCapabilities | None = None, constraints: ConstraintConfig | None = None):
        self.directory = Path(directory)
        self.models_dir = Path(models_dir) if models_dir is not None else None
        self._caps = (capabilities or ProviderCapabilities()).validate()
        self._constraints = constraints
        self._closed = False
        for sub in ("exports", "pools", "imports"):
            (self.directory / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------------------------------------------ helpers
    @property
    def _target(self) -> dict:
        return {"name": self.name, "version": self.version, "interface": self.interface}

    def _open(self) -> None:
        if self._closed:
            raise VNXProviderError(f"provider {self.name} is closed")

    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    # ------------------------------------------------------------------------------------------------------ writer
    def prepare(self, strands, *, archive: ArchiveRef, profile: str, primers: PrimerPair | None = None,
                pool_tag: int | None = None, out_dir: str | os.PathLike | None = None) -> ExportPackage:
        """One archive → export package (spec §9.2 signature). Pure and deterministic."""
        return self.prepare_pool([(strands, archive)], profiles=[profile], primers=primers, pool_tags=[pool_tag],
                                 out_dir=out_dir)

    def prepare_pool(self, items: Sequence[tuple], *, profiles: Sequence[str | None] | None = None,
                     primers: PrimerPair | None = None, pool_tags: Sequence[int | None] | None = None,
                     out_dir: str | os.PathLike | None = None) -> ExportPackage:
        """Several (strand file, ArchiveRef) pairs → one export package: a pool (pool-composition rule, spec §3.9)."""
        self._open()
        if primers is not None:
            raise VNXConfigurationError("primer flanks are SPECIFIED, NOT IMPLEMENTED (spec §3.6, V7); this provider "
                                        "does not attach primers", stage="export")
        profiles = list(profiles) if profiles is not None else [None] * len(items)
        pool_tags = list(pool_tags) if pool_tags is not None else [None] * len(items)
        for (_, a), prof, tag in zip(items, profiles, pool_tags):
            if prof is not None and prof != a.strand_profile:
                raise VNXConfigurationError(f"profile {prof!r} differs from the archive's strand profile "
                                            f"{a.strand_profile!r}", stage="export")
            if tag is not None and tag != a.pool_tag:
                raise VNXConfigurationError("frame-4 tags are derived from the archive ID and cannot be assigned; "
                                            "assigned pool tags need frame 6 (spec §3.5, V7)", stage="export",
                                            details={"requested": tag, "archive_tag": a.pool_tag})
        return build_export_package(items, Path(out_dir) if out_dir is not None else self.directory / "exports",
                                    provider=self._target, capabilities=self._caps, constraints=self._constraints,
                                    evidence_class="SIMULATED")

    def write(self, package: ExportPackage) -> WriteReceipt:
        """Store the package's strands in a pool directory (the stand-in for a tube) and record their SHA-256."""
        self._open()
        pkg = load_export_package(package.path)          # re-verify: the package may have been edited
        if pkg.manifest["provider_target"] != self._target:
            raise VNXProviderError(f"the package targets {pkg.manifest['provider_target']}, not {self._target}")
        pool_id = pkg.package_id
        pdir = self.directory / "pools" / pool_id
        sha = sha256_file(pkg.strands_path)
        archives = tuple({"archive_id": a["archive_id"], "pool_tag": a["pool_tag"], "primer_id": a["primer_id"],
                          "strand_count": a["strand_count"], "first_strand": a["first_strand"]}
                         for a in pkg.manifest["archives"])
        pool = PoolRef(pool_id, self.name, pkg.package_id, sha, archives)
        receipt = {"schema": "vnx.provider-receipt/1", "operation": "write", "provider": self._target,
                   "evidence_class": "SIMULATED", "pool": pool.to_dict(),
                   "note": "software only: the strands were copied to a directory standing in for a tube"}
        if pdir.exists():
            if self._pool(pool_id) != pool:
                raise VNXProviderError(f"pool {pool_id} exists with other content")
            return WriteReceipt(pool, receipt)
        tmp = Path(tempfile.mkdtemp(prefix=".pool-", dir=self.directory / "pools"))
        try:
            shutil.copyfile(pkg.strands_path, tmp / "strands.fasta")
            if sha256_file(tmp / "strands.fasta") != sha:
                raise VNXProviderError("the stored strands do not match the package (copy failed)")
            (tmp / "pool.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, pdir)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return WriteReceipt(pool, receipt)

    # ------------------------------------------------------------------------------------------------------ catalogue
    def _pool(self, pool_id: str) -> PoolRef:
        if not isinstance(pool_id, str) or not _POOL_ID.match(pool_id):
            raise VNXProviderError(f"invalid pool ID {pool_id!r}")
        p = self.directory / "pools" / pool_id / "pool.json"
        try:
            d = json.loads(p.read_text())["pool"]
            return PoolRef(d["pool_id"], d["provider"], d["export_package_id"], d["strands_sha256"],
                           tuple(dict(a) for a in d["archives"]))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise VNXProviderError(f"pool {pool_id} is unknown to provider {self.name} or its record is damaged: "
                                   f"{error}") from None

    def list(self) -> list[PoolRef]:
        self._open()
        return [self._pool(p.name) for p in sorted((self.directory / "pools").iterdir())
                if p.is_dir() and _POOL_ID.match(p.name)]

    def search(self, *, archive_id: bytes | str | None = None, pool_tag: int | None = None,
               primer_id: int | None = None) -> builtins.list[PoolRef]:
        aid = archive_id.hex() if isinstance(archive_id, (bytes, bytearray)) else archive_id
        out = []
        for pool in self.list():
            if any((aid is None or a["archive_id"] == aid) and (pool_tag is None or a["pool_tag"] == pool_tag)
                   and (primer_id is None or a["primer_id"] == primer_id) for a in pool.archives):
                out.append(pool)
        return out

    # ------------------------------------------------------------------------------------------------------ reader
    def _model(self, spec) -> ChannelModel:
        if isinstance(spec, dict):
            return ChannelModel(spec)
        p = Path(str(spec))
        if not p.is_file() and self.models_dir is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(spec)):
            p = self.models_dir / f"{spec}.json"
        if not p.is_file():
            raise VNXConfigurationError(f"unknown channel model {spec!r} (models directory: {self.models_dir})")
        try:
            return ChannelModel(json.loads(p.read_text()))
        except ValueError as error:
            raise VNXConfigurationError(f"channel model {p}: invalid JSON: {error}") from None

    def retrieve(self, pool: PoolRef, *, selection: Selection | None = None, sequencing: SequencingRequest,
                 out_dir: str | os.PathLike | None = None) -> ImportPackage:
        """Sample the pool, apply the channel model and write an import package (SIMULATED)."""
        self._open()
        if not isinstance(sequencing, SequencingRequest):
            raise VNXConfigurationError("sequencing must be a SequencingRequest")
        seed = sequencing.seed
        if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2 ** 63:
            raise VNXConfigurationError("seed must be a non-negative 63-bit integer")
        model = self._model(sequencing.model)
        cfg = model.channel_config(seed, sequencing.coverage)
        loss = model.loss_config(seed)
        stored = self._pool(pool.pool_id if isinstance(pool, PoolRef) else pool)
        tube = self.directory / "pools" / stored.pool_id / "strands.fasta"
        try:
            ok = tube.is_file() and not tube.is_symlink() and sha256_file(tube) == stored.strands_sha256
        except OSError:
            ok = False
        if not ok:
            raise VNXProviderError(f"pool {stored.pool_id}: the stored strands are missing or do not match their "
                                   "recorded SHA-256", details={"pool_id": stored.pool_id})
        sel = selection or Selection()
        ranges = [(a["first_strand"], a["first_strand"] + a["strand_count"]) for a in stored.archives
                  if sel.primer_id is None or a["primer_id"] == sel.primer_id]
        if not ranges:
            raise VNXProviderError(f"pool {stored.pool_id} holds no archive with primer ID {sel.primer_id}")
        work = Path(tempfile.mkdtemp(prefix=".retrieve-", dir=self.directory / "imports"))
        try:
            src = tube
            if sel.primer_id is not None:
                src = work / "selected.fasta"
                with open(src, "w", encoding="ascii") as out:
                    for i, (name, seq) in enumerate(iter_fasta(tube)):
                        if any(lo <= i < hi for lo, hi in ranges):
                            out.write(f">{name}\n{seq}\n")
            n_in = sum(hi - lo for lo, hi in ranges)
            lost = 0
            if loss.dropout or (loss.burst_count and loss.burst_length):
                surv = work / "surviving.fasta"
                rep = apply_loss(str(src), str(surv), loss)
                lost, src = int(rep["lost"]), surv
                if rep["kept"] == 0:
                    raise VNXProviderError("the simulated retrieval lost every strand")
            stats = simulate_file(src, work / "r1.fastq", cfg, fmt="fastq", workers=sequencing.workers, overwrite=True)
            simulation = {"model": model.name, "model_version": model.version, "model_schema": model.schema,
                          "model_sha256": model.sha256, "seed": seed, "coverage": float(cfg.coverage),
                          "simulator_software": __version__, "strands_in": n_in, "strands_lost": lost,
                          "reads": int(stats.get("reads", 0))}
            pkg = build_import_package([work / "r1.fastq"], Path(out_dir) if out_dir is not None else self.directory / "imports",
                                       provider=self._target, evidence_class="SIMULATED",
                                       export_package_id=stored.export_package_id, simulation=simulation,
                                       sequencing={"platform": None, "run_id": None, "layout": "single",
                                                   "primers_trimmed": False},
                                       primer_id=sel.primer_id, move=True)
        except VNXError:
            raise
        except OSError as error:
            raise VNXProviderError(f"simulated retrieval failed: {error}") from None
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return pkg

    def read(self, package: ImportPackage, *, batch_reads: int = 65536) -> Iterator[ReadBatch]:
        """Verify the import package, then stream its reads (decode stage D0)."""
        self._open()
        pkg = load_import_package(package.path)
        for f in pkg.read_files:
            yield from iter_batches(f, batch_reads)

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> "ReferenceSimulatorProvider":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["NAME", "VERSION", "MODEL_SCHEMA_0", "ChannelModel", "ReferenceSimulatorProvider"]
