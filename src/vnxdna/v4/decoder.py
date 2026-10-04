"""Reads → verified container (or verified files): the V4 reconstruction pipeline.

Pass 1 (streaming over read batches, parallel, bounded memory)::

    read validation (ACGTN, length window, quality → erasures)
      → fast path: exact-length reads, markers stripped, inner RS + CRC
      → sync path: marker-template alignment (indels → erasures), inner RS + CRC
      → reverse-complement retry for reads that fit neither orientation
      → accepted symbols ............................. spilled to bucket files by group
      → failed but aligned reads with a readable header → spilled as *pending* (for consensus)

Pass 2 (per bucket)::

    superblock (kind 1) → geometry, archive ID, container size/SHA-256
    duplicate resolution (strict majority of identical verified payloads)
    consensus of pending reads per address (soft vote → erasures) → inner RS + CRC
    outer decoding per group (Cauchy RS / LT fountain) → container bytes
    whole-container SHA-256 (superblock) + full structural validation → publish

Fail-closed rule: the output container is published only if its SHA-256
equals the one recorded in the superblock *and* it opens as a valid VNX4
container. If groups cannot be decoded, the status is PARTIAL: individual
files whose every chunk verifies can be extracted (``partial_dir``); nothing
unverified is ever written as a result.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .reads import iter_reads
from . import archive as ar
from . import container as ct
from .codecs import CauchyRSCodec, make_outer
from .encoder import SB_BYTES, Superblock, group_k
from .errors import VNXAddressError, VNXConfigurationError, VNXDecodeError, VNXFormatError, VNXIntegrityError
from .frame import HEADER_BYTES, KIND_DATA, KIND_SUPER, PROFILES, Layout, decode_frames, nt_to_bytes, tentative_address
from .sync import SyncCosts, TemplateAligner, frame_erasures_to_bytes, strip_markers_exact
from .util import atomic_output, peak_rss_bytes

_RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)


@dataclass
class DecodeOptions:
    profile: str | None = None          # None = detect from read lengths
    layout: Layout | None = None
    workers: int = 1
    batch_reads: int = 8192
    band: int = 6
    min_quality: int = 0                # bases below this Phred score become erasures (soft-information hook)
    reverse_complement: bool = True
    consensus_threshold: float = 0.6    # minimum posterior for a consensus base; below → erasure
    max_pending_per_address: int = 64
    archive_tag: int | None = None
    sync_costs: SyncCosts = field(default_factory=SyncCosts)
    max_reads: int = 2_000_000_000
    # V5 Phase 3: "segment" = V4 (an indel erases its whole segment); "smart" = bounded local indel recovery
    # (vnxdna.v5.indel) for reads the V4 path cannot decode, plus consensus realignment in pass 2. Opt-in.
    indel_recovery: str = "segment"
    indel_config: object = None         # vnxdna.v5.indel.recovery.IndelRecoveryConfig (None = defaults)
    # V5 Phase 4: "off" (default) | "erasure" (GMD) | "chase" | "auto" — bounded soft-information search for reads
    # every hard path failed, and soft consensus in pass 2. Opt-in; verification is unchanged.
    soft_decoding: str = "off"
    soft_config: object = None          # vnxdna.v5.soft.decoder.SoftDecodeConfig (None = defaults for the mode)
    # V5: when the per-read smart/soft recovery runs. "deferred" = after the cheap pass, only for reads that can still
    # contribute to a group that is not yet decodable (see _deferred_recovery); "eager" = inside pass 1 for every read
    # the V4 paths fail (the Phase 3/4 behaviour). Irrelevant unless smart indel recovery or soft decoding is on.
    recovery_schedule: str = "deferred"
    # V6: limits on expensive recovery (vnxdna.v6.recovery.RecoveryBudget; None = unlimited, the V5 behaviour). Every
    # escalation is decided and recorded by vnxdna.v6.recovery.RecoveryPlanner → report["recovery_plan"].
    recovery_budget: object = None

    def validate(self) -> None:
        if not 1 <= self.workers <= 256:
            raise VNXConfigurationError("workers must be in 1..256")
        if not 1 <= self.band <= 64:
            raise VNXConfigurationError("band must be in 1..64")
        if not 0.25 <= self.consensus_threshold <= 1.0:
            raise VNXConfigurationError("consensus_threshold must be in [0.25, 1]")
        if not 0 <= self.min_quality <= 93:
            raise VNXConfigurationError("min_quality must be in 0..93")
        if self.indel_recovery not in ("segment", "smart"):
            raise VNXConfigurationError("indel_recovery must be 'segment' (V4) or 'smart' (V5)")
        if self.indel_recovery == "smart":
            from ..v5.indel.recovery import IndelRecoveryConfig
            if self.indel_config is None:
                self.indel_config = IndelRecoveryConfig()
            elif not isinstance(self.indel_config, IndelRecoveryConfig):
                raise VNXConfigurationError("indel_config must be an IndelRecoveryConfig")
            self.indel_config.validate()
        if self.recovery_schedule not in ("deferred", "eager"):
            raise VNXConfigurationError("recovery_schedule must be 'deferred' or 'eager'")
        if self.soft_decoding not in ("off", "erasure", "chase", "auto"):
            raise VNXConfigurationError("soft_decoding must be off, erasure, chase or auto")
        if self.soft_decoding != "off":
            from dataclasses import replace
            from ..v5.soft.decoder import SoftDecodeConfig
            if self.soft_config is None:
                self.soft_config = SoftDecodeConfig(mode=self.soft_decoding)
            elif not isinstance(self.soft_config, SoftDecodeConfig):
                raise VNXConfigurationError("soft_config must be a SoftDecodeConfig")
            else:
                self.soft_config = replace(self.soft_config, mode=self.soft_decoding)
            self.soft_config.validate()
        from ..v6.recovery import RecoveryBudget
        if self.recovery_budget is None:
            self.recovery_budget = RecoveryBudget()
        elif not isinstance(self.recovery_budget, RecoveryBudget):
            raise VNXConfigurationError("recovery_budget must be a RecoveryBudget")
        self.recovery_budget.validate()


# ============================================================================ layout detection
def detect_layout(reads_path: str | os.PathLike, opt: DecodeOptions) -> Layout:
    if opt.layout is not None:
        return opt.layout.validate()
    if opt.profile is not None:
        if opt.profile not in PROFILES:
            raise VNXConfigurationError(f"unknown profile {opt.profile!r}")
        return PROFILES[opt.profile][0]
    counts: Counter = Counter()
    sample: list[np.ndarray] = []
    seen = 0
    for batch in iter_reads(reads_path, 4096):
        counts.update(batch.lengths.tolist())
        offs = np.concatenate([[0], np.cumsum(batch.lengths)])
        sample.extend(batch.codes[offs[i]:offs[i + 1]] for i in range(batch.count))
        seen += batch.count
        if seen >= 20000:
            break
    if not counts:
        raise VNXFormatError("no reads found", stage="input")
    # candidate layouts: distinct profile layouts whose strand length is near many reads; several layouts can share a
    # length, so each candidate is scored by how many sampled exact-length reads pass its frame check (both orientations)
    candidates = []
    for name, (lay, _, _) in PROFILES.items():
        near = sum(v for length, v in counts.items() if abs(length - lay.strand_nt) <= opt.band)
        if near >= max(1, seen // 10) and lay not in [c[1] for c in candidates]:
            candidates.append((name, lay))
    if not candidates:
        raise VNXFormatError(f"cannot detect the strand layout (modal read length {counts.most_common(1)[0][0]}); pass --profile",
                             stage="layout")
    if len(candidates) == 1:
        return candidates[0][1]
    from .sync import strip_markers_exact
    best = None
    for name, lay in candidates:
        exact = [r for r in sample if r.size == lay.strand_nt][:4000]
        score = 0
        if exact:
            mat = np.stack(exact)
            for m in (mat, _RC[mat[:, ::-1]]):
                fb, _ = strip_markers_exact(lay, m)
                score += int(decode_frames(lay, nt_to_bytes(np.minimum(fb, 3)), errors_only_retry=False).ok.sum())
        if best is None or score > best[0]:
            best = (score, name, lay)
    if best[0] == 0:
        raise VNXFormatError("several layouts match the read lengths and none verifies on a sample; pass --profile", stage="layout")
    return best[2]


# ============================================================================ pass 1 worker
_P: dict = {}


def _p_init(layout: Layout, band: int, costs: SyncCosts, min_q: int, rc: bool, smart_cfg=None, soft_cfg=None,
            defer: bool = False) -> None:
    # defer: pass 1 of the deferred schedule — the cheap V4 paths only; smart/soft run later on the pending reads
    _P.update(lay=layout, al=TemplateAligner(layout, band, costs), min_q=min_q, rc=rc, smart=smart_cfg, soft=soft_cfg,
              defer=defer)
    if smart_cfg is not None or soft_cfg is not None:
        from ..v5.indel.recovery import Geometry
        _P["geom"] = Geometry(layout)


def _try(reads: list[np.ndarray], quals: list | None) -> tuple:
    lay: Layout = _P["lay"]
    n = len(reads)
    acc = np.zeros(n, dtype=bool)
    proj_bases = np.full((n, lay.frame_nt), 4, dtype=np.uint8)
    hard_bases = np.zeros((n, lay.frame_nt), dtype=np.uint8)
    cost = np.full(n, 1 << 28, dtype=np.int64)
    parsed_fields = np.zeros((n, 4), dtype=np.int64)
    payload = np.zeros((n, lay.payload_bytes), dtype=np.uint8)
    path = np.zeros(n, dtype=np.int8)          # 1 fast, 2 sync
    lengths = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    exact = np.flatnonzero(lengths == lay.strand_nt)
    if exact.size:
        mat = np.stack([reads[i] for i in exact])
        fb, mism = strip_markers_exact(lay, mat)
        er = fb > 3
        if quals is not None and _P["min_q"]:
            qm = np.stack([quals[i] for i in exact])
            tpl, pos = lay.template()
            er |= qm[:, pos] < _P["min_q"]
        frames = nt_to_bytes(np.minimum(fb, 3))
        P = decode_frames(lay, frames, frame_erasures_to_bytes(er))
        ok = P.ok
        idx = exact[ok]
        acc[idx] = True
        path[idx] = 1
        parsed_fields[idx] = np.stack([P.kind, P.tag, P.group, P.symbol], axis=1)[ok]
        payload[idx] = P.payload[ok]
        cost[exact] = mism * _P["al"].costs.marker_mismatch
        hard_bases[exact] = np.minimum(fb, 3)
        pb = fb.copy()
        pb[er] = 4
        proj_bases[exact] = pb
    # exact-length reads whose markers all match gain nothing from re-alignment (same frame, same RS failure)
    hopeless = np.zeros(n, dtype=bool)
    if exact.size and lay.markers:
        hopeless[exact[mism == 0]] = True
    rest = np.flatnonzero(~acc & ~hopeless)
    defer = _P.get("defer", False)
    smart = None if defer else _P.get("smart")
    soft = None if defer else _P.get("soft")
    sstats: Counter = Counter()
    soft_jobs: list = []                   # (read index, evidence) for the V5 Phase 4 soft stage
    if rest.size:
        rreads = [reads[i] for i in rest]
        rquals = None if quals is None else [quals[i] for i in rest]
        if smart is None and soft is None:
            pr = _P["al"].project(rreads, rquals, _P["min_q"])
        else:
            from ..v5.indel.path import align_with_path
            pr, rpos = align_with_path(_P["al"], rreads, rquals, _P["min_q"])   # identical projection + the path
        frames = nt_to_bytes(np.minimum(pr.bases, 3))
        er = frame_erasures_to_bytes(pr.erased)
        P = decode_frames(lay, frames, er, errors_only_retry=False)   # sync erasures come from detected indels
        ok = P.ok & pr.ok
        idx = rest[ok]
        acc[idx] = True
        path[idx] = 2
        parsed_fields[idx] = np.stack([P.kind, P.tag, P.group, P.symbol], axis=1)[ok]
        payload[idx] = P.payload[ok]
        cand_all = np.flatnonzero(~ok & pr.ok)
        if smart is not None:
            # V5 smart indel recovery, only for aligned reads the V4 erasure rule could not decode
            from ..v5.indel import recovery as rv
            for c0 in range(0, cand_all.size, rv.PLAN_CHUNK):     # bounded memory: plans for ≤ PLAN_CHUNK reads at a time
                cand = cand_all[c0:c0 + rv.PLAN_CHUNK]
                plans = rv.plan_reads(_P["geom"], rreads, rquals, pr, rpos, cand, smart)
                outs = rv.recover_batch(_P["geom"], plans, smart)
                sstats.update(rv.summarize_outcomes(plans, outs, pr.erased[cand]))
                for j, o, pl in zip(cand.tolist(), outs, plans):
                    if o.accepted:
                        i = rest[j]
                        acc[i] = True
                        path[i] = 3
                        parsed_fields[i] = o.fields
                        payload[i] = o.payload
                    elif soft is not None:
                        soft_jobs.append((int(rest[j]), _soft_evidence(rreads[j], None if rquals is None else rquals[j],
                                                                       pr.bases[j], pr.erased[j], rpos[j], pl)))
        elif soft is not None:
            for j in cand_all.tolist():
                soft_jobs.append((int(rest[j]), _soft_evidence(rreads[j], None if rquals is None else rquals[j], pr.bases[j],
                                                               pr.erased[j], rpos[j], None)))
        cost[rest] = np.where(pr.ok, pr.cost, 1 << 28)
        hard_bases[rest] = np.minimum(pr.bases, 3)
        pb = pr.bases.copy()
        pb[pr.erased] = 4
        proj_bases[rest] = pb
    if soft is not None:
        # exact-length reads whose markers all match skipped re-alignment: their frame is the read itself
        T = lay.strand_nt
        ident = np.arange(T, dtype=np.int16)
        for i in np.flatnonzero(hopeless & ~acc).tolist():
            fb = reads[i][_P["geom"].frame_pos]
            soft_jobs.append((i, _soft_evidence(reads[i], None if quals is None else quals[i], np.minimum(fb, 4),
                                                fb > 3, ident, None)))
        if soft_jobs:
            from ..v5.soft import decoder as sd
            from ..v5.soft import symbols as ss
            post = []
            for _, ev in soft_jobs:
                try:
                    post.append(ss.normalise(ev))
                except ss.SoftInputError:
                    post.append(np.full((lay.frame_nt, 4), np.nan))    # validated (and rejected) by soft_decode
            for c0 in range(0, len(soft_jobs), 512):
                outs = sd.soft_decode(lay, post[c0:c0 + 512], soft)
                sstats.update({f"soft_{k}": v for k, v in sd.summarize(outs).items() if v is not None})
                for (i, _), o in zip(soft_jobs[c0:c0 + 512], outs):
                    if o.accepted and not acc[i]:
                        acc[i] = True
                        path[i] = 4
                        parsed_fields[i] = o.fields
                        payload[i] = o.payload
    return acc, parsed_fields, payload, proj_bases, cost, path, hard_bases, sstats


def _soft_evidence(read, qual, proj_bases, proj_erased, rpos, plan):
    from ..v5.soft.frames import read_evidence
    return read_evidence(_P["geom"], np.asarray(read), None if qual is None else np.asarray(qual), proj_bases, proj_erased,
                         rpos, plan, _P["soft"].default_error)


def _orientation(codes: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """True where the reverse complement agrees better with the marker template (layouts with markers only).

    Vectorised over the flat batch: marker positions are gathered from each read's start (forward) and from its end
    (reverse complement) without per-read Python work.
    """
    lay: Layout = _P["lay"]
    n = lengths.size
    tpl, _ = lay.template()
    mpos = np.flatnonzero(tpl >= 0)
    if n == 0 or mpos.size == 0:
        return np.zeros(n, dtype=bool)
    offs = np.concatenate([[0], np.cumsum(lengths)])[:-1]
    want = tpl[mpos].astype(np.uint8)
    inside = mpos[None, :] < lengths[:, None]
    fidx = np.minimum(offs[:, None] + mpos[None, :], max(0, codes.size - 1))
    sf = ((codes[fidx] == want[None, :]) & inside).sum(axis=1)
    ridx = np.clip(offs[:, None] + lengths[:, None] - 1 - mpos[None, :], 0, max(0, codes.size - 1))
    sr = ((_RC[codes[ridx]] == want[None, :]) & inside).sum(axis=1)
    return sr > sf + max(2, mpos.size // 8)


def _process(batch_codes: np.ndarray, lengths: np.ndarray, quals: np.ndarray | None) -> dict:
    cpu0 = time.process_time()
    lay: Layout = _P["lay"]
    offs = np.concatenate([[0], np.cumsum(lengths)])
    reads = [batch_codes[offs[i]:offs[i + 1]] for i in range(lengths.size)]
    ql = None if quals is None else [quals[offs[i]:offs[i + 1]] for i in range(lengths.size)]
    rc_used = np.zeros(lengths.size, dtype=bool)
    if _P["rc"]:
        # orientation pre-pass: marker agreement at the expected (unshifted) positions, forward vs reverse complement
        flip = _orientation(batch_codes, lengths)
        for i in np.flatnonzero(flip).tolist():
            reads[i] = _RC[reads[i][::-1]]
            if ql is not None:
                ql[i] = ql[i][::-1]
        rc_used |= flip
    acc, fields, payload, proj, cost, path, hard, sstats = _try(reads, ql)
    oriented = reads                       # the orientation each read's projection refers to (pending raw reads)
    oriented_q = ql
    if _P["rc"]:
        bad = np.flatnonzero(~acc & (cost > 2 * _P["al"].costs.insertion))
        if bad.size:
            rreads = [_RC[reads[i][::-1]] for i in bad]
            rq = None if ql is None else [ql[i][::-1] for i in bad]
            a2, f2, p2, pr2, c2, path2, h2, s2 = _try(rreads, rq)
            sstats.update(s2)
            better = a2 | (c2 < cost[bad])
            sel = bad[better]
            acc[sel], fields[sel], payload[sel], proj[sel], cost[sel], path[sel], hard[sel] = (
                a2[better], f2[better], p2[better], pr2[better], c2[better], path2[better], h2[better])
            rc_used[sel] = ~rc_used[sel]
            if _P.get("smart") is not None or _P.get("soft") is not None:
                oriented = list(reads)
                oriented_q = None if ql is None else list(ql)
                for j in np.flatnonzero(better).tolist():
                    oriented[bad[j]] = rreads[j]
                    if oriented_q is not None:
                        oriented_q[bad[j]] = rq[j]
    # pending: every failed but aligned read. Its tentative address comes from the hard header bases (even where the
    # header was erased by an indel); pass 2 snaps addresses that are not expected to the nearest missing address.
    pend = np.flatnonzero(~acc & (cost < (1 << 28)))
    pend_fields = np.zeros((0, 4), dtype=np.int64)
    pend_bases = np.zeros((0, lay.frame_nt), dtype=np.uint8)
    orphans = 0
    pend_alt = np.zeros((0, 4), dtype=np.int64)
    good = np.zeros(pend.size, dtype=bool)
    if pend.size:
        header_erased = (proj[pend][:, : 4 * HEADER_BYTES] > 3).any(axis=1)
        orphans = int(header_erased.sum())            # header not trusted (kept, addressed by hard bases)
        ta = tentative_address(lay, nt_to_bytes(hard[pend]))
        # second reading of the header: the raw read prefix without any indel correction. The DP puts an indel at the
        # left edge of its segment, so where the projected header is shifted, the unshifted prefix is right.
        _, fpos = lay.template()
        hpos = fpos[: 4 * HEADER_BYTES]
        raw = np.zeros((pend.size, lay.frame_nt), dtype=np.uint8)
        for j, i in enumerate(pend.tolist()):
            r = reads[i]
            take = hpos[hpos < r.size]
            raw[j, : take.size] = np.minimum(r[take], 3)
        alt = tentative_address(lay, nt_to_bytes(raw))
        good = (ta[:, 0] >= 0) | (alt[:, 0] >= 0)
        pend_fields = np.where((ta[:, 0] >= 0)[:, None], ta, alt)[good]
        pend_alt = np.where((alt[:, 0] >= 0)[:, None], alt, ta)[good]
        pend_bases = proj[pend][good]
    out = {"acc_fields": fields[acc], "acc_payload": payload[acc], "acc_index": np.flatnonzero(acc),
           "pend_fields": pend_fields, "pend_bases": pend_bases, "pend_alt": pend_alt,
           "stats": {"reads": int(lengths.size), "fast": int((path == 1).sum()), "sync": int((path == 2).sum()),
                     "reverse_complement": int((rc_used & acc).sum()), "pending": int(pend_fields.shape[0]), "orphans": orphans,
                     "unaligned": int((~acc).sum()) - int(pend.size)}}
    if _P.get("smart") is not None or _P.get("soft") is not None:
        if _P.get("smart") is not None:
            out["stats"]["smart"] = int((path == 3).sum())
        if _P.get("soft") is not None:
            out["stats"]["soft"] = int((path == 4).sum())
        out["indel"] = dict(sstats)
        # raw (oriented) reads of pending records, for consensus realignment in pass 2 (and the deferred stage)
        width = lay.strand_nt + _P["al"].band

        def pack(sel):
            raw = np.zeros((sel.size, width), dtype=np.uint8)
            rawq = np.zeros((sel.size, width), dtype=np.uint8)
            rawlen = np.zeros(sel.size, dtype=np.int64)
            hasq = np.zeros(sel.size, dtype=np.uint8)
            for j, i in enumerate(sel.tolist()):
                r = oriented[i][:width]
                raw[j, : r.size] = r
                rawlen[j] = r.size
                if oriented_q is not None and oriented_q[i] is not None:
                    rawq[j, : r.size] = oriented_q[i][:width]
                    hasq[j] = 1
            return raw, rawq, rawlen, hasq

        out["pend_raw"], out["pend_rawq"], out["pend_rawlen"], out["pend_hasq"] = pack(pend[good])
        if _P.get("defer"):
            # aligned reads without a readable header: the eager schedule tried them in pass 1, so the deferred stage
            # keeps them (address unknown) for its last round
            out["orph_raw"], out["orph_rawq"], out["orph_rawlen"], out["orph_hasq"] = pack(pend[~good])
    out["cpu_seconds"] = time.process_time() - cpu0          # observability only (never in the report)
    return out


# ============================================================================ spill
class Spill:
    """Fixed-width records in bucket files (bucket = group mod B); memory stays bounded by one bucket."""

    def __init__(self, workdir: Path, buckets: int, payload: int, frame_nt: int, raw_nt: int = 0):
        self.dir = workdir
        self.B = buckets
        self.acc_dtype = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("payload", "u1", (payload,))])
        pend = [("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("alt", ">i8", (4,)), ("bases", "u1", (frame_nt,))]
        if raw_nt:     # V5 smart indel recovery keeps the raw read for consensus realignment
            pend += [("raw", "u1", (raw_nt,)), ("rawq", "u1", (raw_nt,)), ("rawlen", ">u2"), ("hasq", "u1")]
        self.pend_dtype = np.dtype(pend)
        self.acc_files = [open(workdir / f"acc{b}.bin", "wb") for b in range(buckets)]
        self.pend_files = [open(workdir / f"pend{b}.bin", "wb") for b in range(buckets)]
        self.orph_file = open(workdir / "orph.bin", "wb") if raw_nt else None

    def write(self, res: dict) -> None:
        if self.orph_file is not None and "orph_raw" in res and len(res["orph_raw"]):
            rec = np.zeros(len(res["orph_raw"]), dtype=self.pend_dtype)     # address fields stay 0: unknown
            rec["raw"], rec["rawq"], rec["rawlen"], rec["hasq"] = (res["orph_raw"], res["orph_rawq"], res["orph_rawlen"],
                                                                   res["orph_hasq"])
            self.orph_file.write(rec.tobytes())
        for fields, data, files, dtype, name in ((res["acc_fields"], res["acc_payload"], self.acc_files, self.acc_dtype, "payload"),
                                                  (res["pend_fields"], res["pend_bases"], self.pend_files, self.pend_dtype, "bases")):
            if not len(fields):
                continue
            rec = np.zeros(len(fields), dtype=dtype)
            rec["kind"], rec["tag"], rec["group"], rec["symbol"] = fields[:, 0], fields[:, 1], fields[:, 2], fields[:, 3]
            rec[name] = data
            if name == "bases":
                rec["alt"] = res["pend_alt"]
                if "raw" in dtype.names:
                    rec["raw"], rec["rawq"], rec["rawlen"], rec["hasq"] = (res["pend_raw"], res["pend_rawq"], res["pend_rawlen"],
                                                                           res["pend_hasq"])
            b = fields[:, 2] % self.B
            for k in np.unique(b):
                files[int(k)].write(rec[b == k].tobytes())

    def close(self) -> None:
        for f in self.acc_files + self.pend_files + ([self.orph_file] if self.orph_file is not None else []):
            f.close()

    def load(self, b: int) -> tuple[np.ndarray, np.ndarray]:
        return (np.fromfile(self.dir / f"acc{b}.bin", dtype=self.acc_dtype), np.fromfile(self.dir / f"pend{b}.bin", dtype=self.pend_dtype))

    def load_orphans(self) -> np.ndarray:
        """Read-only memory map of the unaddressed reads (pages are read on demand, so memory stays bounded)."""
        path = self.dir / "orph.bin"
        if not path.exists() or path.stat().st_size < self.pend_dtype.itemsize:
            return np.zeros(0, dtype=self.pend_dtype)
        return np.memmap(path, dtype=self.pend_dtype, mode="r")

    # after close(): the deferred recovery stage adds verified frames and removes the pending records they came from
    def append_acc(self, fields: np.ndarray, payload: np.ndarray) -> None:
        """Verified frames go to the bucket of their *verified* group (never the read's tentative one)."""
        if not len(fields):
            return
        rec = np.zeros(len(fields), dtype=self.acc_dtype)
        rec["kind"], rec["tag"], rec["group"], rec["symbol"] = fields[:, 0], fields[:, 1], fields[:, 2], fields[:, 3]
        rec["payload"] = payload
        b = fields[:, 2] % self.B
        for k in np.unique(b):
            with open(self.dir / f"acc{int(k)}.bin", "ab") as f:
                f.write(rec[b == k].tobytes())

    def rewrite_pend(self, b: int, keep: np.ndarray) -> None:
        pend = np.fromfile(self.dir / f"pend{b}.bin", dtype=self.pend_dtype)
        pend[keep].tofile(self.dir / f"pend{b}.bin")


# ============================================================================ consensus
def consensus_soft(bases: np.ndarray) -> np.ndarray:
    """(m, L) codes with 4 = erased → (L, 4) posterior base probabilities (add-½ smoothing).

    This is the soft-information interface: a future soft-decision inner decoder can
    consume these probabilities directly; the current decoder thresholds them into
    hard bases plus erasures.
    """
    onehot = np.zeros((bases.shape[1], 4), dtype=np.float64)
    for b in range(4):
        onehot[:, b] = (bases == b).sum(axis=0)
    return (onehot + 0.5) / (onehot.sum(axis=1, keepdims=True) + 2.0)


def consensus_hard(bases: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray]:
    votes = np.zeros((bases.shape[1], 4), dtype=np.int64)
    for b in range(4):
        votes[:, b] = (bases == b).sum(axis=0)
    total = votes.sum(axis=1)
    best = votes.argmax(axis=1).astype(np.uint8)
    share = np.where(total > 0, votes.max(axis=1) / np.maximum(total, 1), 0.0)
    erased = (total == 0) | (share < threshold)
    return best, erased


def resolve_duplicates(acc: np.ndarray) -> tuple[dict, int]:
    """(kind, tag, group, symbol) → payload by strict majority of identical verified copies; ties are dropped."""
    out: dict = {}
    conflicts = 0
    if not acc.size:
        return out, 0
    order = np.lexsort((acc["symbol"], acc["group"], acc["tag"], acc["kind"]))
    acc = acc[order]
    keys = np.stack([acc["kind"].astype(np.int64), acc["tag"].astype(np.int64), acc["group"].astype(np.int64),
                     acc["symbol"].astype(np.int64)], axis=1)
    change = np.flatnonzero((np.diff(keys, axis=0) != 0).any(axis=1)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(acc)]])
    pay = acc["payload"]
    first = np.repeat(starts, ends - starts)
    same = (pay == pay[first]).all(axis=1)
    uniform = np.logical_and.reduceat(same, starts)
    klist = keys[starts].tolist()
    for gi in np.flatnonzero(uniform).tolist():
        out[tuple(klist[gi])] = pay[starts[gi]]
    for gi in np.flatnonzero(~uniform).tolist():
        s, e = int(starts[gi]), int(ends[gi])
        key = tuple(klist[gi])
        c = Counter(acc["payload"][i].tobytes() for i in range(s, e))
        (top, n1), *rest = c.most_common(2) + [(None, 0)]
        n2 = rest[0][1] if rest else 0
        if len(c) > 1:
            conflicts += 1
        if n1 > n2:
            out[key] = np.frombuffer(top, dtype=np.uint8)
    return out, conflicts


# ============================================================================ main entry
def _bucket_count(est_reads: int) -> int:
    """About 200 000 reads per spill bucket, so pass-2 memory is bounded by one bucket. V4/V5 capped this at 256
    buckets (unbounded bucket size beyond ~51 M reads); the cap now follows the open-file limit (two files per bucket
    stay open in pass 1), up to 4096. Below 51 M reads the count is unchanged."""
    want = max(1, est_reads // 200_000)
    try:
        import resource
        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        cap = 4096 if soft == resource.RLIM_INFINITY else max(256, min(4096, (soft - 128) // 2))
    except (ImportError, OSError, ValueError):
        cap = 256
    return int(min(cap, want))



@dataclass
class DecodeResult:
    status: str                    # SUCCESS | PARTIAL | FAILURE
    report: dict
    output: str | None = None


def decode_reads(reads_path: str | os.PathLike, output: str | os.PathLike | None, options: DecodeOptions | None = None, *,
                 overwrite: bool = False, partial_dir: str | os.PathLike | None = None, select: list[str] | None = None,
                 select_dir: str | os.PathLike | None = None, key: bytes | None = None, passphrase: str | None = None,
                 progress=None, workdir: str | os.PathLike | None = None, observer=None,
                 task_id: str | None = None) -> DecodeResult:
    """``observer``: optional callable receiving structured events (vnxdna.v6.observe); observability only."""
    opt = options or DecodeOptions()
    opt.validate()
    t0 = time.perf_counter()
    from ..v6.observe import Events
    from ..v6.recovery import RecoveryPlanner
    ev = Events(observer, task_id, t0)
    try:
        res = _decode_reads(reads_path, output, opt, t0, ev, RecoveryPlanner(opt.recovery_budget, t0), overwrite=overwrite,
                            partial_dir=partial_dir, select=select, select_dir=select_dir, key=key, passphrase=passphrase,
                            progress=progress, workdir=workdir)
    except Exception as error:
        ev.emit("error", getattr(error, "stage", "unknown"), error_class=type(error).__name__, message=str(error)[:500])
        raise
    rep_ = res.report
    ev.emit("decode_end", "output", status=res.status, seconds=round(rep_.get("seconds", 0.0), 4),
            groups_decoded=rep_.get("groups_decoded"), groups_failed=rep_.get("groups_failed"),
            peak_rss_bytes=rep_.get("peak_rss_bytes"))
    return res


def _decode_reads(reads_path, output, opt: DecodeOptions, t0: float, ev, planner, *, overwrite, partial_dir, select,
                  select_dir, key, passphrase, progress, workdir) -> DecodeResult:
    stage: dict = {}
    reads_path = Path(reads_path)
    lay = detect_layout(reads_path, opt)
    if ev:
        try:
            size = reads_path.stat().st_size
        except OSError:
            size = None
        ev.emit("decode_start", "input", reads_bytes=size, workers=opt.workers, batch_reads=opt.batch_reads,
                strand_nt=lay.strand_nt, indel_recovery=opt.indel_recovery, soft_decoding=opt.soft_decoding,
                recovery_budget=opt.recovery_budget.to_dict())
    try:
        est_reads = max(1, reads_path.stat().st_size // (lay.strand_nt + 20))
    except OSError as error:
        raise VNXFormatError(f"cannot read {reads_path}: {error.strerror or error}") from None
    buckets = _bucket_count(est_reads)
    tmp_root = tempfile.mkdtemp(prefix="vnx4-decode-", dir=workdir)
    try:
        smart = opt.indel_recovery == "smart"
        softm = opt.soft_decoding != "off"
        spill = Spill(Path(tmp_root), buckets, lay.payload_bytes, lay.frame_nt,
                      lay.strand_nt + opt.band if (smart or softm) else 0)
        stats = Counter()
        indel_stats: Counter = Counter()
        t1 = time.perf_counter()
        deferred = (smart or softm) and opt.recovery_schedule == "deferred"
        initargs = (lay, opt.band, opt.sync_costs, opt.min_quality, opt.reverse_complement, opt.indel_config if smart else None,
                    opt.soft_config if softm else None, deferred)

        cpu = {"seconds": 0.0, "batches": 0}

        def take(res, depth=0):
            planner.checkpoint("pass 1")
            spill.write(res)
            stats.update(res["stats"])
            indel_stats.update(res.get("indel", {}))
            cpu["seconds"] += res.get("cpu_seconds", 0.0)
            cpu["batches"] += 1
            if progress:
                progress({"stage": "pass1", "reads": stats["reads"], "elapsed": time.perf_counter() - t0})
            if ev and (cpu["batches"] % 16 == 1):
                wall = max(1e-9, time.perf_counter() - t1)
                ev.emit("pass1_progress", "pass1", reads_processed=stats["reads"],
                        reads_accepted=stats["fast"] + stats["sync"], reads_pending=stats["pending"],
                        reads_rejected=stats["unaligned"], batches=cpu["batches"], queue_depth=depth,
                        worker_cpu_seconds=round(cpu["seconds"], 3),
                        worker_utilisation=round(cpu["seconds"] / (wall * opt.workers), 4))

        def batches():
            n = 0
            for batch in iter_reads(reads_path, opt.batch_reads, max_reads=opt.max_reads):
                n += batch.count
                q = batch.quals if (opt.min_quality or smart or softm) else None
                yield batch.codes, batch.lengths, q

        if opt.workers == 1:
            _p_init(*initargs)
            for b in batches():
                take(_process(*b))
        else:
            with ProcessPoolExecutor(max_workers=opt.workers, initializer=_p_init, initargs=initargs) as pool:
                window: deque = deque()
                for b in batches():
                    window.append(pool.submit(_process, *b))
                    if len(window) >= 2 * opt.workers:
                        take(window.popleft().result(), len(window))
                while window:
                    take(window.popleft().result(), len(window))
        spill.close()
        stage["pass1_reads"] = time.perf_counter() - t1
        stage["pass1_worker_cpu"] = cpu["seconds"]
        if stats["reads"] == 0:
            raise VNXFormatError("the read file contains no reads", stage="input")
        ev.emit("pass1_end", "pass1", reads_processed=stats["reads"], fast=stats["fast"], sync=stats["sync"],
                reverse_complement=stats["reverse_complement"], reads_pending=stats["pending"],
                reads_rejected=stats["unaligned"], orphans=stats["orphans"], seconds=round(stage["pass1_reads"], 4),
                worker_cpu_seconds=round(cpu["seconds"], 3),
                worker_utilisation=round(cpu["seconds"] / max(1e-9, stage["pass1_reads"] * opt.workers), 4))
        _plan_pass1(planner, opt, stats, deferred)
        recover_groups = None
        if deferred and select:
            # random access: smart/soft recovery only for the index groups, then for the stripes of the selected files
            ra_state: dict = {}

            def recover_groups(groups: set) -> dict:
                tr = time.perf_counter()
                info = _deferred_recovery(spill, lay, opt, stats, indel_stats, initargs[:-1] + (False,), vote=False,
                                          planner=planner, groups=set(groups), state=ra_state)
                stage["deferred_recovery"] = stage.get("deferred_recovery", 0.0) + time.perf_counter() - tr
                return info
            recover_groups.state = ra_state
        elif deferred:
            t1b = time.perf_counter()
            stats["_schedule"] = _deferred_recovery(spill, lay, opt, stats, indel_stats, initargs[:-1] + (False,),
                                                    vote=not select, planner=planner)
            stage["deferred_recovery"] = time.perf_counter() - t1b
            for rnd, r in stats["_schedule"].get("rounds", {}).items():
                ev.emit("recovery_round", "recovery", round=rnd, recovery_attempts=r.get("reads", 0),
                        recovered=r.get("recovered", 0), seconds=r.get("seconds"))
        if smart or softm:
            stats["_indel"] = indel_stats          # pass 2 adds its consensus counters; reported as report["indel_recovery"]
        planner.checkpoint("pass 2 start")
        result = _pass2(spill, lay, opt, stats, stage, t0, output, overwrite, partial_dir, select, select_dir, key, passphrase,
                        tmp_root, planner, ev, recover_groups)
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _plan_pass1(planner, opt: DecodeOptions, stats: Counter, deferred: bool) -> None:
    """Record pass 1 (FAST, SYNC and, under the eager schedule, SMART/SOFT inline) in the recovery plan."""
    reads = stats["reads"]
    planner.decide("FAST", True, "every read: exact-length frame, inner RS + CRC", {"reads": reads})
    planner.outcome("FAST", reads_verified=stats["fast"])
    planner.decide("SYNC", True, "reads FAST did not verify: marker-template alignment, indels as erasures",
                   {"reads_not_fast": reads - stats["fast"]})
    planner.outcome("SYNC", reads_verified=stats["sync"], reverse_complement=stats["reverse_complement"],
                    pending_after_confidence_check=stats["pending"], unaligned=stats["unaligned"])
    smart, soft = opt.indel_recovery == "smart", opt.soft_decoding != "off"
    if not (smart or soft):
        planner.decide("SMART", False, "indel_recovery is 'segment' (V4 erasure rule only)")
        planner.decide("SOFT", False, "soft_decoding is off")
    elif not deferred:
        why = "eager schedule: inside pass 1 for every read the hard paths failed (no budget admission)"
        if smart:
            planner.decide("SMART", True, why)
            planner.outcome("SMART", reads_verified=stats["smart"])
        if soft:
            planner.decide("SOFT", True, why)
            planner.outcome("SOFT", reads_verified=stats["soft"])


def _decode_superblock(spill: Spill, lay: Layout, opt: DecodeOptions, stats: Counter) -> tuple[Superblock, dict]:
    acc, pend = spill.load(0)
    acc_sb = acc[acc["kind"] == KIND_SUPER]
    pend_sb = pend[pend["kind"] == KIND_SUPER]
    symbols, conflicts = resolve_duplicates(acc_sb)
    ks, ms = Superblock.symbols(lay.payload_bytes)
    # consensus rescue for superblock symbols that no single read delivered
    if pend_sb.size:
        symbols.update(_consensus_symbols(pend_sb, symbols, lay, opt, stats))
    tags = sorted({k[1] for k in symbols})
    candidates = {}
    for tag in tags:
        got = {k[3]: v for k, v in symbols.items() if k[1] == tag and k[0] == KIND_SUPER and k[2] == 0 and k[3] < ks + ms}
        if len(got) < ks:
            continue
        try:
            data = CauchyRSCodec(ks, ms).decode(got, ks, lay.payload_bytes).reshape(-1).tobytes()
            sb = Superblock.unpack(data[:SB_BYTES])
        except (VNXDecodeError, VNXFormatError):
            continue
        if int.from_bytes(sb.archive_id[:2], "big") == tag and sb.layout == lay:
            candidates[tag] = sb
    if not candidates:
        raise VNXDecodeError("no superblock could be decoded (too few superblock strands survived, or the layout is wrong)",
                             stage="superblock", details={"superblock_symbols_seen": len(symbols), "tags_seen": [f"{t:04x}" for t in tags]},
                             hint="check --profile, increase coverage, or confirm the reads come from a VNX4 strand pool")
    if opt.archive_tag is not None:
        if opt.archive_tag not in candidates:
            raise VNXAddressError(f"archive tag {opt.archive_tag:04x} not found; pools present: {[f'{t:04x}' for t in candidates]}")
        tag = opt.archive_tag
    elif len(candidates) > 1:
        raise VNXAddressError(f"the reads contain several archives {[f'{t:04x}' for t in candidates]}; choose one with --archive-tag")
    else:
        tag = next(iter(candidates))
    return candidates[tag], {"archive_tags_seen": [f"{t:04x}" for t in tags], "superblock_conflicts": conflicts}


def _addr_bytes(key: tuple) -> bytes:
    kind, tag, group, symbol = key
    return bytes([(4 << 4) | (kind & 15)]) + int(tag).to_bytes(2, "big") + int(group).to_bytes(4, "big") + int(symbol).to_bytes(2, "big")


def snap_addresses(keys: np.ndarray, missing: set, alt: np.ndarray | None = None) -> tuple[np.ndarray, int]:
    """Map tentative addresses to the unique missing address within one header byte.

    Each read can carry two readings of its header (``keys`` from the indel-corrected projection, ``alt`` from the raw
    prefix). A candidate address matches a byte position if either reading has that byte; a read is snapped when
    exactly one missing address differs from it in at most one position. Reads whose address is already missing are
    unchanged; reads matching nothing (or several) are left as they are and later ignored. A wrong snap can only
    cost a consensus vote: every recovered frame is still RS- and CRC-verified, and the address it decodes to must
    equal the group's address.
    """
    if not missing or not len(keys):
        return keys, 0
    index: dict = {}
    for m in missing:
        b = _addr_bytes(m)
        for pos in range(9):
            index.setdefault((pos, b[:pos] + b[pos + 1:]), []).append(m)
    out = keys.copy()
    snapped = 0
    alts = keys if alt is None else alt
    cache: dict = {}
    for i, (row, arow) in enumerate(zip(keys.tolist(), alts.tolist())):
        key, akey = tuple(row), tuple(arow)
        if key in missing:
            continue
        if akey in missing:
            out[i] = akey
            snapped += 1
            continue
        ck = (key, akey)
        hit = cache.get(ck)
        if hit is None:
            b1, b2 = _addr_bytes(key), _addr_bytes(akey)
            cands = set()
            for b in {b1, b2}:
                for pos in range(9):
                    cands.update(index.get((pos, b[:pos] + b[pos + 1:]), ()))
            good = [m for m in cands
                    if sum(1 for x, y, z in zip(_addr_bytes(m), b1, b2) if x != y and x != z) <= 1]
            hit = cache[ck] = good[0] if len(good) == 1 else False
        if hit:
            out[i] = hit
            snapped += 1
    return out, snapped


def _consensus_symbols(pend: np.ndarray, known: dict, lay: Layout, opt: DecodeOptions, stats: Counter,
                       missing: set | None = None) -> dict:
    """Consensus over pending reads per (snapped) tentative address; addresses already known are skipped."""
    out = {}
    if not pend.size:
        return out
    keys = np.stack([pend["kind"].astype(np.int64), pend["tag"].astype(np.int64), pend["group"].astype(np.int64),
                     pend["symbol"].astype(np.int64)], axis=1)
    if missing is not None:
        alt = np.asarray(pend["alt"], dtype=np.int64) if "alt" in pend.dtype.names else None
        keys, snapped = snap_addresses(keys, missing, alt)
        stats["addresses_snapped"] += snapped
        wanted = np.fromiter((tuple(k) in missing for k in keys.tolist()), dtype=bool, count=len(keys))
        keys, pend = keys[wanted], pend[wanted]
        if not len(keys):
            return out
    order = np.lexsort((keys[:, 3], keys[:, 2], keys[:, 1], keys[:, 0]))
    keys = keys[order]
    bases = pend["bases"][order]
    change = np.flatnonzero((np.diff(keys, axis=0) != 0).any(axis=1)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(keys)]])
    cand_keys, frames, ers = [], [], []
    for s, e in zip(starts.tolist(), ends.tolist()):
        key = tuple(int(x) for x in keys[s])
        if key in known:
            continue
        group = bases[s:min(e, s + opt.max_pending_per_address)]
        best, erased = consensus_hard(group, opt.consensus_threshold)
        cand_keys.append(key)
        frames.append(best)
        ers.append(erased)
    stats["consensus_attempted"] += len(cand_keys)
    if not cand_keys:
        return out
    fr = nt_to_bytes(np.stack(frames))
    er = frame_erasures_to_bytes(np.stack(ers))
    P = decode_frames(lay, fr, er)
    for i, key in enumerate(cand_keys):
        if P.ok[i] and (int(P.kind[i]), int(P.tag[i]), int(P.group[i]), int(P.symbol[i])) == key:
            out[key] = P.payload[i]
    stats["consensus_recovered"] += len(out)
    if opt.indel_recovery == "smart" and "raw" in pend.dtype.names:
        out.update(_smart_consensus(cand_keys, out, starts, ends, keys, pend[order], lay, opt, stats))
    if opt.soft_decoding != "off" and "raw" in pend.dtype.names:
        out.update(_soft_consensus(cand_keys, out, starts, ends, keys, pend[order], lay, opt, stats))
    return out


def _soft_consensus(cand_keys, done, starts, ends, keys, pend, lay, opt, stats) -> dict:
    """V5 Phase 4: sum the reads' own soft evidence per still-missing address (≥ 2 reads) and soft-decode it."""
    from ..v5.indel import recovery as rv
    from ..v5.indel.path import align_with_path
    from ..v5.soft import decoder as sd
    from ..v5.soft import frames as sf
    from ..v5.soft import symbols as ss
    geom = rv.Geometry(lay)
    al = TemplateAligner(lay, opt.band, opt.sync_costs)
    icfg = opt.indel_config if opt.indel_recovery == "smart" else None
    wanted = set(cand_keys)
    ist = stats["_indel"] if isinstance(stats.get("_indel"), Counter) else Counter()
    keys_out, posts = [], []
    for s, e in zip(starts.tolist(), ends.tolist()):
        key = tuple(int(x) for x in keys[s])
        if key in done or key not in wanted or e - s < 2:
            continue
        grp = pend[s:min(e, s + opt.max_pending_per_address)]
        reads = [grp["raw"][j, : int(grp["rawlen"][j])] for j in range(len(grp))]
        quals = [grp["rawq"][j, : int(grp["rawlen"][j])] if grp["hasq"][j] else None for j in range(len(grp))]
        proj, rpos = align_with_path(al, reads, None if any(q is None for q in quals) else quals)
        evs = []
        for j in np.flatnonzero(proj.ok).tolist():
            plan = None if icfg is None else rv.plan_read(geom, reads[j], quals[j], proj.bases[j], proj.erased[j], rpos[j], icfg)
            evs.append(sf.read_evidence(geom, reads[j], quals[j], proj.bases[j], proj.erased[j], rpos[j], plan,
                                        opt.soft_config.default_error))
        if len(evs) < 2:
            continue
        try:
            posts.append(ss.normalise(sf.consensus_evidence(evs)))
        except ss.SoftInputError:
            continue
        keys_out.append(key)
    out = {}
    if keys_out:
        outs = sd.soft_decode(lay, posts, opt.soft_config, expected=keys_out)
        for key, o in zip(keys_out, outs):
            ist[f"soft_consensus_{o.mode}"] += 1
            if o.accepted:
                out[key] = o.payload
    stats["_indel"] = ist
    stats["consensus_recovered_soft"] += len(out)
    return out


def _smart_consensus(cand_keys, done, starts, ends, keys, pend, lay, opt, stats) -> dict:
    """V5 consensus realignment for addresses the V4 vote could not decode (groups of ≥ 2 pending reads)."""
    from ..v5.indel.consensus import consensus_recover
    from ..v5.indel.recovery import Geometry
    geom = Geometry(lay)
    al = TemplateAligner(lay, opt.band, opt.sync_costs)
    out = {}
    ist = stats["_indel"] if isinstance(stats.get("_indel"), Counter) else Counter()
    wanted = set(cand_keys)
    for s, e in zip(starts.tolist(), ends.tolist()):
        key = tuple(int(x) for x in keys[s])
        if key in done or key not in wanted or e - s < 2:
            continue
        grp = pend[s:min(e, s + opt.max_pending_per_address)]
        reads = [grp["raw"][j, : int(grp["rawlen"][j])] for j in range(len(grp))]
        quals = None
        if grp["hasq"].all():
            quals = [grp["rawq"][j, : int(grp["rawlen"][j])] for j in range(len(grp))]
        res = consensus_recover(geom, al, reads, quals, opt.indel_config, threshold=opt.consensus_threshold,
                                max_reads=opt.max_pending_per_address, expected=key)
        ist["consensus_groups"] += 1
        ist[f"consensus_route_{res.route}"] += 1
        if res.accepted:
            out[key] = res.payload
    stats["_indel"] = ist
    stats["consensus_recovered_smart"] += len(out)
    return out


# ============================================================================ deferred per-read recovery (V5)
_DEFER_CHUNK = 256          # pending reads per recovery task
_ORPHAN_SLICE = 65536       # unaddressed reads materialised at once in round B


def _group_decodable(codec, syms: dict, k: int, P: int, g: int) -> bool:
    """The predicate of pass 2's outer decode: would group g decode from these verified symbols?"""
    if isinstance(codec, CauchyRSCodec):     # MDS erasure code: any k distinct symbols (the codec's own check)
        n = codec.symbols_for(k)
        return sum(1 for s in syms if 0 <= s < n) >= k
    try:
        if hasattr(codec, "decode_block"):
            codec.decode_block(syms, k, P, g)
        else:
            codec.decode(syms, k, P)
    except VNXDecodeError:
        return False
    return True


def _pending_keys(pend: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    keys = np.stack([pend["kind"].astype(np.int64), pend["tag"].astype(np.int64), pend["group"].astype(np.int64),
                     pend["symbol"].astype(np.int64)], axis=1) if len(pend) else np.zeros((0, 4), dtype=np.int64)
    alt = np.asarray(pend["alt"], dtype=np.int64) if len(pend) else np.zeros((0, 4), dtype=np.int64)
    return keys, alt


def _targeted(pend: np.ndarray, needed: set) -> np.ndarray:
    """Pending reads whose header reading — either reading, or its unique one-byte snap — is a needed address."""
    if not len(pend) or not needed:
        return np.zeros(len(pend), dtype=bool)
    keys, alt = _pending_keys(pend)
    snapped, _ = snap_addresses(keys, needed, alt)
    return np.fromiter((tuple(k) in needed for k in snapped.tolist()), dtype=bool, count=len(pend))


def _group_state(spill: Spill, sb: Superblock, codec, lay: Layout, opt: DecodeOptions, consumed: list,
                 vote: bool, groups: set | None = None) -> tuple[set, int, int]:
    """(needed addresses, groups, decodable groups) from the verified frames in the spill.

    vote: also count what pass 2's V4 consensus vote will recover — computed exactly as pass 2 computes it (same known
    symbols, same missing set, same pending reads), so a group called decodable here is decodable in pass 2, which only
    adds smart/soft consensus on top. Without it (random access, whose pass 2 uses other missing sets) only single
    verified reads count, which pass 2 can only extend. ``groups``: only these data groups are considered (random
    access); the group count returned is then the number of those groups.
    """
    from dataclasses import replace
    tag = int.from_bytes(sb.archive_id[:2], "big")
    K, P = sb.K, lay.payload_bytes
    v4 = replace(opt, indel_recovery="segment", soft_decoding="off")
    needed: set = set()
    decodable = 0
    for b in range(spill.B):
        acc, pend = spill.load(b)
        acc = acc[(acc["kind"] == KIND_DATA) & (acc["tag"] == tag) & (acc["group"] < sb.group_count)]
        symbols, _ = resolve_duplicates(acc)
        groups_b = [g for g in range(b, sb.group_count, spill.B) if groups is None or g in groups]
        if not groups_b:
            continue
        if vote and len(pend):
            missing = set()
            for g in groups_b:
                n_sym = codec.symbols_for(group_k(sb.container_size, K, P, g))
                missing.update((KIND_DATA, tag, g, s) for s in range(n_sym) if (KIND_DATA, tag, g, s) not in symbols)
            symbols.update(_consensus_symbols(pend[~consumed[b]], symbols, lay, v4, Counter(), missing))
        by_group: dict[int, dict[int, np.ndarray]] = {}
        for (kd, tg, g, s), v in symbols.items():
            if kd == KIND_DATA and tg == tag:
                by_group.setdefault(g, {})[s] = v
        for g in groups_b:
            k = group_k(sb.container_size, K, P, g)
            have = by_group.get(g, {})
            if _group_decodable(codec, have, k, P, g):
                decodable += 1
            else:
                needed.update((KIND_DATA, tag, g, s) for s in range(codec.symbols_for(k)) if s not in have)
    total = sb.group_count if groups is None else sum(1 for g in groups if 0 <= g < sb.group_count)
    return needed, total, decodable


def _deferred_recovery(spill: Spill, lay: Layout, opt: DecodeOptions, stats: Counter, indel_stats: Counter,
                       initargs: tuple, vote: bool = True, planner=None, groups: set | None = None,
                       state: dict | None = None) -> dict:
    """Per-read smart/soft recovery after the cheap pass, only where it can still change the result.

    State after the cheap pass (fast + sync paths, both orientations), all from verified frames:

      address RECOVERED   at least one verified copy that survives duplicate resolution (strict majority)
      address MISSING     expected by the superblock, not recovered
      group   DECODABLE   its recovered symbols, plus what pass 2's V4 consensus vote will recover from the pending
                          reads (``_group_state``), already satisfy pass 2's outer decode (``_group_decodable``)
      group   INCOMPLETE  not decodable yet
      address NEEDED      MISSING and in an INCOMPLETE group
      read    TARGETED    pending, and a header reading (or its unique one-byte snap) is a NEEDED address
      read    UNADDRESSED aligned, failed, no readable header (kept only for round B)

    Rounds, each running the unchanged eager per-read recovery (``_process`` with smart/soft on) on the read's stored
    orientation:

      S  every pending read whose header says superblock (the superblock defines what is expected)
      A  TARGETED reads
      B  only if the superblock is still undecodable or a group is still INCOMPLETE: every read not yet tried,
         including UNADDRESSED reads and reads whose header points at recovered addresses. A header can be wrong, so
         while data is missing nothing the eager schedule would have tried is left untried.

    A verified frame is stored under its *verified* address and its pending record is removed, as if pass 1 had
    accepted it. Reads never tried stay pending for pass 2 exactly like any failed read. Pass 2 is unchanged.

    Random access (V6) calls this once per stage with ``groups`` (first the index groups, then the stripes holding the
    selected files) and a shared ``state``, so reads tried in one stage are not tried again and later stages only
    consider what is still untried; groups outside ``groups`` never make round A or B run.
    """
    B = spill.B
    sizes = [(spill.dir / f"pend{b}.bin").stat().st_size // spill.pend_dtype.itemsize for b in range(B)]
    st = state if state is not None else {}
    if "tried" not in st:
        st.update(tried=[np.zeros(n, dtype=bool) for n in sizes], orph_done=0, calls=0,
                  info={"mode": "deferred", "pending_reads": int(sum(sizes)), "rounds": {}})
    st["calls"] += 1
    tried = st["tried"]
    consumed = [np.zeros(n, dtype=bool) for n in sizes]
    orph = spill.load_orphans()
    info: dict = st["info"]
    info["unaddressed_reads"] = int(len(orph))
    first = st["calls"] == 1
    pool = None
    if planner is None:
        from ..v6.recovery import RecoveryPlanner
        planner = RecoveryPlanner()
    modes = "+".join(m for m, on in (("SMART", opt.indel_recovery == "smart"), ("SOFT", opt.soft_decoding != "off")) if on)

    def recover(recs: np.ndarray, rnd: str) -> tuple[np.ndarray, int]:
        """Eager per-read recovery on stored records; returns the accepted mask and the number of records examined
        (fewer than ``len(recs)`` only when the wall-time budget cut the round short), and spills the verified
        frames."""
        t = time.perf_counter()
        ok = np.zeros(len(recs), dtype=bool)
        examined = len(recs)
        r = info["rounds"].setdefault(rnd, {"reads": 0, "recovered": 0, "unique_addresses": 0, "seconds": 0.0})
        if len(recs):
            nonlocal pool
            # about four tasks per worker (≤ _DEFER_CHUNK reads each), so small rounds still use every worker. Per-read
            # results do not depend on the chunking.
            size = _DEFER_CHUNK if opt.workers == 1 else max(8, min(_DEFER_CHUNK, -(-len(recs) // (4 * opt.workers))))
            jobs = []
            for c0 in range(0, len(recs), size):
                c = recs[c0:c0 + size]
                lens = c["rawlen"].astype(np.int64)
                codes = np.concatenate([c["raw"][j, : lens[j]] for j in range(len(c))])
                quals = np.concatenate([c["rawq"][j, : lens[j]] for j in range(len(c))]) if c["hasq"].all() else None
                jobs.append((codes, lens, quals))
            timed = planner.budget.max_wall_seconds is not None
            if opt.workers == 1:
                _p_init(*initargs)
                outs = []
                for j in jobs:
                    if timed and planner.out_of_time():
                        break
                    outs.append(_process(*j))
            else:
                if pool is None:
                    pool = ProcessPoolExecutor(max_workers=opt.workers, initializer=_p_init, initargs=initargs)
                if not timed:
                    outs = list(pool.map(_process, *zip(*jobs)))
                else:
                    # bounded window in job order, so a wall-time cut leaves a deterministic prefix examined
                    outs, window, it = [], deque(), iter(jobs)
                    for j in it:
                        window.append(pool.submit(_process, *j))
                        if len(window) >= 2 * opt.workers:
                            outs.append(window.popleft().result())
                            if planner.out_of_time():
                                break
                    for f in window:
                        f.cancel()
                    while window and not planner.out_of_time():
                        outs.append(window.popleft().result())
            examined = min(len(recs), len(outs) * size)
            fields, payloads = [], []
            for c0, out in zip(range(0, len(recs), size), outs):
                ok[c0 + out["acc_index"]] = True
                fields.append(out["acc_fields"])
                payloads.append(out["acc_payload"])
                indel_stats.update(out.get("indel", {}))
                for key in ("smart", "soft"):
                    if key in out["stats"]:
                        stats[key] += out["stats"][key]
            if fields:
                fields_a = np.concatenate(fields)
                spill.append_acc(fields_a, np.concatenate(payloads))
                r["recovered_addresses"] = r.get("recovered_addresses", 0) + len({tuple(f) for f in fields_a.tolist()})
            r["recovered"] += int(ok.sum())
            if examined < len(recs):
                planner.unexamined(rnd, len(recs) - examined)
                planner.exhausted.append({"limit": "max_wall_seconds", "at": f"round {rnd}", "requested": int(len(recs)),
                                          "admitted": int(examined), "effect": "round cut short; the rest stay pending"})
        r["reads"] += int(examined)
        r["seconds"] = round(r["seconds"] + time.perf_counter() - t, 3)
        return ok, examined

    def round_over(rnd: str, select) -> None:
        addrs: set = set()
        for b in range(B):
            if not sizes[b]:
                continue
            _, pend = spill.load(b)
            sel = np.flatnonzero(select(b, pend) & ~tried[b])
            if not sel.size:
                continue
            sel = sel[: planner.admit_reads(rnd, int(sel.size))]
            if not sel.size:
                continue
            ok, examined = recover(pend[sel], rnd)
            sel, ok = sel[:examined], ok[:examined]
            tried[b][sel] = True
            keys, _ = _pending_keys(pend[sel])
            addrs.update(map(tuple, keys.tolist()))
            consumed[b][sel[ok]] = True
        info["rounds"].setdefault(rnd, {"reads": 0, "recovered": 0, "unique_addresses": 0, "seconds": 0.0})
        info["rounds"][rnd]["unique_addresses"] += len(addrs)

    try:
        def is_super(b, pend):
            keys, alt = _pending_keys(pend)
            return (keys[:, 0] == KIND_SUPER) | (alt[:, 0] == KIND_SUPER)

        if first:
            planner.decide(modes, True, "round S: pending reads whose header says superblock (the superblock defines "
                           "what is expected)", {"pending_reads": info["pending_reads"],
                                                 "unaddressed_reads": info["unaddressed_reads"]})
            round_over("S", is_super)
        try:
            sb, _ = _decode_superblock(spill, lay, opt, Counter())
        except (VNXDecodeError, VNXAddressError):
            sb = None
        info["superblock_decoded"] = sb is not None
        info["v4_vote_counted"] = vote
        complete = False
        if sb is not None:
            codec = make_outer(sb.outer_code, sb.K, sb.M, sb.lt_seed, sb.lt_distribution)
            needed, n_groups, dec = _group_state(spill, sb, codec, lay, opt, consumed, vote, groups)
            info.update(groups=n_groups, groups_decodable_after_cheap_pass=dec, needed_addresses=len(needed))
            sig = {"groups": n_groups, "groups_decodable": dec, "needed_addresses": len(needed)}
            if groups is not None:
                sig["random_access_stage"] = st["calls"]
            if planner.decide(modes, bool(needed), "round A: reads targeting addresses needed by incomplete groups"
                              if needed else "round A skipped: no address is needed", sig):
                round_over("A", lambda b, pend: _targeted(pend, needed))
                needed, _, dec = _group_state(spill, sb, codec, lay, opt, consumed, vote, groups)
            info["groups_decodable_after_round_a"] = dec
            complete = dec == n_groups
        info["round_b"] = not complete
        why = ("round B: superblock undecodable after round S" if sb is None else
               f"round B: {info['groups'] - info['groups_decodable_after_round_a']} group(s) still incomplete" if not complete
               else "round B skipped: every group decodable")
        planner.decide("EXPENSIVE", not complete, why, {"superblock_decoded": sb is not None,
                                                        "groups_incomplete": None if sb is None else
                                                        info["groups"] - info["groups_decodable_after_round_a"]})
        if not complete:
            planner.checkpoint("round B")
            round_over("B", lambda b, pend: np.ones(len(pend), dtype=bool))
            o0 = st["orph_done"]                           # unaddressed reads already tried by an earlier stage
            n = planner.admit_reads("B", int(len(orph)) - o0)
            examined, found = 0, 0
            for c0 in range(o0, o0 + n, _ORPHAN_SLICE):    # bounded memory: one slice of unaddressed reads at a time
                part = np.array(orph[c0:min(o0 + n, c0 + _ORPHAN_SLICE)])
                ok, done_ = recover(part, "B")
                examined += done_
                found += int(ok.sum())
                if done_ < len(part):
                    planner.unexamined("B", o0 + n - c0 - len(part))
                    break
            st["orph_done"] = o0 + examined
            info["rounds"]["B"]["unaddressed_recovered"] = info["rounds"]["B"].get("unaddressed_recovered", 0) + found
    finally:
        if pool is not None:
            pool.shutdown()
    skipped_keys: set = set()
    skipped = 0
    for b in range(B):
        if sizes[b] and not tried[b].all():          # before the rewrite: indices refer to the pass-1 file
            _, pend = spill.load(b)
            keys, _ = _pending_keys(pend[~tried[b]])
            skipped += len(keys)
            skipped_keys.update(map(tuple, keys.tolist()))
        if consumed[b].any():
            spill.rewrite_pend(b, ~consumed[b])
            st["tried_consumed"] = st.get("tried_consumed", 0) + int(consumed[b].sum())
            tried[b] = tried[b][~consumed[b]]                # keep indices aligned with the rewritten file
    info["reads_skipped"] = skipped
    info["addresses_skipped"] = len(skipped_keys)
    info["reads_tried"] = int(sum(int(t.sum()) for t in tried)) + st.get("tried_consumed", 0) + st["orph_done"]
    rounds = info["rounds"]
    planner.outcome(modes, reads_examined=sum(rounds.get(k, {}).get("reads", 0) for k in "SA"),
                    reads_verified=sum(rounds.get(k, {}).get("recovered", 0) for k in "SA"))
    if info["round_b"]:
        planner.outcome("EXPENSIVE", reads_examined=rounds.get("B", {}).get("reads", 0),
                        reads_verified=rounds.get("B", {}).get("recovered", 0))
    planner.checkpoint("deferred recovery end")
    return info


def _pass2(spill: Spill, lay: Layout, opt: DecodeOptions, stats: Counter, stage: dict, t0: float, output, overwrite, partial_dir,
           select, select_dir, key, passphrase, tmp_root, planner=None, ev=None, recover_groups=None) -> DecodeResult:
    if planner is None:
        from ..v6.recovery import RecoveryPlanner
        planner = RecoveryPlanner()
    t2 = time.perf_counter()
    sb, sb_info = _decode_superblock(spill, lay, opt, stats)
    if ev is not None:
        ev.archive_id = sb.archive_id.hex()
    tag = int.from_bytes(sb.archive_id[:2], "big")
    K, P = sb.K, lay.payload_bytes
    codec = make_outer(sb.outer_code, K, sb.M, sb.lt_seed, sb.lt_distribution)
    # V6 (superblock version 2): column-parity groups G … total − 1 are full rows; rows that fail row-wise are kept
    # (verified symbols only) for the iterative stripe decoder (vnxdna.v6.decode). Version 1: total = G, as in V4.
    total = sb.total_groups
    v6 = None
    if sb.version != 1:
        from ..v6.decode import StripeRecovery
        v6 = StripeRecovery(sb.geometry(), Path(tmp_root) / "parity_rows.bin")

    def row_k(g: int) -> int:
        return K if g >= sb.group_count else group_k(sb.container_size, K, P, g)

    work = Path(tmp_root) / "container.vnx"
    with open(work, "wb") as f:
        f.truncate(sb.container_size)
    failed: dict[int, str] = {}
    decoded = 0
    conflicts = 0
    wanted: set[int] | None = None
    tail_groups = set(range(sb.index_offset // (K * P), sb.group_count))
    if select:
        wanted = set(tail_groups) | {0}   # the index section plus the group holding the container header
    fd = os.open(work, os.O_RDWR)
    try:
        def run(groups_filter: set[int] | None, done: set[int]) -> None:
            nonlocal decoded, conflicts
            for b in range(spill.B):
                planner.checkpoint("pass 2")
                acc, pend = spill.load(b)
                acc = acc[(acc["kind"] == KIND_DATA) & (acc["tag"] == tag)]
                acc = acc[acc["group"] < total]
                if groups_filter is not None:
                    acc = acc[np.isin(acc["group"], list(groups_filter))]
                symbols, c = resolve_duplicates(acc)
                conflicts += c
                targets_b = [g for g in range(b, total, spill.B)
                             if g not in done and (groups_filter is None or g in groups_filter)]
                missing = set()
                for g in targets_b:
                    n_sym = codec.symbols_for(row_k(g))
                    missing.update((KIND_DATA, tag, g, s_) for s_ in range(n_sym) if (KIND_DATA, tag, g, s_) not in symbols)
                symbols.update(_consensus_symbols(pend, symbols, lay, opt, stats, missing))
                by_group: dict[int, dict[int, np.ndarray]] = {}
                for (kd, tg, g, s), v in symbols.items():
                    if kd == KIND_DATA and tg == tag:
                        by_group.setdefault(g, {})[s] = v
                targets = [g for g in range(b, total, spill.B)
                           if g not in done and (groups_filter is None or g in groups_filter)]
                for g in targets:
                    k = row_k(g)
                    syms = by_group.get(g, {})
                    try:
                        if hasattr(codec, "decode_block"):
                            data = codec.decode_block(syms, k, P, g)
                        else:
                            data = codec.decode(syms, k, P)
                    except VNXDecodeError as error:
                        done.add(g)
                        if v6 is not None:
                            v6.row_failed(g, syms, str(error))
                        else:
                            failed[g] = str(error)
                        continue
                    done.add(g)
                    if v6 is not None:
                        v6.row_decoded(g, data, syms)
                        if g >= sb.group_count:
                            continue
                    raw = data.reshape(-1).tobytes()
                    start = g * K * P
                    raw = raw[: max(0, min(len(raw), sb.container_size - start))]
                    os.pwrite(fd, raw, start)
                    decoded += 1
            if v6 is not None:
                decoded += v6.finish(fd, run, done, failed, admit=planner.admit_stripe)

        done: set[int] = set()
        planner.decide("OUTER", True, "row decode of every group" + (" holding the index and the selected files"
                                                                     if select else "") +
                       ("; V6 stripe/column recovery for rows that fail" if v6 is not None else ""),
                       {"groups": sb.group_count, "total_rows": total, "superblock_version": sb.version})
        def stripes_of(groups: set) -> set:
            """Data groups sharing a stripe with ``groups`` (column recovery of a row needs its stripe's rows)."""
            if v6 is None:
                return set(groups)
            geo = sb.geometry()
            out: set = set()
            for g in groups:
                if 0 <= g < sb.group_count:
                    out.update(geo.stripe_rows(geo.stripe_of(g))[0])
            return out

        if select:
            if recover_groups is not None:
                recover_groups(stripes_of(wanted))
            run(wanted, done)
        else:
            run(None, done)
        stage["pass2_decode"] = time.perf_counter() - t2
        if ev is not None:
            ev.emit("pass2_end", "outer", groups_total=total, groups_decoded=decoded, groups_failed=len(failed),
                    consensus_attempted=stats.get("consensus_attempted", 0),
                    consensus_recovered=stats.get("consensus_recovered", 0),
                    rows_recovered_by_columns=None if v6 is None else len(v6.recovered),
                    seconds=round(stage["pass2_decode"], 4))
        planner.outcome("OUTER", groups_failed=len(failed), consensus_recovered=stats.get("consensus_recovered", 0),
                        rows_recovered_by_columns=None if v6 is None else len(v6.recovered))
        indel = stats.pop("_indel", None)
        schedule = stats.pop("_schedule", None)
        report = {"superblock": {"archive_id": sb.archive_id.hex(), "container_size": sb.container_size, "groups": sb.group_count,
                                 "outer_code": codec.configuration(), "layout": lay.to_dict()},
                  "reads": dict(stats), **sb_info, "duplicate_conflicts": conflicts}
        if v6 is not None:
            report["outer_v6"] = v6.report()
        indel = dict(indel or {})
        softd = {k[5:]: v for k, v in indel.items() if k.startswith("soft_")}
        indel = {k: v for k, v in indel.items() if not k.startswith("soft_")}
        if opt.indel_recovery == "smart":
            report["indel_recovery"] = {"mode": "smart", "config": dict(opt.indel_config.__dict__), **indel}
        if opt.soft_decoding != "off":
            report["soft_decoding"] = {"mode": opt.soft_decoding, "config": dict(opt.soft_config.__dict__), **softd}
        if opt.indel_recovery == "smart" or opt.soft_decoding != "off":
            report["recovery_schedule"] = schedule if schedule is not None else {"mode": "eager"}
        report["recovery_plan"] = planner.report()
        if select:
            recover = None if recover_groups is None else (lambda need: recover_groups(stripes_of(need)))
            res = _selective(sb, work, fd, run, done, failed, select, select_dir, key, passphrase, overwrite, report, stage, t0,
                             recover)
            if v6 is not None:
                res.report["outer_v6"] = v6.report()
            if recover_groups is not None:
                res.report["recovery_schedule"] = dict(recover_groups.state.get("info", {}), random_access_stages=
                                                       recover_groups.state.get("calls", 0))
            res.report["recovery_plan"] = planner.report()
            return res
    finally:
        os.close(fd)
        if v6 is not None:
            v6.parity_rows.close()
    report["groups_decoded"] = decoded
    report["groups_failed"] = len(failed)
    report["failed_groups"] = sorted(failed)[:100]
    if failed:
        planner.decide("REJECT", True, f"{len(failed)} group(s) unrecovered: the container is not published; only files "
                       "whose every chunk verifies may be extracted", {"groups_failed": len(failed)})
        report["recovery_plan"] = planner.report()
        report["status"] = "PARTIAL"
        report.update(_partial(sb, work, failed, partial_dir, key, passphrase, overwrite))
        report["stage_seconds"] = stage
        report["seconds"] = time.perf_counter() - t0
        report["peak_rss_bytes"] = peak_rss_bytes()
        return DecodeResult("PARTIAL" if report.get("files_recovered") else "FAILURE", report)
    t3 = time.perf_counter()
    h = hashlib.sha256()
    with open(work, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    if ev is not None:
        ev.emit("verify", "integrity", sha256_match=h.digest() == sb.container_sha256)
    if h.digest() != sb.container_sha256:
        planner.decide("REJECT", True, "container SHA-256 differs from the superblock: nothing published")
        report["recovery_plan"] = planner.report()
        report["status"] = "FAILURE"
        raise VNXIntegrityError("reconstructed container does not match the SHA-256 recorded in the superblock; nothing published",
                                details=report)
    ct.open_container(work)                      # structural + manifest + Merkle validation
    stage["verify"] = time.perf_counter() - t3
    report["container_sha256"] = h.hexdigest()
    report["status"] = "SUCCESS"
    if output is not None:
        with atomic_output(output, overwrite=overwrite) as tmp:
            shutil.copyfile(work, tmp)
    report["stage_seconds"] = stage
    report["seconds"] = time.perf_counter() - t0
    report["peak_rss_bytes"] = peak_rss_bytes()
    return DecodeResult("SUCCESS", report, str(output) if output else None)


def _partial(sb: Superblock, work: Path, failed: dict, partial_dir, key, passphrase, overwrite) -> dict:
    """Recover individually verified files from an incomplete container (only if the index section survived)."""
    K, P = sb.K, sb.layout.payload_bytes
    lost_ranges = [[g * K * P, min(sb.container_size, (g + 1) * K * P)] for g in sorted(failed)]
    info: dict = {"lost_container_ranges": lost_ranges[:100], "files_recovered": [], "files_lost": []}
    index_lost = any(e > sb.index_offset for _, e in lost_ranges)
    if index_lost:
        info["partial_note"] = "the archive index (tables/manifest) was not recovered; no file can be verified"
        return info
    if 0 in failed:
        # the 16-byte header is a constant of format 4.0 (magic, version, zero flags); restoring it lets the surviving
        # index be read. Every file is still verified chunk by chunk (SHA-256 + chunk ID + file SHA-256) below.
        import struct
        from .version import FORMAT_VERSION
        with open(work, "r+b") as f:
            f.write(ct.MAGIC + struct.pack(">HHI", FORMAT_VERSION[0], FORMAT_VERSION[1], 0))
        info["header_restored"] = True
    try:
        c = ct.open_container(work, key=key, passphrase=passphrase, require_key=True)
    except Exception as error:  # noqa: BLE001 - report, never publish
        info["partial_note"] = f"index section did not validate: {error}"
        return info
    for rec in c.files:
        if rec.type != ct.TYPE_FILE:
            continue
        ok = True
        for idx in c.file_chunks(rec).tolist():
            off, n = c.chunk_range(idx)
            if any(s < off + n and off < e for s, e in lost_ranges):
                ok = False
                break
        (info["files_recovered"] if ok else info["files_lost"]).append(rec.path)
    if partial_dir and info["files_recovered"]:
        res = ar.extract(work, partial_dir, key=key, passphrase=passphrase, names=info["files_recovered"], overwrite=overwrite)
        info["partial_extract"] = res
    return info


def _selective(sb, work, fd, run, done, failed, select, select_dir, key, passphrase, overwrite, report, stage, t0,
               recover=None) -> DecodeResult:
    """Random access: decode the index groups, then only the groups holding the selected files."""
    K, P = sb.K, sb.layout.payload_bytes
    if failed:
        raise VNXDecodeError("the archive index could not be decoded; selective extraction impossible",
                             details={"failed_groups": sorted(failed)[:20]})
    c = ct.open_container(work, key=key, passphrase=passphrase, require_key=True)
    need: set[int] = set()
    for name in select:
        rec = c.file(name)
        for idx in c.file_chunks(rec).tolist():
            off, n = c.chunk_range(idx)
            need.update(range(off // (K * P), (off + n - 1) // (K * P) + 1))
    t = time.perf_counter()
    if recover is not None and need - done:
        recover(need - done)                     # smart/soft recovery for the selected files' stripes only
    run(need - done, done)
    stage["selective_decode"] = time.perf_counter() - t
    if failed:
        raise VNXDecodeError("groups holding the selected files could not be decoded", details={"failed_groups": sorted(failed)[:20]})
    res = ar.extract(work, select_dir or ".", key=key, passphrase=passphrase, names=select, overwrite=overwrite)
    report.update({"status": "SUCCESS", "selected": select, "groups_decoded": len(done), "groups_total": sb.group_count,
                   "fraction_of_groups_decoded": round(len(done) / sb.group_count, 6), "extract": res, "stage_seconds": stage,
                   "seconds": time.perf_counter() - t0, "peak_rss_bytes": peak_rss_bytes()})
    return DecodeResult("SUCCESS", report, str(select_dir))
