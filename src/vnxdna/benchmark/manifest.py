"""Experiment manifests (``vnx.experiment/1``): the record from which one experiment is re-run and checked.

A manifest names everything the result depends on (V6 directive §24): ``experiment_id``, ``source_class``,
``input_hash``, ``codec_version``, ``commit``, ``simulator_version``, ``seed``, ``parameters``, ``hardware``,
``workers`` and ``result_hash``, plus ``schema`` and ``kind``. Two kinds can be re-run by software:

``channel-simulation``
    strands → reads through :func:`vnxdna.simulation.engine.simulate_file`. ``parameters.model`` is the complete
    ``vnx.channel-model/1`` document (overrides already applied), so reproducing never depends on which models a later
    release ships; ``parameters.input.file`` is the strand file (relative to the manifest's directory unless absolute)
    and ``parameters.format`` the read format. ``input_hash`` = SHA-256 of the strand file, ``result_hash`` = SHA-256
    of the read file (the ``output.sha256`` of ``vnx.simulation-metadata/1``).
``experiment``
    an experiment configuration of :mod:`vnxdna.benchmark.experiment` (``parameters.config``: encode → SIMULATED
    channel → decode sweeps, end-to-end runs …). Inputs are generated from the configuration, so ``input_hash`` =
    SHA-256 of the canonical JSON of the configuration, and ``result_hash`` = SHA-256 of the canonical JSON of the
    deterministic results (:func:`vnxdna.benchmark.experiment.deterministic`: timings and memory removed). The
    generated input's own SHA-256 is part of those results.

Both kinds are SIMULATED (``source_class``). :func:`validate` is strict: unknown fields, wrong types, inconsistent
hashes or seeds are typed errors (:class:`ManifestError`, ``FORMAT_ERROR``, exit 3; an unknown schema is
``SCHEMA_UNSUPPORTED``, exit 6), never assertions. :func:`reproduce` re-runs a validated manifest and answers whether
``result_hash`` matches; ``codec_version``, ``commit``, ``simulator_version`` and ``hardware`` differences are reported,
not judged (the result hash is the verdict).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from vnxdna.core.errors import VNXConfigurationError, VNXFormatError, VNXUnsupportedVersionError

SCHEMA = "vnx.experiment/1"
REPRODUCTION_SCHEMA = "vnx.experiment-reproduction/1"
KINDS = ("channel-simulation", "experiment")
SOURCE_CLASSES = ("SIMULATED", "SYNTHETIC", "LABORATORY", "PHYSICAL_VALIDATION")
#: the source class of every kind this software can re-run
RERUNNABLE_SOURCE = "SIMULATED"
FIELDS = ("schema", "experiment_id", "kind", "source_class", "input_hash", "codec_version", "commit",
          "simulator_version", "seed", "parameters", "hardware", "workers", "result_hash")
OPTIONAL_FIELDS = ("created_utc", "statement", "result_hash_of")
MAX_BYTES = 16 << 20
MAX_WORKERS = 1024
MAX_DEPTH = 32
SEED_LIMIT = 2 ** 63
STATEMENT = ("SIMULATED: software inputs through a software channel model. No DNA was synthesised, stored, amplified or "
             "sequenced.")
HASH_OF = {"channel-simulation": "SHA-256 of the read file written by the simulator",
           "experiment": "SHA-256 of the canonical JSON of the deterministic results (timings and memory removed)"}

_HEX64 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?(?:-dirty)?")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}")
_VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")


class ManifestError(VNXFormatError):
    """A ``vnx.experiment/1`` manifest is malformed, inconsistent or cannot be read (``FORMAT_ERROR``, exit 3)."""

    stage = "manifest"


def _bad(field: str, message: str) -> ManifestError:
    return ManifestError(f"experiment manifest: {field}: {message}", details={"field": field})


# ============================================================================================================ hashing
def canonical_bytes(obj: Any) -> bytes:
    """Canonical JSON (sorted keys, no whitespace, UTF-8) — the form every manifest hash is computed over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def experiment_result_hash(results: dict) -> str:
    """``result_hash`` of an ``experiment`` run: the deterministic fields only (the ones ``reproduce`` compares)."""
    from vnxdna.benchmark.experiment import deterministic
    return sha256_json(deterministic(results))


# ============================================================================================================ validation
def _type(value: Any, types: tuple, field: str, what: str) -> Any:
    if isinstance(value, bool) and bool not in types:
        raise _bad(field, f"must be {what}, not a boolean")
    if not isinstance(value, types):
        raise _bad(field, f"must be {what}")
    return value


def _string(value: Any, field: str, pattern: re.Pattern | None = None, max_len: int = 4096) -> str:
    _type(value, (str,), field, "a string")
    if not value or len(value) > max_len or "\x00" in value:
        raise _bad(field, f"must be a non-empty string of at most {max_len} characters without NUL")
    if pattern is not None and not pattern.fullmatch(value):
        raise _bad(field, f"{value!r} does not match {pattern.pattern}")
    return value


def _integer(value: Any, field: str, lo: int, hi: int) -> int:
    _type(value, (int,), field, "an integer")
    if not lo <= value <= hi:
        raise _bad(field, f"must be in {lo}..{hi}")
    return value


def _json_value(value: Any, field: str, depth: int = 0) -> None:
    """Plain JSON only (objects with string keys, lists, strings, finite numbers, booleans, null), bounded depth."""
    if depth > MAX_DEPTH:
        raise _bad(field, f"nested deeper than {MAX_DEPTH} levels")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise _bad(field, "object keys must be strings")
            _json_value(v, f"{field}.{k}", depth + 1)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _json_value(v, f"{field}[{i}]", depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise _bad(field, "numbers must be finite")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise _bad(field, f"unsupported JSON value of type {type(value).__name__}")


def _object(value: Any, field: str, required: tuple, optional: tuple = ()) -> dict:
    _type(value, (dict,), field, "an object")
    missing = [k for k in required if k not in value]
    if missing:
        raise _bad(field, f"missing {missing}")
    extra = sorted(set(value) - set(required) - set(optional))
    if extra:
        raise _bad(field, f"unknown fields {extra}")
    return value


def _codec_version(value: Any) -> None:
    v = _object(value, "codec_version", ("software", "spec"), ("backends",))
    _string(v["software"], "codec_version.software", _VERSION)
    _string(v["spec"], "codec_version.spec", _VERSION)
    if "backends" in v:
        b = _type(v["backends"], (dict,), "codec_version.backends", "an object")
        for k, x in b.items():
            if x is not None:
                _string(x, f"codec_version.backends.{k}", max_len=64)


def _simulator_version(value: Any) -> None:
    v = _object(value, "simulator_version", ("simulator", "version", "model_schema", "model"))
    _string(v["simulator"], "simulator_version.simulator", max_len=128)
    _string(v["version"], "simulator_version.version", _VERSION)
    _string(v["model_schema"], "simulator_version.model_schema", max_len=64)
    if v["model"] is not None:
        m = _object(v["model"], "simulator_version.model", ("name", "version", "sha256"))
        _string(m["name"], "simulator_version.model.name", max_len=64)
        _string(m["version"], "simulator_version.model.version", _VERSION)
        _string(m["sha256"], "simulator_version.model.sha256", _HEX64)


def _hardware(value: Any) -> None:
    h = _type(value, (dict,), "hardware", "an object")
    for key in ("cpu_model", "logical_cpus"):
        if key not in h:
            raise _bad("hardware", f"missing {key!r}")
    if h["cpu_model"] is not None:
        _string(h["cpu_model"], "hardware.cpu_model", max_len=256)
    if h["logical_cpus"] is not None:
        _integer(h["logical_cpus"], "hardware.logical_cpus", 1, 1 << 20)
    _json_value(h, "hardware")


def _channel_parameters(doc: dict) -> None:
    from vnxdna.core.errors import VNXError
    from vnxdna.simulation.model import SCHEMA_V1, from_doc
    p = _object(doc["parameters"], "parameters", ("model", "input", "format"))
    inp = _object(p["input"], "parameters.input", ("file",))
    _string(inp["file"], "parameters.input.file")
    if p["format"] not in ("fasta", "fastq"):
        raise _bad("parameters.format", "must be 'fasta' or 'fastq'")
    model_doc = _type(p["model"], (dict,), "parameters.model", f"a {SCHEMA_V1} object")
    if model_doc.get("schema") != SCHEMA_V1:
        raise _bad("parameters.model", f"must be a {SCHEMA_V1} document")
    try:
        model, _ = from_doc(model_doc)
    except VNXError as error:
        raise _bad("parameters.model", f"invalid channel model ({error})") from None
    sv = doc["simulator_version"]
    if sv["model"] is None or sv["model_schema"] != SCHEMA_V1:
        raise _bad("simulator_version", f"a channel-simulation names its {SCHEMA_V1} model")
    if model.sha256 != sv["model"]["sha256"] or model.name != sv["model"]["name"] or \
            model.version != sv["model"]["version"]:
        raise _bad("simulator_version.model", "does not match parameters.model (name, version or canonical SHA-256)")


def _config_seed(cfg: dict) -> int:
    """The seed recorded for an ``experiment``: the channel seed of the configuration (0 if it has none)."""
    channel = cfg.get("channel")
    if isinstance(channel, dict) and "seed" in channel:
        return channel["seed"]
    return cfg.get("seed", 0)


def _experiment_parameters(doc: dict) -> None:
    from vnxdna.benchmark.experiment import TYPES
    p = _object(doc["parameters"], "parameters", ("config",))
    cfg = _type(p["config"], (dict,), "parameters.config", "an experiment configuration object")
    if cfg.get("type") not in TYPES:
        raise _bad("parameters.config.type", f"must be one of {list(TYPES)}")
    if sha256_json(cfg) != doc["input_hash"]:
        raise _bad("input_hash", "is not the SHA-256 of the canonical JSON of parameters.config")
    if _config_seed(cfg) != doc["seed"]:
        raise _bad("seed", "differs from the channel seed of parameters.config")
    if cfg.get("workers", 1) != doc["workers"]:
        raise _bad("workers", "differs from parameters.config.workers")


def validate(doc: Any) -> dict:
    """Check a manifest object; returns it unchanged. Raises :class:`ManifestError` (or ``SCHEMA_UNSUPPORTED``)."""
    _type(doc, (dict,), "manifest", "a JSON object")
    sid = doc.get("schema")
    if sid != SCHEMA:
        if isinstance(sid, str) and re.fullmatch(r"vnx\.experiment/\d+", sid):
            raise VNXUnsupportedVersionError(f"unsupported manifest schema {sid!r}: this software reads {SCHEMA}",
                                             code="SCHEMA_UNSUPPORTED", stage="manifest", details={"schema": sid})
        raise _bad("schema", f"must be {SCHEMA!r}")
    _object(doc, "manifest", FIELDS, OPTIONAL_FIELDS)
    _json_value(doc, "manifest")
    _string(doc["experiment_id"], "experiment_id", _ID)
    if doc["kind"] not in KINDS:
        raise _bad("kind", f"must be one of {list(KINDS)}")
    if doc["source_class"] not in SOURCE_CLASSES:
        raise _bad("source_class", f"must be one of {list(SOURCE_CLASSES)}")
    if doc["source_class"] != RERUNNABLE_SOURCE:
        raise _bad("source_class", f"a {doc['kind']} manifest is {RERUNNABLE_SOURCE} (software re-runs only simulations)")
    _string(doc["input_hash"], "input_hash", _HEX64)
    _string(doc["result_hash"], "result_hash", _HEX64)
    _codec_version(doc["codec_version"])
    if doc["commit"] is not None:
        _string(doc["commit"], "commit", _COMMIT)
    _simulator_version(doc["simulator_version"])
    _integer(doc["seed"], "seed", 0, SEED_LIMIT - 1)
    _integer(doc["workers"], "workers", 1, MAX_WORKERS)
    _hardware(doc["hardware"])
    for key in OPTIONAL_FIELDS:
        if key in doc:
            _string(doc[key], key)
    if doc["kind"] == "channel-simulation":
        _channel_parameters(doc)
    else:
        _experiment_parameters(doc)
    return doc


def _no_duplicates(pairs: list) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise _bad("manifest", f"duplicate key {k!r}")
        out[k] = v
    return out


def _no_constants(name: str) -> Any:
    raise _bad("manifest", f"non-standard JSON constant {name}")


def loads(data: bytes | str) -> dict:
    """Parse and validate manifest bytes (UTF-8 JSON, at most 16 MiB, no duplicate keys, no NaN/Infinity)."""
    raw = data.encode() if isinstance(data, str) else data
    if len(raw) > MAX_BYTES:
        raise _bad("manifest", f"larger than {MAX_BYTES} bytes")
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except UnicodeDecodeError:
        raise _bad("manifest", "not UTF-8") from None
    except RecursionError:
        raise _bad("manifest", "nested too deeply") from None
    except ValueError as error:
        if isinstance(error, ManifestError):
            raise
        raise _bad("manifest", f"invalid JSON ({error})") from None
    return validate(doc)


def read(path: str | os.PathLike) -> dict:
    p = Path(path)
    try:
        if p.stat().st_size > MAX_BYTES:
            raise _bad("manifest", f"{p} is larger than {MAX_BYTES} bytes")
        raw = p.read_bytes()
    except OSError as error:
        raise ManifestError(f"cannot read experiment manifest {p}: {error.strerror or error}",
                            details={"path": str(p)}) from None
    return loads(raw)


def dumps(doc: dict) -> str:
    return json.dumps(validate(doc), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(doc: dict, path: str | os.PathLike, *, overwrite: bool = False) -> Path:
    """Validate and write a manifest atomically (an invalid manifest is never written)."""
    from vnxdna.core.util import atomic_output
    text = dumps(doc)
    with atomic_output(path, overwrite=overwrite, mode=0o644) as tmp:
        tmp.write_text(text, encoding="utf-8")
    return Path(path)


# ============================================================================================================ writing
def codec_version() -> dict:
    from vnxdna._version import __version__
    from vnxdna.core.version import SPEC_VERSION
    from vnxdna.native import backend_summary
    backends = {k: (v.get("backend") if isinstance(v, dict) else None) for k, v in backend_summary().items()}
    return {"software": __version__, "spec": SPEC_VERSION, "backends": backends}


def hardware() -> dict:
    """The machine part of :func:`vnxdna.core.util.environment` (timestamp and git state are recorded elsewhere)."""
    from vnxdna.core.util import environment
    env = environment()
    return {k: env[k] for k in ("cpu_model", "logical_cpus", "ram_bytes", "os", "platform", "python", "packages")}


def _commit() -> str | None:
    from vnxdna.core.util import git_commit
    return git_commit()


def _simulator(model_ref: dict | None) -> dict:
    from vnxdna.simulation.engine import SIMULATOR, SIMULATOR_VERSION
    from vnxdna.simulation.model import SCHEMA_V1
    return {"simulator": SIMULATOR, "version": SIMULATOR_VERSION, "model_schema": SCHEMA_V1, "model": model_ref}


def _created() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _input_ref(strands: Path, manifest_path: str | os.PathLike | None) -> str:
    """The strand file as the manifest records it: relative to the manifest's directory when it lies inside that
    directory (so the pair can be moved together), otherwise absolute."""
    strands = Path(strands).resolve()
    if manifest_path is None:
        return str(strands)
    base = Path(manifest_path).resolve().parent
    try:
        return strands.relative_to(base).as_posix()
    except ValueError:
        return str(strands)


def simulation_manifest(metadata: dict, model, strands: str | os.PathLike, *, workers: int = 1,
                        manifest_path: str | os.PathLike | None = None, experiment_id: str | None = None) -> dict:
    """The manifest of one finished simulation, from its ``vnx.simulation-metadata/1`` and the model that ran."""
    seed = metadata["seed"]
    input_hash = metadata["input"]["sha256"]
    doc = {
        "schema": SCHEMA,
        "experiment_id": experiment_id or f"sim-{model.name}-s{seed}-{input_hash[:12]}",
        "kind": "channel-simulation",
        "source_class": metadata["data_source"],
        "input_hash": input_hash,
        "codec_version": codec_version(),
        "commit": _commit(),
        "simulator_version": _simulator({"name": model.name, "version": model.version, "sha256": model.sha256}),
        "seed": seed,
        "parameters": {"model": model.to_json(), "input": {"file": _input_ref(Path(strands), manifest_path)},
                       "format": metadata["output"]["format"]},
        "hardware": hardware(),
        "workers": workers,
        "result_hash": metadata["output"]["sha256"],
        "result_hash_of": HASH_OF["channel-simulation"],
        "created_utc": _created(),
        "statement": STATEMENT,
    }
    return validate(doc)


def _safe_id(value: Any) -> str | None:
    """A configuration ``id`` as an ``experiment_id`` (characters outside the allowed set become ``-``)."""
    if not isinstance(value, str) or not value:
        return None
    text = re.sub(r"[^A-Za-z0-9._:+-]", "-", value)[:128]
    return text if _ID.fullmatch(text) else None


def experiment_manifest(cfg: dict, results: dict, *, experiment_id: str | None = None) -> dict:
    """The manifest of one finished :func:`vnxdna.benchmark.experiment.run`."""
    doc = {
        "schema": SCHEMA,
        "experiment_id": experiment_id or _safe_id(cfg.get("id")) or f"exp-{sha256_json(cfg)[:12]}",
        "kind": "experiment",
        "source_class": RERUNNABLE_SOURCE,
        "input_hash": sha256_json(cfg),
        "codec_version": codec_version(),
        "commit": _commit(),
        "simulator_version": _simulator(None),
        "seed": _config_seed(cfg),
        "parameters": {"config": cfg},
        "hardware": hardware(),
        "workers": cfg.get("workers", 1),
        "result_hash": experiment_result_hash(results),
        "result_hash_of": HASH_OF["experiment"],
        "created_utc": _created(),
        "statement": STATEMENT,
    }
    return validate(doc)


# ============================================================================================================ reproducing
def _resolve_input(doc: dict, manifest_path: Path | None, override: str | os.PathLike | None) -> Path:
    if override is not None:
        return Path(override)
    p = Path(doc["parameters"]["input"]["file"])
    if not p.is_absolute() and manifest_path is not None:
        p = manifest_path.resolve().parent / p
    return p


def _compare(recorded: Any, observed: Any) -> str:
    if recorded is None or observed is None:
        return "UNKNOWN"
    return "SAME" if recorded == observed else "DIFFERENT"


def reproduce(manifest: str | os.PathLike | dict, *, input_path: str | os.PathLike | None = None,
              workers: int | None = None, workdir: str | os.PathLike | None = None) -> dict:
    """Re-run the experiment a manifest describes and compare ``result_hash`` (``vnx.experiment-reproduction/1``).

    ``reproduced`` is true only when the input hash and the result hash both match. A different input stops before
    the re-run (``checks.input_hash = MISMATCH``). ``workers`` overrides the recorded worker count (results do not
    depend on it); ``input_path`` overrides the recorded strand file of a ``channel-simulation``."""
    from vnxdna.simulation.engine import sha256_file
    path = None
    if isinstance(manifest, dict):
        doc = validate(manifest)
    else:
        path = Path(manifest)
        doc = read(path)
    if workers is not None and (isinstance(workers, bool) or not isinstance(workers, int)
                                or not 1 <= workers <= MAX_WORKERS):
        raise VNXConfigurationError(f"workers must be an integer in 1..{MAX_WORKERS}", details={"workers": workers})
    run_workers = workers if workers is not None else doc["workers"]
    observed: dict[str, Any] = {"input_hash": None, "result_hash": None, "workers": run_workers}
    checks: dict[str, str] = {}
    if doc["kind"] == "channel-simulation":
        from vnxdna.simulation.model import from_doc
        from vnxdna.simulation.engine import simulate_file
        strands = _resolve_input(doc, path, input_path)
        if not strands.is_file():
            raise ManifestError(f"experiment input not found: {strands}", details={"path": str(strands)},
                                hint="pass the strand file with --input")
        observed["input_hash"] = sha256_file(strands)
        if observed["input_hash"] == doc["input_hash"]:
            model, _ = from_doc(doc["parameters"]["model"])
            fmt = doc["parameters"]["format"]
            with tempfile.TemporaryDirectory(prefix="vnx-reproduce-", dir=workdir) as tmp:
                body = simulate_file(strands, Path(tmp) / f"reads.{fmt}", model, doc["seed"], fmt=fmt,
                                     workers=run_workers)
            observed["result_hash"] = body["metadata"]["output"]["sha256"]
    else:
        from vnxdna.benchmark import experiment
        cfg = dict(doc["parameters"]["config"])
        observed["input_hash"] = sha256_json(cfg)
        if workers is not None:
            cfg["workers"] = workers
        results = experiment.execute(cfg, None if workdir is None else str(workdir))
        observed["result_hash"] = experiment_result_hash(results)
    checks["input_hash"] = "MATCH" if observed["input_hash"] == doc["input_hash"] else "MISMATCH"
    checks["result_hash"] = ("NOT_RUN" if observed["result_hash"] is None
                             else "MATCH" if observed["result_hash"] == doc["result_hash"] else "MISMATCH")
    now_codec = codec_version()
    now_sim = _simulator(doc["simulator_version"]["model"])
    context = {"codec_version.software": _compare(doc["codec_version"]["software"], now_codec["software"]),
               "codec_version.spec": _compare(doc["codec_version"]["spec"], now_codec["spec"]),
               "codec_version.backends": _compare(doc["codec_version"].get("backends"), now_codec["backends"]),
               "commit": _compare(doc["commit"], _commit()),
               "simulator_version": _compare(doc["simulator_version"], now_sim)}
    reproduced = checks["input_hash"] == "MATCH" and checks["result_hash"] == "MATCH"
    return {"schema": REPRODUCTION_SCHEMA, "manifest": None if path is None else str(path),
            "experiment_id": doc["experiment_id"], "kind": doc["kind"], "source_class": doc["source_class"],
            "evidence_class": doc["source_class"], "reproduced": reproduced,
            "recorded": {"input_hash": doc["input_hash"], "result_hash": doc["result_hash"], "seed": doc["seed"],
                         "workers": doc["workers"]},
            "observed": observed, "checks": checks, "context": context,
            "compared": doc.get("result_hash_of") or HASH_OF[doc["kind"]], "statement": STATEMENT}


__all__ = ["SCHEMA", "REPRODUCTION_SCHEMA", "KINDS", "SOURCE_CLASSES", "ManifestError", "validate", "loads", "read",
           "dumps", "write", "simulation_manifest", "experiment_manifest", "experiment_result_hash", "reproduce",
           "canonical_bytes", "sha256_json", "codec_version", "hardware"]
