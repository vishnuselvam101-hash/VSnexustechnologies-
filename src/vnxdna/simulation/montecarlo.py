"""Monte Carlo runs and parameter sweeps over channel models (SIMULATED).

* :func:`monte_carlo` runs one model over a strand file for ``trials`` seeds ``base_seed + i`` (the repository's trial
  seed rule) and summarises the realised event rates (mean, standard deviation, min, max over trials).
* :func:`sweep` runs :func:`monte_carlo` for every point of a grid of dotted-path parameters
  (``{"sequencing.substitution.rate": [0.001, 0.01]}``); each point is a derived model whose ``provenance.derived`` names
  the change, so a sweep point never passes for the original model.

Every trial records the read file's SHA-256 and the full ``vnx.simulation-metadata/1``; results are deterministic for equal
(strands, model, seeds). An optional ``evaluate(reads_path, metadata) -> dict`` callback (e.g. a decode) is called per
trial and its return value stored under ``evaluation``.
"""
from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any, Callable

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.simulation.engine import simulate_file
from vnxdna.simulation.model import ChannelModel

MC_SCHEMA = "vnx.channel-sweep/1"
MAX_TRIALS = 100_000
MAX_POINTS = 10_000


def realised_rates(stats: dict, strand_length: int | None) -> dict:
    """Event rates realised in one simulation (sequencing events per base position of the reads before duplication)."""
    reads0 = stats["reads"] - stats["duplicates"] - stats.get("contaminant_reads", 0)
    pos = reads0 * (strand_length or 0)
    pool = stats.get("pool_strands") or 0
    return {
        "reads_per_input_strand": stats["reads"] / pool if pool else 0.0,
        "strand_loss_fraction": (stats.get("storage_lost", 0) + stats["dropped"] + stats.get("broken_strands", 0)) / pool
        if pool else 0.0,
        "substitution_rate": stats["substitutions"] / pos if pos else 0.0,
        "insertion_rate": stats["insertions"] / pos if pos else 0.0,
        "deletion_rate": stats["deletions"] / pos if pos else 0.0,
        "n_rate": stats["n_calls"] / stats["bases"] if stats["bases"] else 0.0,
        "duplication_rate": stats["duplicates"] / reads0 if reads0 > 0 else 0.0,
        "reverse_complement_rate": stats["reverse_complement"] / stats["reads"] if stats["reads"] else 0.0,
        "zero_coverage_fraction": stats["zero_coverage"] / pool if pool else 0.0,
    }


def _summary(values: list[float]) -> dict:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    return {"mean": mean, "sd": sd, "min": min(values), "max": max(values), "n": n}


def monte_carlo(model: ChannelModel, strands, out_dir, *, trials: int = 10, base_seed: int = 0, workers: int = 1,
                keep_reads: bool = False, fmt: str = "fastq", evaluate: Callable[[Path, dict], Any] | None = None,
                label: str | None = None) -> dict:
    """``trials`` simulations of ``model`` with seeds ``base_seed + i``. Reads go to ``out_dir`` (deleted after hashing
    unless ``keep_reads``)."""
    if isinstance(trials, bool) or not isinstance(trials, int) or not 1 <= trials <= MAX_TRIALS:
        raise VNXConfigurationError(f"trials must be an integer in 1..{MAX_TRIALS}")
    if isinstance(base_seed, bool) or not isinstance(base_seed, int) or base_seed < 0 or base_seed + trials >= 2 ** 63:
        raise VNXConfigurationError("base_seed must be a non-negative integer with base_seed + trials < 2^63")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = label or model.name
    runs = []
    for i in range(trials):
        seed = base_seed + i
        reads = out_dir / f"{stem}.s{seed}.{fmt}"
        body = simulate_file(strands, reads, model, seed, fmt=fmt, workers=workers, overwrite=True)
        meta = body["metadata"]
        run = {"trial": i, "seed": seed, "reads_sha256": meta["output"]["sha256"], "reads": meta["output"]["reads"],
               "stats": meta["stats"], "realised_rates": realised_rates(meta["stats"], meta["input"]["strand_length"]),
               "metadata": meta}
        if evaluate is not None:
            run["evaluation"] = evaluate(reads, meta)
        if not keep_reads:
            reads.unlink(missing_ok=True)
        else:
            run["reads_file"] = str(reads)
        runs.append(run)
    keys = runs[0]["realised_rates"].keys()
    return {"model": {"name": model.name, "version": model.version, "sha256": model.sha256},
            "trials": trials, "base_seed": base_seed, "seeds": [r["seed"] for r in runs],
            "summary": {k: _summary([r["realised_rates"][k] for r in runs]) for k in keys}, "runs": runs}


def grid_points(grid: dict[str, list]) -> list[dict]:
    if not isinstance(grid, dict):
        raise VNXConfigurationError("a sweep grid is an object of dotted parameter paths to lists of values")
    for path, values in grid.items():
        if not isinstance(values, list) or not values:
            raise VNXConfigurationError(f"sweep parameter {path!r} needs a non-empty list of values")
    names = sorted(grid)
    points = [dict(zip(names, combo)) for combo in itertools.product(*(grid[n] for n in names))]
    if len(points) > MAX_POINTS:
        raise VNXConfigurationError(f"sweep grid has {len(points)} points (limit {MAX_POINTS})")
    return points


def sweep(model: ChannelModel, strands, out_dir, grid: dict[str, list] | None = None, *, trials: int = 10,
          base_seed: int = 0, workers: int = 1, keep_reads: bool = False, fmt: str = "fastq",
          evaluate: Callable[[Path, dict], Any] | None = None) -> dict:
    """A Monte Carlo run per grid point (an empty grid = one Monte Carlo run of the model). Returns ``vnx.channel-sweep/1``."""
    from vnxdna.simulation.engine import STATEMENT, versions
    points = grid_points(grid or {})
    # every point is derived (and validated) before any run, so a bad grid fails fast
    models = [model.with_parameters(p, label=f"sweep point {k}") if p else model for k, p in enumerate(points)]
    out = []
    for k, (point, derived) in enumerate(zip(points, models)):
        mc = monte_carlo(derived, strands, out_dir, trials=trials, base_seed=base_seed, workers=workers,
                         keep_reads=keep_reads, fmt=fmt, evaluate=evaluate, label=f"{model.name}.p{k}")
        out.append({"point": k, "parameters": point, "model_sha256": derived.sha256, **mc})
    return {"schema": MC_SCHEMA, "data_source": "SIMULATED", "evidence_class": "SIMULATED", "statement": STATEMENT,
            "model": {"name": model.name, "version": model.version, "sha256": model.sha256,
                      "data_source": model.doc["data_source"], "evidence_class": model.doc["evidence_class"]},
            "grid": {k: list(v) for k, v in (grid or {}).items()}, "trials": trials, "base_seed": base_seed,
            "seed_rule": "trial seed = base_seed + trial index", "versions": versions(), "points": out}


__all__ = ["monte_carlo", "sweep", "grid_points", "realised_rates", "MC_SCHEMA"]
