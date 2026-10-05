"""V6 resilient outer code: stripes of row groups protected by column-parity groups, plus cross-group interleaving.

Row code (unchanged V4): group g is a systematic Cauchy RS codeword of K data + M parity symbols over GF(2^8).
The last data group may be short (k_g < K source symbols, shortened code).

V6 adds, opt-in (superblock version 2, docs/V6_OUTER_CODE.md):

* **Stripes.** Data groups are cut into S = ⌈G / D⌉ *balanced* stripes of consecutive groups: stripe s holds data
  groups [⌊s·G / S⌋, ⌊(s+1)·G / S⌋), so stripe sizes differ by at most one and none is much shorter than D (a short
  last stripe would be the weak point for bursts and for the column code). For each stripe, Mc *column-parity groups* are appended (group indices
  G + s·Mc … G + s·Mc + Mc − 1). Each column-parity group is a full row codeword (K + M symbols).
* **Full-position view.** Every row is viewed as a full K + M codeword: position j < K is data symbol j,
  position K + i is parity symbol i. A short group's transmitted symbol s maps to position s (s < k_g) or
  K + s − k_g (parity); positions k_g … K − 1 are known zeros. A stripe with d < D data rows uses rows d … D − 1
  as known all-zero rows (shortened column code).
* **Column code.** For every position j, the D data rows and Mc column-parity rows of a stripe form a Cauchy RS
  (D, Mc) codeword (the shortened code for a short stripe). The row and column codes commute (both are linear
  maps over GF(2^8) acting on different axes), so the column-parity rows are row codewords too: a product code.
* **Decoding.** Iterative erasure decoding on verified symbols: a row with ≥ K known positions is decoded and
  re-encoded; a column with ≥ D known rows is decoded and re-encoded; repeat until no progress. Erasure decoding
  of an MDS code never invents a symbol: the result is a function of verified symbols only, and the container
  SHA-256 check remains the final arbiter.
* **Strand order.** ``sequential`` keeps the V4 group-major order (column-parity groups after their stripe's data
  groups, superblock strands first). ``interleaved`` emits each stripe position-major (position 0 of every row of
  the stripe, then position 1, …) and spreads the superblock strands evenly over the whole file, so a contiguous
  run of lost strands (a burst in synthesis/pool order) costs each row of a stripe about L / R symbols instead of
  wiping whole groups.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..ecc.cauchy import CauchyErasureCode
from ..v2.encoder import cauchy_parity
from vnxdna.core.errors import V6ConfigurationError

ORDERS = ("sequential", "interleaved")


@dataclass(frozen=True)
class Geometry:
    """Outer-code geometry of one archive (everything the superblock fixes)."""
    K: int                      # data symbols per full row
    M: int                      # row parity symbols
    D: int                      # data rows per stripe (≥ 1)
    Mc: int                     # column-parity rows per stripe (0 = no column code)
    P: int                      # payload bytes per symbol
    container_size: int
    order: str = "sequential"

    def validate(self) -> "Geometry":
        if not (1 <= self.K and 0 <= self.M and self.K + self.M <= 256):
            raise V6ConfigurationError("row code needs 1 <= K, 0 <= M, K + M <= 256")
        if not (1 <= self.D <= 65535 and 0 <= self.Mc <= 255):
            raise V6ConfigurationError("stripe depth D must be in 1..65535 and column parity Mc in 0..255")
        if self.Mc and self.D + self.Mc > 256:
            raise V6ConfigurationError("column code needs D + Mc <= 256")
        if self.order not in ORDERS:
            raise V6ConfigurationError(f"strand order must be one of {ORDERS}")
        if self.container_size < 1 or self.P < 1:
            raise V6ConfigurationError("container size and payload must be positive")
        return self

    # ---------------------------------------------------------------- counts
    @property
    def n(self) -> int:
        return self.K + self.M

    @property
    def G(self) -> int:
        """Data groups."""
        return -(-self.container_size // (self.K * self.P))

    @property
    def stripes(self) -> int:
        return -(-self.G // self.D)

    @property
    def parity_groups(self) -> int:
        return self.stripes * self.Mc

    @property
    def total_groups(self) -> int:
        return self.G + self.parity_groups

    def k_of(self, g: int) -> int:
        """Source symbols of row g (data rows may be short; column-parity rows are full)."""
        if g >= self.G:
            return self.K
        if g < self.G - 1:
            return self.K
        rem = self.container_size - (self.G - 1) * self.K * self.P
        return -(-rem // self.P)

    def symbols_of(self, g: int) -> int:
        return self.k_of(g) + self.M

    def data_strands(self) -> int:
        return -(-self.container_size // self.P)

    def strands(self) -> int:
        """Data + row parity + column-parity strands (superblock excluded)."""
        return self.data_strands() + self.G * self.M + self.parity_groups * self.n

    def overhead(self) -> float:
        """Redundant strands per data strand (superblock excluded)."""
        return self.strands() / self.data_strands() - 1.0

    # ---------------------------------------------------------------- stripe structure
    def stripe_start(self, s: int) -> int:
        """First data group of stripe s (balanced stripes)."""
        return (s * self.G) // self.stripes

    def stripe_of(self, g: int) -> int:
        if g >= self.G:
            return (g - self.G) // max(1, self.Mc)
        s = (g * self.stripes) // self.G
        while s + 1 < self.stripes and self.stripe_start(s + 1) <= g:
            s += 1
        while self.stripe_start(s) > g:
            s -= 1
        return s

    def stripe_rows(self, s: int) -> tuple[list[int], list[int]]:
        """(data groups, column-parity groups) of stripe s."""
        data = list(range(self.stripe_start(s), self.stripe_start(s + 1)))
        par = list(range(self.G + s * self.Mc, self.G + (s + 1) * self.Mc))
        return data, par

    def position(self, g: int, s: int) -> int:
        """Full position of transmitted symbol s of row g."""
        k = self.k_of(g)
        return s if s < k else self.K + s - k

    def transmitted(self, g: int, pos: int) -> int | None:
        """Transmitted symbol index of full position pos of row g (None for a known-zero padding position)."""
        k = self.k_of(g)
        if pos < k:
            return pos
        if pos < self.K:
            return None
        return k + pos - self.K

    # ---------------------------------------------------------------- strand order
    def order_keys(self) -> np.ndarray:
        """(N, 2) array of (group, transmitted symbol) for every non-superblock strand, in file order."""
        out = []
        for s in range(self.stripes):
            data, par = self.stripe_rows(s)
            rows = data + par
            if self.order == "sequential":
                for g in rows:
                    n = self.symbols_of(g)
                    out.append(np.stack([np.full(n, g), np.arange(n)], axis=1))
            else:
                for pos in range(self.n):
                    for g in rows:
                        t = self.transmitted(g, pos)
                        if t is not None:
                            out.append(np.array([[g, t]]))
        return np.concatenate(out).astype(np.int64) if out else np.zeros((0, 2), dtype=np.int64)

    def stripe_order(self, s: int) -> list[tuple[int, int]]:
        """(group, transmitted symbol) of stripe s in file order."""
        data, par = self.stripe_rows(s)
        rows = data + par
        if self.order == "sequential":
            return [(g, t) for g in rows for t in range(self.symbols_of(g))]
        out = []
        for pos in range(self.n):
            for g in rows:
                t = self.transmitted(g, pos)
                if t is not None:
                    out.append((g, t))
        return out

    def superblock_slots(self, n_super: int, n_data: int) -> list[int]:
        """Number of data strands written before each superblock strand (non-decreasing)."""
        if self.order == "sequential":
            return [0] * n_super
        return [((2 * i + 1) * n_data) // (2 * n_super) for i in range(n_super)]

    def to_dict(self) -> dict:
        return {"K": self.K, "M": self.M, "D": self.D, "Mc": self.Mc, "P": self.P, "order": self.order,
                "container_size": self.container_size, "groups": self.G, "stripes": self.stripes,
                "column_parity_groups": self.parity_groups, "strands": self.strands(), "overhead": round(self.overhead(), 6)}


# ============================================================================ encoding
_CODES: dict[tuple[int, int], CauchyErasureCode] = {}


def code(k: int, m: int) -> CauchyErasureCode:
    c = _CODES.get((k, m))
    if c is None:
        c = _CODES[(k, m)] = CauchyErasureCode(k, m)
    return c


def row_codewords(data: np.ndarray, K: int, M: int) -> np.ndarray:
    """(R, K, P) zero-padded data rows → (R, K + M, P) full-position row codewords."""
    data = np.ascontiguousarray(data, dtype=np.uint8)
    return np.concatenate([data, cauchy_parity(code(K, M), data)], axis=1)


def column_parity_rows(data: np.ndarray, D: int, Mc: int) -> np.ndarray:
    """(d, K, P) data rows of one stripe (d ≤ D, zero-padded to K) → (Mc, K, P) column-parity data rows."""
    d, K, P = data.shape
    full = np.zeros((D, K * P), dtype=np.uint8)
    full[:d] = data.reshape(d, K * P)
    return cauchy_parity(code(D, Mc), full[None])[0].reshape(Mc, K, P)


# ============================================================================ iterative stripe decoding
@dataclass
class StripeStats:
    row_decodes: int = 0
    column_decodes: int = 0
    iterations: int = 0


def decode_stripe(cw: np.ndarray, known: np.ndarray, K: int, M: int, D: int, Mc: int,
                  stats: StripeStats | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Iterative row/column erasure decoding of one stripe.

    ``cw``: (D + Mc, K + M, P) full-position symbols (unknown entries ignored); ``known``: (D + Mc, K + M) bool.
    Rows 0 … D − 1 are data rows, D … D + Mc − 1 column-parity rows; padding positions/rows must be passed as known
    zeros. Returns updated (cw, known); entries become known only through MDS erasure decoding of known entries.
    """
    cw = np.array(cw, dtype=np.uint8, copy=True)
    known = np.array(known, dtype=bool, copy=True)
    st = stats if stats is not None else StripeStats()
    n = K + M
    rows_total = D + Mc
    while True:
        st.iterations += 1
        progress = False
        cnt = known.sum(axis=1)
        rows = np.flatnonzero((cnt >= K) & (cnt < n))
        if rows.size:
            data = code(K, M).decode(cw[rows], known[rows])
            cw[rows] = row_codewords(data, K, M)
            known[rows] = True
            st.row_decodes += int(rows.size)
            progress = True
        if Mc:
            ccnt = known.sum(axis=0)
            cols = np.flatnonzero((ccnt >= D) & (ccnt < rows_total))
            if cols.size:
                shards = np.ascontiguousarray(cw[:, cols, :].transpose(1, 0, 2))      # (ncols, D + Mc, P)
                present = np.ascontiguousarray(known[:, cols].T)
                data = code(D, Mc).decode(shards, present)                           # (ncols, D, P)
                par = cauchy_parity(code(D, Mc), data)                                # (ncols, Mc, P)
                cw[:D, cols, :] = data.transpose(1, 0, 2)
                cw[D:, cols, :] = par.transpose(1, 0, 2)
                known[:, cols] = True
                st.column_decodes += int(cols.size)
                progress = True
        if not progress or known.all():
            return cw, known


def structural_decode(known: np.ndarray, K: int, M: int, D: int, Mc: int) -> np.ndarray:
    """The same iteration as :func:`decode_stripe` on erasure patterns only: (B, D + Mc, K + M) bool → final known.

    Used by the planner and the large-archive erasure-pattern simulations; tests check that it predicts the real
    decoder's outcome exactly.
    """
    known = np.array(known, dtype=bool, copy=True)
    while True:
        before = int(known.sum())
        cnt = known.sum(axis=2, keepdims=True)
        known |= cnt >= K
        if Mc:
            ccnt = known.sum(axis=1, keepdims=True)
            known |= ccnt >= D
        if int(known.sum()) == before:
            return known


def stripe_padding(geo: Geometry, s: int) -> np.ndarray:
    """(D + Mc, K + M) bool: positions of stripe s that are known zeros (short last group, absent data rows)."""
    pad = np.zeros((geo.D + geo.Mc, geo.n), dtype=bool)
    data, _ = geo.stripe_rows(s)
    for r in range(len(data), geo.D):
        pad[r] = True
    for r, g in enumerate(data):
        k = geo.k_of(g)
        pad[r, k:geo.K] = True
    return pad


# ============================================================================ analytic estimates and planner
def _binom_cdf(n: int, k: int, p: float) -> float:
    """P(Binomial(n, p) ≤ k), stable for the small n used here."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    total = 0.0
    lp, lq = math.log(p) if p > 0 else -math.inf, math.log1p(-p) if p < 1 else -math.inf
    for i in range(k + 1):
        term = math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
        term += (i * lp if i else 0.0) + ((n - i) * lq if n - i else 0.0)
        total += math.exp(term)
    return min(1.0, total)


def failure_bound(geo: Geometry, p: float) -> float:
    """Upper bound on P(archive not decodable) under i.i.d. strand loss with probability p (superblock excluded).

    A row fails row-wise with probability q = P(more than M of its symbols lost). A stripe certainly decodes if at
    most Mc of its rows fail row-wise (every column then has ≥ D known rows). Rows are independent under i.i.d. loss,
    so P(stripe fails) ≤ P(more than Mc of its R rows fail); the bound ignores what further iterations recover.
    """
    if p <= 0:
        return 0.0
    log_ok = 0.0
    memo: dict[tuple, float] = {}
    for s in range(geo.stripes):
        data, par = geo.stripe_rows(s)
        shape = tuple(geo.symbols_of(g) for g in data + par)
        if shape in memo:
            ok = memo[shape]
            if ok <= 0:
                return 1.0
            log_ok += math.log(ok)
            continue
        qs = [1.0 - _binom_cdf(n_, geo.M, p) for n_ in shape]
        if geo.Mc == 0:
            ok = math.prod(1.0 - q for q in qs)
        else:
            # Poisson-binomial tail via DP over rows
            dist = np.zeros(len(qs) + 1)
            dist[0] = 1.0
            for q in qs:
                dist[1:] = dist[1:] * (1 - q) + dist[:-1] * q
                dist[0] *= 1 - q
            ok = float(dist[: geo.Mc + 1].sum())
        memo[shape] = ok
        if ok <= 0:
            return 1.0
        log_ok += math.log(ok)
    return 1.0 - math.exp(log_ok)


def dropout_threshold(geo: Geometry, epsilon: float = 1e-3) -> float:
    """Largest i.i.d. strand-loss rate p with failure_bound(p) ≤ epsilon (bisection, 1e-4 resolution)."""
    lo, hi = 0.0, 0.95
    if failure_bound(geo, hi) <= epsilon:
        return hi
    while hi - lo > 1e-4:
        mid = (lo + hi) / 2
        if failure_bound(geo, mid) <= epsilon:
            lo = mid
        else:
            hi = mid
    return lo


def _stripe_template(geo: Geometry, s: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(row, position) of every strand of stripe s in file order, and the stripe's padding mask."""
    order = geo.stripe_order(s)
    data, par = geo.stripe_rows(s)
    index = {g: r for r, g in enumerate(data)}
    index.update({g: geo.D + i for i, g in enumerate(par)})
    rows = np.array([index[g] for g, _ in order], dtype=np.int64)
    pos = np.array([geo.position(g, t) for g, t in order], dtype=np.int64)
    return rows, pos, stripe_padding(geo, s)


def _windows_ok(geo: Geometry, rows: np.ndarray, pos: np.ndarray, pad: np.ndarray, starts, L: int) -> bool:
    """Every burst [st, st + L) ∩ stripe (st in starts, clipped to the stripe) leaves all data rows decodable."""
    starts = list(starts)
    n = len(rows)
    if geo.Mc == 0:
        # rows only: a row decodes iff it loses at most M of its transmitted symbols (no iteration to model)
        data_rows = rows < geo.D
        for st in starts:
            lo, hi = max(0, st), min(n, st + L)
            lost = rows[lo:hi][data_rows[lo:hi]]
            if lost.size and np.bincount(lost).max() > geo.M:
                return False
        return True
    for c0 in range(0, len(starts), 256):
        chunk = starts[c0:c0 + 256]
        known = np.broadcast_to(~np.zeros_like(pad), (len(chunk), *pad.shape)).copy()
        for b, st in enumerate(chunk):
            lo, hi = max(0, st), min(n, st + L)
            known[b, rows[lo:hi], pos[lo:hi]] = False
        known |= pad[None]
        out = structural_decode(known, geo.K, geo.M, geo.D, geo.Mc)
        if not out[:, : geo.D].all():
            return False
    return True


def burst_tolerance(geo: Geometry) -> int:
    """Longest contiguous run of lost strands (file order, any start, superblock strands excluded) that always decodes.

    Rows of a full stripe are interchangeable (MDS row and column codes, no padding), so shifting a burst by one
    period (R rows in interleaved order, one row of n symbols in sequential order) gives an equivalent erasure
    pattern: one period of starts per full stripe is exhaustive. The last (possibly short) stripe is checked at every
    start. A burst that crosses a stripe boundary is a suffix of one stripe plus a prefix of the next; both are
    monotone in length, so it is covered by checking every suffix/prefix split. Exact under these definitions.
    """
    S = geo.stripes

    def shape(s: int) -> tuple[int, bool]:
        data, _ = geo.stripe_rows(s)
        return len(data), geo.G - 1 in data

    reps: dict[tuple[int, bool], int] = {}
    for s in range(S):
        reps.setdefault(shape(s), s)
    temps = {key: _stripe_template(geo, s) for key, s in reps.items()}

    def longest(t, suffix: bool) -> int:
        rows, pos, pad = t
        lo, hi = 0, len(rows) + 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if _windows_ok(geo, rows, pos, pad, [len(rows) - mid if suffix else 0], mid):
                lo = mid
            else:
                hi = mid
        return lo

    pre = {key: longest(t, False) for key, t in temps.items()}
    suf = {key: longest(t, True) for key, t in temps.items()}
    straddle = min((min(suf[shape(s)], pre[shape(s + 1)]) for s in range(S - 1)), default=None)

    def survives(L: int) -> bool:
        for key, (rows, pos, pad) in temps.items():
            n = len(rows)
            if L > n:
                return False
            d, has_short = key
            period = (d + geo.Mc) if geo.order == "interleaved" else geo.n
            short_row = has_short and geo.k_of(geo.G - 1) < geo.K
            inside = range(0, n - L + 1) if short_row else range(0, min(period, n - L + 1))
            if not _windows_ok(geo, rows, pos, pad, inside, L):
                return False
        # a burst crossing a stripe boundary loses a suffix a of one stripe and a prefix L − a of the next, 1 ≤ a < L
        return straddle is None or L - 1 <= straddle

    hi_n = max(len(t[0]) for t in temps.values()) + 1
    lo, hi = 0, hi_n
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if survives(mid):
            lo = mid
        else:
            hi = mid
    return lo


@dataclass(frozen=True)
class Plan:
    geometry: Geometry
    threshold: float            # dropout_threshold at the planner's epsilon
    burst: int | None           # burst_tolerance (None if not evaluated)
    candidates: int

    def to_dict(self) -> dict:
        return {"geometry": self.geometry.to_dict(), "iid_dropout_threshold_bound": round(self.threshold, 4),
                "burst_tolerance_strands": self.burst, "candidates_evaluated": self.candidates}


ROW_SIZES = (64, 96, 128, 160, 192, 224, 255)
STRIPE_DEPTHS = (16, 32, 64, 128)
COLUMN_PARITY = (0, 1, 2, 3, 4)


def plan(container_size: int, P: int, budget: float, *, order: str = "interleaved", epsilon: float = 1e-3,
         row_sizes=ROW_SIZES, depths=STRIPE_DEPTHS, column_parity=COLUMN_PARITY) -> Plan:
    """Adaptive geometry: maximise the analytic i.i.d. dropout threshold subject to overhead ≤ budget.

    For each row length n and stripe shape (D, Mc), the largest M whose total overhead (row parity on the real,
    possibly short, last group plus column-parity rows) fits the budget is used. Ties break towards fewer
    column-parity rows and then shorter rows (cheaper decoding). Deterministic: no randomness.
    """
    if not 0 < budget < 4:
        raise V6ConfigurationError("redundancy budget must be in (0, 4)")
    best: tuple | None = None
    count = 0
    for n in row_sizes:
        for mc in column_parity:
            for d in (depths if mc else (max(depths),)):
                if mc and d + mc > 256:
                    continue
                geo = None
                for m in range(n - 1, 0, -1):
                    g = Geometry(n - m, m, d, mc, P, container_size, order)
                    try:
                        g.validate()
                    except V6ConfigurationError:
                        continue
                    if g.overhead() <= budget + 1e-12:
                        geo = g
                        break
                if geo is None:
                    continue
                # do not use stripes deeper than the archive (identical code, wasted column parity otherwise)
                if mc and geo.D > geo.G and geo.D != min(d for d in depths):
                    continue
                count += 1
                thr = dropout_threshold(geo, epsilon)
                key = (round(thr, 4), -mc, -n)
                if best is None or key > best[0]:
                    best = (key, geo, thr)
    if best is None:
        raise V6ConfigurationError(f"no outer geometry fits the redundancy budget {budget}")
    return Plan(best[1], best[2], None, count)
