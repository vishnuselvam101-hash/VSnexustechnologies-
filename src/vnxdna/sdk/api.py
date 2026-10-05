"""The stable VNX-DNA Python API (V6_ARCHITECTURE §4). Every function returns a :class:`~vnxdna.sdk.envelope.Result`
(``to_json()`` = ``vnx.result/1``) and raises :class:`vnxdna.core.errors.VNXError` subclasses with stable codes.

The ``vnx`` CLI (:mod:`vnxdna.commands`) is a thin layer over these functions: argument parsing, one call, JSON output.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from vnxdna.archive import container as _ct
from vnxdna.archive import operations as _ar
from vnxdna.archive.operations import ArchiveOptions
from vnxdna.core.errors import (VNXConfigurationError, VNXDecodeError, VNXIntegrityError, VNXOutputError)
from vnxdna.core.version import FORMAT_VERSION, FRAME_VERSION, codec_id
from vnxdna.pipeline import decode as _de
from vnxdna.pipeline import encode as _en
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.planner import RecoveryBudget
from vnxdna.sdk.envelope import (ArchiveResult, BenchmarkResult, DecodeResult, EncodeResult, ExtractResult, InputHasher,
                                 InspectResult, ListResult, Result, SimulateResult, VerifyResult, error_json, file_ref)

PathLike = str | os.PathLike

__all__ = ["archive", "encode", "decode", "inspect", "verify", "extract", "list_entries", "locate", "simulate", "benchmark",
           "codec_compare", "parse_size", "sweep", "channel_models", "channel_model", "channel_convert",
           "channel_sweep", "experiment_run", "experiment_reproduce", "generate", "validate_strands", "profiles",
           "native", "keygen", "load_keys", "version", "conformance", "is_container"]

CONTAINER = [FORMAT_VERSION[0], FORMAT_VERSION[1]]


def _resolved(obj) -> dict:
    from vnxdna.sdk.config import resolved
    return resolved(obj)


def is_container(path: PathLike) -> bool:
    """True if ``path`` is a file that starts with the VNX4 container magic (reads 8 bytes only)."""
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size < 8:
            return False
        with open(p, "rb") as f:
            return f.read(8) == _ct.MAGIC
    except OSError:
        return False


# ================================================================================================================= keys
def load_keys(key_file: PathLike | None = None, passphrase_env: str | None = None) -> tuple[bytes | None, str | None]:
    """(key, passphrase) from a key file and/or the name of an environment variable holding a passphrase."""
    from vnxdna.archive.crypto import load_key_file
    key = load_key_file(key_file) if key_file else None
    pw = None
    if passphrase_env:
        pw = os.environ.get(passphrase_env)
        if not pw:
            raise VNXConfigurationError(f"environment variable {passphrase_env} is empty or not set")
    return key, pw


def keygen(output: PathLike) -> Result:
    from vnxdna.archive.crypto import generate_key_file
    try:
        generate_key_file(output)
    except FileExistsError:
        raise VNXOutputError(f"{output} exists") from None
    return Result("keygen", "OK", {"status": "OK", "key_file": str(output)}, outputs=(file_ref("key", output),))


# ================================================================================================================= archive
def archive(inputs: Sequence[PathLike], output: PathLike, *, options: ArchiveOptions | None = None, overwrite: bool = False,
            progress=None, archive_id: bytes | None = None, salt: bytes | None = None) -> ArchiveResult:
    """E0–E6: files and directories → a VNX4 container."""
    t0 = time.perf_counter()
    opts = options or ArchiveOptions()
    rep = _ar.build_archive([Path(p) for p in inputs], output, opts, overwrite=overwrite, progress=progress,
                            archive_id=archive_id, salt=salt).to_dict()
    return ArchiveResult("archive", "SUCCESS", rep, inputs=tuple(file_ref("input", p) for p in inputs),
                         outputs=(file_ref("container", output, hash_file=True),),
                         formats={"container": CONTAINER, "frame_version": None, "superblock_version": None, "codec": None},
                         seconds=time.perf_counter() - t0, workers=opts.workers, config=_resolved(opts))


def _archive_formats() -> dict:
    return {"container": CONTAINER, "frame_version": None, "superblock_version": None, "codec": None}


def inspect(path: PathLike, *, deep: bool = False, key: bytes | None = None, passphrase: str | None = None,
            allow_unencrypted: bool = False) -> InspectResult:
    """"Can I read this?" for a container or a read/strand file (spec §4.3). A container gives its manifest and structure;
    a read file gives the ``vnx.probe/1`` answer (frame version, strand profile, superblock with ``deep=True``)."""
    t0 = time.perf_counter()
    p = Path(path)
    if is_container(p):
        body = _ar.inspect_container(p, key=key, passphrase=passphrase, allow_unencrypted=allow_unencrypted)
        body.update(object="container", readable="yes")
        formats = _archive_formats()
    else:
        from vnxdna.recovery.probe import probe_file
        body = probe_file(p, deep=deep)
        formats = {"container": None, "frame_version": (body.get("frame") or {}).get("version"),
                   "superblock_version": (body.get("superblock") or {}).get("version"),
                   "codec": (body.get("superblock") or {}).get("codec")}
    return InspectResult("inspect", "SUCCESS", body, inputs=(file_ref("input", p),), formats=formats,
                         seconds=time.perf_counter() - t0)


def verify(container: PathLike, *, key: bytes | None = None, passphrase: str | None = None, chunk: int | None = None,
           allow_unencrypted: bool = False) -> VerifyResult:
    t0 = time.perf_counter()
    body = _ar.verify_container(container, key=key, passphrase=passphrase, chunk=chunk, allow_unencrypted=allow_unencrypted)
    return VerifyResult("verify", body.get("status", "SUCCESS"), body, inputs=(file_ref("container", container),),
                        formats=_archive_formats(), seconds=time.perf_counter() - t0,
                        warnings=tuple(body.get("warnings") or ()))


def extract(container: PathLike, output_dir: PathLike, *, files: Sequence[str] | None = None, key: bytes | None = None,
            passphrase: str | None = None, overwrite: bool = False, apply_metadata: bool = False,
            allow_unencrypted: bool = False) -> ExtractResult:
    t0 = time.perf_counter()
    body = _ar.extract(container, output_dir, key=key, passphrase=passphrase, names=list(files) if files else None,
                       overwrite=overwrite, apply_metadata=apply_metadata, allow_unencrypted=allow_unencrypted)
    return ExtractResult("extract", "SUCCESS", body, inputs=(file_ref("container", container),),
                         outputs=(file_ref("directory", output_dir),), formats=_archive_formats(),
                         seconds=time.perf_counter() - t0)


def list_entries(container: PathLike, *, key: bytes | None = None, passphrase: str | None = None,
                 allow_unencrypted: bool = False) -> ListResult:
    t0 = time.perf_counter()
    rows = _ar.list_container(container, key=key, passphrase=passphrase, allow_unencrypted=allow_unencrypted)
    return ListResult("list", "SUCCESS", {"entries": rows}, inputs=(file_ref("container", container),),
                      formats=_archive_formats(), seconds=time.perf_counter() - t0)


def locate(container: PathLike, name: str, *, dna_profile: str | None = None, dna: DNAOptions | None = None,
           key: bytes | None = None, passphrase: str | None = None, allow_unencrypted: bool = False) -> Result:
    """Chunk indices and container byte ranges of a file; with a DNA profile also its strand groups and records."""
    from vnxdna.pipeline.locate import locate as _locate
    t0 = time.perf_counter()
    body = _locate(container, name, profile=dna_profile, dna=dna, key=key, passphrase=passphrase,
                   allow_unencrypted=allow_unencrypted)
    return Result("locate", "SUCCESS", body, inputs=(file_ref("container", container),), formats=_archive_formats(),
                  seconds=time.perf_counter() - t0)


# ================================================================================================================= encode
def encode(source: PathLike | Sequence[PathLike], output: PathLike, *, dna: DNAOptions | None = None,
           archive_options: ArchiveOptions | None = None, key: bytes | None = None, passphrase: str | None = None,
           verify: bool = False, overwrite: bool = False, keep_archive: PathLike | None = None, progress=None) -> EncodeResult:
    """A container → strands (E7–E14), or files/directories → archive (E0–E6, with ``archive_options``) → strands.

    Archive options are passed to the archive builder; for a container source they are refused with
    ``CONFIGURATION_ERROR`` (the container is already built), never silently ignored (spec §2.3.3)."""
    t0 = time.perf_counter()
    opts = dna or DNAOptions()
    sources = [Path(source)] if isinstance(source, (str, os.PathLike)) else [Path(s) for s in source]
    container_source = len(sources) == 1 and is_container(sources[0])
    if container_source and archive_options is not None:
        raise VNXConfigurationError("archive options apply only when the source is not a VNX4 container "
                                    f"({sources[0]} already is one)", details={"source": str(sources[0])})
    if container_source and keep_archive is not None:
        raise VNXConfigurationError("--keep-archive applies only when the source is not a VNX4 container")
    tmpdir = None
    src = sources[0]
    archived = None
    try:
        if not container_source:
            aopt = archive_options or ArchiveOptions(workers=opts.workers)
            if key is not None or passphrase is not None:
                from dataclasses import replace
                aopt = replace(aopt, key=key, passphrase=passphrase)
            tmpdir = tempfile.mkdtemp(prefix="vnx-encode-")
            src = Path(keep_archive) if keep_archive else Path(tmpdir) / "archive.vnx"
            archived = _ar.build_archive(list[PathLike](sources), src, aopt, overwrite=overwrite).to_dict()
        rep = _en.encode_container(src, output, opts, overwrite=overwrite, progress=progress)
        if archived is not None:
            rep = {**rep, "archive": archived}
        if verify:
            res = _de.decode_reads(output, None, DecodeOptions(layout=opts.resolve()[0], workers=opts.workers))
            rep["verified_by_decoding"] = res.status == "SUCCESS"
            if res.status != "SUCCESS":
                raise VNXIntegrityError("encode verification failed: the strands do not decode to the container")
        rep["verified_after_encode"] = bool(verify)
        sb_version = rep.get("outer_v6", {}).get("superblock_version", 1)
        outer = rep["outer_code"].get("name", opts.outer_code) if isinstance(rep.get("outer_code"), dict) else opts.outer_code
        formats = {"container": CONTAINER, "frame_version": FRAME_VERSION, "superblock_version": sb_version,
                   "codec": codec_id(FRAME_VERSION, sb_version, outer)}
        outputs = [file_ref("strands", output, rep.get("file_sha256"))]
        if keep_archive:
            outputs.append(file_ref("container", keep_archive, hash_file=True))
        return EncodeResult("encode", "SUCCESS", rep, inputs=tuple(file_ref("source", s) for s in sources),
                            outputs=tuple(outputs),
                            formats=formats, seconds=time.perf_counter() - t0, workers=opts.workers, config=_resolved(opts))
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ================================================================================================================= decode
def decode(reads: PathLike, output: PathLike | None = None, *, options: DecodeOptions | None = None,
           select: Sequence[str] | None = None, extract_to: PathLike | None = None, partial_dir: PathLike | None = None,
           key: bytes | None = None, passphrase: str | None = None, allow_unencrypted: bool = False,
           budget: RecoveryBudget | None = None, overwrite: bool = False, observer=None, task_id: str | None = None,
           progress=None, input_hash: bool = True) -> DecodeResult:
    """Reads → verified container (D0–D13), optionally extracted (D14); ``select`` = random access to some files.

    status SUCCESS | PARTIAL | FAILURE. A FAILURE without an exception carries ``error`` (INSUFFICIENT_REDUNDANCY).
    An encrypted archive decoded without a key publishes the verified ciphertext container: ``encrypted: true``,
    ``content_verified: false`` (spec §2.3.3)."""
    t0 = time.perf_counter()
    opts = options or DecodeOptions()
    if budget is not None:
        from dataclasses import replace
        opts = replace(opts, recovery_budget=budget)      # never modify the caller's options
    if output is None and extract_to is None and not select:
        raise VNXConfigurationError("give an output container, an extraction directory, or files to select")
    hasher = InputHasher("reads", [reads], enabled=input_hash)
    target, tmp = output, None
    ok = False
    try:
        if select:
            res = _de.decode_reads(reads, None, opts, select=list(select), select_dir=extract_to or Path("."), key=key,
                                   passphrase=passphrase, overwrite=overwrite, progress=progress, observer=observer,
                                   task_id=task_id, allow_unencrypted=allow_unencrypted)
        else:
            if output is None:
                tmp = tempfile.mkdtemp(prefix="vnx-decode-")
                target = Path(tmp) / "recovered.vnx"
            res = _de.decode_reads(reads, target, opts, overwrite=overwrite, partial_dir=partial_dir, key=key,
                                   passphrase=passphrase, progress=progress, observer=observer, task_id=task_id,
                                   allow_unencrypted=allow_unencrypted)
            if res.status == "SUCCESS" and extract_to is not None and target is not None:
                res.report["extract"] = _ar.extract(target, extract_to, key=key, passphrase=passphrase, overwrite=overwrite,
                                                    allow_unencrypted=allow_unencrypted)
        ok = True
    finally:
        if not ok:
            hasher.cancel()
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    rep = res.report
    rep.setdefault("status", res.status)
    encrypted = rep.get("encrypted")
    written = rep.get("extract") if isinstance(rep.get("extract"), dict) else rep.get("partial_extract")
    rep["files_published"] = int(written.get("files", 0)) if isinstance(written, dict) else 0
    rep["content_verified"] = res.status == "SUCCESS" and (encrypted is False or isinstance(rep.get("extract"), dict))
    if select:
        rep["selected"] = list(select)
    error = None
    if res.status == "FAILURE":
        error = error_json(VNXDecodeError(f"{rep.get('groups_failed', 0)} group(s) could not be recovered and no file "
                                          "verified; nothing was published", stage="outer",
                                          details={"failed_groups": rep.get("failed_groups", [])}))
    sb = rep.get("superblock") or {}
    sbv = sb.get("version")
    outer = (sb.get("outer_code") or {}).get("name")
    formats = {"container": CONTAINER, "frame_version": FRAME_VERSION, "superblock_version": sbv,
               "codec": codec_id(FRAME_VERSION, sbv, outer) if sbv and outer else None}
    outputs = []
    if output is not None and res.status == "SUCCESS":
        outputs.append(file_ref("container", output, rep.get("container_sha256")))
    if extract_to is not None:
        outputs.append(file_ref("directory", extract_to))
    return DecodeResult("decode", res.status, rep, inputs=tuple(hasher.refs()), outputs=tuple(outputs), formats=formats,
                        seconds=time.perf_counter() - t0, stage_seconds=rep.get("stage_seconds"), workers=opts.workers,
                        error=error, config=_resolved(opts))


# ================================================================================================================= simulation
def _channel_model(model, config):
    """(ChannelModel, seed from the source, body labels) for ``simulate``: a named/path model or a ChannelConfig."""
    from vnxdna.simulation import channel as ch
    from vnxdna.simulation import model as cm
    from vnxdna.simulation import registry
    if model is not None and config is not None:
        raise VNXConfigurationError("give either a channel model or a channel configuration, not both")
    if model is not None:
        m, file_seed = (model, None) if isinstance(model, cm.ChannelModel) else registry.resolve(model)
    elif config is None or isinstance(config, ch.ChannelConfig):
        cfg = (config or ch.ChannelConfig()).validate()
        m, file_seed = cm.channel_config_to_model(cfg.to_dict()), cfg.seed
    else:
        m, file_seed = cm.read_file(config)
    if m.read_as in (cm.CONFIG_SCHEMA_V0, cm.CONFIG_SCHEMA_V1):
        labels = {"model": "channel-config", "model_version": None, "model_schema": m.read_as}
    else:
        labels = {"model": m.name, "model_version": m.version, "model_schema": cm.SCHEMA_V1, "model_read_as": m.read_as,
                  "model_sha256": m.sha256}
    return m, file_seed, labels


def simulate(strands: PathLike, output: PathLike, *, config=None, model=None, seed: int | None = None,
             coverage: float | None = None, overrides: dict | None = None, workers: int = 1, overwrite: bool = False,
             progress=None, metadata: PathLike | None = None) -> SimulateResult:
    """SIMULATED channel: strands → reads, through synthesis → storage → amplification → sequencing.

    ``model`` is a shipped model name, ``NAME@VERSION``, a path to a model JSON (``vnx.channel-model/1`` or ``/0``) or a
    :class:`~vnxdna.simulation.model.ChannelModel`. ``config`` is the V4 ``ChannelConfig`` (an object or a JSON path,
    ``vnx.channel-config/0``); with neither, the ChannelConfig defaults are used. ``seed`` defaults to the config's seed
    (ChannelConfig) or 0 (models). ``coverage`` and ``overrides`` (V4 field names such as ``substitution_rate``, or
    dotted /1 paths such as ``sequencing.substitution.matrix``) change parameters and are recorded in the metadata.
    The result body carries ``metadata`` (``vnx.simulation-metadata/1``), also written to ``metadata`` if given."""
    import json as _json
    from vnxdna.simulation import engine
    from vnxdna.simulation import model as cm
    t0 = time.perf_counter()
    m, file_seed, labels = _channel_model(model, config)
    changes = dict(overrides or {})
    chosen_seed = changes.pop("seed", None)
    seed = seed if seed is not None else chosen_seed if chosen_seed is not None else file_seed if file_seed is not None else 0
    changes["coverage"] = coverage
    paths = cm.override_paths(m, changes)
    if paths:
        m = m.with_parameters(paths, label="overrides")
    body = engine.simulate_file(strands, output, m, seed, workers=workers, overwrite=overwrite, progress=progress,
                                overrides=paths)
    outputs = [file_ref("reads", output, body["metadata"]["output"]["sha256"])]
    if metadata is not None:
        from vnxdna.core.util import atomic_output
        with atomic_output(metadata, overwrite=True, mode=0o644) as tmp:
            Path(tmp).write_text(_json.dumps(body["metadata"], indent=2, sort_keys=True) + "\n")
        outputs.append(file_ref("metadata", metadata, hash_file=True))
    cfg_doc = None
    if labels["model"] == "channel-config" and not cm.v0_expressible(m.stages):
        cfg_doc = cm.v1_to_v0(m.doc)["channel"]
        cfg_doc["seed"] = seed
        body["config"] = cfg_doc
    seq = m.stages["sequencing"]
    body = {**body, **labels, "seed": seed, "coverage": seq["coverage"]["mean"], "coverage_model": seq["coverage"]["model"],
            "data_source": engine.DATA_SOURCE, "evidence_class": "SIMULATED"}
    return SimulateResult("simulate", "SUCCESS", body, inputs=(file_ref("strands", strands),), outputs=tuple(outputs),
                          seconds=time.perf_counter() - t0, workers=workers,
                          config=cfg_doc if cfg_doc is not None else {"model": m.doc, "seed": seed},
                          seeds={"channel": seed})


def channel_models() -> Result:
    """The shipped channel models (name, version, SHA-256, description, data source, evidence class)."""
    from vnxdna.simulation import registry
    models = [registry.load_model(f"{n}@{v}").describe() for n, v in registry.available()]
    return Result("channel-model", "SUCCESS", {"models": models, "count": len(models), "evidence_class": "SIMULATED"})


def channel_model(ref, *, schema: str = "vnx.channel-model/1") -> Result:
    """One model as its canonical ``vnx.channel-model/1`` document (or ``/0`` when it is expressible there)."""
    from vnxdna.simulation import model as cm
    from vnxdna.simulation import registry
    m, _ = registry.resolve(ref)
    if schema == cm.SCHEMA_V1:
        doc = m.to_json()
    elif schema == cm.SCHEMA_V0:
        doc = m.to_v0()
    else:
        raise VNXConfigurationError(f"schema must be {cm.SCHEMA_V1} or {cm.SCHEMA_V0}")
    return Result("channel-model", "SUCCESS", {"model": doc, "schema": schema, "info": m.describe()})


def channel_convert(source: PathLike, output: PathLike, *, overwrite: bool = False) -> Result:
    """Read a model in any readable schema (/0, /1, channel-config) and write its canonical ``vnx.channel-model/1``."""
    from vnxdna.core.util import atomic_output
    from vnxdna.simulation import model as cm
    m, _ = cm.read_file(source)
    with atomic_output(output, overwrite=overwrite, mode=0o644) as tmp:
        Path(tmp).write_text(m.dumps())
    return Result("channel-model", "SUCCESS", {"converted": True, "read_as": m.read_as, "schema": cm.SCHEMA_V1,
                                               "info": m.describe()},
                  inputs=(file_ref("model", source, hash_file=True),), outputs=(file_ref("model", output, hash_file=True),))


def channel_sweep(strands: PathLike, out_dir: PathLike, *, model=None, config=None, grid: dict | None = None,
                  trials: int = 10, base_seed: int = 0, workers: int = 1, keep_reads: bool = False,
                  output: PathLike | None = None) -> Result:
    """Monte Carlo (``trials`` seeds ``base_seed + i``) over every point of a parameter grid; ``vnx.channel-sweep/1``."""
    import json as _json
    from vnxdna.simulation import montecarlo
    t0 = time.perf_counter()
    m, _, _ = _channel_model(model, config)
    doc = montecarlo.sweep(m, strands, out_dir, grid, trials=trials, base_seed=base_seed, workers=workers,
                           keep_reads=keep_reads)
    outputs = []
    if output is not None:
        from vnxdna.core.util import atomic_output
        with atomic_output(output, overwrite=True, mode=0o644) as tmp:
            Path(tmp).write_text(_json.dumps(doc, indent=2, sort_keys=True) + "\n")
        outputs.append(file_ref("results", output, hash_file=True))
    return Result("channel-sweep", "SUCCESS", doc, inputs=(file_ref("strands", strands),), outputs=tuple(outputs),
                  seconds=time.perf_counter() - t0, workers=workers, config={"model": m.doc, "grid": grid},
                  seeds={"base_seed": base_seed, "trials": trials})


# ================================================================================================================= benchmarks
def benchmark(profile: str = "balanced", sizes: Sequence[int] = (1 << 20,), output: PathLike | None = None) -> BenchmarkResult:
    from vnxdna.benchmark import bench
    t0 = time.perf_counter()
    doc = bench.run_suite(profile, tuple(sizes), str(output) if output else None)
    return BenchmarkResult("benchmark", "SUCCESS", doc, seconds=time.perf_counter() - t0,
                           outputs=(file_ref("results", output),) if output else ())


def benchmark_markdown(results) -> str:
    """Markdown table of benchmark or codec-comparison results."""
    from vnxdna.benchmark import bench
    return bench.markdown_report(results)


def parse_size(text: str) -> int:
    """'1MB', '64MiB', '4096' → bytes."""
    from vnxdna.benchmark import datagen
    return datagen.parse_size(text)


def codec_compare(trials: int = 100) -> BenchmarkResult:
    from vnxdna.benchmark import bench
    t0 = time.perf_counter()
    return BenchmarkResult("benchmark", "SUCCESS", bench.codec_compare(trials=trials), seconds=time.perf_counter() - t0)


def sweep(config: PathLike, progress=None) -> Result:
    import json
    from vnxdna.benchmark import experiment, sweep as sw
    t0 = time.perf_counter()
    raw = json.loads(Path(config).read_text())
    cfg = experiment.load(config) if raw.get("type") else raw
    res = sw.run_sweep(cfg, progress=progress)
    return Result("sweep", "SUCCESS", res, inputs=(file_ref("config", config),), seconds=time.perf_counter() - t0)


def sweep_table(res: dict) -> str:
    from vnxdna.benchmark import sweep as sw
    return sw.curve_table(res)


def experiment_run(config: PathLike, progress=None) -> Result:
    from vnxdna.benchmark import experiment
    t0 = time.perf_counter()
    res = experiment.run(config, progress=progress)
    return Result("experiment", "DONE", {"status": "DONE", "directory": str(Path(config).parent),
                                         "experiment": res.get("experiment")},
                  inputs=(file_ref("config", config),), seconds=time.perf_counter() - t0)


def experiment_reproduce(directory: PathLike) -> Result:
    from vnxdna.benchmark import experiment
    t0 = time.perf_counter()
    rep = experiment.reproduce(directory)
    return Result("experiment", "SUCCESS" if rep.get("reproduced") else "FAILURE", rep, seconds=time.perf_counter() - t0)


def generate(output: PathLike, size: str | int = "1MB", pattern: str = "mixed", seed: int = 42) -> Result:
    from vnxdna.benchmark import datagen
    n = datagen.parse_size(size) if isinstance(size, str) else int(size)
    sha = datagen.generate(output, n, pattern, seed)
    return Result("generate", "SUCCESS", {"output": str(output), "size": n, "pattern": pattern, "seed": seed, "sha256": sha},
                  outputs=(file_ref("data", output, sha),), seeds={"datagen": seed})


def validate_strands(sequences: PathLike, *, constraints: PathLike | None = None, overrides: dict | None = None,
                     forbid: Sequence[str] | None = None, max_reported: int = 100) -> Result:
    """Biological constraint diagnostics for a strand file; status FAILURE when any sequence violates them."""
    from vnxdna.dnaenc.constraints import ConstraintConfig, validate_file
    cfg = ConstraintConfig.load(constraints) if constraints else ConstraintConfig()
    for name, v in (overrides or {}).items():
        if v is not None:
            setattr(cfg, name, v)
    if forbid:
        cfg.forbidden_motifs = list(forbid)
    cfg.validate()
    rep = validate_file(sequences, cfg, max_reported=max_reported)
    return Result("validate", "SUCCESS" if rep["valid"] else "FAILURE", rep, inputs=(file_ref("strands", sequences),))


def profiles() -> Result:
    from vnxdna.codec.profiles import REDUNDANCY_PROFILES
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.pipeline.performance import PERFORMANCE_PROFILES
    body = {"layouts": {n: {**lay.to_dict(), "outer_K": k, "outer_M": m} for n, (lay, k, m) in PROFILES.items()},
            "performance": PERFORMANCE_PROFILES, "redundancy": {n: dict(v) for n, v in REDUNDANCY_PROFILES.items()}}
    return Result("profiles", "SUCCESS", body)


def native() -> Result:
    """Backend of every native kernel; the top-level fields describe the V5 marker aligner (as since V5)."""
    from vnxdna.native import align as na
    from vnxdna.native import native_status
    st = native_status()
    return Result("native", "SUCCESS", {**na.status(), "kernels": st["kernels"], "all_native": st["all_native"]})


# ================================================================================================================= version
def version() -> dict:
    """``vnx.version/1`` (spec §4.2): every version axis, plus the 5.x ``vnx version`` keys."""
    import platform
    import sys
    from vnxdna._version import __version__
    from vnxdna.core import version as v
    from vnxdna.native import align as na
    from vnxdna.native import backend_summary
    from vnxdna.sdk.envelope import backends
    st = na.status()
    b = backends()
    return {"schema": "vnx.version/1", "software": __version__, "spec": v.SPEC_VERSION,
            "container": {"read": v.CONTAINER_READ, "write": v.CONTAINER_WRITE},
            "frame": {"read": v.FRAME_READ, "write": v.FRAME_WRITE, "legacy_detected": v.LEGACY_DETECTED},
            "superblock": {"read": v.SUPERBLOCK_READ, "write": v.SUPERBLOCK_WRITE}, "codecs": list(v.CODECS),
            "backends": {k: {kk: vv for kk, vv in r.items() if kk in ("active", "abi", "level")} for k, r in b.items()},
            "python": platform.python_version(), "platform": f"{sys.platform}-{platform.machine()}",
            # 5.x keys (kept in 6.x)
            "vnx": __version__, "vnx4_format": list(v.FORMAT_VERSION), "frame_version": v.FRAME_VERSION,
            "alignment_backend": st["active_backend"], "native_alignment": st["native_available"],
            "native_backends": backend_summary()}


# ================================================================================================================= conformance
def conformance(vectors: PathLike | None = None, *, select: Sequence[str] | None = None, backend: str = "auto") -> Result:
    """Run conformance vectors (spec §6; the package ships a small subset); ``result`` is ``vnx.conformance/1``."""
    from vnxdna.conformance import run
    t0 = time.perf_counter()
    doc = run(vectors, select=select, backend=backend)
    status = "SUCCESS" if doc["verdict"] == "CONFORMANT" else "FAILURE"
    return Result("conformance", status, doc, seconds=time.perf_counter() - t0)
