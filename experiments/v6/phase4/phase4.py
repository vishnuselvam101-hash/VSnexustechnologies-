"""V6 Phase 4: failure analysis and the pre-registered consensus comparison (SIMULATED).

SIMULATED: software-generated strands, the versioned channel models of ``experiments/v6/channel`` (vnxdna.v6.loss +
vnxdna.v4.channel) and the stock decoder. No DNA was synthesised, stored or sequenced; not biological validation.

    python experiments/v6/phase4/phase4.py run --config experiments/v6/phase4/P4-EXP-01-failure-taxonomy/config.json
    python experiments/v6/phase4/phase4.py run --config experiments/v6/phase4/P4-EXP-02-qw-consensus/config.json

A trial = (cell, seed): encode once per (profile, size) → simulate the cell's model with the trial seed → decode the SAME
read file once per arm (decoder options per arm; workers = 1) → compare the container SHA-256. Arms are therefore paired
by construction. Outcomes: ``exact``, ``failed-detected``, ``FALSE_SUCCESS`` (must be 0).

Observability. The harness wraps ``vnxdna.recovery.outer._consensus_symbols`` (pass 2, data groups) with an observer
that calls the original unchanged and records, per missing address, the pending reads it had after address snapping and
the hard / quality-weighted vote errors and erasures against the ground-truth frame (the encoder's strands). It never
changes what the decoder sees or returns. Ground truth: the strand file (addresses from the clean frames) and the loss
mask of the model's loss layer (``vnxdna.simulation.loss.loss_mask``, the same function the simulator uses).

Failure classification of a trial that did not decode exactly (``stage``):
  * ``superblock``     the decoder raised before pass 2 (no superblock / configuration);
  * ``verify``         the reconstructed container failed its SHA-256 (CONTAINER_HASH_MISMATCH);
  * ``outer-loss``     some failed group lacks more symbols than its outer parity M *without any pending read*
                       (strand lost by the loss layer, zero coverage, every read unaligned or unaddressable): even a
                       perfect consensus could not have decoded it;
  * ``outer-consensus`` every failed group would have decoded had consensus recovered the addresses that had pending
                       reads (consensus-limited);
  * ``other``          anything else (reported verbatim).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "experiments" / "v6" / "channel"))
import channel as chn  # noqa: E402

from vnxdna.core.provenance import environment  # noqa: E402
from vnxdna.dnaenc.frame4 import decode_frames  # noqa: E402
from vnxdna.dnaenc.layout import KIND_DATA, Layout  # noqa: E402
from vnxdna.dnaenc.mapping import nt_to_bytes  # noqa: E402
from vnxdna.simulation.loss import loss_mask  # noqa: E402
from vnxdna.sync.template import frame_erasures_to_bytes, strip_markers_exact  # noqa: E402
from vnxdna.v2.experiment import wilson  # noqa: E402
from vnxdna.v4 import archive as ar  # noqa: E402
from vnxdna.v4 import datagen  # noqa: E402
from vnxdna.v4 import decoder as de  # noqa: E402
from vnxdna.v4 import encoder as en  # noqa: E402
from vnxdna.v4.constraints import iter_fasta  # noqa: E402
from vnxdna.v4.errors import VNXError  # noqa: E402
import vnxdna.recovery.consensus as cons  # noqa: E402
import vnxdna.recovery.outer as outer_mod  # noqa: E402

CLASSIFICATION = "SIMULATED"
STATEMENT = chn.STATEMENT


# ------------------------------------------------------------------------------------------------------ models
def materialise_model(spec, out_dir: Path) -> str:
    """A model name, or {"base": name, "name": new, "channel": {...}, "loss": {...}} → path of a full model file."""
    if isinstance(spec, str):
        return spec
    base = chn.load_model(spec["base"])
    doc = json.loads(base.path.read_text())
    doc["name"] = spec["name"]
    doc["version"] = spec.get("version", "1.0.0")
    doc["description"] = spec.get("description", f"derived from {base.name} {base.version}")
    doc["note"] = "Phase 4 derived stress profile (SIMULATED); not fitted to any measured platform."
    doc["derived_from"] = {"name": base.name, "version": base.version, "sha256": base.sha256}
    for section in ("channel", "loss"):
        for k, v in spec.get(section, {}).items():
            if k not in doc[section]:
                raise SystemExit(f"model {spec['name']}: unknown {section} key {k}")
            doc[section][k] = v
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec['name']}.json"
    text = json.dumps(doc, indent=2, sort_keys=False) + "\n"
    if not path.exists() or path.read_text() != text:
        path.write_text(text)
    chn.load_model(path)                      # validates every parameter
    return str(path)


# ------------------------------------------------------------------------------------------------------ setup
def layout_of(cell: dict) -> Layout | None:
    return Layout(**cell["layout"]).validate() if cell.get("layout") else None


def prepare(work: Path, cell: dict, data_seed: int) -> dict:
    datagen.generate(work / "payload.bin", cell["size"], "random", data_seed)
    ar.build_archive([work / "payload.bin"], work / "a.vnx", ar.ArchiveOptions(compression="none"))
    lay = layout_of(cell)
    dna = {"profile": cell.get("profile", "v4-balanced")}
    if lay is not None:
        dna.update(layout=lay, data_symbols=cell["data_symbols"], parity_symbols=cell["parity_symbols"])
    t = time.perf_counter()
    rep = en.encode_container(work / "a.vnx", work / "strands.fasta", en.DNAOptions(**dna))
    secs = time.perf_counter() - t
    strand_nt = len(next(iter_fasta(work / "strands.fasta"))[1])
    size = (work / "a.vnx").stat().st_size
    return {"container_sha256": chn.sha256_file(work / "a.vnx"), "container_bytes": size,
            "payload_sha256": chn.sha256_file(work / "payload.bin"), "strand_count": rep["strands"],
            "strand_nt": strand_nt, "nt_per_container_byte": round(rep["strands"] * strand_nt / size, 4),
            "encode_seconds": round(secs, 3)}


def truth_frames(strands: Path, lay: Layout) -> tuple[dict, list]:
    """address → true frame nt codes (data and superblock strands), plus the address of every strand in file order."""
    codes = chn.read_strand_codes(strands)
    fb, _ = strip_markers_exact(lay, codes)
    P = decode_frames(lay, nt_to_bytes(fb), np.zeros((fb.shape[0], lay.frame_bytes), dtype=bool))
    assert P.ok.all(), "clean strands must parse"
    keys = [(int(k), int(t), int(g), int(s)) for k, t, g, s in zip(P.kind, P.tag, P.group, P.symbol)]
    return {k: fb[i] for i, k in enumerate(keys)}, keys


# ------------------------------------------------------------------------------------------------------ observer
class Observer:
    """Records what pass-2 consensus had to work with (never changes the decoder's inputs or outputs)."""

    def __init__(self, truth: dict, lay: Layout):
        self.truth, self.lay = truth, lay
        self.calls: list = []

    def wrap(self, orig):
        def hooked(pend, known, lay, opt, stats, missing=None, snap_to=None):
            out = orig(pend, known, lay, opt, stats, missing, snap_to)
            if missing is not None:
                try:
                    self.calls.append(self.analyse(pend, known, missing, snap_to, out, lay, opt))
                except Exception as error:        # observability must never break a decode
                    self.calls.append({"observer_error": repr(error)})
            return out
        return hooked

    def analyse(self, pend, known, missing, snap_to, out, lay, opt) -> dict:
        r = lay.inner_parity
        known_keys = {k for k in known if k[0] == KIND_DATA}
        per_key: dict = {}
        groups: dict = defaultdict(lambda: {"known": 0, "missing": 0})
        for k in known_keys:
            groups[k[2]]["known"] += 1
        for k in missing:
            groups[k[2]]["missing"] += 1
        if pend.size:
            keys = np.stack([pend["kind"].astype(np.int64), pend["tag"].astype(np.int64), pend["group"].astype(np.int64),
                             pend["symbol"].astype(np.int64)], axis=1)
            alt = np.asarray(pend["alt"], dtype=np.int64) if "alt" in pend.dtype.names else None
            keys, _ = cons.snap_addresses(keys, missing if snap_to is None else snap_to, alt)
            rows: dict = defaultdict(list)
            for i, k in enumerate(map(tuple, keys.tolist())):
                if k in missing:
                    rows[k].append(i)
        else:
            rows = {}
        for k in missing:
            idx = rows.get(k, [])
            rec = {"reads": len(idx), "recovered": k in out}
            t = self.truth.get(k)
            if idx and t is not None:
                sel = sorted(idx)[: opt.max_pending_per_address]
                # the decoder orders a group by its sorted keys (stable lexsort): same reads, same order
                bases = pend["bases"][sel]
                for name, (best, er) in self.votes(pend, sel, bases, opt).items():
                    eb = frame_erasures_to_bytes(er[None, :])[0]
                    wrong = nt_to_bytes(best[None, :])[0] != nt_to_bytes(t[None, :])[0]
                    e = int((wrong & ~eb).sum())
                    f = int(eb.sum())
                    rec[name] = {"e": e, "f": f, "fits": 2 * e + f <= r,
                                 "err_bytes": np.flatnonzero(wrong & ~eb).tolist()}
            per_key[k] = rec
        return {"missing": {f"{k[2]}:{k[3]}": v for k, v in per_key.items()},
                "groups": {int(g): v for g, v in groups.items()},
                "recovered": len([k for k in out if k in missing])}

    @staticmethod
    def votes(pend, sel, bases, opt) -> dict:
        res = {"hard": cons.consensus_hard(bases, opt.consensus_threshold)}
        qw = getattr(cons, "consensus_quality_weighted", None)
        if qw is not None and "pq" in pend.dtype.names:
            res["qw"] = qw(bases, pend["pq"][sel], opt.consensus_threshold)
        return res


# ------------------------------------------------------------------------------------------------------ trial
def classify(status: str, rep: dict, obs_calls: list, dropped: set, M: int, exact: bool) -> dict:
    if exact:
        return {"stage": "ok"}
    if status.startswith("ERROR:"):
        stage = "verify" if "IntegrityError" in status else "superblock"
        return {"stage": stage, "error": rep.get("error")}
    failed = set(rep.get("failed_groups") or [])
    if not failed:
        return {"stage": "other", "status": status}
    # merge the observer's per-group view (one call per bucket; a full decode visits each group once)
    groups: dict = {}
    missing: dict = {}
    for c in obs_calls:
        for g, v in c.get("groups", {}).items():
            groups.setdefault(int(g), v)
        for k, v in c.get("missing", {}).items():
            missing.setdefault(k, v)
    detail = []
    loss_limited = False
    for g in sorted(failed):
        gv = groups.get(g, {"known": 0, "missing": 0})
        n_sym = gv["known"] + gv["missing"]
        k_need = n_sym - M
        ms = {k: v for k, v in missing.items() if int(k.split(":")[0]) == g}
        cls = Counter()
        for k, v in ms.items():
            if v["recovered"]:
                cls["recovered_by_consensus"] += 1
            elif (g, int(k.split(":")[1])) in dropped:
                cls["lost_dropout"] += 1
            elif v["reads"] == 0:
                cls["no_pending_read"] += 1
            elif v["reads"] == 1:
                cls["consensus_failed_1_read"] += 1
            else:
                cls["consensus_failed_multi"] += 1
        have = gv["known"] + cls["recovered_by_consensus"]
        lost = cls["lost_dropout"] + cls["no_pending_read"]
        limited = n_sym - lost < k_need
        loss_limited |= limited
        detail.append({"group": g, "symbols": n_sym, "k": k_need, "verified_pass1": gv["known"], "have": have,
                       "deficit": k_need - have, "loss_limited": limited, **cls})
    return {"stage": "outer-loss" if loss_limited else "outer-consensus", "groups": detail}


def consensus_quality(obs_calls: list, r: int) -> dict:
    """Per vote kind: attempts with ≥ 2 / exactly 1 reads, mean e and f, share fitting 2e + f ≤ r, erroneous-byte histogram."""
    out: dict = {}
    for c in obs_calls:
        for v in c.get("missing", {}).values():
            for name in ("hard", "qw"):
                if name not in v:
                    continue
                bucket = "multi" if v["reads"] >= 2 else "single"
                d = out.setdefault(name, {}).setdefault(bucket, {"n": 0, "e": 0, "f": 0, "fits": 0, "err_pos": Counter()})
                d["n"] += 1
                d["e"] += v[name]["e"]
                d["f"] += v[name]["f"]
                d["fits"] += v[name]["fits"]
                d["err_pos"].update(v[name]["err_bytes"])
    for name in out.values():
        for d in name.values():
            d["err_pos"] = {str(k): n for k, n in sorted(d["err_pos"].items())}
    return out


def run_trial(job: dict) -> dict:
    model = chn.load_model(job["model"])
    work = Path(tempfile.mkdtemp(prefix="vnx-p4-", dir=job["tmp"]))
    lay_cell = layout_of(job["cell"])
    try:
        reads = work / "reads.fastq"
        sc = chn.simulate_with_sidecar(model, job["strands"], reads, job["seed"], workers=1, command=job["command"])
        n_strands = sc["input"]["strands"]
        keep = loss_mask(n_strands, model.loss_config(job["seed"]))
        truth, order = truth_frames(Path(job["strands"]), lay_cell or job["layout"])
        lay = lay_cell or job["layout"]
        dropped = {(k[2], k[3]) for k, kept in zip(order, keep.tolist()) if not kept and k[0] == KIND_DATA}
        arms = {}
        for arm, dopts in job["arms"].items():
            if job["cell"].get("arms") and arm not in job["cell"]["arms"]:
                continue
            opts = de.DecodeOptions(workers=1, layout=lay_cell, **dopts)
            obs = Observer(truth, lay)
            orig = outer_mod._consensus_symbols
            outer_mod._consensus_symbols = obs.wrap(orig)
            out = work / f"out-{arm}.vnx"
            t = time.perf_counter()
            try:
                res = de.decode_reads(reads, out, opts, overwrite=True, workdir=work)
                status, rep = res.status, res.report
            except VNXError as error:
                status, rep = f"ERROR:{type(error).__name__}", {"error": str(error)[:500]}
            finally:
                outer_mod._consensus_symbols = orig
            secs = time.perf_counter() - t
            got = chn.sha256_file(out) if out.exists() else None
            exact = status == "SUCCESS" and got == job["container_sha256"]
            false_success = status == "SUCCESS" and not exact
            M = job["parity_symbols"]
            cls = classify(status, rep, obs.calls, dropped, M, exact)
            reads_stats = rep.get("reads") or {}
            arms[arm] = {
                "status": status, "outcome": "FALSE_SUCCESS" if false_success else ("exact" if exact else "failed-detected"),
                "false_success": bool(false_success), "container_sha256_decoded": got,
                "decode_seconds": round(secs, 3), "peak_rss_bytes": rep.get("peak_rss_bytes"),
                "stage_seconds": rep.get("stage_seconds"), "reads": reads_stats,
                "groups_failed": rep.get("groups_failed"), "failed_groups": rep.get("failed_groups"),
                "recovery_schedule": rep.get("recovery_schedule"), "indel_recovery": rep.get("indel_recovery"),
                "soft_decoding": rep.get("soft_decoding"), "recovery_plan_spent": (rep.get("recovery_plan") or {}).get("spent"),
                "classification": cls, "consensus_quality": consensus_quality(obs.calls, lay.inner_parity),
                "observer_errors": [c["observer_error"] for c in obs.calls if "observer_error" in c],
                "decoder": json.loads(json.dumps(asdict(opts), default=str)),
            }
        return {"record": "trial", "classification": CLASSIFICATION, "cell": job["cell"]["id"], "model": model.name,
                "model_version": model.version, "model_sha256": model.sha256, "seed": job["seed"],
                "reads_sha256": sc["output"]["sha256"], "reads": sc["output"]["reads"],
                "realised_rates": sc["realised_rates"], "strands_lost": int((~keep).sum()), "arms": arms}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------ statistics
def paired_difference(a: list, bvals: list, z: float = 1.959963984540054) -> dict:
    """Paired binary outcomes (lists of bools, same seeds) → Newcombe method-10 interval for p_B − p_A."""
    n = len(a)
    s_a, s_b = sum(a), sum(bvals)
    b = sum(1 for x, y in zip(a, bvals) if y and not x)
    c = sum(1 for x, y in zip(a, bvals) if x and not y)
    d = sum(1 for x, y in zip(a, bvals) if not x and not y)
    e = sum(1 for x, y in zip(a, bvals) if x and y)
    if n == 0:
        return {"n": 0}
    pa, pb = s_a / n, s_b / n
    l1, u1 = wilson(s_b, n, z)
    l2, u2 = wilson(s_a, n, z)
    diff = pb - pa
    # phi: correlation of the paired outcomes (0 when undefined, as recommended)
    denom = math.sqrt((e + b) * (d + c) * (e + c) * (d + b)) if min(e + b, d + c, e + c, d + b) > 0 else 0.0
    phi = (e * d - b * c) / denom if denom else 0.0
    dl = math.sqrt(max(0.0, (pb - l1) ** 2 - 2 * phi * (pb - l1) * (u2 - pa) + (u2 - pa) ** 2))
    du = math.sqrt(max(0.0, (u1 - pb) ** 2 - 2 * phi * (u1 - pb) * (pa - l2) + (pa - l2) ** 2))
    return {"n": n, "baseline_exact": s_a, "candidate_exact": s_b, "only_candidate": b, "only_baseline": c,
            "difference": round(diff, 4), "ci95_low": round(max(-1.0, diff - dl), 4), "ci95_high": round(min(1.0, diff + du), 4)}


def t975(df: int) -> float:
    """0.975 quantile of Student's t (Cornish-Fisher expansion; |error| < 0.002 for df >= 5, exact table below)."""
    table = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776}
    if df in table:
        return table[df]
    z = 1.959963984540054
    v = float(df)
    return (z + (z**3 + z) / (4 * v) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * v**2)
            + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * v**3))


def mean_ci(x: list) -> dict:
    """Mean of paired differences with a t-based 95 % interval."""
    n = len(x)
    if n == 0:
        return {"n": 0}
    m = float(np.mean(x))
    if n < 2:
        return {"n": n, "mean": m}
    sd = float(np.std(x, ddof=1))
    half = t975(n - 1) * sd / math.sqrt(n)
    return {"n": n, "mean": round(m, 4), "sd": round(sd, 4), "ci95_low": round(m - half, 4), "ci95_high": round(m + half, 4)}


def summarise(trials: list, arms: list, baseline: str | None) -> dict:
    cells: dict = {}
    for t in trials:
        cells.setdefault(t["cell"], []).append(t)
    out = {}
    for cid, ts in cells.items():
        ts.sort(key=lambda t: t["seed"])
        c = {"trials": len(ts), "arms": {}}
        for arm in arms:
            if any(arm not in t["arms"] for t in ts):
                continue
            rs = [t["arms"][arm] for t in ts]
            ex = sum(r["outcome"] == "exact" for r in rs)
            lo, hi = wilson(ex, len(rs))
            secs = sorted(r["decode_seconds"] for r in rs)
            rss = sorted((r["peak_rss_bytes"] or 0) for r in rs)
            stages = Counter(r["classification"]["stage"] for r in rs)
            cq = Counter()
            for r in rs:
                for name, d in r["consensus_quality"].items():
                    for bucket, v in d.items():
                        for key in ("n", "e", "f", "fits"):
                            cq[f"{name}.{bucket}.{key}"] += v[key]
            c["arms"][arm] = {"exact": ex, "failed_detected": sum(r["outcome"] == "failed-detected" for r in rs),
                              "false_success": sum(r["false_success"] for r in rs), "success_rate": round(ex / len(rs), 4),
                              "wilson95_low": round(lo, 4), "wilson95_high": round(hi, 4),
                              "median_decode_seconds": secs[len(secs) // 2], "max_decode_seconds": secs[-1],
                              "median_peak_rss_mib": round(rss[len(rss) // 2] / 2**20, 1),
                              "stages": dict(stages), "consensus_quality_totals": dict(cq),
                              "consensus_recovered_total": sum((r["reads"] or {}).get("consensus_recovered", 0) for r in rs)}
        if baseline is not None:
            base = [t["arms"][baseline] for t in ts]
            for arm in arms:
                if arm == baseline or any(arm not in t["arms"] or baseline not in t["arms"] for t in ts):
                    continue
                cand = [t["arms"][arm] for t in ts]
                pd = paired_difference([r["outcome"] == "exact" for r in base], [r["outcome"] == "exact" for r in cand])
                cr = mean_ci([(y["reads"] or {}).get("consensus_recovered", 0) - (x["reads"] or {}).get("consensus_recovered", 0)
                              for x, y in zip(base, cand)])
                ratio = [y["decode_seconds"] / x["decode_seconds"] for x, y in zip(base, cand) if x["decode_seconds"] > 0]
                rss = [(y["peak_rss_bytes"] or 0) / (x["peak_rss_bytes"] or 1) for x, y in zip(base, cand)]
                c[f"paired_{arm}_vs_{baseline}"] = {"exact": pd, "consensus_recovered_difference": cr,
                                                    "median_decode_time_ratio": round(float(np.median(ratio)), 3) if ratio else None,
                                                    "median_peak_rss_ratio": round(float(np.median(rss)), 3) if rss else None}
        out[cid] = c
    return out


# ------------------------------------------------------------------------------------------------------ driver
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--jobs", type=int, default=3)
    r.add_argument("--cells", help="comma-separated cell ids (default: all)")
    r.add_argument("--out", help="results JSON-lines (default: <config dir>/trials.jsonl)")
    s = sub.add_parser("summarise")
    s.add_argument("--config", required=True)
    s.add_argument("--trials", nargs="+", required=True)
    s.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    cfg_path = Path(a.config).resolve()
    cfg = json.loads(cfg_path.read_text())
    exp_dir = cfg_path.parent
    arms = list(cfg["arms"])
    if a.cmd == "summarise":
        trials = [json.loads(line) for p in a.trials for line in open(p) if '"record": "trial"' in line]
        doc = {"record": "summary", "classification": CLASSIFICATION, "statement": STATEMENT, "config": cfg,
               "trials": len(trials), "false_success_total": sum(v["false_success"] for t in trials for v in t["arms"].values()),
               "cells": summarise(trials, arms, cfg.get("baseline"))}
        Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return 2 if doc["false_success_total"] else 0
    cells = cfg["cells"] if not a.cells else [c for c in cfg["cells"] if c["id"] in a.cells.split(",")]
    out_path = Path(a.out) if a.out else exp_dir / "trials.jsonl"
    tmp = Path(tempfile.mkdtemp(prefix="vnx-p4-setup-", dir=cfg.get("tmp_dir")))
    command = " ".join(sys.argv)
    t0 = time.perf_counter()
    try:
        prepared: dict = {}
        jobs = []
        for cell in cells:
            model = materialise_model(cell["model"], exp_dir / "models")
            pkey = json.dumps({k: cell.get(k) for k in ("profile", "layout", "data_symbols", "parity_symbols", "size")},
                              sort_keys=True)
            if pkey not in prepared:
                wd = tmp / f"prep{len(prepared)}"
                wd.mkdir()
                info = prepare(wd, cell, cfg["data_seed"])
                lay = layout_of(cell)
                if lay is None:
                    from vnxdna.dnaenc.layout import PROFILES
                    lay, _, M = PROFILES[cell.get("profile", "v4-balanced")]
                else:
                    M = cell["parity_symbols"]
                prepared[pkey] = (wd, info, lay, M)
            wd, info, lay, M = prepared[pkey]
            for i in range(cell.get("seeds", cfg["seeds"])):
                jobs.append({"cell": cell, "model": model, "seed": cfg["base_seed"] + i, "strands": str(wd / "strands.fasta"),
                             "container_sha256": info["container_sha256"], "arms": cfg["arms"], "layout": lay,
                             "parity_symbols": M, "tmp": str(tmp), "command": command})
        header = {"record": "header", "classification": CLASSIFICATION, "statement": STATEMENT, "command": command,
                  "config": cfg, "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
                  "git": chn.git_info(), "environment": environment(),
                  "backends": backends(), "setup": {k: v[1] for k, v in prepared.items()},
                  "cells_run": [c["id"] for c in cells], "jobs": a.jobs, "nice": os.nice(0)}
        with open(out_path, "w") as fh:
            print(json.dumps(header, sort_keys=True, default=str), file=fh, flush=True)
            if a.jobs > 1:
                with ProcessPoolExecutor(max_workers=a.jobs) as pool:
                    for res in pool.map(run_trial, jobs):
                        print(json.dumps(res, sort_keys=True), file=fh, flush=True)
            else:
                for j in jobs:
                    print(json.dumps(run_trial(j), sort_keys=True), file=fh, flush=True)
            print(json.dumps({"record": "end", "wall_seconds": round(time.perf_counter() - t0, 1)}), file=fh, flush=True)
        trials = [json.loads(line) for line in open(out_path) if '"record": "trial"' in line]
        fs = sum(v["false_success"] for t in trials for v in t["arms"].values())
        print(f"{len(trials)} trials, {fs} false SUCCESS, {round(time.perf_counter() - t0, 1)} s -> {out_path}")
        return 2 if fs else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def backends() -> dict:
    out = {}
    try:
        from vnxdna.native import align as na
        out["align"] = na.resolve_backend(None)
    except Exception as error:  # pragma: no cover - provenance only
        out["align"] = repr(error)
    for name, mod in (("reads", "vnxdna.native.reads"), ("rs", "vnxdna.native.rs")):
        try:
            out[name] = "native" if __import__(mod, fromlist=["x"]).available() else "reference"
        except Exception as error:  # pragma: no cover
            out[name] = repr(error)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
