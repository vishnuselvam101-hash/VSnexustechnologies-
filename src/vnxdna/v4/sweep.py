"""Error-sweep engine: recovery curves over channel parameters, with every outcome counted.

The input is archived and encoded once. For every sweep point and trial, the
channel runs with seed ``hash(base_seed, point, trial)`` and the reads are
decoded independently (trials run in parallel processes, each decoding with
one worker). Outcomes:

* ``SUCCESS``            container SHA-256 + structure verified (decode succeeded)
* ``PARTIAL``            some files individually verified and recoverable, others lost
* ``DECODER_FAILURE``    not enough information (superblock or groups beyond the codes' capability)
* ``INTEGRITY_FAILURE``  reconstructed bytes failed verification (counted separately; never output)
* ``ADDRESS_FAILURE``    pool ambiguity / address problems
* ``ERROR``              any other exception (a bug if it ever appears)

Nothing is filtered: every trial is recorded in ``trials``; aggregates are per point.
"""
from __future__ import annotations

import hashlib
import itertools
import os
import resource
import shutil
import statistics
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from vnxdna.archive import operations as ar
from . import channel as ch
from . import datagen
from . import decoder as de
from . import encoder as en
from vnxdna.core.errors import VNXAddressError, VNXConfigurationError, VNXDecodeError, VNXIntegrityError


def _seed(base: int, point: int, trial: int) -> int:
    return int.from_bytes(hashlib.sha256(f"{base}|{point}|{trial}".encode()).digest()[:7], "big")


def _trial(args: tuple) -> dict:
    strands, channel, decode, profile, point, trial, workdir = args
    tmp = Path(tempfile.mkdtemp(prefix="vnx4-trial-", dir=workdir))
    try:
        cfg = ch.ChannelConfig.from_dict(channel)
        t = time.perf_counter()
        st = ch.simulate_file(strands, tmp / "r.fastq", cfg)
        chan_s = time.perf_counter() - t
        rec = {"point": point, "trial": trial, "seed": cfg.seed, "reads": st["reads"], "channel_seconds": round(chan_s, 4),
               "observed": {k: st[k] for k in ("substitutions", "insertions", "deletions", "dropped", "zero_coverage", "bases")}}
        t = time.perf_counter()
        try:
            res = de.decode_reads(tmp / "r.fastq", None, de.DecodeOptions(profile=profile, workers=1, **decode),
                                  workdir=str(tmp))
            outcome = res.status if res.status != "FAILURE" else "DECODER_FAILURE"
            rep = res.report
        except VNXDecodeError as error:
            outcome, rep = "DECODER_FAILURE", {"message": str(error)[:300]}
        except VNXIntegrityError as error:
            outcome, rep = "INTEGRITY_FAILURE", {"message": str(error)[:300]}
        except VNXAddressError as error:
            outcome, rep = "ADDRESS_FAILURE", {"message": str(error)[:300]}
        except Exception as error:  # noqa: BLE001 - recorded, never hidden
            outcome, rep = "ERROR", {"message": f"{type(error).__name__}: {str(error)[:300]}"}
        rec.update({"outcome": outcome, "decode_seconds": round(time.perf_counter() - t, 4),
                    "groups_failed": rep.get("groups_failed"), "read_paths": rep.get("reads"), "detail": rep.get("message"),
                    "files_recovered": len(rep.get("files_recovered", []) or []),
                    "worker_peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)})
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def channel_fields(point: dict) -> dict:
    """Channel parameters of a sweep point; other keys (e.g. "level", "model") are labels kept only in the report."""
    names = set(ch.ChannelConfig.__dataclass_fields__)
    return {k: v for k, v in point.items() if k in names}


def expand_points(spec: dict) -> list[dict]:
    """``{"parameter": name, "values": [...]}`` or ``{"grid": {name: [...], ...}}`` → list of overrides."""
    if "grid" in spec:
        names = sorted(spec["grid"])
        return [dict(zip(names, combo)) for combo in itertools.product(*(spec["grid"][n] for n in names))]
    if "points" in spec:
        return [dict(p) for p in spec["points"]]
    if "parameter" in spec and "values" in spec:
        return [{spec["parameter"]: v} for v in spec["values"]]
    raise VNXConfigurationError("sweep needs {parameter, values}, {grid} or {points}")


def run_sweep(config: dict, *, workdir: str | None = None, progress=None) -> dict:
    """Run a sweep described by an experiment configuration (see docs/BENCHMARKING.md)."""
    t0 = time.perf_counter()
    inp = config.get("input", {"size": 1 << 20, "pattern": "mixed", "seed": 42})
    trials = int(config.get("trials", 5))
    workers = int(config.get("workers", 1))
    base_channel = dict(config.get("channel", {}))
    base_seed = int(base_channel.pop("seed", config.get("seed", 12345)))
    dna = dict(config.get("dna", {}))
    decode = dict(config.get("decode", {}))
    profile = dna.get("profile", "v4-balanced")
    points = expand_points(config["sweep"])
    if trials < 1 or len(points) * trials > 100_000:
        raise VNXConfigurationError("trials must be >= 1 and points × trials <= 100000")
    tmp = Path(tempfile.mkdtemp(prefix="vnx4-sweep-", dir=workdir))
    try:
        if "path" in inp:
            src = Path(inp["path"])
            in_sha = hashlib.sha256(src.read_bytes()).hexdigest()
        else:
            src = tmp / "input.bin"
            in_sha = datagen.generate(src, datagen.parse_size(inp["size"]), inp.get("pattern", "mixed"), int(inp.get("seed", 42)))
        arc = ar.build_archive([src], tmp / "a.vnx", ar.ArchiveOptions(**config.get("archive", {})))
        layout = dna.pop("layout", None)
        opts = en.DNAOptions(**{k: v for k, v in dna.items() if k in ("profile", "outer_code", "data_symbols", "parity_symbols",
                                                                      "lt_seed", "lt_distribution", "experimental")})
        if layout:
            from .frame import Layout
            opts.layout = Layout(**layout)
            decode.setdefault("layout", opts.layout)
        if opts.outer_code != "cauchy-rs":
            opts.experimental = True
        enc = en.encode_container(tmp / "a.vnx", tmp / "s.fasta", opts)
        tasks = []
        for pi, over in enumerate(points):
            for tr in range(trials):
                chan = {**base_channel, **channel_fields(over), "seed": _seed(base_seed, pi, tr)}
                ch.ChannelConfig.from_dict(chan)
                tasks.append((str(tmp / "s.fasta"), chan, decode, profile if not layout else None, pi, tr, str(tmp)))
        records = []
        if workers == 1:
            for i, t in enumerate(tasks):
                records.append(_trial(t))
                if progress:
                    progress({"stage": "sweep", "done": i + 1, "total": len(tasks), "elapsed": time.perf_counter() - t0})
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                for i, r in enumerate(pool.map(_trial, tasks)):
                    records.append(r)
                    if progress:
                        progress({"stage": "sweep", "done": i + 1, "total": len(tasks), "elapsed": time.perf_counter() - t0})
        summary = []
        for pi, over in enumerate(points):
            rs = [r for r in records if r["point"] == pi]
            counts = {o: sum(r["outcome"] == o for r in rs) for o in
                      ("SUCCESS", "PARTIAL", "DECODER_FAILURE", "INTEGRITY_FAILURE", "ADDRESS_FAILURE", "ERROR")}
            dts = [r["decode_seconds"] for r in rs]
            summary.append({"point": pi, "parameters": over, "trials": len(rs), "outcomes": counts,
                            "success_rate": round(counts["SUCCESS"] / len(rs), 4),
                            "decode_seconds_mean": round(statistics.fmean(dts), 4),
                            "decode_seconds_median": round(statistics.median(dts), 4),
                            "reads_mean": round(statistics.fmean(r["reads"] for r in rs), 1),
                            "groups_failed_max": max((r["groups_failed"] or 0) for r in rs),
                            "worker_peak_rss_mb_max": max(r["worker_peak_rss_mb"] for r in rs)})
        return {"case": "sweep", "input_sha256": in_sha, "input": inp, "container_bytes": arc.container_bytes,
                "strands": enc["strands"], "bases": enc["bases"], "strand_nt": enc["strand_nt"], "layout": enc["layout"],
                "outer_code": enc["outer_code"], "nt_per_input_byte": round(enc["bases"] / max(1, os.path.getsize(src)), 4),
                "base_channel": {**base_channel, "seed": base_seed}, "decode": {k: (v if not hasattr(v, "to_dict") else v.to_dict())
                                                                                 for k, v in decode.items()},
                "trials_per_point": trials, "points": summary, "trials": records, "seconds": round(time.perf_counter() - t0, 2)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def curve_table(result: dict) -> str:
    lines = ["| parameters | trials | SUCCESS | PARTIAL | DECODER_FAILURE | INTEGRITY_FAILURE | other | success rate | decode s (median) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for p in result["points"]:
        o = p["outcomes"]
        params = ", ".join(f"{k}={v}" for k, v in p["parameters"].items())
        lines.append(f"| {params} | {p['trials']} | {o['SUCCESS']} | {o['PARTIAL']} | {o['DECODER_FAILURE']} | "
                     f"{o['INTEGRITY_FAILURE']} | {o['ADDRESS_FAILURE'] + o['ERROR']} | {p['success_rate']} | {p['decode_seconds_median']} |")
    return "\n".join(lines) + "\n"
