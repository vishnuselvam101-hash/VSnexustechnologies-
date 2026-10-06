"""Full-template consensus polish (``ClusterConfig.consensus_template="full"``, EXPERIMENTAL, opt-in).

The reference rounds (:mod:`vnxdna.recovery.cluster.consensus`) align every read to a template whose undecided frame
positions are cost-0 wildcards, so a read's indel can slide anywhere between two markers and the read's bases around it
are erased or, for a compensating insertion/deletion pair, called one position off. The polish instead keeps a *full*
template (a base at every frame position) and edits it to lower the summed optimal alignment cost of the cluster's
reads (substitution ``c_sub``, insertion/deletion ``c_indel``, markers ``marker_mismatch``):

* one banded forward pass F and backward pass G per read give, exactly, the summed cost of every single substitution,
  template-base deletion and template-base insertion (F[i] + edit + G[i+1]);
* the template length between two markers is fixed by the layout, so indels are moved, not added: per marker-delimited
  segment the best deletions and insertions are paired into length-preserving *shift* moves, and those are scored
  exactly by aligning every read to the edited template;
* per segment and round the move with the lowest cost (a shift, or the set of improving substitutions) is applied when
  it lowers the summed cost. Markers never change; they anchor every alignment.

The decision is unchanged: the reads' certain calls against the polished template, :func:`consensus.vote`
(``vote_share``, ``min_votes``). No quality values are used. The result does not depend on batching.
"""
from __future__ import annotations

import numpy as np

INF = np.int32(1 << 28)
PAIRS_PER_SIDE = 3          # deletions × insertions paired per hot segment (exactly scored)


def fg(tpl: np.ndarray, mc: np.ndarray, reads: list, band: np.ndarray, c_indel: int):
    """Banded forward/backward cost tables of reads against per-read templates (same conventions as ``consensus._fb``).

    Returns F, G of shape (T + 1, W, n) (diagonal w = j − i + B; INF outside the band), the read bases ``Rp`` padded by
    B columns, the band half-width B and the optimal costs (INF when a read does not fit its band)."""
    n, T = tpl.shape
    lens = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    B = int(band.max(initial=0))
    W = 2 * B + 1
    width = T + B + 2
    Rp = np.full((n, width + 2 * B + 1), 5, dtype=np.int16)
    for k, r in enumerate(reads):
        Rp[k, B: B + min(r.size, width)] = r[:width]
    tpl = tpl.astype(np.int16)
    mc = mc.astype(np.int32)
    rows = np.arange(n)
    dvec = np.arange(-B, B + 1, dtype=np.int64)[:, None]
    bandok = np.abs(dvec) <= band[None, :]
    ramp = (np.arange(W, dtype=np.int32) * c_indel)[:, None]

    def sub_at(t: int) -> tuple[np.ndarray, np.ndarray]:
        jd = t + dvec
        okd = (jd >= 0) & (jd < lens[None, :])
        rb = Rp[:, t: t + W].T
        tc = tpl[:, t][None, :]
        return okd, np.where((tc < 0) | (rb == tc), 0, mc[:, t][None, :]).astype(np.int32)

    F = np.empty((T + 1, W, n), dtype=np.int32)
    F[0] = np.where((dvec >= 0) & (dvec <= lens[None, :]) & bandok, dvec * c_indel, INF)
    for i in range(1, T + 1):
        okd, sub = sub_at(i - 1)
        prev = F[i - 1]
        diag = np.where(okd, prev + sub, INF)
        dele = np.full_like(prev, INF)
        dele[:-1] = prev[1:] + c_indel
        new = np.minimum(diag, dele)
        new = np.minimum(new, np.minimum.accumulate(new - ramp, axis=0) + ramp)
        j = i + dvec
        F[i] = np.where((j >= 0) & (j <= lens[None, :]) & bandok, new, INF)
    G = np.empty_like(F)
    jT = T + dvec
    G[T] = np.where((jT >= 0) & (jT <= lens[None, :]) & bandok, (lens[None, :] - jT) * c_indel, INF)
    for i in range(T - 1, -1, -1):
        okd, sub = sub_at(i)
        nxt = G[i + 1]
        diag = np.where(okd, sub + nxt, INF)
        dele = np.full_like(nxt, INF)
        dele[1:] = nxt[:-1] + c_indel
        new = np.minimum(diag, dele)
        new = np.minimum(new, np.minimum.accumulate((new + ramp)[::-1], axis=0)[::-1] - ramp)
        j = i + dvec
        G[i] = np.where((j >= 0) & (j <= lens[None, :]) & bandok, new, INF)
    fit = np.abs(lens - T) <= band
    w_end = np.clip(lens - T + B, 0, W - 1)
    opt = np.where(fit, F[T][w_end, rows], int(INF)).astype(np.int64)
    return F, G, Rp, B, np.minimum(opt, int(INF))


def edit_costs(tpl: np.ndarray, mc: np.ndarray, reads: list, band: np.ndarray, c_indel: int, c_sub: int):
    """Per read, the exact optimal cost after one template edit (reads that do not fit their band: INF everywhere).

    Returns (opt (n,), sub (n, T, 4): base b at position i, dele (n, T): position i removed, ins (n, T, 4): base b
    inserted before position i). An inserted base costs ``c_sub`` on mismatch; a substituted base keeps ``mc``."""
    F, G, Rp, B, opt = fg(tpl, mc, reads, band, c_indel)
    T1, W, n = F.shape
    T = T1 - 1
    lens = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    dvec = np.arange(-B, B + 1, dtype=np.int64)[:, None]
    inf_row = np.full((1, n), INF, dtype=np.int64)
    sub = np.empty((n, T, 4), dtype=np.int64)
    dele = np.empty((n, T), dtype=np.int64)
    ins = np.empty((n, T, 4), dtype=np.int64)
    mc = mc.astype(np.int64)
    bases = np.arange(4)
    for i in range(T):
        jd = i + dvec
        okd = (jd >= 0) & (jd < lens[None, :])
        rb = Rp[:, i: i + W].T
        A = F[i].astype(np.int64)
        Gn = G[i + 1].astype(np.int64)
        Gn_dn = np.concatenate([inf_row, Gn[:-1]])            # G[i+1] at diagonal w−1: read index unchanged
        Gc_up = np.concatenate([G[i][1:].astype(np.int64), inf_row])   # G[i] at diagonal w+1: one read base consumed
        skip = (A + Gn_dn).min(axis=0)                         # template base i removed
        dele[:, i] = skip
        path_del = skip + c_indel                              # base i kept but deleted in the read
        path_ins_del = (A + G[i].astype(np.int64)).min(axis=0) + c_indel
        # V8.14: the four candidate bases in one broadcast (W, n, 4) instead of four passes; same integer arithmetic and
        # the same minima, so the result is bit-identical to _edit_costs_reference (tests/v8/test_polish_equivalence.py)
        mis = (rb[:, :, None] != bases).astype(np.int64)
        ok3 = okd[:, :, None]
        A3 = A[:, :, None]
        s_best = np.where(ok3, A3 + mis * mc[:, i][None, :, None] + Gn[:, :, None], INF).min(axis=0)
        i_best = np.where(ok3, A3 + mis * c_sub + Gc_up[:, :, None], INF).min(axis=0)
        sub[:, i, :] = np.minimum(s_best, path_del[:, None])
        ins[:, i, :] = np.minimum(i_best, path_ins_del[:, None])
    dead = opt >= INF
    for arr in (sub, dele, ins):
        np.minimum(arr, INF, out=arr)
        arr[dead] = INF
    return opt, sub, dele, ins


def _edit_costs_reference(tpl: np.ndarray, mc: np.ndarray, reads: list, band: np.ndarray, c_indel: int, c_sub: int):
    """V7 implementation of :func:`edit_costs`, kept as the equivalence reference (V8.14 tests compare the two bit for bit).

    Per read, the exact optimal cost after one template edit (reads that do not fit their band: INF everywhere).

    Returns (opt (n,), sub (n, T, 4): base b at position i, dele (n, T): position i removed, ins (n, T, 4): base b
    inserted before position i). An inserted base costs ``c_sub`` on mismatch; a substituted base keeps ``mc``."""
    F, G, Rp, B, opt = fg(tpl, mc, reads, band, c_indel)
    T1, W, n = F.shape
    T = T1 - 1
    lens = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    dvec = np.arange(-B, B + 1, dtype=np.int64)[:, None]
    inf_row = np.full((1, n), INF, dtype=np.int64)
    sub = np.empty((n, T, 4), dtype=np.int64)
    dele = np.empty((n, T), dtype=np.int64)
    ins = np.empty((n, T, 4), dtype=np.int64)
    mc = mc.astype(np.int64)
    for i in range(T):
        jd = i + dvec
        okd = (jd >= 0) & (jd < lens[None, :])
        rb = Rp[:, i: i + W].T
        A = F[i].astype(np.int64)
        Gn = G[i + 1].astype(np.int64)
        Gn_dn = np.concatenate([inf_row, Gn[:-1]])            # G[i+1] at diagonal w−1: read index unchanged
        Gc_up = np.concatenate([G[i][1:].astype(np.int64), inf_row])   # G[i] at diagonal w+1: one read base consumed
        skip = (A + Gn_dn).min(axis=0)                         # template base i removed
        dele[:, i] = skip
        path_del = skip + c_indel                              # base i kept but deleted in the read
        path_ins_del = (A + G[i].astype(np.int64)).min(axis=0) + c_indel
        for b in range(4):
            mis = (rb != b).astype(np.int64)
            sub[:, i, b] = np.minimum(np.where(okd, A + mis * mc[:, i][None, :] + Gn, INF).min(axis=0), path_del)
            ins[:, i, b] = np.minimum(np.where(okd, A + mis * c_sub + Gc_up, INF).min(axis=0), path_ins_del)
    dead = opt >= INF
    for arr in (sub, dele, ins):
        np.minimum(arr, INF, out=arr)
        arr[dead] = INF
    return opt, sub, dele, ins


def segments(tpl0: np.ndarray) -> np.ndarray:
    """Segment id per strand position: frame runs between markers are 0, 1, …; marker positions −1."""
    frame = tpl0 < 0
    starts = frame & ~np.concatenate([[False], frame[:-1]])
    return np.where(frame, np.cumsum(starts) - 1, -1)


def shift(tpl: np.ndarray, p: int, q: int, x: int) -> np.ndarray:
    """Remove position p and insert base x before (original) position q; the length is unchanged."""
    out = list(tpl.tolist())
    del out[p]
    out.insert(q - 1 if q > p else q, x)
    return np.asarray(out, dtype=tpl.dtype)
