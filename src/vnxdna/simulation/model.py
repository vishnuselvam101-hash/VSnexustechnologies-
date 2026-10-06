"""Channel-model documents: ``vnx.channel-model/1`` (staged), ``/0`` (the V6 Phase 1 files) and ``vnx.channel-config/0``.

A channel model describes, with independent parameters per stage, what happens to designed strands on the way to reads:

    synthesis ─► storage ─► amplification ─► sequencing

The stages and their fields are listed in :data:`DEFAULTS` (the canonical, fully explicit form of a model) and documented in
``docs/CHANNEL_MODEL.md``. Every model also states where its *parameters* came from:

* ``data_source``  ``SIMULATED`` (synthetic stress settings), ``SYNTHETIC`` (software test data), ``LABORATORY`` (fitted to
  laboratory or public sequencing data) or ``PHYSICAL_VALIDATION`` (a VNX physical validation round);
* ``evidence_class`` the evidence-ladder label of the parameter file (spec §9.3 vocabulary);
* ``provenance``   conversion source, datasets (accession + SHA-256), fitter, references.

Reads produced by the simulator are **always SIMULATED**, whatever the parameters' origin.

Reading rules (spec §4.1, §4.5): a file with ``"schema": "vnx.channel-model/1"`` is read as /1; a file without ``schema``
that has the Phase 1 keys (``name``, ``version``, ``classification``, ``loss``, ``channel``) is read as ``/0`` and converted;
a plain ``ChannelConfig`` object is ``vnx.channel-config/0``. An unknown schema major is refused with ``SCHEMA_UNSUPPORTED``.
A model *version* is an identifier and is never refused.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vnxdna.core.errors import VNXConfigurationError, VNXUnsupportedVersionError

SCHEMA_V1 = "vnx.channel-model/1"
SCHEMA_V2 = "vnx.channel-model/2"
SCHEMA_V0 = "vnx.channel-model/0"
CONFIG_SCHEMA_V0 = "vnx.channel-config/0"
CONFIG_SCHEMA_V1 = "vnx.channel-config/1"

DATA_SOURCES = ("SIMULATED", "SYNTHETIC", "LABORATORY", "PHYSICAL_VALIDATION")
EVIDENCE_CLASSES = ("SIMULATED", "SYNTHETIC SOFTWARE TEST", "PUBLIC-DATA-DERIVED", "REAL PHYSICAL RESULT")
#: which evidence classes a parameter file of each data source may carry
ALLOWED_EVIDENCE = {"SIMULATED": ("SIMULATED",), "SYNTHETIC": ("SYNTHETIC SOFTWARE TEST",),
                    "LABORATORY": ("PUBLIC-DATA-DERIVED", "REAL PHYSICAL RESULT"),
                    "PHYSICAL_VALIDATION": ("REAL PHYSICAL RESULT",)}
STAGES = ("synthesis", "storage", "amplification", "sequencing")
COVERAGE_MODELS = ("fixed", "poisson", "negative-binomial", "lognormal")
RUN_DISTRIBUTIONS = ("single", "geometric")
PROFILE_BASES = ("absolute", "relative")
_NAME = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


def _sub(rate: float = 0.0) -> dict:
    return {"rate": rate, "matrix": None, "from_multipliers": None}


def _ins(rate: float = 0.0) -> dict:
    return {"rate": rate, "base_weights": None}


def _del(rate: float = 0.0) -> dict:
    return {"rate": rate, "run_length": {"distribution": "single", "mean": 1.0}}


#: The canonical /1 stage sections with every field and its default (identity: no errors, coverage 1).
DEFAULTS: dict[str, dict[str, Any]] = {
    "synthesis": {
        "dropout_rate": 0.0,                 # strand species absent from the pool (Bernoulli per strand; V4 dropout_rate)
        "substitution": _sub(), "insertion": _ins(), "deletion": _del(),   # per base per molecule, shared by its reads
        "position_profile": None,
        "truncation": {"rate": 0.0, "min_fraction": 0.5},                  # incomplete products keep a 3' suffix
        "yield_sigma": 0.0,                  # synthesis bias: per-strand abundance ~ LogNormal(-s^2/2, s)
        "molecules_per_strand": 1,           # independent molecule variants per strand that reads sample from
    },
    "storage": {
        "strand_loss": {"rate": 0.0, "burst_count": 0, "burst_length": 0},  # pool order (vnxdna.simulation.loss)
        "retention": 1.0,                    # fraction of molecules retained (scales expected coverage)
        "damage": _sub(),                    # degradation: per-base damage substitutions on molecules
        "breakage_rate": 0.0,                # degradation: per-base strand breaks (a broken molecule is unreadable)
        "contamination_rate": 0.0,           # expected fraction of output reads that are foreign random sequences
    },
    "amplification": {
        "gc_bias": {"strength": 0.0, "optimum": 0.5},   # coverage weight exp(-s((gc - optimum)/0.1)^2)
        "efficiency_sigma": 0.0,             # uneven representation: per-strand weight ~ LogNormal(-s^2/2, s)
        "cycles": 0,
        "substitution_per_cycle": _sub(),    # polymerase substitutions, rate per base per cycle (per read)
        "duplicate_rate": 0.0,               # PCR duplicates: extra read of the same molecule, own sequencing errors
    },
    "sequencing": {
        "coverage": {"model": "fixed", "mean": 1.0, "dispersion": 5.0, "sigma": 0.0},
        "substitution": _sub(), "insertion": _ins(), "deletion": _del(),
        "position_profile": None,
        "homopolymer": {"min_run": 3, "indel_multiplier": 1.0, "substitution_multiplier": 1.0},
        "bursts": {"rate": 0.0, "max_length": 0},       # per read: one contiguous deletion of 1..max_length bases
        "n_rate": 0.0,
        "reverse_complement_rate": 0.0,
        "quality": {"correct": 35, "error": 12, "informative": 0.0, "sd": 0.0, "position_slope": 0.0},
        "read_length": {"max_length": None, "truncation_rate": 0.0, "min_fraction": 0.5},
        "duplicate_rate": 0.0,               # identical copies of a read (optical/clustering duplicates; V4 duplication)
        "missing_read_rate": 0.0,            # reads lost after sequencing (failed calls, filtering)
        "shuffle_window": 0,                 # 0 = strand order; otherwise seeded shuffle of reads within windows
    },
}
TOP_KEYS = ("schema", "name", "version", "description", "note", "data_source", "evidence_class", "provenance", "stages")
PROVENANCE_KEYS = ("converted_from", "datasets", "fitter", "references", "derived")

#: /0 sections (V6 Phase 1 files) and the ChannelConfig fields
V0_LOSS = ("dropout", "burst_count", "burst_length")
V0_CHANNEL = ("substitution_rate", "insertion_rate", "deletion_rate", "dropout_rate", "coverage", "coverage_model",
              "coverage_dispersion", "duplication_rate", "homopolymer_min_run", "homopolymer_indel_multiplier",
              "homopolymer_substitution_multiplier", "gc_bias_strength", "gc_bias_optimum", "burst_rate", "burst_max_len",
              "n_rate", "reverse_complement_rate", "quality_correct", "quality_error", "quality_informative", "shuffle_window")


def _err(msg: str, **details) -> VNXConfigurationError:
    return VNXConfigurationError(f"channel model: {msg}", details=details or None)


def canonical_json(doc: Any) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ================================================================================================================ the model
@dataclass(frozen=True)
class ChannelModel:
    """A validated, canonical ``vnx.channel-model/1`` document plus where it was read from.

    ``doc`` is fully explicit (every stage field present). ``read_as`` is the schema the source was read as (/1, /0 or
    channel-config/0); ``source`` describes the file (path, SHA-256 of its bytes) or ``None`` for an in-memory model.
    """

    doc: dict
    read_as: str = SCHEMA_V1
    source: dict | None = None

    @property
    def name(self) -> str:
        return self.doc["name"]

    @property
    def version(self) -> str:
        return self.doc["version"]

    @property
    def stages(self) -> dict:
        return self.doc["stages"]

    @property
    def sha256(self) -> str:
        """SHA-256 of the canonical JSON of the /1 document (stable across file formatting and /0 → /1 conversion)."""
        return sha256_bytes(canonical_json(self.doc))

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def parameters(self) -> dict:
        return copy.deepcopy(self.doc["stages"])

    def to_json(self) -> dict:
        return copy.deepcopy(self.doc)

    def dumps(self) -> str:
        """The file form of the /1 document (indented, sorted keys, trailing newline)."""
        return json.dumps(self.doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    def describe(self) -> dict:
        d = self.doc
        return {"name": d["name"], "version": d["version"], "schema": d["schema"], "read_as": self.read_as,
                "sha256": self.sha256, "description": d["description"], "data_source": d["data_source"],
                "evidence_class": d["evidence_class"], "source": self.source}

    def with_parameters(self, changes: dict[str, Any], *, label: str | None = None) -> "ChannelModel":
        """A new model with dotted-path parameter changes (``{"sequencing.substitution.rate": 0.01}``), validated; the
        changes are recorded in ``provenance.derived`` so that a derived model never passes for the original."""
        if not changes:
            return self
        doc = copy.deepcopy(self.doc)
        v2 = doc["schema"] == SCHEMA_V2
        for path, value in changes.items():
            set_path(doc["stages"], path, value, v2=v2)
            if v2:    # a changed parameter no longer has its fitted value, interval and basis: drop the record
                doc["parameters"] = {k: v for k, v in doc["parameters"].items()
                                     if not (k == path or k.startswith(path + ".") or path.startswith(k + "."))}
        derived = list(doc["provenance"].get("derived") or [])
        derived.append({"from": self.ref, "from_sha256": self.sha256, "label": label,
                        "changes": {k: changes[k] for k in sorted(changes)}})
        doc["provenance"]["derived"] = derived
        fitting = doc["provenance"].get("fitting")
        if isinstance(fitting, dict):     # V8.3: the fitted parameter identity no longer describes a derived model
            fitting.pop("parameter_sha256", None)
        return ChannelModel(normalize_doc(doc), self.read_as, self.source)

    def to_v1(self) -> dict:
        """The ``/1`` document. A ``/2`` model is converted only if none of its /2 effects is active (never silently dropped)."""
        if self.doc["schema"] == SCHEMA_V1:
            return copy.deepcopy(self.doc)
        from vnxdna.simulation.model2 import to_v1_doc
        return to_v1_doc(self.doc)

    def unsupported_effects(self) -> list[str]:
        """/2 effects set in this model that the simulator cannot honour (``correlation``, ``asymmetry``)."""
        from vnxdna.simulation.model2 import unsupported_effects
        return unsupported_effects(self.doc["stages"])

    def to_v0(self) -> dict:
        """The equivalent ``/0`` document. Raises if the model uses anything /0 cannot express."""
        return v1_to_v0(self.to_v1())


def get_path(stages: dict, path: str) -> Any:
    node: Any = stages
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise _err(f"unknown parameter {path!r}")
        node = node[part]
    return node


def set_path(stages: dict, path: str, value: Any, *, v2: bool = False) -> None:
    """Set one dotted-path parameter in a stages dict (validated later by :func:`normalize_stages`)."""
    if v2:
        from vnxdna.simulation.model2 import DEFAULTS_V2 as defaults
    else:
        defaults = DEFAULTS
    parts = path.split(".")
    if len(parts) < 2 or parts[0] not in STAGES:
        raise _err(f"unknown parameter {path!r}: it must start with a stage {list(STAGES)}")
    node: dict = stages
    tmpl: Any = defaults
    for part in parts[:-1]:
        if tmpl is not None:
            if not isinstance(tmpl, dict) or part not in tmpl:
                raise _err(f"unknown parameter {path!r}")
            tmpl = None if part in ("position_profile", "context", "read_heterogeneity", "correlation", "asymmetry") else tmpl[part]
        if node.get(part) is None:
            node[part] = {}
        node = node[part]
        if not isinstance(node, dict):
            raise _err(f"unknown parameter {path!r}")
    if tmpl is not None and (not isinstance(tmpl, dict) or parts[-1] not in tmpl):
        raise _err(f"unknown parameter {path!r}")
    node[parts[-1]] = value


# ================================================================================================================ validation
def _num(v: Any, where: str, lo: float = -math.inf, hi: float = math.inf) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise _err(f"{where} must be a number in [{lo}, {hi}]", value=v)
    return v


def _prob(v: Any, where: str) -> float:
    return _num(v, where, 0.0, 1.0)


def _int(v: Any, where: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise _err(f"{where} must be an integer in [{lo}, {hi}]", value=v)
    return v


def _keys(obj: Any, allowed, where: str) -> dict:
    if not isinstance(obj, dict):
        raise _err(f"{where} must be a JSON object")
    unknown = sorted(set(obj) - set(allowed), key=str)
    if unknown:
        raise _err(f"{where}: unknown keys {unknown}")
    return obj


def _merge(defaults: dict, given: dict | None, where: str) -> dict:
    given = _keys({} if given is None else given, defaults, where)
    out = {}
    for k, dv in defaults.items():
        if isinstance(dv, dict):
            out[k] = _merge(dv, given.get(k), f"{where}.{k}")
        else:
            out[k] = copy.deepcopy(given[k]) if k in given else copy.deepcopy(dv)
    return out


def _matrix(m: Any, where: str) -> list | None:
    if m is None:
        return None
    if not isinstance(m, list) or len(m) != 4 or any(not isinstance(r, list) or len(r) != 4 for r in m):
        raise _err(f"{where} must be a 4x4 list (rows: from A, C, G, T; columns: to A, C, G, T)")
    for i, row in enumerate(m):
        for j, v in enumerate(row):
            _num(v, f"{where}[{i}][{j}]", 0.0, 1.0)
        if row[i] != 0:
            raise _err(f"{where}: the diagonal must be 0 (a substitution changes the base)")
        if abs(sum(row) - 1.0) > 1e-9:
            raise _err(f"{where}: row {'ACGT'[i]} must sum to 1 (it is P(to | from, substitution))")
    return m


def _vector4(v: Any, where: str, *, normalised: bool) -> list | None:
    if v is None:
        return None
    if not isinstance(v, list) or len(v) != 4:
        raise _err(f"{where} must be a list of 4 numbers (A, C, G, T)")
    for i, x in enumerate(v):
        _num(x, f"{where}[{i}]", 0.0, 100.0)
    if normalised and abs(sum(v) - 1.0) > 1e-9:
        raise _err(f"{where} must sum to 1")
    return v


def _check_sub(s: dict, where: str) -> None:
    _prob(s["rate"], f"{where}.rate")
    _matrix(s["matrix"], f"{where}.matrix")
    _vector4(s["from_multipliers"], f"{where}.from_multipliers", normalised=False)


def _check_profile(p: Any, where: str) -> dict | None:
    if p is None:
        return None
    p = _keys(p, ("basis", "substitution", "insertion", "deletion"), where)
    out = {"basis": p.get("basis", "absolute"), "substitution": p.get("substitution"), "insertion": p.get("insertion"),
           "deletion": p.get("deletion")}
    if out["basis"] not in PROFILE_BASES:
        raise _err(f"{where}.basis must be one of {list(PROFILE_BASES)}")
    for kind in ("substitution", "insertion", "deletion"):
        v = out[kind]
        if v is None:
            continue
        if not isinstance(v, list) or not 1 <= len(v) <= 100_000:
            raise _err(f"{where}.{kind} must be null or a list of 1..100000 multipliers")
        for i, x in enumerate(v):
            _num(x, f"{where}.{kind}[{i}]", 0.0, 1000.0)
    return out


def _check_per_base(st: dict, where: str) -> None:
    _check_sub(st["substitution"], f"{where}.substitution")
    _prob(st["insertion"]["rate"], f"{where}.insertion.rate")
    _vector4(st["insertion"]["base_weights"], f"{where}.insertion.base_weights", normalised=True)
    _prob(st["deletion"]["rate"], f"{where}.deletion.rate")
    rl = st["deletion"]["run_length"]
    if rl["distribution"] not in RUN_DISTRIBUTIONS:
        raise _err(f"{where}.deletion.run_length.distribution must be one of {list(RUN_DISTRIBUTIONS)}")
    _num(rl["mean"], f"{where}.deletion.run_length.mean", 1.0, 1000.0)
    if rl["distribution"] == "single" and rl["mean"] != 1.0:
        raise _err(f"{where}.deletion.run_length: distribution 'single' has mean 1")
    if st["substitution"]["rate"] + st["insertion"]["rate"] + st["deletion"]["rate"] > 0.5:
        raise _err(f"{where}: substitution + insertion + deletion rates must not exceed 0.5")
    st["position_profile"] = _check_profile(st["position_profile"], f"{where}.position_profile")


def normalize_stages(stages: dict | None) -> dict:
    stages = _keys({} if stages is None else stages, STAGES, "stages")
    out = {}
    for name in STAGES:
        sd = DEFAULTS[name]
        given = _keys({} if stages.get(name) is None else stages[name], sd, f"stages.{name}")
        merged = {}
        for k, dv in sd.items():
            if k == "position_profile":
                merged[k] = copy.deepcopy(given.get(k))
            elif isinstance(dv, dict):
                merged[k] = _merge(dv, given.get(k), f"stages.{name}.{k}")
            else:
                merged[k] = copy.deepcopy(given[k]) if k in given else copy.deepcopy(dv)
        out[name] = merged
    _validate_stages(out)
    return out


def _validate_stages(s: dict) -> None:
    syn, sto, amp, seq = (s[k] for k in STAGES)
    _prob(syn["dropout_rate"], "synthesis.dropout_rate")
    _check_per_base(syn, "synthesis")
    _prob(syn["truncation"]["rate"], "synthesis.truncation.rate")
    _num(syn["truncation"]["min_fraction"], "synthesis.truncation.min_fraction", 0.0, 1.0)
    _num(syn["yield_sigma"], "synthesis.yield_sigma", 0.0, 10.0)
    _int(syn["molecules_per_strand"], "synthesis.molecules_per_strand", 1, 64)

    sl = sto["strand_loss"]
    _num(sl["rate"], "storage.strand_loss.rate", 0.0, 0.999999999)
    _int(sl["burst_count"], "storage.strand_loss.burst_count", 0, 1 << 30)
    _int(sl["burst_length"], "storage.strand_loss.burst_length", 0, 1 << 40)
    _num(sto["retention"], "storage.retention", 1e-9, 1.0)
    _check_sub(sto["damage"], "storage.damage")
    _prob(sto["breakage_rate"], "storage.breakage_rate")
    _num(sto["contamination_rate"], "storage.contamination_rate", 0.0, 0.5)

    _num(amp["gc_bias"]["strength"], "amplification.gc_bias.strength", 0.0, 1e6)
    _num(amp["gc_bias"]["optimum"], "amplification.gc_bias.optimum", 0.0, 1.0)
    _num(amp["efficiency_sigma"], "amplification.efficiency_sigma", 0.0, 10.0)
    _int(amp["cycles"], "amplification.cycles", 0, 1000)
    _check_sub(amp["substitution_per_cycle"], "amplification.substitution_per_cycle")
    if amp["cycles"] * amp["substitution_per_cycle"]["rate"] > 0.5:
        raise _err("amplification: cycles x substitution_per_cycle.rate must not exceed 0.5")
    _prob(amp["duplicate_rate"], "amplification.duplicate_rate")

    cov = seq["coverage"]
    if cov["model"] not in COVERAGE_MODELS:
        raise _err(f"sequencing.coverage.model must be one of {list(COVERAGE_MODELS)}")
    _num(cov["mean"], "sequencing.coverage.mean", 0.0, 10_000.0)
    if cov["model"] == "fixed" and float(cov["mean"]) != int(cov["mean"]):
        raise _err("sequencing.coverage: fixed coverage must be an integer")
    if isinstance(cov["dispersion"], bool) or not isinstance(cov["dispersion"], (int, float)) or not cov["dispersion"] > 0:
        raise _err("sequencing.coverage.dispersion must be > 0")
    _num(cov["sigma"], "sequencing.coverage.sigma", 0.0, 10.0)
    _check_per_base(seq, "sequencing")
    hp = seq["homopolymer"]
    _int(hp["min_run"], "sequencing.homopolymer.min_run", 2, 1 << 20)
    _num(hp["indel_multiplier"], "sequencing.homopolymer.indel_multiplier", 0.0, 100.0)
    _num(hp["substitution_multiplier"], "sequencing.homopolymer.substitution_multiplier", 0.0, 100.0)
    _prob(seq["bursts"]["rate"], "sequencing.bursts.rate")
    _int(seq["bursts"]["max_length"], "sequencing.bursts.max_length", 0, 1000)
    if seq["bursts"]["rate"] and not seq["bursts"]["max_length"]:
        raise _err("sequencing.bursts.max_length must be in 1..1000 when bursts.rate > 0")
    _prob(seq["n_rate"], "sequencing.n_rate")
    _prob(seq["reverse_complement_rate"], "sequencing.reverse_complement_rate")
    q = seq["quality"]
    _int(q["correct"], "sequencing.quality.correct", 0, 93)
    _int(q["error"], "sequencing.quality.error", 0, 93)
    _prob(q["informative"], "sequencing.quality.informative")
    _num(q["sd"], "sequencing.quality.sd", 0.0, 50.0)
    _num(q["position_slope"], "sequencing.quality.position_slope", -10.0, 10.0)
    rl = seq["read_length"]
    if rl["max_length"] is not None:
        _int(rl["max_length"], "sequencing.read_length.max_length", 1, 1 << 30)
    _prob(rl["truncation_rate"], "sequencing.read_length.truncation_rate")
    _num(rl["min_fraction"], "sequencing.read_length.min_fraction", 0.0, 1.0)
    _prob(seq["duplicate_rate"], "sequencing.duplicate_rate")
    _prob(seq["missing_read_rate"], "sequencing.missing_read_rate")
    _int(seq["shuffle_window"], "sequencing.shuffle_window", 0, 1 << 40)


def _normalize_provenance(p: Any) -> dict:
    p = _keys({} if p is None else p, PROVENANCE_KEYS, "provenance")
    out = {"converted_from": p.get("converted_from"), "datasets": list(p.get("datasets") or []), "fitter": p.get("fitter"),
           "references": list(p.get("references") or []), "derived": list(p.get("derived") or [])}
    for i, d in enumerate(out["datasets"]):
        if not isinstance(d, dict) or not d.get("accession") or not re.fullmatch(r"[0-9a-f]{64}", str(d.get("sha256", ""))):
            raise _err(f"provenance.datasets[{i}] needs an 'accession' and the 'sha256' (64 hex) of the data used")
    if not all(isinstance(r, str) for r in out["references"]):
        raise _err("provenance.references must be strings (DOI, URL or citation)")
    return out


def normalize_doc(doc: Any) -> dict:
    """Validate a ``/1`` or ``/2`` document (by its ``schema``) and return its canonical form."""
    if isinstance(doc, dict) and doc.get("schema") == SCHEMA_V2:
        from vnxdna.simulation.model2 import normalize_v2
        return normalize_v2(doc)
    return normalize_v1(doc)


def normalize_v1(doc: Any) -> dict:
    """Validate a ``/1`` document and return its canonical, fully explicit form."""
    doc = _keys(doc, TOP_KEYS, "model")
    if doc.get("schema") != SCHEMA_V1:
        raise _err(f"schema must be {SCHEMA_V1!r}")
    name, version = doc.get("name"), doc.get("version")
    if not isinstance(name, str) or not _NAME.match(name):
        raise _err("name must match [a-z0-9][a-z0-9._+-]{0,63}", value=name)
    if not isinstance(version, str) or not _SEMVER.match(version):
        raise _err("version must be a semantic version (e.g. 1.0.0)", value=version)
    ds = doc.get("data_source", "SIMULATED")
    ec = doc.get("evidence_class", "SIMULATED")
    if ds not in DATA_SOURCES:
        raise _err(f"data_source must be one of {list(DATA_SOURCES)}", value=ds)
    if ec not in EVIDENCE_CLASSES:
        raise _err(f"evidence_class must be one of {list(EVIDENCE_CLASSES)}", value=ec)
    if ec not in ALLOWED_EVIDENCE[ds]:
        raise _err(f"evidence_class {ec!r} is not allowed for data_source {ds!r} (allowed: {list(ALLOWED_EVIDENCE[ds])})")
    prov = _normalize_provenance(doc.get("provenance"))
    if ds in ("LABORATORY", "PHYSICAL_VALIDATION") and not prov["datasets"]:
        raise _err(f"data_source {ds} needs provenance.datasets (accession and SHA-256 of every dataset used)")
    for key in ("description", "note"):
        if not isinstance(doc.get(key, ""), str):
            raise _err(f"{key} must be a string")
    return {"schema": SCHEMA_V1, "name": name, "version": version, "description": doc.get("description", ""),
            "note": doc.get("note", ""), "data_source": ds, "evidence_class": ec, "provenance": prov,
            "stages": normalize_stages(doc.get("stages"))}


# ================================================================================================================ /0 and config
def v0_to_v1(doc: dict, *, source: dict | None = None) -> dict:
    """Convert a ``/0`` model (V6 Phase 1 file) to its canonical /1 document. Exact: every /0 field has one /1 home."""
    _keys(doc, ("schema", "name", "version", "classification", "description", "note", "loss", "channel"), "model /0")
    for key in ("name", "version", "classification", "loss", "channel"):
        if key not in doc:
            raise _err(f"/0 model: missing key {key!r}")
    if doc["classification"] != "SIMULATED":
        raise _err("/0 model: classification must be SIMULATED")
    for section, fields in (("loss", V0_LOSS), ("channel", V0_CHANNEL)):
        if not isinstance(doc[section], dict):
            raise _err(f"/0 model: section {section!r} must be an object")
        missing = [f for f in fields if f not in doc[section]]
        extra = [f for f in doc[section] if f not in fields]
        if missing or extra:
            raise _err(f"/0 model: section {section!r} missing {missing} / unknown {extra}")
    stages = _channel_to_stages(doc["channel"])
    stages["storage"]["strand_loss"] = {"rate": doc["loss"]["dropout"], "burst_count": doc["loss"]["burst_count"],
                                        "burst_length": doc["loss"]["burst_length"]}
    return normalize_v1({"schema": SCHEMA_V1, "name": doc["name"], "version": doc["version"],
                         "description": doc.get("description", ""), "note": doc.get("note", ""),
                         "data_source": "SIMULATED", "evidence_class": "SIMULATED",
                         "provenance": {"converted_from": source}, "stages": stages})


def _channel_to_stages(c: dict) -> dict:
    """ChannelConfig / ``/0`` channel fields → /1 stages (all other fields at their identity defaults)."""
    stages = normalize_stages({})
    syn, amp, seq = stages["synthesis"], stages["amplification"], stages["sequencing"]
    syn["dropout_rate"] = c["dropout_rate"]
    amp["gc_bias"] = {"strength": c["gc_bias_strength"], "optimum": c["gc_bias_optimum"]}
    seq["coverage"] = {"model": c["coverage_model"], "mean": c["coverage"], "dispersion": c["coverage_dispersion"],
                       "sigma": 0.0}
    seq["substitution"]["rate"] = c["substitution_rate"]
    seq["insertion"]["rate"] = c["insertion_rate"]
    seq["deletion"]["rate"] = c["deletion_rate"]
    seq["homopolymer"] = {"min_run": c["homopolymer_min_run"], "indel_multiplier": c["homopolymer_indel_multiplier"],
                          "substitution_multiplier": c["homopolymer_substitution_multiplier"]}
    seq["bursts"] = {"rate": c["burst_rate"], "max_length": c["burst_max_len"]}
    seq["n_rate"] = c["n_rate"]
    seq["reverse_complement_rate"] = c["reverse_complement_rate"]
    seq["quality"].update(correct=c["quality_correct"], error=c["quality_error"], informative=c["quality_informative"])
    seq["duplicate_rate"] = c["duplication_rate"]
    seq["shuffle_window"] = c["shuffle_window"]
    return stages


def channel_config_to_model(cfg_dict: dict, *, source: dict | None = None) -> ChannelModel:
    """A ``vnx.channel-config/0`` object (``ChannelConfig.to_dict()`` or its JSON file) as an in-memory /1 model."""
    c = {k: v for k, v in cfg_dict.items() if k != "seed"}
    doc = normalize_v1({"schema": SCHEMA_V1, "name": "channel-config", "version": "0.0.0",
                        "description": "ChannelConfig (vnx.channel-config/0) read as a channel model",
                        "data_source": "SIMULATED", "evidence_class": "SIMULATED",
                        "provenance": {"converted_from": source or {"schema": CONFIG_SCHEMA_V0}},
                        "stages": _channel_to_stages(c)})
    return ChannelModel(doc, CONFIG_SCHEMA_V0, source)


_V0_ONLY = {  # the /1 fields /0 can express, in canonical form: everything else must be at its default
    ("synthesis", "dropout_rate"), ("storage", "strand_loss"), ("amplification", "gc_bias"), ("sequencing", "coverage"),
    ("sequencing", "substitution"), ("sequencing", "insertion"), ("sequencing", "deletion"), ("sequencing", "homopolymer"),
    ("sequencing", "bursts"), ("sequencing", "n_rate"), ("sequencing", "reverse_complement_rate"),
    ("sequencing", "quality"), ("sequencing", "duplicate_rate"), ("sequencing", "shuffle_window")}


def v0_expressible(stages: dict) -> list[str]:
    """The /1 parameters (dotted paths) a /0 document cannot express; empty if the model is /0-expressible."""
    out = []
    base = normalize_stages({})
    for stage in STAGES:
        for key, val in stages[stage].items():
            if (stage, key) in _V0_ONLY:
                continue
            if val != base[stage][key]:
                out.append(f"{stage}.{key}")
    seq = stages["sequencing"]
    for kind in ("substitution", "insertion", "deletion"):
        for k, v in seq[kind].items():
            if k != "rate" and v != base["sequencing"][kind][k]:
                out.append(f"sequencing.{kind}.{k}")
    if seq["coverage"]["model"] == "lognormal" or seq["coverage"]["sigma"] != 0.0:
        out.append("sequencing.coverage.sigma")
    for k in ("sd", "position_slope"):
        if seq["quality"][k] != 0.0:
            out.append(f"sequencing.quality.{k}")
    return out


def v1_to_v0(doc: dict) -> dict:
    s = doc["stages"]
    bad = v0_expressible(s)
    if bad:
        raise _err(f"model {doc['name']}@{doc['version']} uses parameters /0 cannot express: {bad}")
    seq, amp = s["sequencing"], s["amplification"]
    channel = {
        "substitution_rate": seq["substitution"]["rate"], "insertion_rate": seq["insertion"]["rate"],
        "deletion_rate": seq["deletion"]["rate"], "dropout_rate": s["synthesis"]["dropout_rate"],
        "coverage": seq["coverage"]["mean"], "coverage_model": seq["coverage"]["model"],
        "coverage_dispersion": seq["coverage"]["dispersion"], "duplication_rate": seq["duplicate_rate"],
        "homopolymer_min_run": seq["homopolymer"]["min_run"],
        "homopolymer_indel_multiplier": seq["homopolymer"]["indel_multiplier"],
        "homopolymer_substitution_multiplier": seq["homopolymer"]["substitution_multiplier"],
        "gc_bias_strength": amp["gc_bias"]["strength"], "gc_bias_optimum": amp["gc_bias"]["optimum"],
        "burst_rate": seq["bursts"]["rate"], "burst_max_len": seq["bursts"]["max_length"], "n_rate": seq["n_rate"],
        "reverse_complement_rate": seq["reverse_complement_rate"], "quality_correct": seq["quality"]["correct"],
        "quality_error": seq["quality"]["error"], "quality_informative": seq["quality"]["informative"],
        "shuffle_window": seq["shuffle_window"]}
    sl = s["storage"]["strand_loss"]
    return {"name": doc["name"], "version": doc["version"], "classification": "SIMULATED",
            "description": doc["description"], "note": doc["note"],
            "loss": {"dropout": sl["rate"], "burst_count": sl["burst_count"], "burst_length": sl["burst_length"]},
            "channel": channel}


#: V4 ChannelConfig / /0 channel field → /1 parameter path (used for CLI and SDK overrides)
V0_PATHS = {
    "substitution_rate": "sequencing.substitution.rate", "insertion_rate": "sequencing.insertion.rate",
    "deletion_rate": "sequencing.deletion.rate", "dropout_rate": "synthesis.dropout_rate",
    "coverage": "sequencing.coverage.mean", "coverage_model": "sequencing.coverage.model",
    "coverage_dispersion": "sequencing.coverage.dispersion", "duplication_rate": "sequencing.duplicate_rate",
    "homopolymer_min_run": "sequencing.homopolymer.min_run",
    "homopolymer_indel_multiplier": "sequencing.homopolymer.indel_multiplier",
    "homopolymer_substitution_multiplier": "sequencing.homopolymer.substitution_multiplier",
    "gc_bias_strength": "amplification.gc_bias.strength", "gc_bias_optimum": "amplification.gc_bias.optimum",
    "burst_rate": "sequencing.bursts.rate", "burst_max_len": "sequencing.bursts.max_length",
    "n_rate": "sequencing.n_rate", "reverse_complement_rate": "sequencing.reverse_complement_rate",
    "quality_correct": "sequencing.quality.correct", "quality_error": "sequencing.quality.error",
    "quality_informative": "sequencing.quality.informative", "shuffle_window": "sequencing.shuffle_window"}


def override_paths(model: ChannelModel, overrides: dict[str, Any]) -> dict[str, Any]:
    """V4-style overrides (``coverage``, ``substitution_rate``, ...) or dotted /1 paths → /1 parameter changes.

    As in V4, a non-integer coverage on a fixed-coverage model switches the model to Poisson."""
    out: dict[str, Any] = {}
    for key, value in overrides.items():
        if value is None:
            continue
        path = V0_PATHS.get(key, key)
        if "." not in path:
            raise _err(f"unknown override {key!r}")
        out[path] = value
    cov = out.get("sequencing.coverage.mean")
    model_now = out.get("sequencing.coverage.model", model.stages["sequencing"]["coverage"]["model"])
    if cov is not None and model_now == "fixed" and not isinstance(cov, bool) and isinstance(cov, (int, float)) \
            and float(cov) != int(cov):
        out["sequencing.coverage.model"] = "poisson"
    return out


# ================================================================================================================ reading
def detect_schema(doc: Any) -> str:
    """The schema a JSON object is read as: /1, /0, channel-config/0 or channel-config/1; refuses unknown majors."""
    if not isinstance(doc, dict):
        raise _err("a channel model or configuration must be a JSON object")
    sid = doc.get("schema")
    if sid is None:
        if {"name", "loss", "channel"} <= set(doc):
            return SCHEMA_V0
        return CONFIG_SCHEMA_V0
    if not isinstance(sid, str):
        raise _err("schema must be a string")
    m = re.fullmatch(r"(vnx\.channel-model|vnx\.channel-config)/(\d+)", sid)
    if not m:
        raise VNXUnsupportedVersionError(f"unknown schema {sid!r} (expected {SCHEMA_V1})", code="SCHEMA_UNSUPPORTED",
                                         stage="configuration")
    family, major = m.group(1), int(m.group(2))
    known = {"vnx.channel-model": (0, 1, 2), "vnx.channel-config": (0, 1)}[family]
    if major not in known:
        raise VNXUnsupportedVersionError(f"unsupported schema {sid!r}: this software reads {family}/0 and /1"
                                         + (" and /2" if family == "vnx.channel-model" else ""),
                                         code="SCHEMA_UNSUPPORTED", stage="configuration")
    return sid


def from_doc(doc: Any, *, source: dict | None = None) -> tuple[ChannelModel, int | None]:
    """(model, seed from the document or None). Accepts every readable schema (see :func:`detect_schema`)."""
    check_limits(doc)
    sid = detect_schema(doc)
    if sid == SCHEMA_V1:
        return ChannelModel(normalize_v1(doc), SCHEMA_V1, source), None
    if sid == SCHEMA_V2:
        return ChannelModel(normalize_doc(doc), SCHEMA_V2, source), None
    if sid == SCHEMA_V0:
        return ChannelModel(v0_to_v1(doc, source=source), SCHEMA_V0, source), None
    from vnxdna.simulation.channel import ChannelConfig
    body = {k: v for k, v in doc.items() if k != "schema"}
    cfg = ChannelConfig.from_dict(body)                  # the V4 validation, unchanged
    src = dict(source or {}, schema=sid)
    model = channel_config_to_model(cfg.to_dict(), source=src)
    return ChannelModel(model.doc, sid, src), cfg.seed


#: parser limits (V7): a model file is at most 16 MiB, nested at most 24 levels, with at most 4 million JSON values, no
#: duplicate object keys, no NaN/Infinity and no string longer than 1 MiB
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 24
MAX_NODES = 4_000_000
MAX_STRING = 1 << 20


def _depth_of(raw: bytes) -> int:
    """Maximum bracket nesting of JSON text (strings blanked), computed without recursion."""
    import numpy as np
    text = re.sub(rb'"(?:[^"\\]|\\.)*"', b'""', raw)
    a = np.frombuffer(text, dtype=np.uint8)
    step = (a == 0x5B).astype(np.int32) + (a == 0x7B) - (a == 0x5D) - (a == 0x7D)
    return int(np.cumsum(step).max()) if step.size else 0


def check_limits(doc: Any) -> None:
    """Refuse an in-memory document that is too deep, too large or has an over-long string (iterative walk)."""
    stack = [(doc, 1)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise _err(f"document has more than {MAX_NODES} values")
        if isinstance(node, dict):
            if depth > MAX_DEPTH:
                raise _err(f"document is nested deeper than {MAX_DEPTH} levels")
            for k, v in node.items():
                if isinstance(k, str) and len(k) > MAX_STRING:
                    raise _err("document has an over-long key")
                stack.append((v, depth + 1))
        elif isinstance(node, list):
            if depth > MAX_DEPTH:
                raise _err(f"document is nested deeper than {MAX_DEPTH} levels")
            stack.extend((v, depth + 1) for v in node)
        elif isinstance(node, str) and len(node) > MAX_STRING:
            raise _err(f"document has a string longer than {MAX_STRING} characters")


def _no_duplicates(pairs: list) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _refuse_constant(name: str):
    raise ValueError(f"{name} is not allowed")


def loads_limited(raw: bytes, where: str = "model") -> Any:
    """Parse model JSON under the size/depth/duplicate/constant limits; every failure is a configuration error."""
    if len(raw) > MAX_FILE_BYTES:
        raise _err(f"{where}: larger than {MAX_FILE_BYTES} bytes")
    if _depth_of(raw) > MAX_DEPTH + 1:
        raise _err(f"{where}: nested deeper than {MAX_DEPTH} levels")
    try:
        doc = json.loads(raw, object_pairs_hook=_no_duplicates, parse_constant=_refuse_constant)
    except (ValueError, RecursionError, UnicodeDecodeError) as error:
        raise _err(f"{where}: invalid JSON: {error}") from None
    check_limits(doc)
    return doc


def read_file(path: str | os.PathLike) -> tuple[ChannelModel, int | None]:
    p = Path(path)
    try:
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            raise _err(f"{p}: larger than {MAX_FILE_BYTES} bytes")
        raw = p.read_bytes()
    except OSError as error:
        raise _err(f"cannot read {p}: {error.strerror or error}") from None
    doc = loads_limited(raw, str(p))
    return from_doc(doc, source={"file": p.name, "sha256": sha256_bytes(raw), "schema": None if not isinstance(doc, dict)
                                 else (doc.get("schema") or detect_schema(doc))})


__all__ = ["SCHEMA_V1", "SCHEMA_V2", "normalize_doc", "check_limits", "loads_limited", "SCHEMA_V0", "CONFIG_SCHEMA_V0", "CONFIG_SCHEMA_V1", "DATA_SOURCES", "EVIDENCE_CLASSES", "STAGES",
           "COVERAGE_MODELS", "DEFAULTS", "ChannelModel", "normalize_v1", "normalize_stages", "v0_to_v1", "v1_to_v0",
           "v0_expressible", "channel_config_to_model", "V0_PATHS", "override_paths", "detect_schema", "from_doc", "read_file", "get_path", "set_path",
           "canonical_json"]
