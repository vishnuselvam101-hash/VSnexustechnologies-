"""A-DIAG (V7 protocol §8.1): failure taxonomy of the 6.0 default decoder (EXPERIMENTAL, SIMULATED; descriptive).

    PYTHONPATH=src python experiments/v7/a-diag/adiag.py run --config experiments/v7/a-diag/config.json --jobs 4
    PYTHONPATH=src python experiments/v7/a-diag/adiag.py summarise --config experiments/v7/a-diag/config.json

A trial = (cell, seed). The cell's channel model (the UNFITTED V6 models of ``experiments/v6/channel``, with the cell's
coverage) is simulated with the trial seed on the strands of one v4-balanced archive; the SAME read file is decoded by
the 6.0 default decoder (``DecodeOptions()`` plus the observability-only ``stage_counters=True``) with 1 worker, and once
more with 4 workers and 512-read batches (determinism: the outcome and every counter must be identical).

Outcomes follow protocol §6 (``vnxdna.benchmark.outcome``). Ground truth comes from the simulator itself: every read's
source strand and orientation are regenerated with ``simulate_batch(..., truth=True)`` (a pure function of strands,
model and seed; the regenerated reads are checked to be identical to the read file). The decoder never sees it.

What the harness adds to the decoder's own counters (it needs ground truth):

* a per-read trace of the 1-worker decode, captured by wrapping ``vnxdna.pipeline.decode._process`` (the decoder's own
  pass-1 worker; its output is passed on unchanged): each read's first pass-1 failure, final orientation, verified
  address, pending record;
* the pass-2 placement of every pending record and the consensus outcome per address, recomputed with the decoder's own
  functions (``snap_addresses``, ``resolve_duplicates``, ``_consensus_symbols``) on the traced records. When the decoder
  reached pass 2 the recomputed recoveries are checked against the ones it actually made (``replication_ok``). When it
  stopped at the superblock (NO_SUPERBLOCK), the data-strand rows of the funnel are what pass 2 WOULD have done with the
  true geometry (marked ``pass2_reached: false``);
* the stage at which each strand was lost (ordered as ``STRAND_STAGES``): ``coverage`` (the pool holds 0 or 1 reads of
  it), ``read_parsing`` (never: the parser refuses a malformed file as a whole), ``orientation`` (no read of it ends in
  its true orientation), ``alignment`` (no correctly oriented read aligns: net drift beyond the band, or no path inside
  it), ``address`` (aligned reads exist but none reaches the true address: header unreadable, wrong scrambler variant
  byte, or other header bytes corrupted beyond the one-byte snap), then the frame stage of the reads placed at the
  address: ``crc`` (RS decoded, CRC failed), ``indel_placement`` (columns with no read base, i.e. indel segment erasures,
  exceed the inner parity r), ``inner_ecc`` (one placed read, errors + erasures beyond r), ``consensus`` (several placed
  reads, the vote leaves 2e + f > r);
* the decode's FIRST FAILING STAGE: for each unit the decoder could not decode (the superblock for NO_SUPERBLOCK, else
  every failed row), the first stage in ``STRAND_STAGES`` after which fewer strands than the unit needs (ks = 3 of 12
  superblock symbols; k = symbols − M per row) survive; the decode's stage is the earliest over its failed units. A unit
  that still had enough strands is attributed to ``superblock`` / ``outer_ecc``; a SHA-256 refusal to ``archive``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "experiments" / "v6" / "phase4"))
import phase4 as p4  # noqa: E402

import vnxdna.pipeline.decode as dec_mod  # noqa: E402
import vnxdna.recovery.outer as outer_mod  # noqa: E402
from vnxdna.benchmark.outcome import OUTCOMES, classify_outcome, decode_claim  # noqa: E402
from vnxdna.core.provenance import environment  # noqa: E402
from vnxdna.dnaenc.frame4 import decode_frames  # noqa: E402
from vnxdna.dnaenc.layout import KIND_DATA, KIND_SUPER, PROFILES  # noqa: E402
from vnxdna.dnaenc.mapping import nt_to_bytes  # noqa: E402
from vnxdna.dnaenc.superblock import Superblock  # noqa: E402
from vnxdna.native.reads import iter_reads  # noqa: E402
from vnxdna.recovery.consensus import _consensus_symbols, consensus_hard, resolve_duplicates, snap_addresses  # noqa: E402
from vnxdna.recovery.options import DecodeOptions  # noqa: E402
from vnxdna.recovery.spill import _bucket_count  # noqa: E402
from vnxdna.simulation.channel import _strand_batches, simulate_batch  # noqa: E402
from vnxdna.sync.template import frame_erasures_to_bytes  # noqa: E402
from vnxdna.v2.experiment import wilson  # noqa: E402

CLASSIFICATION = "EXPERIMENTAL / SIMULATED"
STATEMENT = ("EXPERIMENTAL (protocol §7 exploration seeds 82000-82099), SIMULATED: software strands and the unfitted V6 "
             "channel models. No DNA was synthesised, stored or sequenced; no public data and no fitted model is used. "
             "Descriptive diagnostic, not a confirmatory result.")
STRAND_STAGES = ("coverage", "read_parsing", "orientation", "alignment", "address", "indel_placement", "inner_ecc", "crc",
                 "consensus")
DECODE_STAGES = STRAND_STAGES + ("superblock", "outer_ecc", "archive")
RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)
FATE = {0: "verified", 1: "drift_beyond_band", 2: "path_beyond_band", 3: "erasures_exceed_parity", 4: "inner_ecc",
        5: "crc", 6: "invalid_version_or_kind"}


# ------------------------------------------------------------------------------------------------------ ground truth
def read_truth(model, strands: Path, seed: int, reads_path: Path) -> tuple[np.ndarray, np.ndarray, list]:
    """(source strand index, simulator reverse-complemented) of every read, regenerated with the simulator's own pure
    batch function, and the reads themselves (as the decoder's parser returns them). Refuses if the regenerated reads
    differ from the file in any base."""
    loss = model.loss_config(seed)
    if loss.dropout or (loss.burst_count and loss.burst_length):
        raise SystemExit("A-DIAG truth needs a model without a strand-loss layer")
    cfg = model.channel_config(seed)
    if cfg.shuffle_window:
        raise SystemExit("A-DIAG truth needs shuffle_window = 0")
    src, rc, codes, lens = [], [], [], []
    off = 0
    for index, batch in _strand_batches(strands):
        res = simulate_batch(batch, cfg, index, truth=True)
        src.append(res["source"] + off)
        rc.append(res["reverse_complement"])
        codes.append(res["codes"])
        lens.append(res["lengths"])
        off += batch.shape[0]
    reads = []
    for b in iter_reads(reads_path, 1 << 17):
        o = np.concatenate([[0], np.cumsum(b.lengths)])
        reads += [b.codes[o[k]:o[k + 1]].copy() for k in range(b.count)]
    sim_codes, sim_lens = np.concatenate(codes), np.concatenate(lens)
    if sim_lens.size != len(reads) or not np.array_equal(sim_codes, np.concatenate(reads) if reads else sim_codes[:0]) \
            or not np.array_equal(sim_lens, np.array([r.size for r in reads])):
        raise SystemExit("regenerated reads differ from the read file: ground truth unusable")
    return np.concatenate(src), np.concatenate(rc), reads


# ------------------------------------------------------------------------------------------------------ decode + trace
class Trace:
    """Wraps the decoder's pass-1 worker (1 worker, in-process) and records its per-read output; passes it on unchanged."""

    def __init__(self):
        self.batches: list = []

    def wrap(self, orig):
        def hooked(batch_codes, lengths, quals):
            out = orig(batch_codes, lengths, quals)
            self.batches.append((int(lengths.size), {k: out[k] for k in ("acc_index", "acc_fields", "acc_payload",
                                                                          "pend_fields", "pend_alt", "pend_bases")},
                                 out["diag"]))
            return out
        return hooked

    def assemble(self, frame_nt: int, payload: int) -> dict:
        fate, rc_used, ver_idx, ver_f, ver_p, pidx, pf, pa, pb = [], [], [], [], [], [], [], [], []
        off = 0
        for n, o, d in self.batches:
            fate.append(d["fate"])
            rc_used.append(d["rc_used"])
            ver_idx.append(o["acc_index"] + off)
            ver_f.append(o["acc_fields"])
            ver_p.append(o["acc_payload"])
            pidx.append(d["pend_index"] + off)
            pf.append(o["pend_fields"])
            pa.append(o["pend_alt"])
            pb.append(o["pend_bases"])
            off += n

        def cat(xs, shape, dtype):
            return np.concatenate(xs) if xs else np.zeros(shape, dtype=dtype)
        return {"fate": cat(fate, (0,), np.int8), "rc_used": cat(rc_used, (0,), bool),
                "ver_idx": cat(ver_idx, (0,), np.int64), "ver_fields": cat(ver_f, (0, 4), np.int64),
                "ver_payload": cat(ver_p, (0, payload), np.uint8), "pend_idx": cat(pidx, (0,), np.int64),
                "pend_fields": cat(pf, (0, 4), np.int64), "pend_alt": cat(pa, (0, 4), np.int64),
                "pend_bases": cat(pb, (0, frame_nt), np.uint8)}


class Pass2Observer:
    """Records the addresses pass-2 consensus actually recovered (data groups); never changes inputs or outputs."""

    def __init__(self):
        self.recovered: set = set()
        self.calls = 0

    def wrap(self, orig):
        def hooked(pend, known, lay, opt, stats, missing=None, snap_to=None):
            out = orig(pend, known, lay, opt, stats, missing, snap_to)
            self.calls += 1
            self.recovered.update(out)
            return out
        return hooked


def run_decode(reads: Path, out: Path, container_sha: str, opts: DecodeOptions, trace: bool) -> dict:
    tr, ob = Trace(), Pass2Observer()
    orig_p, orig_c = dec_mod._process, outer_mod._consensus_symbols
    if trace:
        dec_mod._process = tr.wrap(orig_p)
        outer_mod._consensus_symbols = ob.wrap(orig_c)
    t = time.perf_counter()
    try:
        claim, res, err = decode_claim(lambda: dec_mod.decode_reads(reads, out, opts, overwrite=False), out)
    finally:
        dec_mod._process, outer_mod._consensus_symbols = orig_p, orig_c
    secs = time.perf_counter() - t
    oc = classify_outcome(claim, {"container": container_sha})
    if res is not None:
        rep = res.report
        counters = rep.get("stage_counters")
    else:
        rep = dict(getattr(err, "details", {}) or {})
        counters = rep.pop("stage_counters", None)
    if out.exists():
        out.unlink()
    return {"outcome": oc, "status": None if res is None else res.status, "error_code": getattr(err, "code", None),
            "error": None if err is None else f"{type(err).__name__}: {str(err)[:300]}",
            "failed_groups": (res.report.get("failed_groups") if res is not None else None),
            "groups_failed": (res.report.get("groups_failed") if res is not None else None),
            "reads_report": (res.report.get("reads") if res is not None else None),
            "stage_counters": counters, "decode_seconds": round(secs, 3),
            "_trace": tr if trace else None, "_observer": ob if trace else None}


# ------------------------------------------------------------------------------------------------------ analysis
def _pend_array(fields, alt, bases, frame_nt: int) -> np.ndarray:
    """Pending records with the fields pass 2 reads (the spill's pending dtype without the optional columns)."""
    dtype = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("alt", ">i8", (4,)),
                      ("bases", "u1", (frame_nt,))])
    rec = np.zeros(len(fields), dtype=dtype)
    if len(fields):
        rec["kind"], rec["tag"], rec["group"], rec["symbol"] = fields[:, 0], fields[:, 1], fields[:, 2], fields[:, 3]
        rec["alt"], rec["bases"] = alt, bases
    return rec


def _acc_array(fields, payload_arr, payload: int) -> np.ndarray:
    dtype = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("payload", "u1", (payload,))])
    rec = np.zeros(len(fields), dtype=dtype)
    if len(fields):
        rec["kind"], rec["tag"], rec["group"], rec["symbol"] = fields[:, 0], fields[:, 1], fields[:, 2], fields[:, 3]
        rec["payload"] = payload_arr
    return rec


def analyse(lay, M: int, order: list, truth_fr: dict, src: np.ndarray, sim_rc: np.ndarray, reads: list, tr: dict,
            reads_bytes: int, decode: dict, observed: set | None) -> dict:
    r = lay.inner_parity
    opt = DecodeOptions()
    n_reads, n_strands = len(reads), len(order)
    keys = [tuple(k) for k in order]
    tag = keys[0][1]
    is_sb = np.array([k[0] == KIND_SUPER for k in keys])
    true_byte0 = {k: int(nt_to_bytes(truth_fr[k][None, :4])[0, 0]) for k in keys}
    # ---- per read
    fate, rc_used = tr["fate"], tr["rc_used"]
    assert fate.size == n_reads, "trace does not cover every read"
    oriented_ok = rc_used == sim_rc                       # the decoder must flip exactly the reads the simulator flipped
    aligned = (fate != 1) & (fate != 2)
    ver_key = {}
    for i, f in zip(tr["ver_idx"].tolist(), tr["ver_fields"].tolist()):
        ver_key[i] = tuple(f)
    verified_wrong = sum(1 for i, k in ver_key.items() if k != keys[src[i]])
    # ---- pass 2 (data) replicated with the decoder's functions, per spill bucket, with the true geometry
    B = _bucket_count(max(1, reads_bytes // (lay.strand_nt + 20)))
    acc = _acc_array(tr["ver_fields"], tr["ver_payload"], lay.payload_bytes)
    acc_d = acc[(acc["kind"] == KIND_DATA) & (acc["tag"] == tag)]
    n_sym = Counter(k[2] for k in keys if k[0] == KIND_DATA)
    total = max(n_sym) + 1
    acc_d = acc_d[acc_d["group"] < total]
    verified_syms, _ = resolve_duplicates(acc_d)
    pend = _pend_array(tr["pend_fields"], tr["pend_alt"], tr["pend_bases"], lay.frame_nt)
    placed_key = [None] * len(pend)                       # final pass-2 address of every pending record (data)
    recovered = set()
    for b in range(B):
        sel = np.flatnonzero(pend["group"].astype(np.int64) % B == b)
        missing = {(KIND_DATA, tag, g, s) for g in range(b, total, B) for s in range(n_sym[g])} - set(verified_syms)
        sub = pend[sel]
        if sub.size:
            k4 = np.stack([sub["kind"].astype(np.int64), sub["tag"].astype(np.int64), sub["group"].astype(np.int64),
                           sub["symbol"].astype(np.int64)], axis=1)
            snapped, _ = snap_addresses(k4, missing, np.asarray(sub["alt"], dtype=np.int64))
            for j, kk in zip(sel.tolist(), snapped.tolist()):
                if tuple(kk) in missing:
                    placed_key[j] = tuple(kk)
        recovered |= set(_consensus_symbols(sub, dict(verified_syms), lay, opt, Counter(), missing))
    # ---- superblock replicated (bucket 0; consensus by exact tentative key, no snapping)
    ks, ms = Superblock.symbols(lay.payload_bytes)
    acc_sb = acc[acc["kind"] == KIND_SUPER]
    sb_syms, _ = resolve_duplicates(acc_sb)
    psel = np.flatnonzero((pend["kind"] == KIND_SUPER) & (pend["group"] == 0))      # group 0 lives in bucket 0
    sb_rec = set(_consensus_symbols(pend[psel], dict(sb_syms), lay, opt, Counter()))
    sb_have = {k for k in set(sb_syms) | sb_rec if k[1] == tag and k[2] == 0 and k[3] < ks + ms}
    sb_placed = {j: tuple(int(x) for x in tr["pend_fields"][j]) for j in psel.tolist()}
    pass2_reached = decode["error_code"] != "NO_SUPERBLOCK" and decode["status"] is not None
    replication_ok = None
    if pass2_reached and observed is not None:
        replication_ok = (recovered & {k for k in keys if k[0] == KIND_DATA}) == \
            (observed & {k for k in keys if k[0] == KIND_DATA})
    # ---- per strand
    by_strand: list = [[] for _ in range(n_strands)]
    for i, s in enumerate(src.tolist()):
        by_strand[s].append(i)
    pend_of_read = {int(i): j for j, i in enumerate(tr["pend_idx"].tolist())}
    _, fpos = lay.template()
    hpos = fpos[:4]
    stage_of = []
    addr_reasons_total: Counter = Counter()
    e_f = []
    for s in range(n_strands):
        key = keys[s]
        rs = by_strand[s]
        rec_ok = (key in sb_have) if is_sb[s] else (key in verified_syms or key in recovered)
        if rec_ok:
            stage_of.append("recovered")
            continue
        if len(rs) <= 1:
            stage_of.append("coverage")
            continue
        ro = [i for i in rs if oriented_ok[i]]
        if not ro:
            stage_of.append("orientation")
            continue
        ra = [i for i in ro if aligned[i]]
        if not ra:
            stage_of.append("alignment")
            continue
        where = sb_placed if is_sb[s] else placed_key
        placed = [pend_of_read[i] for i in rs if i in pend_of_read
                  and (where.get(pend_of_read[i]) if is_sb[s] else where[pend_of_read[i]]) == key]
        placed_ver = [i for i in rs if ver_key.get(i) == key]
        if not placed and not placed_ver:
            why = Counter()
            for i in ra:
                if i in ver_key:
                    why["verified_other_address"] += 1
                elif i not in pend_of_read:
                    why["header_unreadable"] += 1
                else:
                    j = pend_of_read[i]
                    pb = np.minimum(tr["pend_bases"][j][None, :4], 3)
                    o = RC[reads[i][::-1]] if rc_used[i] else reads[i]
                    raw = np.minimum(o[hpos[hpos < o.size]], 3)
                    raw = np.concatenate([raw, np.zeros(4 - raw.size, dtype=np.uint8)])[None, :]
                    b_proj, b_raw = int(nt_to_bytes(pb)[0, 0]), int(nt_to_bytes(raw)[0, 0])
                    why["scrambler_variant_wrong" if (b_proj != true_byte0[key] and b_raw != true_byte0[key])
                        else "header_corrupted"] += 1
            addr_reasons_total.update(why)
            stage_of.append("address")
            continue
        # placed but not recovered: the frame the decoder voted (records in spill order, capped)
        grp = np.stack([tr["pend_bases"][j] for j in sorted(placed)[: opt.max_pending_per_address]])
        if grp.shape[0] == 1:
            best, er = np.minimum(grp[0], 3), grp[0] > 3
        else:
            best, er = consensus_hard(grp, opt.consensus_threshold)
        empty = (grp > 3).all(axis=0)
        eb = frame_erasures_to_bytes(er[None, :])[0]
        f_empty = int(frame_erasures_to_bytes(empty[None, :])[0].sum())
        fb = nt_to_bytes(np.minimum(best, 3)[None, :])[0]
        e = int(((fb != nt_to_bytes(truth_fr[key][None, :])[0]) & ~eb).sum())
        P = decode_frames(lay, fb[None, :], eb[None, :])
        if bool(P.rs_ok[0]) and not bool(P.crc_ok[0]):
            st = "crc"
        elif f_empty > r:
            st = "indel_placement"
        elif grp.shape[0] == 1:
            st = "inner_ecc"
        else:
            st = "consensus"
        e_f.append({"m": int(grp.shape[0]), "e": e, "f": int(eb.sum()), "f_empty": f_empty, "stage": st})
        stage_of.append(st)
    stage_of = np.array(stage_of)
    # ---- units and the first failing stage

    def unit_stage(members: np.ndarray, need: int, fallback: str) -> str:
        lost = 0
        for st in STRAND_STAGES:
            lost += int((stage_of[members] == st).sum())
            if members.size - lost < need:
                return st
        return fallback

    rows = {}
    for g in range(total):
        mem = np.array([i for i, k in enumerate(keys) if k[0] == KIND_DATA and k[2] == g])
        rows[g] = (mem, n_sym[g] - M)
    sb_members = np.flatnonzero(is_sb)
    oc = decode["outcome"]["outcome"]
    units: list = []
    if oc == "EXACT":
        first = None
    elif oc == "CRASH":
        first = "crash"
    elif decode["error_code"] == "NO_SUPERBLOCK":
        units = [{"unit": "superblock", "stage": unit_stage(sb_members, ks, "superblock")}]
        first = units[0]["stage"]
    elif decode["error_code"] == "CONTAINER_HASH_MISMATCH":
        first = "archive"
    elif decode["failed_groups"]:
        for g in decode["failed_groups"]:
            units.append({"unit": f"row{g}", "stage": unit_stage(*rows[g], "outer_ecc")})
        first = min((u["stage"] for u in units), key=DECODE_STAGES.index)
    else:
        first = f"other:{decode['error_code'] or decode['status']}"
    would = Counter(unit_stage(mem, k, "decodable") for mem, k in rows.values())
    # ---- funnels
    def survive(members: np.ndarray) -> dict:
        out = {"total": int(members.size)}
        lost = 0
        for st in STRAND_STAGES:
            lost += int((stage_of[members] == st).sum())
            out[f"after_{st}"] = int(members.size - lost)
        out["recovered"] = int((stage_of[members] == "recovered").sum())
        return out

    def rows_surviving() -> dict:
        out = {"total": total}
        for i, st in enumerate(STRAND_STAGES):
            out[f"after_{st}"] = sum(1 for mem, k in rows.values()
                                     if mem.size - int(np.isin(stage_of[mem], STRAND_STAGES[: i + 1]).sum()) >= k)
        return out

    placed_true = sum(1 for i in range(n_reads) if ver_key.get(i) == keys[src[i]]) + \
        sum(1 for j, i in enumerate(tr["pend_idx"].tolist())
            if (sb_placed.get(j) if is_sb[src[i]] else placed_key[j]) == keys[src[i]])
    reads_f = {"reads": n_reads, "oriented_correctly": int(oriented_ok.sum()),
               "oriented_and_aligned": int((oriented_ok & aligned).sum()),
               "verified_pass1": int((fate == 0).sum()), "pending_kept": int(tr["pend_idx"].size),
               "placed_at_true_address": int(placed_true),
               "verified_wrong_address": int(verified_wrong)}
    pass1_fate = Counter(FATE[int(x)] for x in fate.tolist())
    return {"first_failing_stage": first, "units": units, "pass2_reached": bool(pass2_reached),
            "replication_ok": replication_ok, "spill_buckets": B,
            "funnel": {"reads": reads_f, "data_strands": survive(np.flatnonzero(~is_sb)),
                       "superblock_strands": survive(sb_members), "data_rows": rows_surviving(),
                       "superblock_unit": {"need": ks, "have_symbols": len(sb_have)}},
            "strand_stage_counts": {"data": dict(Counter(stage_of[~is_sb].tolist())),
                                    "superblock": dict(Counter(stage_of[is_sb].tolist()))},
            "address_reasons_reads": dict(addr_reasons_total), "pass1_read_fate": dict(pass1_fate),
            "data_rows_would_fail_at": dict(would),
            "placed_frames": {"n": len(e_f), "mean_e": round(float(np.mean([x["e"] for x in e_f])), 3) if e_f else None,
                              "mean_f": round(float(np.mean([x["f"] for x in e_f])), 3) if e_f else None,
                              "mean_f_empty": round(float(np.mean([x["f_empty"] for x in e_f])), 3) if e_f else None,
                              "single_read": sum(1 for x in e_f if x["m"] == 1)}}


# ------------------------------------------------------------------------------------------------------ trial
def run_trial(job: dict) -> dict:
    model = p4.chn.load_model(job["model"])
    lay, _, M = PROFILES[job["profile"]]
    work = Path(tempfile.mkdtemp(prefix="vnx-adiag-", dir=job["tmp"]))
    try:
        reads = work / "reads.fastq"
        sc = p4.chn.simulate_with_sidecar(model, job["strands"], reads, job["seed"], workers=1, command=job["command"])
        src, sim_rc, rlist = read_truth(model, Path(job["strands"]), job["seed"], reads)
        truth_fr, order = p4.truth_frames(Path(job["strands"]), lay)
        d1 = run_decode(reads, work / "o1.vnx", job["container_sha256"], DecodeOptions(stage_counters=True, workers=1),
                        trace=True)
        d4 = run_decode(reads, work / "o4.vnx", job["container_sha256"],
                        DecodeOptions(stage_counters=True, workers=4, batch_reads=512), trace=False)
        tr = d1.pop("_trace").assemble(lay.frame_nt, lay.payload_bytes)
        ob = d1.pop("_observer")
        d4.pop("_trace")
        d4.pop("_observer")
        an = analyse(lay, M, order, truth_fr, src, sim_rc, rlist, tr, reads.stat().st_size, d1, ob.recovered)
        same = (d1["outcome"] == d4["outcome"] and d1["stage_counters"] == d4["stage_counters"]
                and d1["status"] == d4["status"] and d1["error_code"] == d4["error_code"])
        return {"record": "trial", "classification": CLASSIFICATION, "cell": job["cell"], "seed": job["seed"],
                "model": model.name, "model_version": model.version, "model_sha256": model.sha256,
                "reads_sha256": sc["output"]["sha256"], "reads": sc["output"]["reads"],
                "realised_rates": sc["realised_rates"], "decode": d1,
                "determinism_w4": {"identical": bool(same), "outcome": d4["outcome"]["outcome"],
                                   "decode_seconds": d4["decode_seconds"]},
                "diagnosis": an}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------------------------------------------ summary
def summarise(trials: list) -> dict:
    cells: dict = {}
    for t in trials:
        cells.setdefault(t["cell"], []).append(t)
    out = {}
    for cid, ts in cells.items():
        ts.sort(key=lambda t: t["seed"])
        n = len(ts)
        oc = Counter(t["decode"]["outcome"]["outcome"] for t in ts)
        ex = oc.get("EXACT", 0)
        lo, hi = wilson(ex, n)
        first = Counter(t["diagnosis"]["first_failing_stage"] for t in ts if t["diagnosis"]["first_failing_stage"])

        def mean(path):
            vals = []
            for t in ts:
                v = t
                for p in path:
                    v = v[p]
                vals.append(v)
            return round(float(np.mean(vals)), 2)
        fun = {}
        for part in ("reads", "data_strands", "superblock_strands", "data_rows"):
            fun[part] = {k: mean(("diagnosis", "funnel", part, k)) for k in ts[0]["diagnosis"]["funnel"][part]}
        fun["superblock_unit"] = {"need": ts[0]["diagnosis"]["funnel"]["superblock_unit"]["need"],
                                  "have_symbols_mean": mean(("diagnosis", "funnel", "superblock_unit", "have_symbols"))}
        terminal = Counter((t["decode"]["stage_counters"] or {}).get("terminal_stage") for t in ts)
        stage_counts: dict = {"data": Counter(), "superblock": Counter()}
        addr = Counter()
        would = Counter()
        fate = Counter()
        for t in ts:
            for part in ("data", "superblock"):
                stage_counts[part].update(t["diagnosis"]["strand_stage_counts"][part])
            addr.update(t["diagnosis"]["address_reasons_reads"])
            would.update(t["diagnosis"]["data_rows_would_fail_at"])
            fate.update(t["diagnosis"]["pass1_read_fate"])
        out[cid] = {"trials": n, "outcomes": {o: oc.get(o, 0) for o in OUTCOMES},
                    "exact_wilson95": [round(lo, 4), round(hi, 4)],
                    "false_success": oc.get("FALSE_SUCCESS", 0),
                    "false_success_rule_of_three_upper": round(3 / n, 4),
                    "first_failing_stage": dict(sorted(first.items(), key=lambda kv: DECODE_STAGES.index(kv[0])
                                                       if kv[0] in DECODE_STAGES else 99)),
                    "decoder_terminal_stage": {str(k): v for k, v in terminal.items()},
                    "funnel_mean": fun,
                    "strand_stage_totals": {k: dict(v) for k, v in stage_counts.items()},
                    "address_reasons_reads_total": dict(addr), "data_rows_would_fail_at_total": dict(would),
                    "pass1_read_fate_total": dict(fate),
                    "determinism_1_vs_4_identical": sum(t["determinism_w4"]["identical"] for t in ts),
                    "replication_checked": sum(t["diagnosis"]["replication_ok"] is not None for t in ts),
                    "replication_ok": sum(bool(t["diagnosis"]["replication_ok"]) for t in ts),
                    "median_decode_seconds": float(np.median([t["decode"]["decode_seconds"] for t in ts]))}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--cells", help="comma-separated cell ids (default: all)")
    r.add_argument("--seeds", type=int, help="override the number of seeds (smoke runs only)")
    r.add_argument("--out", help="trials JSON-lines (default: <config dir>/trials.jsonl)")
    s = sub.add_parser("summarise")
    s.add_argument("--config", required=True)
    s.add_argument("--trials", help="default: <config dir>/trials.jsonl")
    s.add_argument("--out", help="default: <config dir>/summary.json")
    a = ap.parse_args(argv)
    cfg_path = Path(a.config).resolve()
    cfg = json.loads(cfg_path.read_text())
    exp_dir = cfg_path.parent
    if a.cmd == "summarise":
        tp = Path(a.trials) if a.trials else exp_dir / "trials.jsonl"
        lines = [json.loads(x) for x in open(tp)]
        trials = [x for x in lines if x.get("record") == "trial"]
        header = next(x for x in lines if x.get("record") == "header")
        summ = summarise(trials)
        base = cfg["baseline_reproduction"]
        repro = {c: summ[c]["outcomes"]["EXACT"] for c in base["cells"] if c in summ}
        doc = {"record": "summary", "experiment": cfg["experiment"], "classification": CLASSIFICATION,
               "statement": STATEMENT, "config_sha256": header["config_sha256"], "git": header["git"],
               "trials": len(trials),
               "false_success_total": sum(v["false_success"] for v in summ.values()),
               "crash_total": sum(v["outcomes"]["CRASH"] for v in summ.values()),
               "baseline_reproduction": {"expected_exact": base["expected_exact"], "observed_exact": repro,
                                         "reproduced": all(v == base["expected_exact"] for v in repro.values())
                                         and len(repro) == len(base["cells"])},
               "cells": summ}
        Path(a.out or exp_dir / "summary.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(json.dumps({k: doc[k] for k in ("trials", "false_success_total", "crash_total", "baseline_reproduction")}))
        return 2 if doc["false_success_total"] else 0
    cells = cfg["cells"] if not a.cells else [c for c in cfg["cells"] if c["id"] in a.cells.split(",")]
    out_path = Path(a.out) if a.out else exp_dir / "trials.jsonl"
    tmp = Path(tempfile.mkdtemp(prefix="vnx-adiag-setup-"))
    command = " ".join(sys.argv)
    t0 = time.perf_counter()
    try:
        info = p4.prepare(tmp, {"size": cfg["size"], "profile": cfg["profile"]}, cfg["data_seed"])
        jobs = []
        nseeds = a.seeds or cfg["seeds"]
        for cell in cells:
            model = p4.materialise_model(cell["model"], exp_dir / "models")
            for i in range(nseeds):
                seed = cfg["base_seed"] + i
                if not 82000 <= seed <= 82099:
                    raise SystemExit(f"seed {seed} outside the EXPERIMENTAL range 82000-82099 (protocol §7)")
                jobs.append({"cell": cell["id"], "model": model, "seed": seed, "strands": str(tmp / "strands.fasta"),
                             "container_sha256": info["container_sha256"], "profile": cfg["profile"],
                             "tmp": str(tmp), "command": command})
        header = {"record": "header", "classification": CLASSIFICATION, "statement": STATEMENT, "command": command,
                  "config": cfg, "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
                  "git": p4.chn.git_info(), "environment": environment(), "backends": p4.backends(), "setup": info,
                  "cells_run": [c["id"] for c in cells], "seeds_per_cell": nseeds, "jobs": a.jobs, "nice": os.nice(0)}
        with open(out_path, "w") as fh:
            print(json.dumps(header, sort_keys=True, default=str), file=fh, flush=True)
            with ProcessPoolExecutor(max_workers=a.jobs) as pool:
                for res in pool.map(run_trial, jobs):
                    print(json.dumps(res, sort_keys=True, default=str), file=fh, flush=True)
                    d = res["decode"]
                    print(f"{res['cell']} {res['seed']} {d['outcome']['outcome']} "
                          f"first={res['diagnosis']['first_failing_stage']} w4={res['determinism_w4']['identical']}",
                          flush=True)
            print(json.dumps({"record": "end", "wall_seconds": round(time.perf_counter() - t0, 1)}), file=fh, flush=True)
        trials = [json.loads(x) for x in open(out_path) if '"record": "trial"' in x]
        fs = sum(t["decode"]["outcome"]["outcome"] == "FALSE_SUCCESS" for t in trials)
        print(f"{len(trials)} trials, {fs} FALSE SUCCESS, {round(time.perf_counter() - t0, 1)} s -> {out_path}")
        return 2 if fs else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
