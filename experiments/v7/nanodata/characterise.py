#!/usr/bin/env python3
"""V7 item D3: characterise D13 and CAS9 nanopore reads (FIT/DEV only) and compare them with the simulator.

PUBLIC-DATA-DERIVED (real reads) and SIMULATED (reads from the shipped ``nanopore-like`` model). Pre-registration:
docs/V7_PROTOCOL_AMENDMENT_D3.md. Held-out material is never tallied: D13 run 13 is not opened, D13 segments of held-out
references and CAS9 reads of address g13 or held-out read-ID buckets are counted only.

    PYTHONPATH=src python experiments/v7/nanodata/characterise.py d13  --config experiments/v7/nanodata/config.json
    PYTHONPATH=src python experiments/v7/nanodata/characterise.py cas9 --config experiments/v7/nanodata/config.json
    PYTHONPATH=src python experiments/v7/nanodata/characterise.py compare --config experiments/v7/nanodata/config.json

Results are aggregate statistics only (no read, reference or per-read value). They do not depend on the worker count:
every read is processed independently and every accumulator is a sum of integer counts, merged in input order.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import multiprocessing as mp
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nanolib as nl  # noqa: E402

REPO = HERE.parents[2]
MANIFEST = REPO / "experiments" / "v7" / "datasets" / "MANIFEST.json"
ACCESS_LOG = REPO / "experiments" / "v7" / "datasets" / "ACCESS_LOG.jsonl"
SPLITS = (nl.FIT, nl.DEV)

RL_EDGES = [0, 500, 1000, 1500, 2000, 3000, 4000, 5000, 6000, 10**9]
MQ_EDGES = [0, 8, 10, 12, 14, 16, 18, 20, 99]
RELPOS_BINS = 10
GAP_LO, GAP_HI = -60, 200
CODE2BASE = np.frombuffer(b"ACGTN", dtype=np.uint8)


# ================================================================================================================ helpers
def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def environment() -> dict:
    import edlib
    return {"python": sys.version.split()[0], "numpy": np.__version__,
            "edlib": getattr(edlib, "__version__", "unknown"), "platform": platform.platform(),
            "cpu": platform.processor() or platform.machine(), "cpus": os.cpu_count()}


def provenance(cfg_path: Path, workers: int) -> dict:
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"label": "PUBLIC-DATA-DERIVED (real reads); SIMULATED (nanopore-like comparison reads)",
            "statement": "Reads produced by other groups (Lopez et al. 2019; Imburgia et al. 2025). VNX-DNA has "
                         "synthesised, stored and sequenced nothing. Aggregate statistics only.",
            "preregistration": "docs/V7_PROTOCOL_AMENDMENT_D3.md",
            "commit": git("rev-parse", "HEAD"), "dirty_tracked": bool(status),
            "config": str(cfg_path.relative_to(REPO)) if cfg_path.is_relative_to(REPO) else str(cfg_path),
            "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(), "workers": workers,
            "started_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "environment": environment()}


def manifest_sha(dataset: str, rel: str) -> str:
    ds = json.loads(MANIFEST.read_text())["datasets"][dataset]
    for f in ds.get("files", []) + ds.get("read_files", []):
        if f["path"] == rel:
            return f["sha256"]
    raise KeyError(rel)


def log_access(entry: dict) -> None:
    with open(ACCESS_LOG, "a") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def bin_of(edges: list, x: float) -> int:
    return int(np.searchsorted(edges, x, side="right") - 1)


def counter_hist(c: Counter, lo: int, hi: int) -> list:
    """Histogram over [lo, hi] with under/overflow clamped to the ends."""
    out = [0] * (hi - lo + 1)
    for k, v in c.items():
        out[min(max(k, lo), hi) - lo] += v
    return out


def boot_rates(rows: np.ndarray, seed: int, n_boot: int = 200) -> dict:
    """Rates sub/del/ins_bases/total per site and their bootstrap SE and 95 % CI over units (rows: sites, sub, del, insb)."""
    if not len(rows):
        return {}
    rng = np.random.default_rng(seed)
    tot = rows.sum(axis=0).astype(np.float64)
    names = ("substitution", "deletion", "inserted_bases", "total_edits")

    def rates(t):
        s = max(t[0], 1.0)
        return np.array([t[1] / s, t[2] / s, t[3] / s, (t[1] + t[2] + t[3]) / s])
    point = rates(tot)
    bs = np.empty((n_boot, 4))
    n = len(rows)
    for b in range(n_boot):
        bs[b] = rates(rows[rng.integers(0, n, n)].sum(axis=0).astype(np.float64))
    return {nm: {"value": round(float(point[i]), 6), "se": round(float(bs[:, i].std(ddof=1)), 7),
                 "ci95": [round(float(np.percentile(bs[:, i], 2.5)), 6), round(float(np.percentile(bs[:, i], 97.5)), 6)]}
            for i, nm in enumerate(names)} | {"units": n, "resamples": n_boot}


def seg_counts(ops: str) -> tuple[int, int, int, int]:
    return ops.count("=") + ops.count("X") + ops.count("D"), ops.count("X"), ops.count("D"), ops.count("I")


class Dep:
    """Error sums by a read-level covariate bin: sites, sub, del, inserted bases."""

    def __init__(self, nbins: int):
        self.a = np.zeros((nbins, 4), dtype=np.int64)

    def add(self, b: int, c: tuple) -> None:
        self.a[b] += c

    def export(self, edges_or_bins) -> dict:
        s = np.maximum(self.a[:, 0], 1)
        return {"bins": edges_or_bins, "sites": self.a[:, 0].tolist(),
                "total_edit_rate": [round(float(x), 6) if n else None
                                    for x, n in zip((self.a[:, 1] + self.a[:, 2] + self.a[:, 3]) / s, self.a[:, 0])]}


# ================================================================================================================ D13
_G: dict = {}


def _d13_init(refs, buckets, params):
    _G["refs"], _G["buckets"], _G["params"] = refs, buckets, params
    _G["index"] = nl.build_index(refs, params)


def _new_d13_acc() -> dict:
    return {
        "tally": {s: nl.ErrorTally() for s in SPLITS},
        "per_ref": {s: {} for s in SPLITS},
        "rl_dep": {s: Dep(len(RL_EDGES) - 1) for s in SPLITS},
        "mq_dep": {s: Dep(len(MQ_EDGES) - 1) for s in SPLITS},
        "relpos_dep": {s: Dep(RELPOS_BINS) for s in SPLITS},
        "reads": {s: {"n": 0, "lengths": Counter(), "mean_q": Counter(), "segments": Counter(), "covered_pct": Counter(),
                      "orientation": Counter(), "gaps": Counter(), "with_segment": 0} for s in SPLITS},
        "counts": Counter(),
    }


def _d13_batch(batch):
    acc = _new_d13_acc()
    refs, buckets, params, index = _G["refs"], _G["buckets"], _G["params"], _G["index"]
    for rid, seq, qual in batch:
        res = nl.split_read(seq, qual, index, params)
        c = acc["counts"]
        c["reads"] += 1
        c["candidates"] += res.candidates
        c["rejected_error"] += res.rejected_error
        c["ambiguous"] += res.ambiguous
        c["segments_all"] += len(res.segments)
        rsplit = nl.bucket_split(nl.id_bucket(rid))
        n = len(seq)
        mq = nl.mean_phred(qual)
        for g in res.segments:
            split = nl.bucket_split(int(buckets[g.ref]))
            if split == nl.HELDOUT:
                c["segments_heldout_reference_discarded"] += 1
                continue
            c[f"segments_{split}"] += 1
            acc["tally"][split].add(refs[g.ref], g.seq, g.ops, g.qual)
            sc = seg_counts(g.ops)
            pr = acc["per_ref"][split].setdefault(g.ref, [0, 0, 0, 0, 0])
            pr[0] += 1
            for i in range(4):
                pr[i + 1] += sc[i]
            acc["rl_dep"][split].add(bin_of(RL_EDGES, n), sc)
            acc["mq_dep"][split].add(bin_of(MQ_EDGES, mq), sc)
            acc["relpos_dep"][split].add(min(RELPOS_BINS - 1, ((g.start + g.end) // 2) * RELPOS_BINS // max(n, 1)), sc)
        if rsplit == nl.HELDOUT:
            c["reads_heldout_id_bucket_readlevel_skipped"] += 1
            continue
        r = acc["reads"][rsplit]
        r["n"] += 1
        r["lengths"][n] += 1
        r["mean_q"][int(mq)] += 1
        r["segments"][min(len(res.segments), 80)] += 1
        if res.segments:
            r["with_segment"] += 1
            covered = sum(g.end - g.start for g in res.segments)
            r["covered_pct"][min(100, covered * 100 // max(n, 1))] += 1
            st = {g.strand for g in res.segments}
            r["orientation"]["forward_only" if st == {1} else "reverse_only" if st == {-1} else "mixed"] += 1
            for a, b in zip(res.segments, res.segments[1:]):
                r["gaps"][b.start - a.end] += 1
    return acc


def _merge_d13(a: dict, b: dict) -> None:
    for s in SPLITS:
        a["tally"][s].merge(b["tally"][s])
        for k, v in b["per_ref"][s].items():
            t = a["per_ref"][s].setdefault(k, [0, 0, 0, 0, 0])
            for i in range(5):
                t[i] += v[i]
        for name in ("rl_dep", "mq_dep", "relpos_dep"):
            a[name][s].a += b[name][s].a
        ra, rb = a["reads"][s], b["reads"][s]
        ra["n"] += rb["n"]
        ra["with_segment"] += rb["with_segment"]
        for k in ("lengths", "mean_q", "segments", "covered_pct", "orientation", "gaps"):
            ra[k].update(rb[k])
    a["counts"].update(b["counts"])


def _batches(path: Path, size: int, keep: set | None):
    buf = []
    for rec in nl.read_fastq(path):
        if keep is not None and rec[0] not in keep:
            continue
        buf.append(rec)
        if len(buf) == size:
            yield buf
            buf = []
    if buf:
        yield buf


def subsample_ids(path: Path, max_reads: int | None) -> set | None:
    """PR-9: the first ``max_reads`` read IDs in SHA-256('VNX-D3-SUBSAMPLE/' + ID) order, or None for all reads."""
    if not max_reads:
        return None
    ids = [rid for rid, _, _ in nl.read_fastq(path)]
    if len(ids) <= max_reads:
        return None
    ids.sort(key=lambda r: hashlib.sha256(b"VNX-D3-SUBSAMPLE/" + r).digest())
    return set(ids[:max_reads])


def _length_stats(c: Counter) -> dict:
    n = sum(c.values())
    if not n:
        return {"n": 0}
    keys = sorted(c)
    cum = np.cumsum([c[k] for k in keys])

    def q(p):
        return int(keys[int(np.searchsorted(cum, p * n, side="left"))])
    return {"n": n, "min": keys[0], "p05": q(0.05), "median": q(0.5), "p95": q(0.95), "p99": q(0.99), "max": keys[-1],
            "mean": round(sum(k * v for k, v in c.items()) / n, 2)}


def export_reads(r: dict) -> dict:
    n = max(r["n"], 1)
    return {"reads": r["n"], "length": _length_stats(r["lengths"]),
            "length_histogram_500nt": counter_hist(Counter({k // 500: v for k, v in _bucketed(r["lengths"], 500).items()}), 0, 40),
            "length_histogram_note": "bin i = [500 i, 500 i + 500) nt, last bin 20,000 nt or more",
            "mean_quality_histogram": {str(k): v for k, v in sorted(r["mean_q"].items())},
            "share_with_segment": round(r["with_segment"] / n, 4),
            "segments_per_read": {str(k): v for k, v in sorted(r["segments"].items())},
            "segments_per_read_stats": _length_stats(r["segments"]),
            "covered_percent_stats": _length_stats(r["covered_pct"]),
            "orientation_of_reads_with_segments": dict(sorted(r["orientation"].items())),
            "gap_between_consecutive_segments": {"range": [GAP_LO, GAP_HI], "histogram": counter_hist(r["gaps"], GAP_LO, GAP_HI),
                                                 "stats": _length_stats(r["gaps"])}}


def _bucketed(c: Counter, w: int) -> Counter:
    out: Counter = Counter()
    for k, v in c.items():
        out[k // w * w] += v
    return out


def coverage_stats(counts: np.ndarray) -> dict:
    n = len(counts)
    if not n:
        return {"references": 0}
    m = float(counts.mean())
    return {"references": n, "mean": round(m, 4), "variance": round(float(counts.var()), 4),
            "cv": round(float(counts.std() / m), 4) if m else None, "zero_fraction": round(float((counts == 0).mean()), 6),
            "le1_fraction": round(float((counts <= 1).mean()), 6),
            "p10": float(np.percentile(counts, 10)), "median": float(np.median(counts)), "p90": float(np.percentile(counts, 90)),
            "histogram_0_to_100": np.bincount(np.minimum(counts, 100), minlength=101).tolist()}


# ---------------------------------------------------------------------------------------------------------------- simulation
_S: dict = {}


def _sim_init(stages, seed, refs, params):
    from vnxdna.simulation.engine import Simulator
    _S["sim"], _S["seed"], _S["refs"], _S["params"] = Simulator(stages), seed, refs, params


def align_best(ref: bytes, read: bytes, max_err: float):
    """Semi-global alignment of the whole reference in the read, both orientations (forward wins ties); None if rejected."""
    ed = nl._edlib()
    best = None
    for strand, s in ((1, read), (-1, nl.revcomp(read))):
        r = ed.align(ref, s, mode="HW", task="path")
        d = r["editDistance"]
        if d >= 0 and (best is None or d < best[0]):
            x, y = r["locations"][0]
            best = (d, strand, s, x, y + 1, nl.edlib_ops(r["cigar"]))
    if best is None or best[0] > max_err * len(ref):
        return None
    return best


def _sim_task(ref_ids):
    sim, seed, refs, params = _S["sim"], _S["seed"], _S["refs"], _S["params"]
    tally = nl.ErrorTally()
    per_ref = []
    rejected = 0
    for i in ref_ids:
        ref = refs[i]
        codes = nl.LUT[np.frombuffer(ref, dtype=np.uint8)][None, :].astype(np.uint8)
        out = sim.simulate_batch(codes, seed, int(i))
        lens = out["lengths"]
        offs = np.concatenate([[0], np.cumsum(lens)])
        row = [len(lens), 0, 0, 0, 0]
        for k in range(len(lens)):
            read = CODE2BASE[out["codes"][offs[k]:offs[k + 1]]].tobytes()
            q = (out["quals"][offs[k]:offs[k + 1]].astype(np.int64) + 33).clip(33, 126).astype(np.uint8).tobytes()
            best = align_best(ref, read, params.max_err)
            if best is None:
                rejected += 1
                continue
            _, strand, s, x, y, ops = best
            qq = q if strand == 1 else q[::-1]
            tally.add(ref, s[x:y], ops, qq[x:y])
            sc = seg_counts(ops)
            for j in range(4):
                row[j + 1] += sc[j]
        per_ref.append(row)
    return tally, per_ref, rejected


def simulate_refs(model_name: str, refs: list, ids: list, seed: int, params, workers: int) -> dict:
    from vnxdna.simulation.registry import load_model
    model = load_model(model_name)
    chunks = [ids[i:i + 250] for i in range(0, len(ids), 250)]
    tally = nl.ErrorTally()
    rows, rejected = [], 0
    with mp.get_context("fork").Pool(workers, _sim_init, (model.stages, seed, refs, params)) as pool:
        for t, pr, rj in pool.imap(_sim_task, chunks):
            tally.merge(t)
            rows += pr
            rejected += rj
    a = np.array(rows, dtype=np.int64).reshape(-1, 5)
    return {"model": {"name": model.name, "version": model.version, "sha256": model.sha256},
            "seed": seed, "references": len(ids), "reads": int(a[:, 0].sum()), "rejected_reads": rejected,
            "errors": tally.summary(), "bootstrap": boot_rates(a[:, 1:], seed),
            "coverage_per_reference": coverage_stats(a[:, 0])}


def run_d13(cfg: dict, cfg_path: Path, workers: int, out_dir: Path) -> None:
    root = Path(cfg["data_dir"])
    params = nl.SplitParams(**cfg["d13"]["split_params"])
    for run, fname in cfg["d13"]["runs"].items():
        t0 = time.time()
        prov = provenance(cfg_path, workers)
        reffile = root / "d13" / cfg["d13"]["reference_files"][fname]
        refs = nl.parse_d13_refs(reffile)
        buckets = np.array([nl.bucket(r) for r in refs], dtype=np.int8)
        fastq = root / "d13" / f"nanopore_{run}.fastq.gz"
        keep = subsample_ids(fastq, cfg["d13"].get("max_reads_per_run"))
        acc = _new_d13_acc()
        with mp.get_context("fork").Pool(workers, _d13_init, (refs, buckets, params)) as pool:
            for part in pool.imap(_d13_batch, _batches(fastq, 200, keep), chunksize=1):
                _merge_d13(acc, part)
        split_refs = {s: [i for i, b in enumerate(buckets) if nl.bucket_split(int(b)) == s] for s in SPLITS}
        real = {}
        for s in SPLITS:
            pr = acc["per_ref"][s]
            rows = np.array([pr[i][1:] for i in sorted(pr)], dtype=np.int64).reshape(-1, 4)
            cov = np.array([pr.get(i, [0])[0] for i in split_refs[s]], dtype=np.int64)
            real[s] = {"references_in_split": len(split_refs[s]), "references_with_segment": len(pr),
                       "errors": acc["tally"][s].summary(), "bootstrap": boot_rates(rows, 82000 + int(run[3:])),
                       "coverage_per_reference": coverage_stats(cov),
                       "error_by_read_length": acc["rl_dep"][s].export(RL_EDGES[:-1]),
                       "error_by_read_mean_quality": acc["mq_dep"][s].export(MQ_EDGES[:-1]),
                       "error_by_relative_position_in_read": acc["relpos_dep"][s].export(RELPOS_BINS),
                       "reads_by_read_id_split": export_reads(acc["reads"][s])}
        c = acc["counts"]
        fit_refs_with = real[nl.FIT]["references_with_segment"]
        rid_fitdev = acc["reads"][nl.FIT]["n"] + acc["reads"][nl.DEV]["n"]
        with_seg = acc["reads"][nl.FIT]["with_segment"] + acc["reads"][nl.DEV]["with_segment"]
        adequacy = {"fit_references_with_segment": fit_refs_with, "ge_2000": fit_refs_with >= 2000,
                    "share_fitdev_id_reads_with_segment": round(with_seg / max(rid_fitdev, 1), 4),
                    "ge_0_50": with_seg / max(rid_fitdev, 1) >= 0.5,
                    "ambiguous_over_accepted": round(c["ambiguous"] / max(c["ambiguous"] + c["segments_all"], 1), 6)}
        adequacy["le_0_01"] = adequacy["ambiguous_over_accepted"] <= 0.01
        adequacy["adequate"] = adequacy["ge_2000"] and adequacy["ge_0_50"] and adequacy["le_0_01"]
        # simulator comparison (PR-7): same FIT references, nanopore-like
        fit = sorted(split_refs[nl.FIT], key=lambda i: hashlib.sha256(nl.canonical(refs[i])).digest())
        sim_ids = sorted(fit[:cfg["compare"]["max_references"]])
        sim_seed = cfg["compare"]["seed_base"] + int(run[3:])
        sim = simulate_refs(cfg["compare"]["model"], refs, sim_ids, sim_seed, params, workers)
        sim_set = set(sim_ids)
        pr = acc["per_ref"][nl.FIT]
        real_same = {"errors_note": "FIT statistics over all FIT references; coverage restricted to the simulated references",
                     "coverage_per_reference": coverage_stats(np.array([pr.get(i, [0])[0] for i in sim_ids], dtype=np.int64))}
        doc = {"schema": "vnx.d3-characterisation/1", "dataset": "d13-lopez-nanopore", "run": run, "file": fname,
               "provenance": prov | {"wall_seconds": round(time.time() - t0, 1)},
               "inputs": {"fastq": f"d13/{fastq.name}", "fastq_sha256": manifest_sha("d13-lopez-nanopore", f"d13/{fastq.name}"),
                          "references": f"d13/{reffile.name}", "references_sha256": manifest_sha("d13-lopez-nanopore", f"d13/{reffile.name}"),
                          "subsample": None if keep is None else {"reads": len(keep), "rule": "PR-9 SHA-256 order"}},
               "split_params": params.__dict__, "counts": dict(sorted(c.items())), "adequacy": adequacy,
               "real": real, "simulated": sim | {"references_sha_order_first": len(sim_set)},
               "real_on_simulated_references": real_same}
        out = out_dir / f"d13_{run}.json"
        out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(f"{run}: {c['reads']} reads, {c['segments_all']} segments, {time.time() - t0:.0f} s", flush=True)


# ================================================================================================================ CAS9
def _cas9_init(addresses, cfg):
    _G["addresses"], _G["cfg"] = addresses, cfg


def _read_units(seq: bytes, qual: bytes):
    cfg, addresses = _G["cfg"], _G["addresses"]
    motif = cfg["motif"].encode()
    best = None
    for strand, s, q in ((1, seq, qual), (-1, nl.revcomp(seq), qual[::-1])):
        hits = nl.find_addresses(s, motif, cfg["motif_max_ed"], max_hits=cfg["max_hits"])
        if best is None or len(hits) > len(best[3]):
            best = (strand, s, q, hits)
    strand, s, q, hits = best
    pad = cfg["classify_pad"]
    classes = [nl.classify_address(s[max(0, a - pad):b + pad], addresses, cfg["address_max_ed"]) for a, b, _ in hits]
    known = [x for x in classes if x is not None]
    top = Counter(known).most_common(1)
    addr = top[0][0] if top and top[0][1] * 2 > len(classes) else None
    return strand, s, q, hits, addr


def _new_cas9_acc():
    return {"counts": Counter(), "address": Counter(), "unit_lengths_fit": Counter(),
            "reads": {s: {"n": 0, "lengths": Counter(), "mean_q": Counter(), "hits": Counter(), "units": Counter(),
                          "orientation": Counter()} for s in SPLITS},
            "tally": {s: nl.ErrorTally() for s in SPLITS}, "per_read": {s: [] for s in SPLITS},
            "units_dep": {s: Dep(13) for s in SPLITS}, "mq_dep": {s: Dep(len(MQ_EDGES) - 1) for s in SPLITS},
            "sim_templates": []}


def _loo_tally(units: list[bytes], quals: list[bytes] | None, tally, dep_rows):
    """Each unit against the leave-one-out star consensus of the others (PR-5). Returns per-read (sites, sub, del, insb)."""
    ed = nl._edlib()
    dist = nl.pairwise_distances(units)
    keep = np.ones(len(units), dtype=bool)
    tot = np.zeros(4, dtype=np.int64)
    for i in range(len(units)):
        keep[:] = True
        keep[i] = False
        others = [u for k, u in enumerate(units) if k != i]
        cons = nl.star_consensus(others, dist[np.ix_(keep, keep)])
        ops = nl.edlib_ops(ed.align(cons, units[i], mode="NW", task="path")["cigar"])
        tally.add(cons, units[i], ops, None if quals is None else quals[i])
        sc = seg_counts(ops)
        tot += sc
        for d in dep_rows:
            d(sc)
    return tot


def _cas9_task(args):
    path, phase, unit_range = args
    cfg = _G["cfg"]
    acc = _new_cas9_acc()
    c = acc["counts"]
    for rid, seq, qual in nl.read_fastq(path):
        c["reads"] += 1
        strand, s, q, hits, addr = _read_units(seq, qual)
        acc["address"][addr or "unclassified"] += 1
        if addr == cfg["heldout_address"]:
            c["reads_heldout_address"] += 1
            continue
        split = nl.bucket_split(nl.id_bucket(rid))
        if split == nl.HELDOUT:
            c["reads_heldout_id_bucket"] += 1
            continue
        c[f"reads_{split}"] += 1
        if phase == 1:
            r = acc["reads"][split]
            r["n"] += 1
            r["lengths"][len(seq)] += 1
            r["mean_q"][int(nl.mean_phred(qual))] += 1
            r["hits"][min(len(hits), 100)] += 1
            r["orientation"]["forward" if strand == 1 else "reverse"] += 1
            if split == nl.FIT:
                for (a, _, _), (b, _, _) in zip(hits, hits[1:]):
                    acc["unit_lengths_fit"][b - a] += 1
            continue
        units = nl.units_between(hits, *unit_range)[:cfg["max_units"]]
        acc["reads"][split]["units"][len(units)] += 1
        if len(units) < cfg["min_units"]:
            continue
        c[f"loo_reads_{split}"] += 1
        useqs = [s[a:b] for a, b in units]
        uq = [q[a:b] for a, b in units]
        mq = nl.mean_phred(qual)
        nu = len(units)
        tot = _loo_tally(useqs, uq, acc["tally"][split],
                         [lambda sc, sp=split: acc["units_dep"][sp].add(nu, sc),
                          lambda sc, sp=split: acc["mq_dep"][sp].add(bin_of(MQ_EDGES, mq), sc)])
        acc["per_read"][split].append(tot.tolist())
        if split == nl.FIT and int(hashlib.sha256(b"VNX-D3-SIM/" + rid).hexdigest(), 16) % cfg["sim_every"] == 0:
            acc["sim_templates"].append((nl.star_consensus(useqs), nu))
    return acc


def _merge_cas9(a, b):
    a["counts"].update(b["counts"])
    a["address"].update(b["address"])
    a["unit_lengths_fit"].update(b["unit_lengths_fit"])
    for s in SPLITS:
        for k in ("lengths", "mean_q", "hits", "units", "orientation"):
            a["reads"][s][k].update(b["reads"][s][k])
        a["reads"][s]["n"] += b["reads"][s]["n"]
        a["tally"][s].merge(b["tally"][s])
        a["per_read"][s] += b["per_read"][s]
        a["units_dep"][s].a += b["units_dep"][s].a
        a["mq_dep"][s].a += b["mq_dep"][s].a
    a["sim_templates"] += b["sim_templates"]


def _cas9_sim_task(args):
    """SIMULATED rolling-circle reads: ``n`` copies of a template from the nanopore-like model (reverse complement and
    coverage disabled: one molecule read n times), concatenated, then the same unit pipeline as the real reads."""
    from vnxdna.simulation.engine import Simulator
    templates, stages, seed, unit_range, task_index = args
    cfg = _G["cfg"]
    tally = nl.ErrorTally()
    rows, counts = [], Counter()
    for j, (tpl, n) in enumerate(templates):
        st = copy.deepcopy(stages)
        st["sequencing"]["coverage"] = {"model": "fixed", "mean": float(n + 1), "dispersion": 5.0, "sigma": 0.0}
        st["sequencing"]["reverse_complement_rate"] = 0.0
        sim = Simulator(st)
        codes = nl.LUT[np.frombuffer(tpl, dtype=np.uint8)][None, :].astype(np.uint8)
        out = sim.simulate_batch(codes, seed, task_index * 100000 + j)
        read = CODE2BASE[out["codes"]].tobytes()
        qual = (out["quals"].astype(np.int64) + 33).clip(33, 126).astype(np.uint8).tobytes()
        counts["reads"] += 1
        _, s, q, hits, addr = _read_units(read, qual)
        units = nl.units_between(hits, *unit_range)[:cfg["max_units"]]
        if len(units) < cfg["min_units"]:
            counts["too_few_units"] += 1
            continue
        counts["loo_reads"] += 1
        rows.append(_loo_tally([s[a:b] for a, b in units], [q[a:b] for a, b in units], tally, []).tolist())
    return tally, rows, counts


def run_cas9(cfg: dict, cfg_path: Path, workers: int, out_dir: Path) -> None:
    t0 = time.time()
    prov = provenance(cfg_path, workers)
    root = Path(cfg["data_dir"])
    c9 = cfg["cas9"]
    addresses = {}
    name = None
    for ln in (root / "cas9" / "splint_all.fasta").read_text().splitlines():
        if ln.startswith(">"):
            name = ln[1:].strip().removeprefix("splint_")
        elif ln.strip():
            addresses[name] = ln.strip().encode()
    files = sorted((root / "cas9" / "20200715_basecalled_reads").glob("*.fastq.gz"))
    if c9.get("max_files"):
        files = files[:c9["max_files"]]
    log_access({"timestamp_utc": prov["started_utc"], "commit": prov["commit"], "script": "experiments/v7/nanodata/characterise.py cas9",
                "dataset": "cas9-random-access", "material": f"address classification of every read in {len(files)} chunks",
                "purpose": "identify held-out reads (address g13, read-ID buckets 8-9); held-out reads are counted only (PR-2)"})
    p1 = _new_cas9_acc()
    with mp.get_context("fork").Pool(workers, _cas9_init, (addresses, c9)) as pool:
        for part in pool.imap(_cas9_task, [(str(f), 1, None) for f in files]):
            _merge_cas9(p1, part)
        U = max(p1["unit_lengths_fit"].items(), key=lambda kv: (kv[1], -kv[0]))[0]
        unit_range = (int(round(0.8 * U)), int(round(1.2 * U)))
        p2 = _new_cas9_acc()
        for part in pool.imap(_cas9_task, [(str(f), 2, unit_range) for f in files]):
            _merge_cas9(p2, part)
        from vnxdna.simulation.registry import load_model
        model = load_model(cfg["compare"]["model"])
        tpls = p2["sim_templates"][:cfg["compare"]["cas9_max_templates"]]
        chunks = [tpls[i:i + 50] for i in range(0, len(tpls), 50)]
        sim_seed = cfg["compare"]["seed_base"] + 900
        st, srows, scounts = nl.ErrorTally(), [], Counter()
        for t, r, cc in pool.imap(_cas9_sim_task, [(ch, model.stages, sim_seed, unit_range, k) for k, ch in enumerate(chunks)]):
            st.merge(t)
            srows += r
            scounts.update(cc)
    real = {}
    for s in SPLITS:
        r = p1["reads"][s]
        real[s] = {"reads": r["n"], "length": _length_stats(r["lengths"]),
                   "length_histogram_100nt": {str(k): v for k, v in sorted(_bucketed(r["lengths"], 100).items())},
                   "mean_quality_histogram": {str(k): v for k, v in sorted(r["mean_q"].items())},
                   "address_hits_per_read": _length_stats(r["hits"]),
                   "orientation": dict(sorted(r["orientation"].items())),
                   "admissible_units_per_read": {str(k): v for k, v in sorted(p2["reads"][s]["units"].items())},
                   "loo_reads": p2["counts"][f"loo_reads_{s}"],
                   "errors_vs_leave_one_out_consensus": p2["tally"][s].summary(),
                   "bootstrap_over_reads": boot_rates(np.array(p2["per_read"][s], dtype=np.int64).reshape(-1, 4), 82900),
                   "error_by_units_in_read": p2["units_dep"][s].export(list(range(13))),
                   "error_by_read_mean_quality": p2["mq_dep"][s].export(MQ_EDGES[:-1])}
    doc = {"schema": "vnx.d3-characterisation/1", "dataset": "cas9-random-access",
           "provenance": prov | {"wall_seconds": round(time.time() - t0, 1)},
           "inputs": {"chunks": len(files), "chunk_sha256_source": "MANIFEST.json read_files"},
           "unit_rule": {"modal_unit_length_fit": U, "admissible_range": list(unit_range),
                         "inter_hit_distance_fit_stats": _length_stats(p1["unit_lengths_fit"]),
                         "min_units": c9["min_units"], "max_units": c9["max_units"]},
           "counts": dict(sorted(p1["counts"].items())), "reads_by_address": dict(sorted(p1["address"].items())),
           "basis": "inferred: unit vs leave-one-out consensus of the same molecule's other units (PR-5)",
           "real": real,
           "simulated": {"model": {"name": model.name, "version": model.version, "sha256": model.sha256}, "seed": sim_seed,
                         "templates": len(tpls), "template_rule": "full star consensus of FIT reads with SHA-256('VNX-D3-SIM/'+ID) mod sim_every == 0",
                         "changes_to_model": "coverage fixed at units + 1, reverse_complement_rate 0 (one molecule read repeatedly)",
                         "counts": dict(scounts), "errors_vs_leave_one_out_consensus": st.summary(),
                         "bootstrap_over_reads": boot_rates(np.array(srows, dtype=np.int64).reshape(-1, 4), sim_seed)}}
    (out_dir / "cas9.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"cas9: {p1['counts']['reads']} reads, U={U}, {time.time() - t0:.0f} s", flush=True)


# ================================================================================================================ compare
def _hist_from(errors: dict) -> np.ndarray:
    return np.array(errors["segment_edit_distance_percent"]["histogram_0_to_100"], dtype=np.float64)


def _ks_tv(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    pa, pb = a / max(a.sum(), 1), b / max(b.sum(), 1)
    return float(np.abs(np.cumsum(pa) - np.cumsum(pb)).max()), float(0.5 * np.abs(pa - pb).sum())


def _pct(h: np.ndarray, p: float) -> int:
    c = np.cumsum(h)
    return int(np.searchsorted(c, p * c[-1], side="left"))


def compare_errors(real: dict, real_boot: dict, sim: dict) -> dict:
    """M1-M6 and the descriptive topology checks, real (FIT) vs simulated (PR-7)."""
    out = {}
    m1 = {}
    for k in ("substitution", "deletion", "inserted_bases", "total_edits"):
        rv, se = real_boot[k]["value"], real_boot[k]["se"]
        sv = sim["rates_per_reference_site"][k]
        tol = max(0.05 * rv, 3 * se)
        m1[k] = {"real": rv, "simulated": sv, "tolerance": round(tol, 7), "pass": abs(sv - rv) <= tol}
    out["M1_rates"] = m1 | {"pass": all(v["pass"] for v in m1.values())}
    hr, hs = _hist_from(real), _hist_from(sim)
    ks, tv = _ks_tv(hr, hs)
    p90r, p90s, p99r, p99s = _pct(hr, .9), _pct(hs, .9), _pct(hr, .99), _pct(hs, .99)
    out["M2_edit_distance"] = {"ks_d": round(ks, 4), "tv": round(tv, 4), "p90_percent": [p90r, p90s], "p99_percent": [p99r, p99s],
                               "pass": ks <= 0.03 and tv <= 0.05 and abs(p90s - p90r) <= 0.1 * p90r and abs(p99s - p99r) <= 0.1 * p99r}
    m3 = {k: [real["length_drift"][k], sim["length_drift"][k]] for k in ("abs_drift_le_0", "abs_drift_le_3", "abs_drift_le_6")}
    out["M3_length_drift"] = m3 | {"pass": all(abs(a - b) <= 0.01 for a, b in m3.values())}
    pr, ps = real["position_profile"], sim["position_profile"]

    def tot(p):
        return [None if a is None else a + b + c for a, b, c in zip(p["substitution"], p["deletion"], p["insertion_events"])]
    tr, ts = tot(pr), tot(ps)
    ratio = [None if (a is None or b is None or not a) else round(b / a, 4) for a, b in zip(tr, ts)]
    out["M4_position_profile"] = {"real_total": tr, "simulated_total": ts, "sim_over_real": ratio,
                                  "real_max_over_min": round(max(tr) / min(tr), 3) if tr and min(tr) else None,
                                  "pass": all(r is not None and 0.9 <= r <= 1.1 for r in ratio)}
    dr = np.array(real["deletion_run_lengths"]["counts"], dtype=np.float64)
    ds = np.array(sim["deletion_run_lengths"]["counts"], dtype=np.float64)
    dr8 = np.concatenate([dr[:7], [dr[7:].sum()]])
    ds8 = np.concatenate([ds[:7], [ds[7:].sum()]])
    _, tvd = _ks_tv(dr8, ds8)
    out["M5_deletion_runs"] = {"real_1_to_8plus": dr8.astype(int).tolist(), "simulated_1_to_8plus": ds8.astype(int).tolist(),
                               "real_share_ge2": round(float(dr8[1:].sum() / max(dr8.sum(), 1)), 4),
                               "simulated_share_ge2": round(float(ds8[1:].sum() / max(ds8.sum(), 1)), 4),
                               "tv": round(tvd, 4), "pass": tvd <= 0.05}
    hpr, hps = real["homopolymer"], sim["homopolymer"]
    m6 = []
    for i in range(8):
        if hpr["sites"][i] < 1000 or hps["sites"][i] < 1000:
            continue
        a = (hpr["deletion"][i] or 0) + (hpr["extension_insertions_per_site"][i] or 0)
        b = (hps["deletion"][i] or 0) + (hps["extension_insertions_per_site"][i] or 0)
        m6.append({"run_length": i + 1, "real_indel_per_site": round(a, 5), "simulated_indel_per_site": round(b, 5),
                   "pass": a > 0 and abs(b - a) <= 0.1 * a})
    r1 = next((x for x in m6 if x["run_length"] == 1), None)
    for x in m6:
        x["real_rel_to_run1"] = round(x["real_indel_per_site"] / r1["real_indel_per_site"], 3) if r1 and r1["real_indel_per_site"] else None
        x["simulated_rel_to_run1"] = round(x["simulated_indel_per_site"] / r1["simulated_indel_per_site"], 3) if r1 and r1["simulated_indel_per_site"] else None
    out["M6_homopolymer_indel"] = {"by_run_length": m6, "pass": bool(m6) and all(x["pass"] for x in m6)}
    ec_r, ec_s = real["error_correlation"], sim["error_correlation"]
    lift_r = ec_r["p_event_given_previous_event"] / ec_r["p_event"] if ec_r["p_event"] else None
    lift_s = ec_s["p_event_given_previous_event"] / ec_s["p_event"] if ec_s["p_event"] else None
    out["burstiness"] = {"real_lift": None if lift_r is None else round(lift_r, 3), "simulated_lift": None if lift_s is None else round(lift_s, 3),
                         "real_ins_runs_share_ge2": _share_ge2(real["insertion_run_lengths"]["counts"]),
                         "simulated_ins_runs_share_ge2": _share_ge2(sim["insertion_run_lengths"]["counts"]),
                         "reproduced_within_10pct": lift_r is not None and lift_s is not None and abs(lift_s - lift_r) <= 0.1 * lift_r}
    vr, vs = real["segment_edit_count"]["variance_over_mean"], sim["segment_edit_count"]["variance_over_mean"]
    out["read_level_heterogeneity"] = {"real_variance_over_mean": vr, "simulated_variance_over_mean": vs,
                                       "reproduced_within_10pct": vr is not None and vs is not None and abs(vs - vr) <= 0.1 * vr}
    smr = real["substitution_matrix"]["transition_share"]
    sms = sim["substitution_matrix"]["transition_share"]
    out["substitution_spectrum"] = {"real_transition_share": smr, "simulated_transition_share": sms}
    qr = {b["phred"]: b["empirical_phred"] for b in real["quality_calibration"]["empirical_phred_bins_ge_1000_bases"]}
    qs = {b["phred"]: b["empirical_phred"] for b in sim["quality_calibration"]["empirical_phred_bins_ge_1000_bases"]}
    out["M9_quality"] = {"real_occupied_phred_values": len(real["quality_calibration"]["bins"]),
                         "simulated_occupied_phred_values": len(sim["quality_calibration"]["bins"]),
                         "real_empirical_phred_at_q": {str(k): qr[k] for k in sorted(qr) if k in (5, 7, 10, 15, 20, 25, 30)},
                         "simulated_empirical_phred_at_q": {str(k): v for k, v in sorted(qs.items())},
                         "real_expected_calibration_error": real["quality_calibration"]["expected_calibration_error"],
                         "simulated_expected_calibration_error": sim["quality_calibration"]["expected_calibration_error"]}
    return out


def _share_ge2(counts: list) -> float:
    a = np.array(counts, dtype=np.float64)
    return round(float(a[1:].sum() / max(a.sum(), 1)), 4)


def nb_theory(mean: float, k: float) -> dict:
    """Coverage of the model's negative binomial (shape k) rescaled to ``mean`` (THEORETICAL)."""
    return {"mean": round(mean, 4), "cv": round(float(np.sqrt(1 / mean + 1 / k)), 4) if mean else None,
            "zero_fraction": round(float((k / (k + mean)) ** k), 6),
            "le1_fraction": round(float((k / (k + mean)) ** k * (1 + k * mean / (k + mean))), 6)}


def run_compare(cfg: dict, out_dir: Path) -> None:
    rows = {}
    for run in cfg["d13"]["runs"]:
        p = out_dir / f"d13_{run}.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        real = d["real"][nl.FIT]
        cmp = compare_errors(real["errors"], real["bootstrap"], d["simulated"]["errors"])
        rc = d["real_on_simulated_references"]["coverage_per_reference"]
        sc = d["simulated"]["coverage_per_reference"]
        cmp["M7_coverage"] = {"real": {k: rc[k] for k in ("mean", "cv", "zero_fraction", "le1_fraction", "p10", "median", "p90")},
                              "simulated_as_shipped": {k: sc[k] for k in ("mean", "cv", "zero_fraction", "le1_fraction", "p10", "median", "p90")},
                              "model_nb_at_real_mean_theoretical": nb_theory(rc["mean"], 4.0),
                              "real_vs_dev_zero_fraction": [real["coverage_per_reference"]["zero_fraction"],
                                                            d["real"][nl.DEV]["coverage_per_reference"]["zero_fraction"]]}
        cmp["M7_coverage"]["cv_pass_at_real_mean"] = (abs(cmp["M7_coverage"]["model_nb_at_real_mean_theoretical"]["cv"] - rc["cv"])
                                                      <= 0.1 * rc["cv"]) if rc["cv"] else None
        dep = real["error_by_read_length"]["total_edit_rate"]
        depq = real["error_by_read_mean_quality"]["total_edit_rate"]
        cmp["length_and_quality_dependence"] = {
            "real_total_rate_by_read_length_bin": dep, "read_length_bin_edges": RL_EDGES[:-1],
            "real_total_rate_by_read_mean_q_bin": depq, "read_mean_q_bin_edges": MQ_EDGES[:-1],
            "simulator": "no read-length or read-quality dependence: every read has the same per-site rates"}
        cmp["read_length"] = {"real_reads_fit_id": real["reads_by_read_id_split"]["length"],
                              "simulator": "one read per strand of the strand's length (no concatemers, truncation_rate 0)"}
        cmp["split_check_fit_vs_dev"] = {k: {"fit": real["bootstrap"][k]["value"], "dev": d["real"][nl.DEV]["bootstrap"][k]["value"],
                                             "z": round((d["real"][nl.DEV]["bootstrap"][k]["value"] - real["bootstrap"][k]["value"])
                                                        / max(np.hypot(real["bootstrap"][k]["se"], d["real"][nl.DEV]["bootstrap"][k]["se"]), 1e-12), 2)}
                                         for k in ("substitution", "deletion", "inserted_bases", "total_edits")}
        cmp["adequacy"] = d["adequacy"]
        rows[run] = cmp
    cas = out_dir / "cas9.json"
    if cas.exists():
        d = json.loads(cas.read_text())
        real = d["real"][nl.FIT]
        rows["cas9"] = compare_errors(real["errors_vs_leave_one_out_consensus"], real["bootstrap_over_reads"],
                                      d["simulated"]["errors_vs_leave_one_out_consensus"])
        rows["cas9"]["basis"] = d["basis"]
    doc = {"schema": "vnx.d3-comparison/1", "label": "PUBLIC-DATA-DERIVED (real, FIT split) vs SIMULATED (nanopore-like)",
           "thresholds": "docs/research/V7_CHANNEL_FITTING_PLAN.md 3.4 (M1-M7, M9); burstiness and heterogeneity: descriptive, 10 % relative",
           "commit": git("rev-parse", "HEAD"), "comparisons": rows}
    (out_dir / "comparison.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps({r: {k: v.get("pass", v.get("reproduced_within_10pct")) for k, v in c.items() if isinstance(v, dict)}
                      for r, c in rows.items()}, indent=1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["d13", "cas9", "compare"])
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--out", default=str(HERE / "results"))
    a = ap.parse_args(argv)
    cfg_path = Path(a.config).resolve()
    cfg = json.loads(cfg_path.read_text())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.what == "d13":
        run_d13(cfg, cfg_path, a.workers, out)
    elif a.what == "cas9":
        run_cas9(cfg, cfg_path, a.workers, out)
    else:
        run_compare(cfg, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
