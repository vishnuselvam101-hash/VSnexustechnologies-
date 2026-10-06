"""``vnx.channel-model/2`` (V7, docs/V7_PROTOCOL.md section 5.3): ``/1`` plus provenance of fitted models, per-parameter
confidence intervals and bases, and opt-in effects that ``/1`` cannot express.

A ``/2`` document has the ``/1`` top-level keys and ``stages`` (every ``/1`` stage field keeps its meaning) and adds:

* ``model_id``       identifier of this fitted model (for example ``ont-guppy-hac-pass-fwd-fit-F``)
* ``provenance``     the ``/1`` keys plus, for a model fitted to data (``data_source: LABORATORY``): ``datasets`` (id, accession,
                     url, files with SHA-256, and a ``sha256`` digest of them), ``split`` (name FIT or HELDOUT and the SHA-256
                     of the split manifest), ``fitting`` (method, version, commit SHA, dirty flag, seed, UTC timestamp,
                     software versions)
* ``parameters``     per fitted parameter, keyed by its dotted path in ``stages``: ``value`` (must equal the value in
                     ``stages``), ``ci95`` (bootstrap over references; null if not estimated) and ``basis``
                     (measured / estimated / inferred / assumed / synthetic)
* ``fit_report``     optional: adequacy (ADEQUATE / INADEQUATE / UNVALIDATED), failed metrics, validation metrics, misfit
                     and measured statistics that are not model parameters
* in ``stages.sequencing`` (all optional, default off):
    ``insertion.run_length``  ``{"distribution": "single" | "geometric", "mean"}``: bases inserted at one gap. HONOURED.
    ``context``               ``{"k": 3, "substitution": [64], "insertion": [64], "deletion": [64]}``: rate multipliers by
                              the reference 3-mer centred on the site (previous, base, next; A=0 C=1 G=2 T=3; an edge uses the
                              base itself as the missing neighbour). HONOURED.
    ``read_heterogeneity``    ``{"distribution": "gamma", "shape": k}``: every read draws one multiplier m ~ Gamma(k, 1/k)
                              (mean 1) that scales its per-site sub/ins/del probabilities (protocol 5.5, A2.1). HONOURED.
                              Present in the canonical form only when set, so models without it keep their SHA-256.
    ``correlation``           P(event at i+1 | event at i): NOT HONOURED by the simulator.
    ``asymmetry``             forward/backward differences: NOT HONOURED by the simulator.

A model with ``correlation`` or ``asymmetry`` set is refused by every simulation path (``unsupported_effects``); it is never
simulated with the effect silently dropped. A ``/1`` reader refuses ``/2`` documents (unknown schema major), and
``ChannelModel.to_v1`` refuses a ``/2`` model whose effects are active.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any

from vnxdna.simulation import model as _m

SCHEMA_V2 = _m.SCHEMA_V2
BASES = ("measured", "estimated", "inferred", "assumed", "synthetic")
SPLIT_NAMES = ("FIT", "HELDOUT")
ADEQUACY = ("ADEQUATE", "INADEQUATE", "UNVALIDATED")
HONOURED = ("context", "insertion.run_length", "read_heterogeneity")
UNHONOURED = ("correlation", "asymmetry")
TOP_KEYS_V2 = _m.TOP_KEYS + ("model_id", "parameters", "fit_report")
PROVENANCE_KEYS_V2 = _m.PROVENANCE_KEYS + ("split", "fitting")
FIT_REPORT_KEYS = ("adequacy", "failed_metrics", "metrics", "misfit", "measured_statistics", "notes", "validation")
FITTING_KEYS = ("method", "version", "commit", "dirty", "seed", "timestamp_utc", "software")
MAX_PARAMETERS = 1024
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:@-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
CONTEXT_LEN = 64
HETEROGENEITY_DISTRIBUTIONS = ("gamma",)
HETEROGENEITY_SHAPE = (0.05, 1e6)


def _err(msg: str, **details):
    return _m._err(msg, **details)


def _build_defaults() -> dict:
    d = copy.deepcopy(_m.DEFAULTS)
    d["sequencing"]["insertion"]["run_length"] = {"distribution": "single", "mean": 1.0}
    d["sequencing"]["context"] = None
    d["sequencing"]["read_heterogeneity"] = None
    d["sequencing"]["correlation"] = None
    d["sequencing"]["asymmetry"] = None
    return d


DEFAULTS_V2: dict[str, dict[str, Any]] = _build_defaults()


# ================================================================================================================ stages
def _check_context(c: Any, seq: dict) -> dict | None:
    if c is None:
        return None
    c = _m._keys(c, ("k", "substitution", "insertion", "deletion"), "stages.sequencing.context")
    if c.get("k") != 3:
        raise _err("stages.sequencing.context.k must be 3")
    out: dict[str, Any] = {"k": 3}
    top = 0.0
    for kind in ("substitution", "insertion", "deletion"):
        v = c.get(kind)
        if v is not None:
            if not isinstance(v, list) or len(v) != CONTEXT_LEN:
                raise _err(f"stages.sequencing.context.{kind} must be null or a list of {CONTEXT_LEN} multipliers (3-mers AAA..TTT)")
            for i, x in enumerate(v):
                _m._num(x, f"stages.sequencing.context.{kind}[{i}]", 0.0, 1000.0)
            top += seq[kind]["rate"] * max(v)
        else:
            top += seq[kind]["rate"]
        out[kind] = v
    if top > 1.0:
        raise _err("stages.sequencing.context: rate x largest multiplier summed over events must not exceed 1")
    return out


def _check_heterogeneity(h: Any) -> dict | None:
    if h is None:
        return None
    h = _m._keys(h, ("distribution", "shape"), "stages.sequencing.read_heterogeneity")
    if h.get("distribution") not in HETEROGENEITY_DISTRIBUTIONS:
        raise _err(f"stages.sequencing.read_heterogeneity.distribution must be one of {list(HETEROGENEITY_DISTRIBUTIONS)}")
    lo, hi = HETEROGENEITY_SHAPE
    return {"distribution": h["distribution"], "shape": _m._num(h.get("shape"), "stages.sequencing.read_heterogeneity.shape", lo, hi)}


def _check_correlation(c: Any) -> dict | None:
    if c is None:
        return None
    c = _m._keys(c, ("lag", "p_event_given_event", "p_event_given_no_event"), "stages.sequencing.correlation")
    _m._int(c.get("lag"), "stages.sequencing.correlation.lag", 1, 8)
    return {"lag": c["lag"], "p_event_given_event": _m._prob(c.get("p_event_given_event"), "correlation.p_event_given_event"),
            "p_event_given_no_event": _m._prob(c.get("p_event_given_no_event"), "correlation.p_event_given_no_event")}


def _check_asymmetry(a: Any) -> dict | None:
    if a is None:
        return None
    a = _m._keys(a, ("orientation", "backward_substitution_matrix"), "stages.sequencing.asymmetry")
    if a.get("orientation") not in ("forward", "backward", "both"):
        raise _err("stages.sequencing.asymmetry.orientation must be forward, backward or both")
    return {"orientation": a["orientation"],
            "backward_substitution_matrix": _m._matrix(a.get("backward_substitution_matrix"),
                                                      "stages.sequencing.asymmetry.backward_substitution_matrix")}


def normalize_stages_v2(stages: dict | None) -> dict:
    """The /1 canonical stages plus the /2 opt-in sequencing fields, validated."""
    if stages is not None and not isinstance(stages, dict):
        raise _err("stages must be a JSON object")
    stages = copy.deepcopy({} if stages is None else stages)
    seq = stages.get("sequencing")
    if seq is not None and not isinstance(seq, dict):
        raise _err("stages.sequencing must be a JSON object")
    ext: dict[str, Any] = {}
    run_length = None
    if seq:
        ext = {k: seq.pop(k) for k in ("context", "read_heterogeneity", "correlation", "asymmetry") if k in seq}
        ins = seq.get("insertion")
        if isinstance(ins, dict) and "run_length" in ins:
            run_length = ins.pop("run_length")
    out = _m.normalize_stages(stages)
    s = out["sequencing"]
    rl = _m._merge(DEFAULTS_V2["sequencing"]["insertion"]["run_length"], run_length, "stages.sequencing.insertion.run_length")
    if rl["distribution"] not in _m.RUN_DISTRIBUTIONS:
        raise _err(f"stages.sequencing.insertion.run_length.distribution must be one of {list(_m.RUN_DISTRIBUTIONS)}")
    _m._num(rl["mean"], "stages.sequencing.insertion.run_length.mean", 1.0, 1000.0)
    if rl["distribution"] == "single" and rl["mean"] != 1.0:
        raise _err("stages.sequencing.insertion.run_length: distribution 'single' has mean 1")
    s["insertion"]["run_length"] = rl
    s["context"] = _check_context(ext.get("context"), s)
    het = _check_heterogeneity(ext.get("read_heterogeneity"))
    if het is not None:                      # only when set: documents without it keep their canonical form and SHA-256
        s["read_heterogeneity"] = het
    s["correlation"] = _check_correlation(ext.get("correlation"))
    s["asymmetry"] = _check_asymmetry(ext.get("asymmetry"))
    return out


def unsupported_effects(stages: dict) -> list[str]:
    """The /2 effects set in ``stages`` that the simulator cannot honour (empty for /1 stages and honourable /2 models)."""
    seq = stages.get("sequencing", {})
    return [k for k in UNHONOURED if seq.get(k) is not None]


def active_effects(stages: dict) -> list[str]:
    """Every /2 effect that changes the model relative to /1 (honoured or not)."""
    seq = stages.get("sequencing", {})
    out = []
    if seq.get("context") is not None:
        out.append("context")
    rl = seq.get("insertion", {}).get("run_length")
    if rl and rl.get("distribution") != "single":
        out.append("insertion.run_length")
    if seq.get("read_heterogeneity") is not None:
        out.append("read_heterogeneity")
    return out + [k for k in UNHONOURED if seq.get(k) is not None]


def refuse_unsupported(stages: dict, *, who: str = "the simulator") -> None:
    bad = unsupported_effects(stages)
    if bad:
        raise _err(f"this model sets {bad}, which {who} cannot honour; refusing to simulate it with the effect ignored")


# ================================================================================================================ provenance
def _normalize_provenance_v2(p: Any, data_source: str) -> dict:
    p = _m._keys({} if p is None else p, PROVENANCE_KEYS_V2, "provenance")
    base_keys = {k: v for k, v in p.items() if k in _m.PROVENANCE_KEYS and k != "datasets"}
    datasets = p.get("datasets") or []
    if not isinstance(datasets, list):
        raise _err("provenance.datasets must be a list")
    out_ds = []
    for i, d in enumerate(datasets):
        w = f"provenance.datasets[{i}]"
        d = _m._keys(d, ("id", "accession", "url", "files", "sha256", "role"), w)
        files = d.get("files")
        if not isinstance(files, list) or not files:
            raise _err(f"{w}.files must list every file used with its sha256")
        for j, f in enumerate(files):
            f = _m._keys(f, ("name", "sha256"), f"{w}.files[{j}]")
            if not isinstance(f.get("name"), str) or not f["name"] or len(f["name"]) > 512 or not _HEX64.match(str(f.get("sha256", ""))):
                raise _err(f"{w}.files[{j}] needs a 'name' and a 64-hex 'sha256'")
        if not d.get("accession") or not isinstance(d["accession"], str) or not _HEX64.match(str(d.get("sha256", ""))):
            raise _err(f"{w} needs an 'accession' and the 64-hex dataset 'sha256' digest")
        out_ds.append({k: d.get(k) for k in ("id", "accession", "url", "files", "sha256", "role") if k in d})
    out = _m._normalize_provenance({**base_keys, "datasets": [{"accession": d["accession"], "sha256": d["sha256"]} for d in out_ds]})
    out["datasets"] = out_ds
    split, fitting = p.get("split"), p.get("fitting")
    if split is not None:
        split = _m._keys(split, ("name", "manifest_sha256"), "provenance.split")
        if split.get("name") not in SPLIT_NAMES or not _HEX64.match(str(split.get("manifest_sha256", ""))):
            raise _err(f"provenance.split needs name in {list(SPLIT_NAMES)} and the 64-hex manifest_sha256")
    if fitting is not None:
        fitting = _m._keys(fitting, FITTING_KEYS, "provenance.fitting")
        if data_source != "LABORATORY":
            raise _err("provenance.fitting is only valid for data_source LABORATORY (a fitted model is fitted to data)")
        miss = [k for k in FITTING_KEYS if k not in fitting]
        if miss:
            raise _err(f"provenance.fitting is missing {miss}")
        if not isinstance(fitting["method"], str) or not isinstance(fitting["version"], str):
            raise _err("provenance.fitting.method and version must be strings")
        if not _SHA40.match(str(fitting["commit"])) or not isinstance(fitting["dirty"], bool):
            raise _err("provenance.fitting needs the 40-hex commit and a boolean dirty flag")
        _m._int(fitting["seed"], "provenance.fitting.seed", 0, 1 << 63)
        if not _TS.match(str(fitting["timestamp_utc"])):
            raise _err("provenance.fitting.timestamp_utc must be YYYY-MM-DDTHH:MM:SSZ")
        sw = fitting["software"]
        if not isinstance(sw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in sw.items()) or len(sw) > 64:
            raise _err("provenance.fitting.software must map names to version strings")
    if data_source == "LABORATORY" and (fitting is None or split is None):
        raise _err("a LABORATORY /2 model needs provenance.split and provenance.fitting")
    out["split"], out["fitting"] = split, fitting
    return out


# ================================================================================================================ parameters
def _shape(v: Any) -> Any:
    return [_shape(x) for x in v] if isinstance(v, list) else None


def _lookup(stages: dict, path: str) -> Any:
    node: Any = stages
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise _err(f"parameters: {path!r} is not a parameter of stages")
        node = node[part]
    return node


def _finite_tree(v: Any, where: str) -> None:
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str):
                raise _err(f"{where} has a non-string key")
            _finite_tree(x, where)
        return
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            raise _err(f"{where} must be finite")
        return
    if isinstance(v, list):
        for x in v:
            _finite_tree(x, where)
        return
    raise _err(f"{where} has an unsupported value type")


def _check_ci(ci: Any, value: Any, where: str) -> Any:
    if ci is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not isinstance(ci, list) or len(ci) != 2:
            raise _err(f"{where}.ci95 must be [low, high]")
        lo, hi = (_m._num(x, f"{where}.ci95", -1e18, 1e18) for x in ci)
        if lo > hi:
            raise _err(f"{where}.ci95: low > high")
        return [lo, hi]
    if isinstance(value, list):
        ci = _m._keys(ci, ("lo", "hi"), f"{where}.ci95")
        for side in ("lo", "hi"):
            if _shape(ci.get(side)) != _shape(value):
                raise _err(f"{where}.ci95.{side} must have the shape of the value")
            _finite_tree(ci[side], f"{where}.ci95.{side}")
        return {"lo": ci["lo"], "hi": ci["hi"]}
    raise _err(f"{where}: only numeric and list parameters have a ci95")


def _normalize_parameters(par: Any, stages: dict) -> dict:
    if par is None:
        return {}
    if not isinstance(par, dict) or len(par) > MAX_PARAMETERS:
        raise _err(f"parameters must be an object with at most {MAX_PARAMETERS} entries")
    out = {}
    for path in sorted(par):
        w = f"parameters[{path!r}]"
        if not isinstance(path, str) or len(path) > 160:
            raise _err("parameters: a key is not a short string")
        entry = _m._keys(par[path], ("value", "ci95", "basis"), w)
        if "value" not in entry or entry.get("basis") not in BASES:
            raise _err(f"{w} needs 'value' and 'basis' in {list(BASES)}")
        target = _lookup(stages, path)
        _finite_tree(entry["value"], f"{w}.value")
        if entry["value"] != target:
            raise _err(f"{w}.value differs from the value in stages")
        out[path] = {"value": entry["value"], "ci95": _check_ci(entry.get("ci95"), entry["value"], w), "basis": entry["basis"]}
    return out


def _normalize_fit_report(r: Any) -> dict | None:
    if r is None:
        return None
    r = _m._keys(r, FIT_REPORT_KEYS, "fit_report")
    if r.get("adequacy", "UNVALIDATED") not in ADEQUACY:
        raise _err(f"fit_report.adequacy must be one of {list(ADEQUACY)}")
    fm = r.get("failed_metrics", [])
    if not isinstance(fm, list) or not all(isinstance(x, str) and len(x) <= 32 for x in fm) or len(fm) > 32:
        raise _err("fit_report.failed_metrics must be a short list of metric names")
    if r.get("adequacy") == "ADEQUATE" and fm:
        raise _err("fit_report: an ADEQUATE model has no failed metrics")
    for k in ("metrics", "misfit", "measured_statistics", "validation"):
        if k in r:
            if not isinstance(r[k], dict):
                raise _err(f"fit_report.{k} must be an object")
            _finite_tree(r[k], f"fit_report.{k}")
    if "notes" in r and not (isinstance(r["notes"], list) and all(isinstance(x, str) for x in r["notes"])):
        raise _err("fit_report.notes must be a list of strings")
    return {**copy.deepcopy(r), "adequacy": r.get("adequacy", "UNVALIDATED"), "failed_metrics": list(fm)}


# ================================================================================================================ document
def normalize_v2(doc: Any) -> dict:
    """Validate a ``/2`` document and return its canonical, fully explicit form."""
    doc = _m._keys(doc, TOP_KEYS_V2, "model")
    if doc.get("schema") != SCHEMA_V2:
        raise _err(f"schema must be {SCHEMA_V2!r}")
    name, version = doc.get("name"), doc.get("version")
    if not isinstance(name, str) or not _m._NAME.match(name):
        raise _err("name must match [a-z0-9][a-z0-9._+-]{0,63}", value=name)
    if not isinstance(version, str) or not _m._SEMVER.match(version):
        raise _err("version must be a semantic version (e.g. 1.0.0)", value=version)
    mid = doc.get("model_id", f"{name}@{version}")
    if not isinstance(mid, str) or not _MODEL_ID.match(mid):
        raise _err("model_id must match [A-Za-z0-9][A-Za-z0-9._+:@-]{0,127}", value=mid)
    ds, ec = doc.get("data_source", "SIMULATED"), doc.get("evidence_class", "SIMULATED")
    if ds not in _m.DATA_SOURCES:
        raise _err(f"data_source must be one of {list(_m.DATA_SOURCES)}", value=ds)
    if ec not in _m.EVIDENCE_CLASSES:
        raise _err(f"evidence_class must be one of {list(_m.EVIDENCE_CLASSES)}", value=ec)
    if ec not in _m.ALLOWED_EVIDENCE[ds]:
        raise _err(f"evidence_class {ec!r} is not allowed for data_source {ds!r}")
    prov = _normalize_provenance_v2(doc.get("provenance"), ds)
    if ds in ("LABORATORY", "PHYSICAL_VALIDATION") and not prov["datasets"]:
        raise _err(f"data_source {ds} needs provenance.datasets (accession and SHA-256 of every dataset used)")
    for key in ("description", "note"):
        if not isinstance(doc.get(key, ""), str):
            raise _err(f"{key} must be a string")
    stages = normalize_stages_v2(doc.get("stages"))
    return {"schema": SCHEMA_V2, "name": name, "version": version, "model_id": mid, "description": doc.get("description", ""),
            "note": doc.get("note", ""), "data_source": ds, "evidence_class": ec, "provenance": prov, "stages": stages,
            "parameters": _normalize_parameters(doc.get("parameters"), stages),
            "fit_report": _normalize_fit_report(doc.get("fit_report"))}


def to_v1_doc(doc: dict) -> dict:
    """The /1 document of a /2 model with no active effect. Refuses otherwise (a /1 reader would lose the effect)."""
    eff = active_effects(doc["stages"])
    if eff:
        raise _err(f"model {doc['name']}@{doc['version']} uses /2 effects {eff} that vnx.channel-model/1 cannot express")
    stages = copy.deepcopy(doc["stages"])
    seq = stages["sequencing"]
    for k in ("context", "read_heterogeneity", "correlation", "asymmetry"):
        seq.pop(k, None)
    seq["insertion"].pop("run_length", None)
    prov = {k: copy.deepcopy(doc["provenance"][k]) for k in _m.PROVENANCE_KEYS}
    prov["datasets"] = [{"accession": d["accession"], "sha256": d["sha256"]} for d in doc["provenance"]["datasets"]]
    return _m.normalize_v1({"schema": _m.SCHEMA_V1, "name": doc["name"], "version": doc["version"],
                            "description": doc["description"], "note": doc["note"], "data_source": doc["data_source"],
                            "evidence_class": doc["evidence_class"], "provenance": prov, "stages": stages})


def dataset_digest(files: list[dict]) -> str:
    """The ``sha256`` digest of a dataset entry: SHA-256 of ``name:sha256`` lines, sorted by name."""
    import hashlib
    text = "".join(f"{f['name']}:{f['sha256']}\n" for f in sorted(files, key=lambda f: f["name"]))
    return hashlib.sha256(text.encode()).hexdigest()
