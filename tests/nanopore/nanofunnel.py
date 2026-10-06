"""Nanopore regression corpus: deterministic cases, per-original-strand loss funnel, ORACLE address test.

DIAGNOSTIC / SIMULATED. Test and experiment code only: nothing here is imported by the decoder, and the ORACLE hook
(``oracle_frames``) exists only in this module. It is never a decoding or acceptance result.

A *case* (``corpus/cases.json``) fixes everything a decode depends on: archive size, data seed and profile, the full
``ChannelConfig`` (its ``seed`` is the trial seed) and the decoder arm (``read_clustering`` and ``ClusterConfig``
overrides). :func:`run_case` rebuilds the archive and the read file from those seeds, regenerates the simulator's
per-read ground truth (source strand, reverse complement; the regenerated reads are checked base for base against the
file), decodes, and attributes every original strand to the first funnel stage that loses it:

    total → observed (≥ 1 read) → ≥ 2 reads → stored (≥ 2 reads in the unplaced store) → clustered (≥ 2 of its reads
    in one cluster dominated by it) → oriented (cluster orientation correct) → candidate (a consensus candidate exists:
    ≥ 2 reads inside the consensus band) → rs_recoverable (2e + f ≤ r on the candidate) → valid_frame (a verified frame
    with its address and payload) → archive (container SHA-256)

with a REASON for every lost strand. Ground truth is used only to attribute losses after the fact.

ORACLE address test (founder addendum, 2026-10-06): the unplaced reads are grouped by their TRUE source strand and
nothing else — no orientation (each read is oriented relative to the group's first read by the smaller banded edit
distance, as the decoder would), no offsets, boundaries or bases — and the unchanged consensus, inner RS + CRC and
fill-only merge run on those groups, then the outer code and the container SHA-256. A strand the normal decode lost is

* ADDRESS-CAUSED  when the oracle grouping recovers it and the normal home cluster verified a frame at another
                  address (the cluster-first path reads the address from the verified frame only);
* CLUSTERING-CAUSED when the oracle grouping recovers it otherwise (unassigned, split, merged, or a cluster whose
                  membership or orientation differs from the true strand's reads);
* STRUCTURAL      when the oracle fails and the pool holds fewer than 2 stored reads of it (no consensus possible);
* BOUNDARY-CAUSED when the oracle fails and the oracle consensus is wrong mainly by shifted runs (an indel placed at
                  the wrong position: most wrong decided bases equal the true base one position to the left or right);
* PAYLOAD-CAUSED  otherwise (oracle fails; errors/erasures beyond the inner code without a shift signature).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import numpy as np

import vnxdna.recovery.cluster.stage as stage_mod
from vnxdna.benchmark.outcome import classify_outcome, decode_claim
from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.dnaenc.layout import KIND_DATA, KIND_SUPER, PROFILES
from vnxdna.dnaenc.mapping import nt_to_bytes
from vnxdna.dnaenc.superblock import Superblock
from vnxdna.pipeline.decode import decode_reads
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster.consensus import cluster_consensus
from vnxdna.recovery.cluster.editdist import banded_distance, revcomp
from vnxdna.recovery.options import DecodeOptions
from vnxdna.simulation.channel import ChannelConfig, _strand_batches, simulate_batch, simulate_file
from vnxdna.native.reads import iter_reads
from vnxdna.sync.template import strip_markers_exact
from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus" / "cases.json"
SCHEMA = "vnx.nanopore-funnel/1"
LABEL = "DIAGNOSTIC / SIMULATED (oracle results are ORACLE / DIAGNOSTIC, never decoding or acceptance results)"
STAGES = ("observed", "two_reads", "stored", "clustered", "oriented", "candidate", "rs_recoverable", "valid_frame")
ORACLE_CLASSES = ("ADDRESS-CAUSED", "CLUSTERING-CAUSED", "BOUNDARY-CAUSED", "PAYLOAD-CAUSED", "STRUCTURAL")


def load_corpus(path: Path = CORPUS) -> list[dict]:
    doc = json.loads(Path(path).read_text())
    return doc["cases"]


def case_by_id(case_id: str, path: Path = CORPUS) -> dict:
    for c in load_corpus(path):
        if c["id"] == case_id:
            return c
    raise KeyError(case_id)


# ------------------------------------------------------------------------------------------------------ inputs
def build_archive(work: Path, size: int, data_seed: int, profile: str) -> dict:
    """Uncompressed archive of ``size`` random bytes (data seed), encoded with ``profile``; the strands' truth."""
    work.mkdir(parents=True, exist_ok=True)
    datagen.generate(work / "in.bin", size, "random", data_seed)
    ar.build_archive([work / "in.bin"], work / "a.vnx", ar.ArchiveOptions(compression="none"))
    en.encode_container(work / "a.vnx", work / "strands.fasta", en.DNAOptions(profile=profile))
    lay = PROFILES[profile][0]
    codes = np.concatenate([b for _, b in _strand_batches(work / "strands.fasta")])
    fb, _ = strip_markers_exact(lay, codes)
    P = decode_frames(lay, nt_to_bytes(fb), np.zeros((fb.shape[0], lay.frame_bytes), dtype=bool))
    assert P.ok.all(), "clean strands must parse"
    keys = [(int(k), int(t), int(g), int(s)) for k, t, g, s in zip(P.kind, P.tag, P.group, P.symbol)]
    return {"container_sha256": hashlib.sha256((work / "a.vnx").read_bytes()).hexdigest(), "strands": codes,
            "keys": keys, "frame_nt": fb, "payload": P.payload.copy(), "lay": lay, "M": PROFILES[profile][2],
            "strands_path": work / "strands.fasta"}


def simulate(strands_path: Path, out: Path, channel: dict) -> dict:
    """Write the read file and regenerate the per-read truth (refuses if it does not reproduce the file)."""
    cfg = ChannelConfig.from_dict(dict(channel))
    if cfg.shuffle_window:
        raise ValueError("the corpus needs shuffle_window = 0 (truth is regenerated in strand order)")
    stats = simulate_file(strands_path, out, cfg, fmt="fastq", workers=1, overwrite=True)
    src, rc, codes, lens = [], [], [], []
    off = 0
    for index, batch in _strand_batches(strands_path):
        res = simulate_batch(batch, cfg, index, truth=True)
        src.append(res["source"] + off)
        rc.append(res["reverse_complement"])
        codes.append(res["codes"])
        lens.append(res["lengths"])
        off += batch.shape[0]
    reads = []
    for b in iter_reads(out, 1 << 17):
        o = np.concatenate([[0], np.cumsum(b.lengths)])
        reads += [b.codes[o[k]:o[k + 1]].copy() for k in range(b.count)]
    sim_codes, sim_lens = np.concatenate(codes), np.concatenate(lens)
    if sim_lens.size != len(reads) or not np.array_equal(sim_lens, np.array([r.size for r in reads], dtype=np.int64)) \
            or not np.array_equal(sim_codes, np.concatenate(reads) if reads else sim_codes[:0]):
        raise RuntimeError("regenerated reads differ from the read file: ground truth unusable")
    return {"src": np.concatenate(src), "rc": np.concatenate(rc), "reads": reads, "stats": stats,
            "reads_sha256": hashlib.sha256(out.read_bytes()).hexdigest()}


# ------------------------------------------------------------------------------------------------------ capture
class Capture:
    """Observes the clustering stage (unplaced reads, clustering, consensus trace, frames); changes nothing."""

    def __init__(self):
        self.reads: list | None = None
        self.clustering = None
        self.trace: list = []
        self.frames: list = []


@contextmanager
def observe(cap: Capture, replace_frames=None):
    """Wrap the stage's module-level functions. ``replace_frames`` (ORACLE only) replaces ``cluster_frames``."""
    orig = (stage_mod.cluster_frames, stage_mod.cluster_reads, stage_mod.cluster_consensus)
    of, orr, oc = orig

    def frames(reads, *a, **k):
        cap.reads = [np.array(r) for r in reads]
        out = (replace_frames or of)(reads, *a, **k)
        cap.frames = list(out[0])
        return out

    def creads(*a, **k):
        cap.clustering = orr(*a, **k)
        return cap.clustering

    def ccons(*a, **k):
        return oc(*a, **{**k, "trace": cap.trace})
    stage_mod.cluster_frames, stage_mod.cluster_reads, stage_mod.cluster_consensus = frames, creads, ccons
    try:
        yield cap
    finally:
        stage_mod.cluster_frames, stage_mod.cluster_reads, stage_mod.cluster_consensus = orig


def decoder_options(case: dict) -> DecodeOptions:
    d = case.get("decoder", {})
    cfg = ClusterConfig(**d.get("cluster_config", {}))
    return DecodeOptions(stage_counters=True, workers=1, read_clustering=d.get("read_clustering", "fallback"),
                         cluster_config=cfg)


def run_decode(reads_path: Path, out: Path, sha: str, opts: DecodeOptions, replace_frames=None) -> tuple[dict, Capture]:
    cap = Capture()
    t = time.perf_counter()
    with observe(cap, replace_frames):
        claim, res, err = decode_claim(lambda: decode_reads(reads_path, out, opts), out)
    secs = time.perf_counter() - t
    oc = classify_outcome(claim, {"container": sha})
    rep = res.report if res is not None else dict(getattr(err, "details", {}) or {})
    if out.exists():
        out.unlink()
    cl = rep.get("clustering") or {}
    return ({"outcome": oc["outcome"], "reason": oc["reason"], "status": None if res is None else res.status,
             "error_code": getattr(err, "code", None), "groups_failed": rep.get("groups_failed"),
             "terminal_stage": (rep.get("stage_counters") or {}).get("terminal_stage"),
             "cluster_status": cl.get("status"), "cluster_trigger": cl.get("trigger"),
             "cluster_consensus": cl.get("consensus"), "cluster_fill": cl.get("fill"),
             "peak_rss_bytes": rep.get("peak_rss_bytes"), "decode_seconds": round(secs, 3)}, cap)


# ------------------------------------------------------------------------------------------------------ analysis
def map_stored(stored: list, reads: list) -> np.ndarray:
    """Read-file index of each stored read (the store keeps input order and the raw bases)."""
    out = np.full(len(stored), -1, dtype=np.int64)
    j = 0
    for i, r in enumerate(stored):
        while j < len(reads) and not (reads[j].size == r.size and np.array_equal(reads[j], r)):
            j += 1
        if j == len(reads):
            raise RuntimeError("stored read not found in the read file (store order assumption broken)")
        out[i] = j
        j += 1
    return out


def candidate_stats(lay, tr: dict, truth_nt: np.ndarray) -> dict:
    """e (wrong decided bytes), f (erased bytes), e_star (wrong bytes with erasures filled by the plurality base),
    wrong decided bases, and how many of them are a shift (equal the true base one position left or right)."""
    best_b = nt_to_bytes(np.minimum(tr["best"], 3)[None, :])[0]
    true_b = nt_to_bytes(truth_nt[None, :])[0]
    er = tr["erased"]
    wrong = best_b != true_b
    best, dec = np.minimum(tr["best"], 3), tr["decided"]
    wn = np.flatnonzero((best != truth_nt) & dec)
    left = np.concatenate([[9], truth_nt[:-1]])
    right = np.concatenate([truth_nt[1:], [9]])
    shifted = int(((best[wn] == left[wn]) | (best[wn] == right[wn])).sum())
    e, f = int((wrong & ~er).sum()), int(er.sum())
    return {"e": e, "f": f, "e_star": int(wrong.sum()), "wrong_nt": int(wn.size), "shifted_nt": shifted,
            "reads": int(tr["reads"]), "recoverable": bool(2 * e + f <= lay.inner_parity)}


def is_shift(cs: dict) -> bool:
    return cs["wrong_nt"] >= 3 and cs["shifted_nt"] * 4 >= cs["wrong_nt"] * 3


def verified_keys(frames: list, key_index: dict, payload: np.ndarray) -> tuple[set, int]:
    ok, false = set(), 0
    for fr in frames:
        k = (int(fr["kind"]), int(fr["tag"]), int(fr["group"]), int(fr["symbol"]))
        if k in key_index and np.array_equal(np.asarray(fr["payload"], dtype=np.uint8), payload[key_index[k]]):
            ok.add(k)
        else:
            false += 1
    return ok, false


def strand_funnel(arc: dict, sim: dict, cap: Capture, idx: np.ndarray) -> tuple[list, set, int]:
    """One record per original strand: counts, cluster, orientation, consensus statistics, first lost stage + reason."""
    lay = arc["lay"]
    keys = arc["keys"]
    key_index = {k: i for i, k in enumerate(keys)}
    n_str = len(keys)
    src, rc = sim["src"], sim["rc"]
    per_strand = np.bincount(src, minlength=n_str)
    verified, false = verified_keys(cap.frames, key_index, arc["payload"])
    s_src = src[idx] if idx.size else np.zeros(0, dtype=np.int64)
    cl = cap.clustering
    labels = cl.labels if cl is not None else np.full(idx.size, -1)
    corient = cl.orient if cl is not None else np.zeros(idx.size, dtype=np.uint8)
    comp: dict = {}
    for i, lab in enumerate(labels.tolist()):
        if lab >= 0:
            comp.setdefault(lab, Counter())[int(s_src[i])] += 1
    major = {c: cnt.most_common(1)[0][0] for c, cnt in comp.items()}
    trace_by: dict = {}
    for t in cap.trace:
        trace_by.setdefault(t["cluster"], []).append(t)
    frame_of = {}
    for fr in cap.frames:
        frame_of.setdefault(int(fr["cluster"]), []).append((int(fr["kind"]), int(fr["tag"]), int(fr["group"]),
                                                            int(fr["symbol"])))
    stored_of: list = [[] for _ in range(n_str)]
    for i, s in enumerate(s_src.tolist()):
        stored_of[s].append(i)
    # reads the 6.0 pass 1 verified (not stored although their length fits the store): their strand is recovered by
    # the 6.0 path (fill-only never touches it)
    if cap.reads is not None:
        T = lay.strand_nt
        in_store = np.zeros(len(sim["reads"]), dtype=bool)
        in_store[idx] = True
        fits = np.array([T // 2 <= r.size <= -(-5 * T // 4) for r in sim["reads"]], dtype=bool)
        for s in np.unique(src[~in_store & fits]).tolist():
            verified.add(keys[s])
    out = []
    for s in range(n_str):
        key = keys[s]
        rec = {"strand": s, "kind": "superblock" if key[0] == KIND_SUPER else "data", "group": key[2],
               "symbol": key[3], "reads": int(per_strand[s]), "stored": len(stored_of[s])}
        labs = [int(labels[i]) for i in stored_of[s] if labels[i] >= 0]
        rec["clustered_reads"] = len(labs)
        rec["clusters"] = len(set(labs))
        lost, reason = None, None
        if key in verified:
            lost = None
        elif per_strand[s] == 0:
            lost, reason = "observed", "no read in the pool"
        elif per_strand[s] == 1:
            lost, reason = "two_reads", "one read only (consensus needs 2)"
        elif len(stored_of[s]) < 2:
            lost, reason = "stored", "fewer than 2 reads stored (pass-1 verified elsewhere, too short or too long)"
        elif len(labs) < 2:
            lost, reason = "clustered", "unassigned: fewer than 2 reads in any cluster"
        else:
            home, nh = Counter(labs).most_common(1)[0]
            rec["home_cluster_reads"] = int(sum(comp[home].values()))
            rec["home_purity"] = round(comp[home][s] / sum(comp[home].values()), 4)
            if nh < 2:
                lost, reason = "clustered", "split: no cluster holds 2 of its reads"
            elif major[home] != s:
                lost, reason = "clustered", "merged: its home cluster is dominated by another strand"
            else:
                trs = trace_by.get(home, [])
                rec["address_assigned"] = [list(k) for k in frame_of.get(home, [])]
                if not trs:
                    lost, reason = "candidate", "no consensus candidate (fewer than 2 reads inside the consensus band)"
                else:
                    mem = [i for i in stored_of[s] if labels[i] == home]
                    want = Counter(int(rc[idx[i]]) ^ int(corient[i]) for i in mem).most_common(1)[0][0]
                    t0 = trs[0]
                    rec["orientation_ok"] = bool(t0["flip"] == want)
                    if not rec["orientation_ok"]:
                        lost, reason = "oriented", "cluster orientation chosen wrong"
                    else:
                        cs = candidate_stats(lay, t0, arc["frame_nt"][s])
                        rec["candidate"] = cs
                        if t0["ok"] and t0["fields"] != key:
                            lost, reason = "valid_frame", "home cluster verified another address (peel order)"
                        elif not cs["recoverable"]:
                            kind = "shifted segment (indel placed wrong)" if is_shift(cs) else "errors/erasures"
                            lost = "rs_recoverable"
                            reason = (f"{kind}: e={cs['e']} f={cs['f']} > r={lay.inner_parity} "
                                      f"({'erasures exceed parity' if cs['f'] > lay.inner_parity else '2e+f > r'})")
                        else:
                            lost, reason = "valid_frame", "2e+f <= r but no verified frame (CRC or decoder)"
        rec["lost_at"] = lost
        rec["reason"] = reason
        out.append(rec)
    return out, verified, false


# ------------------------------------------------------------------------------------------------------ ORACLE
def oracle_frames_factory(sim: dict, trace: list):
    """ORACLE / DIAGNOSTIC ONLY: a replacement for ``stage.cluster_frames`` that groups the unplaced reads by TRUE source
    strand. It exposes the strand identity only: orientation is decided as the decoder would (relative to the group's
    first read by the smaller banded edit distance; the consensus then picks the cluster orientation by markers)."""
    def oracle_frames(reads, lay, cfg, marker_mismatch=4, counts=None, cons=None, checkpoint=None, raw=None):
        cons = Counter() if cons is None else cons
        idx = map_stored(list(reads), sim["reads"])
        by: dict = {}
        for i, j in enumerate(idx.tolist()):
            by.setdefault(int(sim["src"][j]), []).append(np.asarray(reads[i]))
        clusters = []
        for s, rs in sorted(by.items()):
            if len(rs) < 2:
                continue
            ref = rs[0]
            fwd = banded_distance([ref] * (len(rs) - 1), rs[1:], cfg.band_slack)
            rev = banded_distance([ref] * (len(rs) - 1), [revcomp(r) for r in rs[1:]], cfg.band_slack)
            oriented = [ref] + [revcomp(r) if b < a else r for r, a, b in zip(rs[1:], fwd.tolist(), rev.tolist())]
            clusters.append((s, oriented[: cfg.max_cluster_reads]))
        frames: list = []
        for c0 in range(0, len(clusters), stage_mod.CONSENSUS_BATCH):
            frames += cluster_consensus(lay, clusters[c0:c0 + stage_mod.CONSENSUS_BATCH], cfg, marker_mismatch, cons,
                                        trace)
        return frames, None
    return oracle_frames


# ------------------------------------------------------------------------------------------------------ one case
def run_case(case: dict, work: Path, oracle: bool = True) -> dict:
    """Rebuild, simulate, decode, attribute; the machine-readable diagnostics document of the case."""
    work = Path(work)
    t0 = time.perf_counter()
    try:
        arc = build_archive(work / "archive", case["size"], case["data_seed"], case["profile"])
        sim = simulate(arc["strands_path"], work / "reads.fastq", case["channel"])
        opts = decoder_options(case)
        dec, cap = run_decode(work / "reads.fastq", work / "out.vnx", arc["container_sha256"], opts)
        idx = map_stored(cap.reads, sim["reads"]) if cap.reads else np.zeros(0, dtype=np.int64)
        strands, verified, false = strand_funnel(arc, sim, cap, idx)
        cap.src = sim["src"][idx] if idx.size else np.zeros(0, dtype=np.int64)
        doc = {"schema": SCHEMA, "label": LABEL, "case": case, "container_sha256": arc["container_sha256"],
               "reads_sha256": sim["reads_sha256"], "reads": len(sim["reads"]), "strands_total": len(arc["keys"]),
               "decode": dec, "cluster_stage_ran": cap.reads is not None}
        doc.update(summarise_strands(arc, strands, verified, false, cap))
        if oracle and cap.reads is not None:
            otrace: list = []
            odec, ocap = run_decode(work / "reads.fastq", work / "oracle.vnx", arc["container_sha256"], opts,
                                    replace_frames=oracle_frames_factory(sim, otrace))
            oidx = map_stored(ocap.reads, sim["reads"]) if ocap.reads else np.zeros(0, dtype=np.int64)
            doc["oracle"] = oracle_block(arc, sim, strands, ocap, otrace, odec, oidx)
            cls = dict(doc["oracle"].pop("per_strand"))
            for r in strands:
                if r["lost_at"] is not None:
                    r["oracle_class"] = cls[r["strand"]]
        doc["seconds"] = round(time.perf_counter() - t0, 2)
        return doc
    finally:
        shutil.rmtree(work / "archive", ignore_errors=True)
        for p in ("reads.fastq", "out.vnx", "oracle.vnx"):
            (work / p).unlink(missing_ok=True)


def summarise_strands(arc: dict, strands: list, verified: set, false: int, cap: Capture) -> dict:
    keys = arc["keys"]
    data = [r for r in strands if r["kind"] == "data"]
    funnel = {"total": len(data)}
    alive = len(data)
    for st in STAGES:
        alive -= sum(1 for r in data if r["lost_at"] == st)
        funnel[st] = alive
    reasons = Counter(f"{r['lost_at']}: {r['reason'].split(':')[0]}" for r in strands if r["lost_at"])
    n_sym = Counter(k[2] for k in keys if k[0] == KIND_DATA)
    have = Counter(k[2] for k in verified if k[0] == KIND_DATA)
    ks, _ = Superblock.symbols(arc["lay"].payload_bytes)
    rows_ok = sum(int(n - have.get(g, 0) <= arc["M"]) for g, n in n_sym.items())
    purity = None
    if cap.clustering is not None and cap.reads:
        labels = cap.clustering.labels
        comp: dict = {}
        for lab, st in zip(labels.tolist(), cap_src(cap).tolist()):
            if lab >= 0:
                comp.setdefault(lab, Counter())[st] += 1
        if comp:
            purity = {"clusters": len(comp),
                      "impure_clusters": sum(1 for c in comp.values() if len(c) > 1),
                      "read_purity": round(sum(c.most_common(1)[0][1] for c in comp.values())
                                           / sum(sum(c.values()) for c in comp.values()), 5),
                      "unassigned_reads": int((labels < 0).sum())}
    return {"clustering": purity, "funnel_data": funnel, "first_large_drop": first_drop(funnel),
            "loss_reasons": dict(sorted(reasons.items())),
            "frames": {"data_recovered": sum(1 for k in verified if k[0] == KIND_DATA), "data_total": len(data),
                       "superblock_recovered": sum(1 for k in verified if k[0] == KIND_SUPER),
                       "superblock_needed": ks, "false_frames": int(false)},
            "rows": {"total": len(n_sym), "decodable_from_cluster_frames": rows_ok},
            "strands": strands}


def cap_src(cap: Capture) -> np.ndarray:
    return getattr(cap, "src", np.zeros(0, dtype=np.int64))


def first_drop(funnel: dict) -> dict:
    prev, worst = funnel["total"], None
    for st in STAGES:
        d = prev - funnel[st]
        if worst is None or d > worst[1]:
            worst = (st, d)
        prev = funnel[st]
    return {"stage": worst[0], "strands_lost": worst[1]}


def oracle_block(arc: dict, sim: dict, strands: list, ocap: Capture, otrace: list, odec: dict, oidx: np.ndarray) -> dict:
    """ORACLE / DIAGNOSTIC: per failed strand of the normal decode, its class; oracle frame and archive recovery."""
    keys = arc["keys"]
    key_index = {k: i for i, k in enumerate(keys)}
    ok, false = verified_keys(ocap.frames, key_index, arc["payload"])
    tr0 = {t["cluster"]: t for t in otrace if t["peel"] == 0}
    classes: Counter = Counter()
    failed_reads: Counter = Counter()
    per = []
    for r in strands:
        if r["lost_at"] is None:
            continue
        s = r["strand"]
        k = keys[s]
        if k in ok:
            # the oracle grouping recovers it: the normal decode lost it to the grouping. ADDRESS-CAUSED when the
            # normal home cluster verified a frame at another address; CLUSTERING-CAUSED otherwise (unassigned, split,
            # merged, or a cluster whose membership/orientation differs from the true strand's reads)
            c = "ADDRESS-CAUSED" if r["reason"].startswith("home cluster verified another address") else "CLUSTERING-CAUSED"
        elif r["stored"] < 2:
            c = "STRUCTURAL"
        elif s in tr0:
            cs = candidate_stats(arc["lay"], tr0[s], arc["frame_nt"][s])
            c = "BOUNDARY-CAUSED" if is_shift(cs) else "PAYLOAD-CAUSED"
        else:
            c = "STRUCTURAL"            # no oracle consensus candidate (reads outside the band)
        classes[c] += 1
        failed_reads[c] += r["reads"]
        per.append([s, c])
    data_ok = sum(1 for k in ok if k[0] == KIND_DATA)
    n_sym = Counter(k[2] for k in keys if k[0] == KIND_DATA)
    have = Counter(k[2] for k in ok if k[0] == KIND_DATA)
    return {"label": "ORACLE / DIAGNOSTIC / SIMULATED: strand identity supplied; not a decoding result",
            "classes_failed_strands": {c: classes.get(c, 0) for c in ORACLE_CLASSES},
            "failed_reads_by_class": {c: failed_reads.get(c, 0) for c in ORACLE_CLASSES},
            "frames_data_recovered": data_ok, "frames_data_total": sum(n_sym.values()),
            "frames_superblock_recovered": sum(1 for k in ok if k[0] == KIND_SUPER), "false_frames": int(false),
            "rows_decodable": sum(int(n - have.get(g, 0) <= arc["M"]) for g, n in n_sym.items()),
            "archive_outcome": odec["outcome"], "archive_sha_match": odec["outcome"] == "EXACT",
            "per_strand": per}


def expected_of(doc: dict) -> dict:
    """The compact, deterministic part of a diagnostics document that ``expected/<id>.json`` pins."""
    out = {"schema": SCHEMA, "id": doc["case"]["id"], "reads_sha256": doc["reads_sha256"],
           "container_sha256": doc["container_sha256"], "outcome": doc["decode"]["outcome"],
           "funnel_data": doc["funnel_data"], "frames": doc["frames"], "rows": doc["rows"],
           "clustering": doc["clustering"],
           "loss_reasons": doc["loss_reasons"]}
    if "oracle" in doc:
        o = doc["oracle"]
        out["oracle"] = {k: o[k] for k in ("classes_failed_strands", "frames_data_recovered", "rows_decodable",
                                           "archive_outcome", "false_frames")}
    return out


def artifact_json(doc: dict) -> str:
    """The committed failure artifact: everything but the recovered strands; one lost strand per line."""
    head = {k: v for k, v in doc.items() if k != "strands"}
    lost = [r for r in doc["strands"] if r["lost_at"] is not None]
    body = json.dumps(head, indent=1, sort_keys=True, default=str)
    lines = ",\n  ".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in lost)
    return body[:-2] + ',\n "lost_strands": [\n  ' + lines + "\n ]\n}\n"
