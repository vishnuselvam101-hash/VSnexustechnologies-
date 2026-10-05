"""Smart indel recovery for one read: bounded local search around each detected indel (V5 Phase 3).

Pipeline for a read the marker aligner has already placed (``readpos`` from :mod:`.path`)::

    windows      template/read intervals between *solid* markers (all bases aligned, in order, matching)
    candidates   for a window with net shift ±1: every placement of the indel inside the window (bounded)
    scoring      log-likelihood of each candidate: marker agreement, base qualities, consensus (if given)
    posterior    per-position base probabilities → known bases + a partial erasure mask (only uncertain bytes)
    trials       frames for the inner code: T0 all windows at their mask; T1 one window at a candidate;
                 T2 two windows at candidates. Each phase is decoded by the *unchanged* V4 inner RS + CRC.
    acceptance   a phase is accepted only if every verified trial decodes to the same frame (unanimity);
                 disagreement is ambiguity → the read is not accepted (fail closed).

Separation of responsibilities (docs/V5_PHASE3_INDEL_RECOVERY.md §4): this module finds synchronisation detail and
decides which symbols are known, which are hypotheses and which are erased. It never corrects a symbol by itself:
correction is the inner RS decoder's (2e + f ≤ r), integrity is the CRC-32's, and the outer code and container
SHA-256 still guard everything downstream. The decoder never sees simulator truth.

Every search is bounded by :class:`IndelRecoveryConfig`; a read whose windows exceed a bound keeps the V4
whole-segment erasure for that window (status UNRECOVERABLE).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from vnxdna.core.errors import VNXConfigurationError
from ...v4.frame import Layout, decode_frames, nt_to_bytes
from ...v4.sync import Projection

UNK = 4                      # unknown base (deleted position, or an N call)
PLAN_CHUNK = 512             # reads planned and searched together by the decoder (bounds plan memory per worker)

# ---------------------------------------------------------------- symbol-level representation (Phase 3 §9)
# Per frame nucleotide: the base, its posterior probability, a status and the evidence it came from.
STATUS_KNOWN = 0             # read base on an unaffected stretch (as V4)
STATUS_RECOVERED = 1         # inside an indel window, decided by local evidence (posterior ≥ keep threshold)
STATUS_CANDIDATE = 2         # inside a window, set by one hypothesis of a code-arbitration trial
STATUS_ERASED = 3            # unknown → an erasure for the inner code
STATUS_NAMES = ("known", "recovered", "candidate", "erased")
SOURCE_READ = 0
SOURCE_QUALITY = 1           # candidate weights shaped by base qualities
SOURCE_CONSENSUS = 2         # candidate weights shaped by other reads of the same address
SOURCE_CODE = 3              # hypothesis verified by inner RS + CRC
SOURCE_V4 = 4                # V4 rule: whole-segment erasure (window outside the bounds)
SOURCE_NAMES = ("read", "quality", "consensus", "code", "v4")
SYMBOL_DTYPE = np.dtype([("base", "u1"), ("conf", "f4"), ("status", "u1"), ("source", "u1")])

# window confidence (Phase 3 §3)
CONF_HIGH, CONF_MEDIUM, CONF_LOW, CONF_UNRECOVERABLE = "HIGH", "MEDIUM", "LOW", "UNRECOVERABLE"


@dataclass(frozen=True)
class IndelRecoveryConfig:
    """Bounds and decision thresholds. Every number is explained in docs/V5_PHASE3_INDEL_RECOVERY.md §5–§6."""

    max_shift: int = 1                 # |net shift| of a window handled by the local search (±1: one placement per position)
    max_window_segments: int = 2       # a window may span this many segments (merged across one unreliable marker)
    max_candidates: int = 64           # distinct candidate reconstructions kept per window (by posterior)
    max_trials: int = 1024             # inner-RS trials per read (all phases together)
    max_joint_windows: int = 2         # windows placed jointly in one trial (T2); 1 disables T2
    max_read_nt: int = 8192 + 64       # reads longer than this are not searched (they cannot align anyway)
    trial_batch: int = 8192            # frames per vectorised inner-RS call (bounds temporary memory)
    substitution_prior: float = 0.005  # p_sub for marker agreement; only the ordering 0 < p_sub ≪ 1/4 matters
    keep_byte_error: float = 1e-3      # keep an unverified byte only if P(wrong) < this (strict; 0.5 = RS-load optimum)
    high_confidence: float = 1e-3      # a window is HIGH if its best candidate class has posterior ≥ 1 − this
    false_accept_budget: float = 1e-9  # Σ over a read's trials of P(a wrong frame passes RS and CRC-32)
    phases: tuple = ("T0", "T1", "T2")  # trial phases in order; the first phase with a verified frame decides

    def validate(self) -> "IndelRecoveryConfig":
        checks = [
            (1 <= self.max_shift <= 2, "max_shift must be 1 or 2"),
            (1 <= self.max_window_segments <= 4, "max_window_segments must be in 1..4"),
            (1 <= self.max_candidates <= 4096, "max_candidates must be in 1..4096"),
            (0 <= self.max_trials <= 1 << 20, "max_trials must be in 0..2^20"),
            (1 <= self.max_joint_windows <= 2, "max_joint_windows must be 1 or 2"),
            (1 <= self.trial_batch <= 1 << 16, "trial_batch must be in 1..65536"),
            (0 < self.substitution_prior < 0.25, "substitution_prior must be in (0, 0.25)"),
            (0 < self.keep_byte_error <= 0.5, "keep_byte_error must be in (0, 0.5]"),
            (0 < self.high_confidence < 0.5, "high_confidence must be in (0, 0.5)"),
            (0 <= self.false_accept_budget <= 1e-3, "false_accept_budget must be in [0, 1e-3]"),
            (len(self.phases) >= 1 and all(p in ("T0", "T1", "T2") for p in self.phases), "phases must be from T0, T1, T2"),
        ]
        for ok, msg in checks:
            if not ok:
                raise VNXConfigurationError(msg)
        return self


# ---------------------------------------------------------------- geometry
class Geometry:
    """Template structure of a layout: markers, segments, frame index of every template position."""

    def __init__(self, layout: Layout):
        self.layout = layout
        tpl, frame_pos = layout.template()
        self.tpl = tpl.astype(np.int16)
        self.T = int(tpl.size)
        self.frame_pos = frame_pos.astype(np.int64)
        self.tmap = np.full(self.T, -1, dtype=np.int64)          # template position → frame index (−1: marker)
        self.tmap[self.frame_pos] = np.arange(layout.frame_nt)
        mk = np.flatnonzero(self.tpl >= 0)
        self.markers: list[tuple[int, int]] = []                  # (start, stop) template ranges
        if mk.size:
            starts = mk[np.concatenate([[True], np.diff(mk) > 1])]
            stops = mk[np.concatenate([np.diff(mk) > 1, [True]])] + 1
            self.markers = list(zip(starts.tolist(), stops.tolist()))
        period = layout.marker_period or layout.frame_nt
        self.seg_of_frame = np.arange(layout.frame_nt) // period
        self.n_bytes = layout.frame_bytes
        self.r = layout.inner_parity


@dataclass
class Window:
    ta: int                    # template interval [ta, tb)
    tb: int
    ra: int                    # read interval [ra, rb)
    rb: int
    active: bool               # the alignment placed an insertion or deletion inside
    n_segments: int
    status: str = ""           # HIGH / MEDIUM / LOW / UNRECOVERABLE (active windows only)
    reason: str = ""           # why a window is UNRECOVERABLE
    cands: np.ndarray | None = None      # (C, tb − ta) uint8, UNK where a base is unknown
    logw: np.ndarray | None = None       # (C,) normalised log posterior
    post: np.ndarray | None = None       # (tb − ta, 4) per-position base posterior
    source: int = SOURCE_READ

    @property
    def shift(self) -> int:
        return (self.rb - self.ra) - (self.tb - self.ta)


def find_windows(geom: Geometry, read: np.ndarray, rpos: np.ndarray, cfg: IndelRecoveryConfig) -> list[Window]:
    """Split an aligned read at its solid markers. ``rpos`` is the alignment path (template → read index, −1 deleted)."""
    T = geom.T
    L = int(read.size)
    cuts: list[tuple[int, int, int, int]] = []     # (template start, template stop, read start, read stop) of solid markers
    for t0, t1 in geom.markers:
        rp = rpos[t0:t1].astype(np.int64)
        if (rp < 0).any() or (np.diff(rp) != 1).any() or rp[-1] >= L:
            continue
        if not np.array_equal(read[rp], geom.tpl[t0:t1].astype(np.uint8)):
            continue
        cuts.append((t0, t1, int(rp[0]), int(rp[-1]) + 1))
    bounds = [(0, 0)] + [(c[0], c[2]) for c in cuts]          # window starts follow each cut
    ends = [(c[0], c[2]) for c in cuts] + [(T, L)]
    starts = [(0, 0)] + [(c[1], c[3]) for c in cuts]
    del bounds
    wins = []
    for (ta, ra), (tb, rb) in zip(starts, ends):
        if tb < ta or rb < ra:                       # cannot happen on a monotone path; defensive
            return []
        rp = rpos[ta:tb]
        aligned = rp[rp >= 0]
        active = (rb - ra) != (tb - ta) or aligned.size != (tb - ta) or aligned.size != (rb - ra)
        fr = geom.tmap[ta:tb]
        segs = np.unique(geom.seg_of_frame[fr[fr >= 0]]).size
        wins.append(Window(ta, tb, ra, rb, bool(active), int(segs)))
    return wins


# ---------------------------------------------------------------- candidates and scoring
def _phred_error(q: np.ndarray) -> np.ndarray:
    return np.clip(10.0 ** (-q.astype(np.float64) / 10.0), 1e-6, 0.75)


def _logsumexp(a: np.ndarray) -> float:
    m = float(a.max())
    return m + math.log(float(np.exp(a - m).sum()))


def score_window(geom: Geometry, win: Window, read: np.ndarray, qual: np.ndarray | None, cfg: IndelRecoveryConfig,
                 prior: np.ndarray | None = None) -> None:
    """Fill ``win.cands``, ``win.logw``, ``win.post`` and ``win.status`` for an active window.

    Model (docs §5): P(window read | placement c) ∝ Π_markers P(read base | marker base) × P(qualities | c) ×
    Π_frame Π_votes P(vote | base), with P(x | y) = 1 − p_sub if x = y else p_sub / 3 for markers and for the other
    reads' votes (pass 2), and an unknown base marginalised over A, C, G, T. Placements are a priori equally likely
    (the V4 channel is position-uniform).
    Candidates producing the same reconstruction are merged (their likelihoods add)."""
    shift = win.shift
    n = win.tb - win.ta
    if win.n_segments > cfg.max_window_segments:
        win.status, win.reason, win.source = CONF_UNRECOVERABLE, "window spans too many segments", SOURCE_V4
        return
    if shift == 0:
        win.status, win.reason, win.source = CONF_UNRECOVERABLE, "insertion and deletion cancel inside the window", SOURCE_V4
        return
    if abs(shift) > cfg.max_shift:
        win.status, win.reason, win.source = CONF_UNRECOVERABLE, f"net shift {shift} exceeds max_shift", SOURCE_V4
        return
    rw = np.minimum(read[win.ra:win.rb], UNK).astype(np.uint8)
    qw = None if qual is None else qual[win.ra:win.rb]
    group = None                 # contiguous candidate groups that produce the same reconstruction (merged below)
    if shift == -1:              # one template base missing: an unknown base at every template position k
        K = np.arange(n)[:, None]
        J = np.arange(n)[None, :]
        src = np.where(J < K, J, J - 1)
        cands = rw[np.clip(src, 0, max(rw.size - 1, 0))] if rw.size else np.zeros((n, n), np.uint8)
        cands = np.where(J == K, UNK, cands).astype(np.uint8)
        ll = np.zeros(n, dtype=np.float64)
    elif shift == 1:             # one extra read base: drop read base k (k = 0 … n)
        K = np.arange(n + 1)[:, None]
        J = np.arange(n)[None, :]
        cands = rw[np.where(J < K, J, J + 1)].astype(np.uint8)
        ll = np.zeros(n + 1, dtype=np.float64) if qw is None else np.log(_phred_error(qw) / (1.0 - _phred_error(qw)))
        # dropping any base of a homopolymer run gives the same reconstruction: runs are contiguous groups
        group = np.concatenate([[0], np.cumsum(rw[1:] != rw[:-1])])
    else:                        # |shift| == 2 (only with max_shift = 2): two placements, bounded below
        cand_list: list[np.ndarray] = []
        loglik: list[float] = []
        if shift == -2:
            for a in range(n):
                for b in range(a + 1, n):
                    c = np.full(n, UNK, dtype=np.uint8)
                    keep = np.ones(n, dtype=bool)
                    keep[[a, b]] = False
                    c[keep] = rw
                    cand_list.append(c)
                    loglik.append(0.0)
        else:
            qe = None if qw is None else _phred_error(qw)
            for a in range(n + 2):
                for b in range(a + 1, n + 2):
                    cand_list.append(np.delete(rw, [a, b]))
                    loglik.append(0.0 if qe is None else math.log(qe[a] / (1 - qe[a])) + math.log(qe[b] / (1 - qe[b])))
        if len(cand_list) > 16 * cfg.max_candidates:
            win.status, win.reason, win.source = CONF_UNRECOVERABLE, "too many two-indel placements", SOURCE_V4
            return
        cands = np.stack(cand_list)
        ll = np.asarray(loglik, dtype=np.float64)
    # marker agreement inside the window (markers that were not solid)
    tw = geom.tpl[win.ta:win.tb]
    mpos = np.flatnonzero(tw >= 0)
    if mpos.size:
        cm = cands[:, mpos]
        known = cm != UNK
        want = tw[mpos].astype(np.uint8)[None, :]
        mism = (known & (cm != want)).sum(axis=1)
        match = (known & (cm == want)).sum(axis=1)
        ps = cfg.substitution_prior
        ll = ll + mism * math.log(ps / 3.0) + match * math.log(1.0 - ps)
    source = SOURCE_READ if qw is None else SOURCE_QUALITY
    base_prior = np.full((n, 4), 0.25)
    if prior is not None:
        # consensus: (n, 4) vote weights from *other* reads. Each vote is a read of the true base through the same
        # per-base error model as the markers: P(vote = v | base = b) = 1 − p_sub if v = b, else p_sub / 3.
        ps = cfg.substitution_prior
        M = np.full((4, 4), math.log(ps / 3.0))
        np.fill_diagonal(M, math.log(1.0 - ps))
        ll_pos = prior.astype(np.float64) @ M                      # (n, 4): log P(votes | base b)
        lse = np.logaddexp.reduce(ll_pos + math.log(0.25), axis=1)   # unknown base: marginal over b
        lp = np.where(cands == UNK, lse[None, :], ll_pos[np.arange(n)[None, :], np.minimum(cands, 3)])
        ll = ll + lp.sum(axis=1)
        base_prior = np.exp(ll_pos - np.logaddexp.reduce(ll_pos, axis=1, keepdims=True))   # P(base | votes)
        if (prior.sum(axis=1) > 0).any():
            source = SOURCE_CONSENSUS
    # merge identical reconstructions (their likelihoods add)
    if group is not None:
        starts = np.flatnonzero(np.concatenate([[True], group[1:] != group[:-1]]))
        ll = np.logaddexp.reduceat(ll, starts)
        cands = cands[starts]
    elif cands.shape[0] > 1 and abs(shift) == 2:
        uniq, inv = np.unique(cands, axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        order_ = np.argsort(inv, kind="stable")
        bounds = np.flatnonzero(np.concatenate([[True], np.diff(inv[order_]) != 0]))
        ll = np.logaddexp.reduceat(ll[order_], bounds)
        cands = uniq
    order = np.argsort(-ll, kind="stable")[: cfg.max_candidates]
    cands, ll = cands[order], ll[order]
    logw = ll - _logsumexp(ll)
    w = np.exp(logw)
    onehot = (cands[:, :, None] == np.arange(4, dtype=np.uint8)[None, None, :]).astype(np.float64)
    post = np.einsum("c,cnb->nb", w, onehot) + np.einsum("c,cn->n", w, (cands == UNK).astype(np.float64))[:, None] * base_prior
    win.cands, win.logw, win.post, win.source = cands, logw, post, source
    top = float(w[0])
    win.status = CONF_HIGH if top >= 1.0 - cfg.high_confidence else (CONF_MEDIUM if top >= 0.5 else CONF_LOW)


# ---------------------------------------------------------------- read-level recovery
@dataclass
class ReadPlan:
    """Everything the trial phases need for one read (built once, no simulator truth)."""

    windows: list[Window]
    base_frame: np.ndarray          # (frame_nt,) uint8 bases outside/inside windows at their posterior mask
    base_erased: np.ndarray         # (frame_nt,) bool
    symbols: np.ndarray             # (frame_nt,) SYMBOL_DTYPE
    v4_erased: np.ndarray           # (frame_nt,) bool, the V4 projection mask (for accounting)
    searchable: list[int] = field(default_factory=list)   # indices of windows with candidates


def plan_read(geom: Geometry, read: np.ndarray, qual: np.ndarray | None, proj_bases: np.ndarray, proj_erased: np.ndarray,
              rpos: np.ndarray, cfg: IndelRecoveryConfig, priors: dict | None = None) -> ReadPlan:
    """Windows, candidates and the T0 (posterior-mask) frame for one aligned read.

    ``proj_bases``/``proj_erased`` are the V4 projection of this read; outside active windows they are kept as they
    are (V4 behaviour, including N / low-quality erasures). ``priors`` maps a window's template start to an (n, 4)
    consensus prior (pass 2 only)."""
    lay = geom.layout
    frame = proj_bases.copy()
    erased = proj_erased.copy()
    sym = np.zeros(lay.frame_nt, dtype=SYMBOL_DTYPE)
    sym["base"] = np.where(erased, UNK, frame)
    sym["conf"] = np.where(erased, 0.0, 1.0)
    sym["status"] = np.where(erased, STATUS_ERASED, STATUS_KNOWN)
    sym["source"] = SOURCE_READ
    wins = find_windows(geom, read, rpos, cfg) if read.size <= cfg.max_read_nt else []
    searchable = []
    for wi, w in enumerate(wins):
        if not w.active:
            continue
        fr = geom.tmap[w.ta:w.tb]
        fsel = fr >= 0
        fidx = fr[fsel]
        score_window(geom, w, read, qual, cfg, None if priors is None else priors.get(w.ta))
        if w.status == CONF_UNRECOVERABLE:
            # V4 rule for this window: every frame base of every segment it touches is erased
            segs = np.unique(geom.seg_of_frame[fidx])
            seg_mask = np.isin(geom.seg_of_frame, segs)
            erased[seg_mask] = True
            frame[seg_mask] = UNK
            sym["base"][seg_mask], sym["conf"][seg_mask] = UNK, 0.0
            sym["status"][seg_mask], sym["source"][seg_mask] = STATUS_ERASED, SOURCE_V4
            continue
        searchable.append(wi)
        best = w.post.argmax(axis=1).astype(np.uint8)[fsel]
        conf = w.post.max(axis=1)[fsel]
        frame[fidx] = best
        sym["base"][fidx] = best
        sym["conf"][fidx] = conf
        sym["status"][fidx] = STATUS_RECOVERED
        sym["source"][fidx] = w.source
        # byte decision: erase a byte unless P(all its bases right) > 1 − keep_byte_error (bases treated independently)
        b_of = fidx // 4
        for b in np.unique(b_of).tolist():
            p_ok = float(np.prod(conf[b_of == b]))
            if 1.0 - p_ok >= cfg.keep_byte_error:
                cols = np.arange(4 * b, 4 * b + 4)
                erased[cols] = True
                sym["status"][cols] = STATUS_ERASED
            else:
                erased[4 * b:4 * b + 4] = False
    # bytes of N / low-quality calls stay erased as in V4 (proj_erased outside windows)
    return ReadPlan(wins, frame, erased, sym, proj_erased.copy(), searchable)


def _false_accept(n_bytes: int, r: int, f: int) -> float:
    """P(a random wrong frame with f erasures passes bounded-distance RS and CRC-32) — the per-trial integrity cost.

    With f erasures the code behaves as RS(n − f, n − r) with t' = ⌊(r − f)/2⌋; a random word lies within t' of
    a codeword with probability V(n − f, t') / 256^(r − f), V(N, t) = Σ_{i ≤ t} C(N, i)·255^i. CRC-32 then passes
    a wrong frame with probability 2^−32."""
    if f > r:
        return 0.0
    t = (r - f) // 2
    vol = sum(math.comb(n_bytes - f, i) * 255 ** i for i in range(t + 1))
    return min(1.0, vol / 256.0 ** (r - f)) * 2.0 ** -32


def _window_patches(geom: Geometry, plan: ReadPlan, wi: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """For one searchable window: (frame indices, candidate bases (C, m), window byte indices, candidate byte erasures (C, k))."""
    cache = plan.__dict__.setdefault("_patch", {})
    hit = cache.get(wi)
    if hit is None:
        w = plan.windows[wi]
        fr = geom.tmap[w.ta:w.tb]
        fsel = fr >= 0
        fidx = fr[fsel]
        rows = w.cands[:, fsel]
        bidx = np.unique(fidx // 4)
        pos = np.searchsorted(bidx, fidx // 4)
        er = np.zeros((rows.shape[0], bidx.size), dtype=bool)
        cu, cp = np.nonzero(rows == UNK)
        er[cu, pos[cp]] = True
        hit = cache[wi] = (fidx, np.minimum(rows, 3).astype(np.uint8), bidx, er)
    return hit


def _phase_specs(geom: Geometry, plan: ReadPlan, phase: str, cfg: IndelRecoveryConfig):
    """Trials of one phase for one read, without building frames: (groups, combos, erasure counts).

    groups: tuples of window indices placed jointly; combos: (N, 2) [group id, flat combination index]; f: (N,) erased
    bytes of each trial (computed from the patches, so selection needs no frame)."""
    base_b = plan.base_erased.reshape(-1, 4).any(axis=1)
    s = plan.searchable
    if phase == "T0":
        return [()], np.zeros((1, 2), dtype=np.int64), np.array([int(base_b.sum())])
    if phase == "T1":
        groups = [(wi,) for wi in s]
    elif phase == "T2" and cfg.max_joint_windows >= 2:
        groups = [(s[a], s[b]) for a in range(len(s)) for b in range(a + 1, len(s))]
    else:
        groups = []
    combos, fs = [], []
    base_total = int(base_b.sum())
    for gi, g in enumerate(groups):
        patches = [_window_patches(geom, plan, wi) for wi in g]
        f = np.array([base_total], dtype=np.int64)
        for fidx, cb, bidx, cer in patches:
            f = (f[:, None] - int(base_b[bidx].sum()) + cer.sum(axis=1)[None, :]).reshape(-1)
        combos.append(np.stack([np.full(f.size, gi), np.arange(f.size)], axis=1))
        fs.append(f)
    if not combos:
        return groups, np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int64)
    return groups, np.concatenate(combos), np.concatenate(fs)


def _build_trials(geom: Geometry, plan: ReadPlan, groups: list, combos: np.ndarray) -> tuple[np.ndarray, np.ndarray, list]:
    """Frames, byte erasures and assignments for the selected trials only."""
    base_f = plan.base_frame
    base_b = plan.base_erased.reshape(-1, 4).any(axis=1)
    n = combos.shape[0]
    frames = np.repeat(base_f[None, :], n, axis=0)
    ers = np.repeat(base_b[None, :], n, axis=0)
    assigns: list = [None] * n
    for gi in np.unique(combos[:, 0]).tolist():
        rows = np.flatnonzero(combos[:, 0] == gi)
        g = groups[gi]
        if not g:
            for k in rows.tolist():
                assigns[k] = {}
            continue
        patches = [_window_patches(geom, plan, wi) for wi in g]
        sizes = [pt[1].shape[0] for pt in patches]
        idx = np.stack(np.unravel_index(combos[rows, 1], sizes))          # (len(g), m) candidate per window
        for (fidx, cb, bidx, cer), ci in zip(patches, idx):
            frames[np.ix_(rows, fidx)] = cb[ci]
            ers[np.ix_(rows, bidx)] = cer[ci]
        for k, col in zip(rows.tolist(), idx.T.tolist()):
            assigns[k] = {wi: int(c) for wi, c in zip(g, col)}
    return frames, ers, assigns


@dataclass
class ReadOutcome:
    accepted: bool = False
    phase: str = "none"            # T0 | T1 | T2 | ambiguous | none
    fields: tuple = ()             # (kind, tag, group, symbol)
    payload: np.ndarray | None = None
    trials: int = 0
    false_accept_bound: float = 0.0
    erased_nt: int = 0             # 4 × erased bytes in the accepted trial (else in T0)
    erased_bytes: int = 0
    rs_errata: int = 0
    window_status: list = field(default_factory=list)
    localized_by_code: list = field(default_factory=list)    # window indices placed by a verified hypothesis
    erased_byte_mask: np.ndarray | None = None                # (n_bytes,) erasures of the accepted trial (else T0)


def recover_batch(geom: Geometry, plans: list[ReadPlan], cfg: IndelRecoveryConfig) -> list[ReadOutcome]:
    """Run the trial phases for many reads; inner RS calls are vectorised across reads and trials.

    Within a phase a read's trials are ordered by erasure count (fewest first, then candidate order) and taken while
    the read's false-acceptance budget and ``max_trials`` allow; a trial with more than r erasures is never run."""
    lay = geom.layout
    nb, r = geom.n_bytes, geom.r
    fa_cost = np.array([_false_accept(nb, r, f) for f in range(nb + 1)])
    outs = [ReadOutcome(window_status=[w.status for w in p.windows if w.active]) for p in plans]
    budget = [cfg.false_accept_budget for _ in plans]
    for phase in cfg.phases:
        verified: dict[int, list] = {}
        buf_f: list = []
        buf_e: list = []
        buf_own: list = []
        buf_assign: list = []
        buffered = 0

        def flush() -> None:
            nonlocal buf_f, buf_e, buf_own, buf_assign, buffered
            if not buffered:
                return
            frames = np.concatenate(buf_f)
            ers = np.concatenate(buf_e)
            own = np.concatenate(buf_own)
            P = decode_frames(lay, nt_to_bytes(frames), ers, errors_only_retry=False)
            for k in np.flatnonzero(P.ok).tolist():
                key = (int(P.kind[k]), int(P.tag[k]), int(P.group[k]), int(P.symbol[k]))
                verified.setdefault(int(own[k]), []).append((key, P.payload[k].copy(), buf_assign[k], int(P.errata[k]),
                                                             int(ers[k].sum()), ers[k].copy()))
            buf_f, buf_e, buf_own, buf_assign, buffered = [], [], [], [], 0

        for i, plan in enumerate(plans):
            o = outs[i]
            if o.phase != "none" or o.trials >= cfg.max_trials:
                continue
            groups, combos, f = _phase_specs(geom, plan, phase, cfg)
            if not f.size:
                continue
            ok = f <= r
            order = np.flatnonzero(ok)[np.argsort(f[ok], kind="stable")]
            cost = fa_cost[f[order]]
            take = order[np.cumsum(cost) <= budget[i]][: cfg.max_trials - o.trials]
            if not take.size:
                continue
            spent = float(fa_cost[f[take]].sum())
            budget[i] -= spent
            o.false_accept_bound += spent
            o.trials += int(take.size)
            # temporary memory: at most trial_batch + max_trials frames are held at any time
            for c0 in range(0, take.size, cfg.trial_batch):
                part = take[c0:c0 + cfg.trial_batch]
                fr, eb, assigns = _build_trials(geom, plan, groups, combos[part])
                buf_f.append(fr)
                buf_e.append(eb)
                buf_own.append(np.full(part.size, i, dtype=np.int64))
                buf_assign.extend(assigns)
                buffered += part.size
                if buffered >= cfg.trial_batch:
                    flush()
        flush()
        for i, hits in verified.items():
            first_key, first_pay = hits[0][0], hits[0][1]
            same = all(h[0] == first_key and np.array_equal(h[1], first_pay) for h in hits)
            o = outs[i]
            if not same:
                o.phase = "ambiguous"          # two hypotheses verify to different frames: never choose
                continue
            o.accepted, o.phase, o.fields, o.payload = True, phase, first_key, first_pay
            best = min(hits, key=lambda h: h[4])          # accounting: the fewest erasures among verified trials
            o.erased_bytes, o.rs_errata = best[4], best[3]
            o.erased_nt = 4 * best[4]
            o.localized_by_code = sorted(best[2])
            o.erased_byte_mask = best[5]
    for i, o in enumerate(outs):
        if not o.accepted:
            m = plans[i].base_erased.reshape(-1, 4).any(axis=1)
            o.erased_bytes, o.erased_nt, o.erased_byte_mask = int(m.sum()), 4 * int(m.sum()), m
    return outs


def plan_reads(geom: Geometry, reads: list, quals: list | None, proj: Projection, rpos: np.ndarray, rows: np.ndarray,
               cfg: IndelRecoveryConfig) -> list[ReadPlan]:
    """Plans for ``rows`` of an aligned batch (rows index ``reads``, ``proj`` and ``rpos``)."""
    plans = []
    for i in rows.tolist():
        q = None if quals is None else quals[i]
        plans.append(plan_read(geom, np.asarray(reads[i]), None if q is None else np.asarray(q), proj.bases[i], proj.erased[i],
                               rpos[i], cfg))
    return plans


def summarize_outcomes(plans: list[ReadPlan], outs: list[ReadOutcome], v4_erased: np.ndarray) -> dict:
    """Counters for the decoder report (no truth): attempts, phases, window confidence, trials, erasures V4 vs V5."""
    c: dict = {"attempted": len(outs)}
    def add(k, v=1):
        c[k] = c.get(k, 0) + v
    for p, o, v4e in zip(plans, outs, v4_erased):
        add(f"phase_{o.phase}")
        add("trials", o.trials)
        add("false_accept_bound", o.false_accept_bound)
        for st in o.window_status:
            add(f"windows_{st}")
        if o.accepted:
            add("recovered")
            add("v4_erased_nt_recovered_reads", int(v4e.sum()))
            add("v5_erased_nt_recovered_reads", o.erased_nt)
            add("windows_localized_by_code", len(o.localized_by_code))
    return c
