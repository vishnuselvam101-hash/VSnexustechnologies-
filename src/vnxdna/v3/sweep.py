"""Error-channel sweeps: recovery statistics per error type and rate (software simulation).

``run_sweep`` stores and encodes the input once, then, for every requested
error type and rate, runs ``trials`` independent passes through the simulated
channel (:mod:`vnxdna.v2.sequencing`) and the full decoder, exactly like
``vnx-dna experiment run`` (the trial function is shared). Each point changes
**one** error parameter on top of a base channel; ``mixed`` points combine
several. Outcomes are classified independently of the decoder's verdict by
comparing SHA-256 with the input:

* ``exact`` — recovered byte for byte;
* ``failed-detected`` — the decoder refused (no output written);
* ``undetected-corruption`` — an output that differs from the input (a bug;
  counted and reported, never hidden);
* ``internal-error`` — an unexpected exception (a bug; reported).

Trials use seeds ``seed, seed + 1, …`` at every point, so results do not depend
on the worker count. Nothing here is a model fitted to a sequencing platform:
the rates are stress parameters, and a recovery rate is a statement about this
decoder under this simulated channel only.

Error types (``--sweep TYPE=RATE[,RATE…]``):

================== ==============================================================
substitution       sequencing substitution rate per base
insertion          sequencing insertion rate per base
deletion           sequencing deletion rate per base
dropout            probability that a strand species is lost
duplication        probability that a read gets an identical duplicate
n                  probability that a base is called N
truncation         probability that a read keeps only a 50–99 % prefix
reverse-complement probability that a read is reverse-complemented
burst-substitution probability that a read carries one burst of substitutions
burst-deletion     … one burst of deletions (contiguous run of lost bases)
burst-insertion    … one burst of inserted random bases
burst-mixed        … one burst of a kind chosen uniformly per burst
================== ==============================================================

Reordering is always on (reads are uniformly shuffled unless the base channel
disables it), and every point includes it.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import ConfigurationError, InvalidInputError, OutputError
from ..provenance import environment
from ..v2.archive import default_workers, store_file
from ..v2.decoder import DecodeOptionsV2
from ..v2.encoder import encode_file
from ..v2.experiment import _trial, wilson
from ..v2.paths import check_output_dir
from ..v2.profiles import StoreOptionsV2
from ..v2.sequencing import SequencingConfig

ERROR_TYPES: dict[str, tuple[str, dict[str, Any]]] = {
    "substitution": ("substitution_rate", {}),
    "insertion": ("insertion_rate", {}),
    "deletion": ("deletion_rate", {}),
    "dropout": ("dropout_rate", {}),
    "duplication": ("duplication_rate", {}),
    "n": ("n_rate", {}),
    "truncation": ("truncation_rate", {}),
    "reverse-complement": ("reverse_complement_rate", {}),
    "burst-substitution": ("burst_rate", {"burst_kind": "substitution"}),
    "burst-deletion": ("burst_rate", {"burst_kind": "deletion"}),
    "burst-insertion": ("burst_rate", {"burst_kind": "insertion"}),
    "burst-mixed": ("burst_rate", {"burst_kind": "mixed"}),
}
CSV_FIELDS = ["point", "error_type", "rate", "trials", "exact", "failed_detected", "undetected_corruption", "internal_errors",
              "success_rate", "ci95_low", "ci95_high", "mean_substitutions", "mean_insertions", "mean_deletions", "mean_bursts",
              "mean_strands_dropped", "worst_group_erasures"]


def parse_sweep(specs: list[str]) -> list[tuple[str, dict[str, float]]]:
    """``["substitution=0,0.002", "mixed=substitution:0.001+deletion:0.0005"]`` → points ``(label, {type: rate})``."""
    points: list[tuple[str, dict[str, float]]] = []
    for spec in specs:
        name, sep, values = spec.partition("=")
        name = name.strip().lower()
        if not sep or not values.strip():
            raise ConfigurationError(f"sweep {spec!r} must look like TYPE=RATE[,RATE...] or mixed=TYPE:RATE+TYPE:RATE")
        if name == "mixed":
            combo: dict[str, float] = {}
            for part in values.split("+"):
                kind, colon, rate = part.partition(":")
                kind = kind.strip().lower()
                if not colon or kind not in ERROR_TYPES:
                    raise ConfigurationError(f"mixed sweep part {part!r} must be TYPE:RATE with TYPE in {sorted(ERROR_TYPES)}")
                combo[kind] = _rate(rate, spec)
            if len({ERROR_TYPES[k][0] for k in combo}) != len(combo):
                raise ConfigurationError(f"mixed sweep {spec!r} combines two burst kinds; one read carries at most one burst")
            points.append(("mixed:" + "+".join(f"{k}:{v:g}" for k, v in combo.items()), combo))
            continue
        if name not in ERROR_TYPES:
            raise ConfigurationError(f"unknown error type {name!r}; choose from {sorted(ERROR_TYPES)} or mixed")
        for value in values.split(","):
            rate = _rate(value, spec)
            points.append((f"{name}:{rate:g}", {name: rate}))
    if not points:
        raise ConfigurationError("give at least one --sweep")
    return points


def _rate(text: str, spec: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ConfigurationError(f"invalid rate {text!r} in sweep {spec!r}") from None
    if not 0.0 <= value <= 1.0:
        raise ConfigurationError(f"rate {value} in sweep {spec!r} is not a probability in [0, 1]")
    return value


def channel_for(base: SequencingConfig, combo: dict[str, float]) -> SequencingConfig:
    changes: dict[str, Any] = {}
    for kind, rate in combo.items():
        field, extra = ERROR_TYPES[kind]
        changes[field] = rate
        changes.update(extra)
    return replace(base, **changes)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def run_sweep(input_path, output_dir, *, sweep: list[str], base: SequencingConfig = SequencingConfig(coverage=1, coverage_model="fixed"),
              trials: int = 10, options: StoreOptionsV2 = StoreOptionsV2(), key: bytes | None = None,
              use_consensus: bool | None = None, indel_repair: bool = False, max_indel: int = 1, burst_repair: int = 0, workers: int = 0,
              overwrite: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    if not isinstance(trials, int) or isinstance(trials, bool) or not 1 <= trials <= 100_000:
        raise ConfigurationError("trials must be an integer in 1..100000")
    points = parse_sweep(sweep)
    channels = [channel_for(base, combo) for _, combo in points]  # validates every rate before any work
    decode_options = {"indel_repair": indel_repair, "max_indel": max_indel, "burst_repair": burst_repair}
    DecodeOptionsV2(**decode_options)  # validated before any work: a bad value must not count as failed trials
    src = Path(input_path)
    if not src.is_file():
        raise InvalidInputError(f"input file not found (or not a regular file): {src}")
    out = Path(output_dir)
    if out.exists() and not out.is_dir():
        raise OutputError(f"sweep output is not a directory: {out}")
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise OutputError(f"sweep directory {out} is not empty (use --force to overwrite)")
    check_output_dir(out, what="sweep directory")
    workers = workers or default_workers()
    consensus = base.coverage > 1 if use_consensus is None else use_consensus
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        input_sha = _sha(src)
        container, strands = work / "archive.vxdna", work / "strands.fasta"
        stored = store_file(src, container, options=options, key=key, workers=workers)
        encoded = encode_file(container, strands, fmt="fasta", workers=workers)
        tasks = [(t, asdict(replace(ch, seed=base.seed + t)), str(strands), input_sha, key, consensus, str(work), decode_options)
                 for ch in channels for t in range(trials)]
        if workers <= 1 or len(tasks) == 1:
            records = [_trial(task) for task in tasks]
        else:
            with ProcessPoolExecutor(min(workers, len(tasks))) as pool:
                records = list(pool.map(_trial, tasks, chunksize=max(1, len(tasks) // (workers * 8))))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rows = []
    for i, (label, combo) in enumerate(points):
        recs = records[i * trials:(i + 1) * trials]
        outcomes = [r["outcome"] for r in recs]
        exact = outcomes.count("exact")
        lo, hi = wilson(exact, trials)
        worst = [r["max_erasures_in_group"] for r in recs if isinstance(r.get("max_erasures_in_group"), int)]
        rows.append({"point": label, "error_type": "mixed" if len(combo) > 1 or label.startswith("mixed") else next(iter(combo)),
                     "rate": None if len(combo) > 1 else next(iter(combo.values())), "combination": combo, "trials": trials,
                     "exact": exact, "failed_detected": outcomes.count("failed-detected"),
                     "undetected_corruption": outcomes.count("undetected-corruption"),
                     "internal_errors": outcomes.count("internal-error"), "success_rate": exact / trials,
                     "ci95_low": round(lo, 4), "ci95_high": round(hi, 4),
                     "mean_substitutions": _mean([r.get("substitutions", 0) for r in recs if "substitutions" in r]),
                     "mean_insertions": _mean([r.get("insertions", 0) for r in recs if "insertions" in r]),
                     "mean_deletions": _mean([r.get("deletions", 0) for r in recs if "deletions" in r]),
                     "mean_bursts": _mean([r.get("bursts", 0) for r in recs if "bursts" in r]),
                     "mean_strands_dropped": _mean([r.get("strands_dropped", 0) for r in recs if "strands_dropped" in r]),
                     "worst_group_erasures": max(worst) if worst else None,
                     "errors": sorted({r.get("error") for r in recs if r.get("error")}),
                     "records": recs})
    summary = {"status": "SUCCESS", "operation": "simulate-errors", "simulation": "SOFTWARE SIMULATION (not a physical experiment)",
               "vnxdna_version": __version__, "input": {"path": str(src), "size": src.stat().st_size, "sha256": input_sha},
               "store_options": options.public_dict(), "encrypted": key is not None, "base_channel": base.to_dict(),
               "consensus": consensus, "decode_options": decode_options, "trials_per_point": trials,
               "archive_id": stored["archive_id"], "strands": encoded["strands"], "strand_nt": encoded["efficiency"]["strand_nt"],
               "points": [{k: v for k, v in row.items() if k != "records"} for row in rows],
               "undetected_corruption_total": sum(r["undetected_corruption"] for r in rows),
               "internal_errors_total": sum(r["internal_errors"] for r in rows),
               "environment": environment(), "elapsed_s": time.perf_counter() - started}
    (out / "sweep.json").write_text(json.dumps({**summary, "points_with_trials": rows}, indent=2, sort_keys=True, default=str) + "\n",
                                    encoding="utf-8")
    with (out / "sweep.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    (out / "sweep.md").write_text(markdown_table(summary), encoding="utf-8")
    return summary


def markdown_table(summary: dict[str, Any]) -> str:
    base = summary["base_channel"]
    lines = [f"Software simulation. vnx-dna {summary['vnxdna_version']}, profile {summary['store_options'].get('profile')}, "
             f"{summary['input']['size']:,} B input, {summary['strands']:,} strands of {summary['strand_nt']} nt; base channel: "
             f"coverage {base['coverage']} ({base['coverage_model']}), seed {base['seed']}, consensus {summary['consensus']}, "
             f"indel repair {summary['decode_options']['indel_repair']}, burst repair {summary['decode_options']['burst_repair']} nt; "
             f"{summary['trials_per_point']} trials per point.", "",
             "| point | exact | detected failures | undetected | internal | success (95% Wilson CI) | mean subs / ins / del / bursts |",
             "|---|---|---|---|---|---|---|"]
    for r in summary["points"]:
        lines.append(f"| {r['point']} | {r['exact']}/{r['trials']} | {r['failed_detected']} | {r['undetected_corruption']} | "
                     f"{r['internal_errors']} | {r['success_rate']:.3f} [{r['ci95_low']:.3f}, {r['ci95_high']:.3f}] | "
                     f"{r['mean_substitutions']} / {r['mean_insertions']} / {r['mean_deletions']} / {r['mean_bursts']} |")
    return "\n".join(lines) + "\n"
