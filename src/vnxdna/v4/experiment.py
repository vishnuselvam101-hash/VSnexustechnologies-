"""Reproducible experiment directories.

Layout (one directory per experiment)::

    experiments/EXP-XXXX/
        README.md          purpose, method, result summary, limitations (written by a human; a results section is appended)
        config.json        the complete experiment definition (input generator, codes, channel, sweep, trials, seed)
        environment.json   machine and software (written by ``vnx experiment run``)
        input.sha256       SHA-256 of the generated input
        seed.json          every seed used
        results.json       machine-readable results (all trials, no filtering)

Inputs are generated deterministically from (pattern, size, seed), so an
experiment directory is self-contained without storing the input data.
``vnx experiment reproduce DIR`` re-runs the configuration in a scratch
directory and compares every deterministic field (outcomes, counts, read
numbers, SHA-256s); timings and memory are reported but not compared.

Experiment types: ``sweep``, ``end_to_end`` (list of runs), ``codec_compare``,
``stages``, ``scaling`` (worker counts), ``memory`` (input sizes).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import bench, sweep
from .errors import VNXConfigurationError
from .util import environment, write_json

TYPES = ("sweep", "end_to_end", "codec_compare", "stages", "scaling", "memory")
TIMING_KEYS = ("seconds", "_s", "mb_s", "rss", "per_second", "_per_day", "reads_s", "frames_s", "leaves_s", "mbases", "strands_s",
               "per_trial", "timestamp", "duration")


def load(path: str | os.PathLike) -> dict:
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise VNXConfigurationError(f"cannot read experiment configuration {path}: {error}") from None
    if not isinstance(cfg, dict) or cfg.get("type") not in TYPES:
        raise VNXConfigurationError(f"experiment config must be an object with type in {TYPES}")
    return cfg


def execute(cfg: dict, workdir: str | None = None, progress=None) -> dict:
    kind = cfg["type"]
    if kind == "sweep":
        return sweep.run_sweep(cfg, workdir=workdir, progress=progress)
    if kind == "end_to_end":
        return {"case": "end_to_end_set", "runs": [bench.isolated("end_to_end", **run, workdir=workdir) for run in cfg["runs"]]}
    if kind == "codec_compare":
        return bench.codec_compare(**cfg.get("parameters", {}))
    if kind == "stages":
        return bench.isolated("stages", **cfg.get("parameters", {}))
    if kind == "scaling":
        base = dict(cfg.get("run", {}))
        reps = int(cfg.get("repeats", 1))
        runs = []
        for w in cfg["workers"]:
            for r in range(reps):
                res = bench.isolated("end_to_end", **{**base, "workers": w}, workdir=workdir)
                res["repeat"] = r
                runs.append(res)
        return {"case": "scaling", "runs": runs}
    if kind == "memory":
        base = dict(cfg.get("run", {}))
        return {"case": "memory", "runs": [bench.isolated("end_to_end", **{**base, "input_size": s}, workdir=workdir)
                                           for s in cfg["sizes"]]}
    raise VNXConfigurationError(f"unknown experiment type {kind!r}")


def _seeds(cfg: dict) -> dict:
    out = {}

    def walk(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "seed" or k.endswith("_seed"):
                    out[prefix + k] = v
                walk(v, prefix + k + ".")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{prefix}{i}.")
    walk(cfg)
    return out


def run(config_path: str | os.PathLike, workdir: str | None = None, progress=None) -> dict:
    path = Path(config_path)
    d = path.parent
    cfg = load(path)
    env = environment()
    write_json(d / "environment.json", env)
    write_json(d / "seed.json", {"seeds": _seeds(cfg), "note": "sweep trial seeds derive from the base seed (vnxdna.v4.sweep._seed)"})
    res = execute(cfg, workdir, progress)
    res["experiment"] = {"id": cfg.get("id", d.name), "type": cfg["type"], "vnx_version": env["vnx_version"],
                         "git_commit": env["git_commit"], "timestamp_utc": env["timestamp_utc"]}
    write_json(d / "results.json", res)
    sha = res.get("input_sha256") or next((r.get("input_sha256") for r in res.get("runs", []) if r.get("input_sha256")), None)
    if sha:
        (d / "input.sha256").write_text(f"{sha}  generated:{json.dumps(cfg.get('input') or cfg.get('run', {}), sort_keys=True)}\n")
    return res


def _deterministic(obj, path=""):
    """Strip timing/memory fields, keep everything that must reproduce exactly."""
    if isinstance(obj, dict):
        return {k: _deterministic(v, f"{path}.{k}") for k, v in obj.items()
                if not any(t in k for t in TIMING_KEYS) and k not in ("experiment", "worker_peak_rss_mb", "detail")}
    if isinstance(obj, list):
        return [_deterministic(v, path) for v in obj]
    return obj


def reproduce(directory: str | os.PathLike, workdir: str | None = None) -> dict:
    d = Path(directory)
    cfg = load(d / "config.json")
    try:
        old = json.loads((d / "results.json").read_text())
    except (OSError, ValueError) as error:
        raise VNXConfigurationError(f"no recorded results in {d}: {error}") from None
    new = execute(cfg, workdir)
    a, b = _deterministic(old), _deterministic(new)
    diffs: list[str] = []
    _diff(a, b, "", diffs)
    return {"directory": str(d), "reproduced": not diffs, "differences": diffs[:50], "difference_count": len(diffs),
            "compared": "all deterministic fields (outcomes, counts, sizes, SHA-256); timings and memory excluded"}


def _diff(a, b, path: str, out: list) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: present in only one result")
            else:
                _diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")
