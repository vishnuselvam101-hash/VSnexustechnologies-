"""Reproducible channel experiments and Monte Carlo trials (software simulation).

``run_experiment`` stores and encodes the input once, then runs ``trials``
independent passes through the simulated channel with seeds ``seed``,
``seed + 1``, … Each trial goes sequence → [cluster → consensus] → recover and
compares the recovered bytes with the original by SHA-256, independently of
the decoder's own verdict. Trials run in parallel processes and are
deterministic for a given seed, whatever the worker count.

Outcomes per trial:

* ``exact``: the decoder returned a file and its SHA-256 equals the input's;
* ``failed-detected``: the decoder refused, with a VNX-DNA error category
  (for example ``INSUFFICIENT_REDUNDANCY``). Nothing was written;
* ``undetected-corruption``: the decoder returned a file that differs from the
  input. This would be a verification bug; it is counted and reported, never hidden;
* ``internal-error``: an unexpected exception (a bug), also reported.

Files written: ``configuration.json`` (inputs, options, channel, seeds,
environment), ``results.json`` (every trial plus the summary), ``results.csv``
and ``summary.txt``. The summary gives the success rate with a Wilson 95 %
confidence interval. These are **software simulation** statistics about this
decoder under this channel model, not claims about physical DNA storage.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import tempfile
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import OutputError, VNXDNAError
from ..provenance import environment
from .archive import default_workers, store_file
from .encoder import encode_file
from .profiles import StoreOptionsV2
from .paths import check_output_dir
from .sequencing import SequencingConfig, sequence_file

CSV_FIELDS = ["trial", "seed", "outcome", "error", "strands", "reads", "reads_per_strand", "strands_dropped", "strands_zero_reads",
              "substitutions", "insertions", "deletions", "bursts", "consensus_valid", "consensus_fallback", "reads_valid",
              "groups_repaired", "max_erasures_in_group", "runtime_s"]


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _trial(args: tuple) -> dict[str, Any]:
    trial, config_dict, strands, expected_sha, key, use_consensus, root = args[:7]
    decode_options = args[7] if len(args) > 7 else {}  # V3: e.g. {"indel_repair": True, "max_indel": 2} (error sweeps)
    from .api import _recover_v2
    from .decoder import DecodeOptionsV2
    config = SequencingConfig(**config_dict)
    started = time.perf_counter()
    work = Path(tempfile.mkdtemp(prefix=f"trial-{trial}-", dir=root))
    record: dict[str, Any] = {"trial": trial, "seed": config.seed}
    try:
        reads = work / "reads.fastq"
        seq = sequence_file(strands, reads, config, fmt="fastq", temp_dir=work)
        record.update({"strands": seq["strands"], "reads": seq["reads"], "reads_per_strand": seq["reads_per_strand_mean"],
                       "strands_dropped": seq["strands_dropped"], "strands_zero_reads": seq["strands_with_zero_reads"],
                       "substitutions": seq["errors"]["sequencing_substitutions"] + seq["errors"]["synthesis_substitutions"],
                       "insertions": seq["errors"]["sequencing_insertions"] + seq["errors"]["synthesis_insertions"],
                       "deletions": seq["errors"]["sequencing_deletions"] + seq["errors"]["synthesis_deletions"],
                       "bursts": sum(seq["errors"].get(f"bursts_{k}", 0) for k in ("substitution", "deletion", "insertion")),
                       "coverage_distribution": seq["coverage_distribution"]})
        source = reads
        out = work / "recovered.bin"
        try:  # clustering refuses (a VNX-DNA error) when no read passes a frame CRC: a detected failure, not a bug
            if use_consensus:
                from .cluster import cluster_file
                from .consensus import consensus_file
                clusters = work / "clusters.jsonl"
                cons = work / "consensus.fasta"
                cluster_file(reads, clusters, workers=1, temp_dir=work)
                c = consensus_file(clusters, cons)
                record["consensus_valid"] = c["stats"].get("consensus_crc_valid", 0) + c["stats"].get("consensus_valid_after_inner_rs", 0)
                record["consensus_fallback"] = c["stats"].get("fallback_to_verified_read", 0)
                source = cons
            rep = _recover_v2(source, out, key=key, options=DecodeOptionsV2(**decode_options), workers=1, overwrite=True,
                              temp_dir=work)
            record.update({"reads_valid": rep["reads"].get("reads_valid", 0),
                           "groups_repaired": rep["recovery"].get("stripes_outer_recovered", 0),
                           "max_erasures_in_group": rep["recovery"].get("max_erasures_in_a_group", 0)})
            actual = _sha(out)
            record["recovered_sha256"] = actual
            record["outcome"] = "exact" if actual == expected_sha else "undetected-corruption"
        except VNXDNAError as error:
            record.update({"outcome": "failed-detected", "error": error.category, "message": str(error)[:300]})
            worst = (error.details or {}).get("worst_group_erasures")
            if isinstance(worst, int):  # failed trials also report the damage they saw (VNX-DNA 2.0 reported 0)
                record["max_erasures_in_group"] = worst
            if out.exists():  # must never happen: a refused recovery leaves no output
                record["outcome"] = "undetected-corruption"
                record["message"] = "output present after a refused recovery"
    except Exception as error:  # noqa: BLE001 - a bug; reported, not hidden
        record.update({"outcome": "internal-error", "error": type(error).__name__, "message": str(error)[:300],
                       "traceback": traceback.format_exc()[-2000:]})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    record["runtime_s"] = time.perf_counter() - started
    return record


def run_experiment(input_path: str | os.PathLike, output_dir: str | os.PathLike, *, channel: SequencingConfig, trials: int = 1,
                   options: StoreOptionsV2 = StoreOptionsV2(), key: bytes | None = None, use_consensus: bool | None = None,
                   workers: int = 0, overwrite: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    src = Path(input_path)
    out = Path(output_dir)
    if out.exists() and not out.is_dir():
        raise OutputError(f"experiment output is not a directory: {out}")
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise OutputError(f"experiment directory {out} is not empty (use --force to overwrite)")
    check_output_dir(out, what="experiment directory")
    workers = workers or default_workers()
    use_consensus = channel.coverage > 1 if use_consensus is None else use_consensus
    work = out / "work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    input_sha = _sha(src)
    container = work / "archive.vxdna"
    strands = work / "strands.fasta"
    stored = store_file(src, container, options=options, key=key, workers=workers)
    encoded = encode_file(container, strands, fmt="fasta", workers=workers)
    configuration = {"experiment": "vnx-dna experiment run", "simulation": "SOFTWARE SIMULATION (not a physical experiment)",
                     "vnxdna_version": __version__, "input": {"path": str(src), "size": src.stat().st_size, "sha256": input_sha},
                     "store_options": options.public_dict(), "encrypted": key is not None, "channel": channel.to_dict(),
                     "trials": trials, "seeds": [channel.seed + t for t in range(trials)] if trials <= 1000 else
                     {"first": channel.seed, "last": channel.seed + trials - 1},
                     "consensus": use_consensus, "archive_id": stored["archive_id"], "strands": encoded["strands"],
                     "strand_nt": encoded["efficiency"]["strand_nt"], "environment": environment()}
    (out / "configuration.json").write_text(json.dumps(configuration, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tasks = [(t, asdict(replace(channel, seed=channel.seed + t)), str(strands), input_sha, key, use_consensus, str(work))
             for t in range(trials)]
    records: list[dict[str, Any]] = []
    if workers <= 1 or trials == 1:
        records = [_trial(task) for task in tasks]
    else:
        with ProcessPoolExecutor(min(workers, trials)) as pool:
            records = list(pool.map(_trial, tasks, chunksize=max(1, trials // (workers * 8))))
    shutil.rmtree(work, ignore_errors=True)
    outcomes = [r["outcome"] for r in records]
    n = len(records)
    exact = outcomes.count("exact")

    def mean(field: str) -> float | None:
        values = [r[field] for r in records if isinstance(r.get(field), (int, float))]
        return sum(values) / len(values) if values else None

    errors_by_category: dict[str, int] = {}
    for r in records:
        if r["outcome"] == "failed-detected":
            errors_by_category[r["error"]] = errors_by_category.get(r["error"], 0) + 1
    summary = {"simulation": "SOFTWARE SIMULATION", "trials": n, "successful_recovery": exact,
               "failed_recovery": n - exact, "detected_failures": outcomes.count("failed-detected"),
               "undetected_corruption": outcomes.count("undetected-corruption"), "internal_errors": outcomes.count("internal-error"),
               "failures_by_category": errors_by_category, "success_rate": exact / n if n else 0.0, "success_ci95": wilson(exact, n),
               "mean_reads_per_strand": mean("reads_per_strand"), "mean_strands_dropped": mean("strands_dropped"),
               "mean_strands_with_zero_reads": mean("strands_zero_reads"), "mean_substitutions": mean("substitutions"),
               "mean_insertions": mean("insertions"), "mean_deletions": mean("deletions"),
               "mean_groups_repaired": mean("groups_repaired"), "max_erasures_in_any_group": max((r["max_erasures_in_group"] for r in records
                                                  if isinstance(r.get("max_erasures_in_group"), int)), default=None),
               "mean_trial_runtime_s": mean("runtime_s"), "total_runtime_s": time.perf_counter() - started}
    results = {"configuration": "configuration.json", "summary": summary, "trials": records}
    (out / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    with (out / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow({k: r.get(k, "") for k in CSV_FIELDS})
    ch = channel
    lo, hi = summary["success_ci95"]
    text = "\n".join([
        "VNX-DNA experiment summary (SOFTWARE SIMULATION; not a physical DNA experiment)",
        f"input            {src.name}: {configuration['input']['size']:,} B, sha256 {input_sha}",
        f"archive          profile {options.profile}, {encoded['strands']:,} strands x {configuration['strand_nt']} nt, "
        f"{'encrypted' if key else 'not encrypted'}",
        f"channel          coverage {ch.coverage} ({ch.coverage_model}), dropout {ch.dropout_rate}, substitution {ch.substitution_rate}, "
        f"insertion {ch.insertion_rate}, deletion {ch.deletion_rate}, synthesis sub/ins/del {ch.synthesis_substitution_rate}/"
        f"{ch.synthesis_insertion_rate}/{ch.synthesis_deletion_rate}, duplication {ch.duplication_rate}"
        + (f", bursts {ch.burst_rate} per read ({ch.burst_kind}, mean {ch.burst_length_mean} nt)" if ch.burst_rate else ""),
        f"read processing  {'cluster + consensus' if use_consensus else 'direct decoding of reads'}",
        f"trials           {n} (seeds {channel.seed}..{channel.seed + n - 1})",
        f"exact recovery   {exact}/{n} = {summary['success_rate']:.4f} (95% Wilson CI [{lo:.4f}, {hi:.4f}])",
        f"failed           {n - exact}: detected {summary['detected_failures']} {errors_by_category or ''}, "
        f"undetected corruption {summary['undetected_corruption']}, internal errors {summary['internal_errors']}",
        f"channel stats    mean reads/strand {summary['mean_reads_per_strand']}, mean dropped strands {summary['mean_strands_dropped']}, "
        f"mean zero-read strands {summary['mean_strands_with_zero_reads']}",
        f"errors (mean)    substitutions {summary['mean_substitutions']}, insertions {summary['mean_insertions']}, "
        f"deletions {summary['mean_deletions']}",
        f"ECC              mean groups repaired {summary['mean_groups_repaired']} (successful trials), "
        f"worst group erasures {summary['max_erasures_in_any_group']} (all trials that reached ECC decoding)"
        f" (guarantee: {options.parity_shards} per group)",
        f"runtime          {summary['total_runtime_s']:.1f} s total, {summary['mean_trial_runtime_s']:.2f} s per trial",
    ]) + "\n"
    (out / "summary.txt").write_text(text, encoding="utf-8")
    return {"status": "SUCCESS", "operation": "experiment", "output": str(out), "summary": summary,
            "files": ["configuration.json", "results.json", "results.csv", "summary.txt"]}
