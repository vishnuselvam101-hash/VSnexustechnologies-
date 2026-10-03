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

    def validate(self) -> None:
        if not 1 <= self.workers <= 256:
            raise VNXConfigurationError("workers must be in 1..256")
        if not 1 <= self.band <= 64:
            raise VNXConfigurationError("band must be in 1..64")
        if not 0.25 <= self.consensus_threshold <= 1.0:
            raise VNXConfigurationError("consensus_threshold must be in [0.25, 1]")
        if not 0 <= self.min_quality <= 93:
            raise VNXConfigurationError("min_quality must be in 0..93")


# ============================================================================ layout detection
def detect_layout(reads_path: str | os.PathLike, opt: DecodeOptions) -> Layout:
    if opt.layout is not None:
        return opt.layout.validate()
    if opt.profile is not None:
        if opt.profile not in PROFILES:
            raise VNXConfigurationError(f"unknown profile {opt.profile!r}")
        return PROFILES[opt.profile][0]
    counts: Counter = Counter()
    seen = 0
    for batch in iter_reads(reads_path, 4096):
        counts.update(batch.lengths.tolist())
        seen += batch.count
        if seen >= 20000:
            break
    if not counts:
        raise VNXFormatError("no reads found", stage="input")
    best = None
    for name, (lay, _, _) in PROFILES.items():
        near = sum(v for length, v in counts.items() if abs(length - lay.strand_nt) <= opt.band)
        if best is None or near > best[0]:
            best = (near, name, lay)
    if not best or best[0] < max(1, seen // 10):
        raise VNXFormatError(f"cannot detect the strand layout (modal read length {counts.most_common(1)[0][0]}); pass --profile",
                             stage="layout")
    return best[2]


# ============================================================================ pass 1 worker
_P: dict = {}


def _p_init(layout: Layout, band: int, costs: SyncCosts, min_q: int, rc: bool) -> None:
    _P.update(lay=layout, al=TemplateAligner(layout, band, costs), min_q=min_q, rc=rc)


def _try(reads: list[np.ndarray], quals: list | None) -> tuple:
    lay: Layout = _P["lay"]
    n = len(reads)
    acc = np.zeros(n, dtype=bool)
    proj_bases = np.full((n, lay.frame_nt), 4, dtype=np.uint8)
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
        pb = fb.copy()
        pb[er] = 4
        proj_bases[exact] = pb
    # exact-length reads whose markers all match gain nothing from re-alignment (same frame, same RS failure)
    hopeless = np.zeros(n, dtype=bool)
    if exact.size and lay.markers:
        hopeless[exact[mism == 0]] = True
    rest = np.flatnonzero(~acc & ~hopeless)
    if rest.size:
        pr = _P["al"].project([reads[i] for i in rest], None if quals is None else [quals[i] for i in rest], _P["min_q"])
        frames = nt_to_bytes(np.minimum(pr.bases, 3))
        er = frame_erasures_to_bytes(pr.erased)
        P = decode_frames(lay, frames, er, errors_only_retry=False)   # sync erasures come from detected indels
        ok = P.ok & pr.ok
        idx = rest[ok]
        acc[idx] = True
        path[idx] = 2
        parsed_fields[idx] = np.stack([P.kind, P.tag, P.group, P.symbol], axis=1)[ok]
        payload[idx] = P.payload[ok]
        cost[rest] = np.where(pr.ok, pr.cost, 1 << 28)
        pb = pr.bases.copy()
        pb[pr.erased] = 4
        proj_bases[rest] = pb
    return acc, parsed_fields, payload, proj_bases, cost, path


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
    acc, fields, payload, proj, cost, path = _try(reads, ql)
    if _P["rc"]:
        bad = np.flatnonzero(~acc & (cost > 2 * _P["al"].costs.insertion))
        if bad.size:
            rreads = [_RC[reads[i][::-1]] for i in bad]
            rq = None if ql is None else [ql[i][::-1] for i in bad]
            a2, f2, p2, pr2, c2, path2 = _try(rreads, rq)
            better = a2 | (c2 < cost[bad])
            sel = bad[better]
            acc[sel], fields[sel], payload[sel], proj[sel], cost[sel], path[sel] = (a2[better], f2[better], p2[better], pr2[better],
                                                                                    c2[better], path2[better])
            rc_used[sel] = ~rc_used[sel]
    # pending: failed reads with an alignment and a fully readable header (bytes 0..9 not erased)
    pend = np.flatnonzero(~acc & (cost < (1 << 28)))
    pend_fields = np.zeros((0, 4), dtype=np.int64)
    pend_bases = np.zeros((0, lay.frame_nt), dtype=np.uint8)
    orphans = 0
    if pend.size:
        pb = proj[pend]
        header_ok = (pb[:, : 4 * HEADER_BYTES] <= 3).all(axis=1)
        orphans = int((~header_ok).sum())
        pb = pb[header_ok]
        if pb.size:
            ta = tentative_address(lay, nt_to_bytes(np.minimum(pb, 3)))
            good = ta[:, 0] >= 0
            orphans += int((~good).sum())
            pend_fields = ta[good]
            pend_bases = pb[good]
    return {"acc_fields": fields[acc], "acc_payload": payload[acc], "pend_fields": pend_fields, "pend_bases": pend_bases,
            "stats": {"reads": int(lengths.size), "fast": int((path == 1).sum()), "sync": int((path == 2).sum()),
                      "reverse_complement": int((rc_used & acc).sum()), "pending": int(pend_fields.shape[0]), "orphans": orphans,
                      "unaligned": int((~acc).sum()) - int(pend.size)}}


# ============================================================================ spill
class Spill:
    """Fixed-width records in bucket files (bucket = group mod B); memory stays bounded by one bucket."""

    def __init__(self, workdir: Path, buckets: int, payload: int, frame_nt: int):
        self.dir = workdir
        self.B = buckets
        self.acc_dtype = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("payload", "u1", (payload,))])
        self.pend_dtype = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("bases", "u1", (frame_nt,))])
        self.acc_files = [open(workdir / f"acc{b}.bin", "wb") for b in range(buckets)]
        self.pend_files = [open(workdir / f"pend{b}.bin", "wb") for b in range(buckets)]

    def write(self, res: dict) -> None:
        for fields, data, files, dtype, name in ((res["acc_fields"], res["acc_payload"], self.acc_files, self.acc_dtype, "payload"),
                                                  (res["pend_fields"], res["pend_bases"], self.pend_files, self.pend_dtype, "bases")):
            if not len(fields):
                continue
            rec = np.zeros(len(fields), dtype=dtype)
            rec["kind"], rec["tag"], rec["group"], rec["symbol"] = fields[:, 0], fields[:, 1], fields[:, 2], fields[:, 3]
            rec[name] = data
            b = fields[:, 2] % self.B
            for k in np.unique(b):
                files[int(k)].write(rec[b == k].tobytes())

    def close(self) -> None:
        for f in self.acc_files + self.pend_files:
            f.close()

    def load(self, b: int) -> tuple[np.ndarray, np.ndarray]:
        return (np.fromfile(self.dir / f"acc{b}.bin", dtype=self.acc_dtype), np.fromfile(self.dir / f"pend{b}.bin", dtype=self.pend_dtype))


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
@dataclass
class DecodeResult:
    status: str                    # SUCCESS | PARTIAL | FAILURE
    report: dict
    output: str | None = None


def decode_reads(reads_path: str | os.PathLike, output: str | os.PathLike | None, options: DecodeOptions | None = None, *,
                 overwrite: bool = False, partial_dir: str | os.PathLike | None = None, select: list[str] | None = None,
                 select_dir: str | os.PathLike | None = None, key: bytes | None = None, passphrase: str | None = None,
                 progress=None, workdir: str | os.PathLike | None = None) -> DecodeResult:
    opt = options or DecodeOptions()
    opt.validate()
    t0 = time.perf_counter()
    stage: dict = {}
    reads_path = Path(reads_path)
    lay = detect_layout(reads_path, opt)
    try:
        est_reads = max(1, reads_path.stat().st_size // (lay.strand_nt + 20))
    except OSError as error:
        raise VNXFormatError(f"cannot read {reads_path}: {error.strerror or error}") from None
    buckets = int(min(256, max(1, est_reads // 200_000)))
    tmp_root = tempfile.mkdtemp(prefix="vnx4-decode-", dir=workdir)
    try:
        spill = Spill(Path(tmp_root), buckets, lay.payload_bytes, lay.frame_nt)
        stats = Counter()
        t1 = time.perf_counter()
        initargs = (lay, opt.band, opt.sync_costs, opt.min_quality, opt.reverse_complement)

        def take(res):
            spill.write(res)
            stats.update(res["stats"])
            if progress:
                progress({"stage": "pass1", "reads": stats["reads"], "elapsed": time.perf_counter() - t0})

        def batches():
            n = 0
            for batch in iter_reads(reads_path, opt.batch_reads, max_reads=opt.max_reads):
                n += batch.count
                q = batch.quals if opt.min_quality else None
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
                        take(window.popleft().result())
                while window:
                    take(window.popleft().result())
        spill.close()
        stage["pass1_reads"] = time.perf_counter() - t1
        if stats["reads"] == 0:
            raise VNXFormatError("the read file contains no reads", stage="input")
        result = _pass2(spill, lay, opt, stats, stage, t0, output, overwrite, partial_dir, select, select_dir, key, passphrase,
                        tmp_root)
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


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


def _consensus_symbols(pend: np.ndarray, known: dict, lay: Layout, opt: DecodeOptions, stats: Counter) -> dict:
    """Consensus over pending reads per tentative address (addresses already known are skipped)."""
    out = {}
    if not pend.size:
        return out
    keys = np.stack([pend["kind"].astype(np.int64), pend["tag"].astype(np.int64), pend["group"].astype(np.int64),
                     pend["symbol"].astype(np.int64)], axis=1)
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
    return out


def _pass2(spill: Spill, lay: Layout, opt: DecodeOptions, stats: Counter, stage: dict, t0: float, output, overwrite, partial_dir,
           select, select_dir, key, passphrase, tmp_root) -> DecodeResult:
    t2 = time.perf_counter()
    sb, sb_info = _decode_superblock(spill, lay, opt, stats)
    tag = int.from_bytes(sb.archive_id[:2], "big")
    K, P = sb.K, lay.payload_bytes
    codec = make_outer(sb.outer_code, K, sb.M, sb.lt_seed, sb.lt_distribution)
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
                acc, pend = spill.load(b)
                acc = acc[(acc["kind"] == KIND_DATA) & (acc["tag"] == tag)]
                pend = pend[(pend["kind"] == KIND_DATA) & (pend["tag"] == tag)]
                acc = acc[acc["group"] < sb.group_count]
                pend = pend[pend["group"] < sb.group_count]
                if groups_filter is not None:
                    acc = acc[np.isin(acc["group"], list(groups_filter))]
                    pend = pend[np.isin(pend["group"], list(groups_filter))]
                symbols, c = resolve_duplicates(acc)
                conflicts += c
                symbols.update(_consensus_symbols(pend, symbols, lay, opt, stats))
                by_group: dict[int, dict[int, np.ndarray]] = {}
                for (_, _, g, s), v in symbols.items():
                    by_group.setdefault(g, {})[s] = v
                targets = [g for g in range(b, sb.group_count, spill.B)
                           if g not in done and (groups_filter is None or g in groups_filter)]
                for g in targets:
                    k = group_k(sb.container_size, K, P, g)
                    syms = by_group.get(g, {})
                    try:
                        if hasattr(codec, "decode_block"):
                            data = codec.decode_block(syms, k, P, g)
                        else:
                            data = codec.decode(syms, k, P)
                    except VNXDecodeError as error:
                        failed[g] = str(error)
                        done.add(g)
                        continue
                    raw = data.reshape(-1).tobytes()
                    start = g * K * P
                    raw = raw[: max(0, min(len(raw), sb.container_size - start))]
                    os.pwrite(fd, raw, start)
                    decoded += 1
                    done.add(g)

        done: set[int] = set()
        if select:
            run(wanted, done)
        else:
            run(None, done)
        stage["pass2_decode"] = time.perf_counter() - t2
        report = {"superblock": {"archive_id": sb.archive_id.hex(), "container_size": sb.container_size, "groups": sb.group_count,
                                 "outer_code": codec.configuration(), "layout": lay.to_dict()},
                  "reads": dict(stats), **sb_info, "duplicate_conflicts": conflicts}
        if select:
            return _selective(sb, work, fd, run, done, failed, select, select_dir, key, passphrase, overwrite, report, stage, t0)
    finally:
        os.close(fd)
    report["groups_decoded"] = decoded
    report["groups_failed"] = len(failed)
    report["failed_groups"] = sorted(failed)[:100]
    if failed:
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
    if h.digest() != sb.container_sha256:
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


def _selective(sb, work, fd, run, done, failed, select, select_dir, key, passphrase, overwrite, report, stage, t0) -> DecodeResult:
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
    run(need - done, done)
    stage["selective_decode"] = time.perf_counter() - t
    if failed:
        raise VNXDecodeError("groups holding the selected files could not be decoded", details={"failed_groups": sorted(failed)[:20]})
    res = ar.extract(work, select_dir or ".", key=key, passphrase=passphrase, names=select, overwrite=overwrite)
    report.update({"status": "SUCCESS", "selected": select, "groups_decoded": len(done), "groups_total": sb.group_count,
                   "fraction_of_groups_decoded": round(len(done) / sb.group_count, 6), "extract": res, "stage_seconds": stage,
                   "seconds": time.perf_counter() - t0, "peak_rss_bytes": peak_rss_bytes()})
    return DecodeResult("SUCCESS", report, str(select_dir))
