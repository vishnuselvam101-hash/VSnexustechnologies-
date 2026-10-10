"""Outer-assisted recovery of short rows (hybrid architecture; EXPERIMENTAL, opt-in ``ClusterConfig.outer_list``).

A row (outer-code group) is short when fewer than k of its symbols were verified. Read clustering usually still holds a
*failed* consensus frame for several of the missing symbols: too many byte errors for the inner RS code, but most bytes
right (measured on the D13-F1 nanopore-like channel, SIMULATED: ~19 of 70 bytes wrong, only ~38 % of them flagged as
erasures). Each round, for every open short row:

1. **Proposals.** Candidates assigned by header (group and symbol bytes exact, :data:`EXACT_MASK`) or, in earlier
   rounds, by payload consistency feed a per-column list decoder of the outer code (C++ kernel
   :mod:`vnxdna.native.outerlist`; reference :func:`decode_row_reference`): with h verified symbols the codewords form
   an f = k − h dimensional affine space, every candidate byte is a linear equation on it, and the solution agreeing
   with the most candidates is the column's proposal (confirmed when it leads with a margin).
2. **Payload-consistency assignment.** Failed frames whose payload agrees with a row's proposal for a missing symbol in
   ``list_min_consistent`` columns (uniquely) become candidates of that symbol, whatever their header says.
3. **Inner decoding.** Every failed frame is tried at each missing address within ``list_max_header_distance`` header
   bytes: header forced, confirmed proposal columns written, disagreeing unconfirmed columns erased, then inner RS
   (errors + erasures) and CRC-32; a symbol is added only when the frame verifies *at that address*.
4. **Tail-verified repair.** Payload variants between a candidate and the proposal (≤ ``list_repair_bytes`` columns, or
   any single byte) are re-encoded; the CRC-32 + inner parity tail (never used above) must match the candidate's in
   ``list_min_tail_matches`` of 20 bytes, with a lead of 2 over every other variant.

A row is returned only with k + ``list_check_symbols`` verified symbols that one codeword reproduces, or with exactly k
whose codeword predicts the full frame of a missing symbol that a failed frame matches in
``list_min_frame_match_percent`` % of its bytes. The result only fills rows that would otherwise fail; the container
SHA-256 + structure validation still decide SUCCESS.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations, product

import numpy as np

from vnxdna.codec import gf256
from vnxdna.codec.cauchy import CauchyErasureCode
from vnxdna.codec.codecs import InnerRS
from vnxdna.core.crc import crc32_bytes_be
from vnxdna.core.version import FRAME_VERSION
from vnxdna.dnaenc.frame4 import decode_frames
from vnxdna.dnaenc.layout import KIND_DATA, Layout
from vnxdna.dnaenc.scrambler import keystreams
from vnxdna.native import outerlist as _native
from vnxdna.native.integrity import crc32_rows

MAX_PER_POS = _native.MAX_PER_POS
#: frame bytes that must match exactly when a candidate is assigned: group (4..7) and symbol (8..9). A one-byte error
#: there would make a frame of a neighbouring address (another symbol of the row, or the next group) look like a
#: missing one; the variant, version/kind and tag bytes (0..3) may differ.
EXACT_MASK = sum(1 << b for b in range(4, 10))
MAX_F = _native.MAX_F
INV = np.array([0] + [gf256.inv(a) for a in range(1, 256)], dtype=np.uint8)


# ---------------------------------------------------------------------------------------------------------- reference
def assign_reference(frames: np.ndarray, ks: np.ndarray, variants: np.ndarray, targets: np.ndarray, max_dist: int,
                     exact_mask: int = 0):
    """(target or -1, variant or -1, best distance, second distance) per candidate frame; see the module docstring.
    Frame bytes b (0..9) with bit b set in ``exact_mask`` must match exactly."""
    exact = np.array([(exact_mask >> (b + 1)) & 1 for b in range(9)], dtype=bool)
    frames = np.asarray(frames, dtype=np.uint8)
    targets = np.asarray(targets, dtype=np.uint8).reshape(-1, 9)
    n = frames.shape[0]
    tgt = np.full(n, -1, np.int32)
    var = np.full(n, -1, np.int16)
    dist = np.zeros(n, np.int64)
    sec = np.zeros(n, np.int64)
    for i in range(n):
        fr = frames[i]
        best, second, bt, bv = 11, 11, -1, -1
        for v in (int(x) for x in variants):
            h = fr[1:10] ^ ks[v][:9]
            d0 = int(fr[0] != v)
            if d0 and exact_mask & 1:
                continue
            ne = (h[None, :] != targets) if len(targets) else np.zeros((0, 9), bool)
            valid = ~(ne & exact[None, :]).any(axis=1)
            dd = d0 + ne.sum(axis=1)
            for x, d in enumerate(dd.tolist()):
                if not valid[x]:
                    continue
                if d < best:
                    if bt != x:
                        second = best
                    best, bt, bv = d, x, v
                elif x != bt and d < second:
                    second = d
        ok = bt >= 0 and best <= max_dist and second > best
        tgt[i] = bt if ok else -1
        var[i] = bv if ok else -1
        dist[i], sec[i] = best, second
    return tgt, var, dist, sec


def _solve(a: list, b: list) -> list | None:
    """Solve the f x f GF(256) system a z = b (lists); None if singular."""
    f = len(a)
    m = [list(r) + [y] for r, y in zip(a, b)]
    mul = gf256.MUL
    for c in range(f):
        p = next((r for r in range(c, f) if m[r][c]), None)
        if p is None:
            return None
        m[c], m[p] = m[p], m[c]
        iv = int(INV[m[c][c]])
        m[c] = [int(mul[x, iv]) for x in m[c]]
        for r in range(f):
            if r != c and m[r][c]:
                t = m[r][c]
                m[r] = [x ^ int(mul[t, y]) for x, y in zip(m[r], m[c])]
    return [m[r][f] for r in range(f)]


def _affine(parity: np.ndarray, known: np.ndarray):
    """Unknown positions U, free count f, and per column a function giving (a_c, R) with x_U = a + R z."""
    mul = gf256.MUL
    m, k = parity.shape
    n = k + m
    H = np.concatenate([parity, np.eye(m, dtype=np.uint8)], axis=1)          # m x n, H c = 0
    U = [p for p in range(n) if not known[p]]
    Kn = [p for p in range(n) if known[p]]
    u = len(U)
    A = [[int(H[r, p]) for p in U] for r in range(m)]
    T = [[int(r == j) for j in range(m)] for r in range(m)]
    pivcol, row = [], 0
    for c in range(u):
        if row >= m:
            break
        p = next((r for r in range(row, m) if A[r][c]), None)
        if p is None:
            continue
        A[row], A[p] = A[p], A[row]
        T[row], T[p] = T[p], T[row]
        iv = int(INV[A[row][c]])
        A[row] = [int(mul[x, iv]) for x in A[row]]
        T[row] = [int(mul[x, iv]) for x in T[row]]
        for r in range(m):
            q = A[r][c]
            if r != row and q:
                A[r] = [x ^ int(mul[q, y]) for x, y in zip(A[r], A[row])]
                T[r] = [x ^ int(mul[q, y]) for x, y in zip(T[r], T[row])]
        pivcol.append(c)
        row += 1
    prow = {c: r for r, c in enumerate(pivcol)}
    free = [c for c in range(u) if c not in prow]
    f = len(free)
    R = [[0] * f for _ in range(u)]
    for c in range(u):
        if c in prow:
            R[c] = [A[prow[c]][fc] for fc in free]
        else:
            R[c][free.index(c)] = 1
    return U, Kn, H, T, prow, R, f


def decode_row_reference(parity, mul, inv, known, sym, cpos, cval, cok, min_extra: int, margin: int, max_subsets: int):
    """Reference of vnx_ol_decode_row: (sym with accepted columns filled, accepted columns, best, second)."""
    del mul, inv                     # the reference uses vnxdna.codec.gf256 directly (the same field)
    parity = np.asarray(parity, dtype=np.uint8)
    m, k = parity.shape
    known = np.asarray(known, dtype=bool)
    sym = np.array(sym, dtype=np.uint8, copy=True)
    P = sym.shape[1]
    cpos = np.asarray(cpos, dtype=np.int64)
    cval = np.asarray(cval, dtype=np.uint8).reshape(-1, P)
    cok = np.asarray(cok, dtype=bool).reshape(-1, P)
    U, Kn, H, T, prow, R, f = _affine(parity, known)
    if not 1 <= f <= MAX_F or len(U) - len(prow) != f:
        raise ValueError("decode_row needs 1 <= k - known <= 8")
    uidx = {p: i for i, p in enumerate(U)}
    qpos, qc = [], []
    for c, p in enumerate(cpos.tolist()):
        if known[p]:
            raise ValueError("candidate at a known position")
        ui = uidx[p]
        if ui not in qpos:
            qpos.append(ui)
            qc.append([])
        lst = qc[qpos.index(ui)]
        if len(lst) < MAX_PER_POS:
            lst.append(c)
    mul_t = gf256.MUL
    Q = len(qpos)
    best_out = np.full(P, -1, np.int64)
    second_out = np.full(P, -1, np.int64)
    accepted = 0
    for col in range(P):
        rhs = [0] * m
        for r in range(m):
            s = 0
            for p in Kn:
                s ^= int(mul_t[H[r, p], sym[p, col]])
            rhs[r] = s
        a = [0] * len(U)
        for c, r in prow.items():
            s = 0
            for j in range(m):
                s ^= int(mul_t[T[r][j], rhs[j]])
            a[c] = s
        if Q < f:
            continue
        best, second, bz = -1, -1, None
        for tried, S in enumerate(combinations(range(Q), f)):
            if tried >= max_subsets:
                break
            for pick in product(*(range(len(qc[q])) for q in S)):
                cs = [qc[q][i] for q, i in zip(S, pick)]
                if not all(cok[c, col] for c in cs):
                    continue
                y = [int(cval[c, col]) ^ a[qpos[q]] for q, c in zip(S, cs)]
                z = _solve([R[qpos[q]] for q in S], y)
                if z is None:
                    continue
                agree = 0
                for q in range(Q):
                    ui = qpos[q]
                    x = a[ui]
                    for j in range(f):
                        x ^= int(mul_t[R[ui][j], z[j]])
                    if any(cok[c, col] and int(cval[c, col]) == x for c in qc[q]):
                        agree += 1
                if bz is not None and z == bz:
                    continue
                if agree > best:
                    second, best, bz = best, agree, z
                elif agree > second:
                    second = agree
        best_out[col], second_out[col] = best, second
        if bz is not None:
            for c, p in enumerate(U):
                x = a[c]
                for j in range(f):
                    x ^= int(mul_t[R[c][j], bz[j]])
                sym[p, col] = x
        if best >= f + min_extra and best - max(second, 0) >= margin:
            accepted += 1
    return sym, accepted, best_out, second_out


# --------------------------------------------------------------------------------------------------------- dispatch
def assign(frames, ks, variants, targets, max_dist, exact_mask=0):
    if _native.resolve_backend() == "native":
        return _native.assign(frames, ks, variants, targets, max_dist, exact_mask)
    return assign_reference(frames, ks, variants, targets, max_dist, exact_mask)


def decode_row(parity, known, sym, cpos, cval, cok, min_extra, margin, max_subsets):
    if _native.resolve_backend() == "native":
        return _native.decode_row(parity, gf256.MUL, INV, known, sym, cpos, cval, cok, min_extra, margin, max_subsets)
    return decode_row_reference(parity, gf256.MUL, INV, known, sym, cpos, cval, cok, min_extra, margin, max_subsets)


# ---------------------------------------------------------------------------------------------------------- rows
def header_bytes(tag: int, group: int, symbol: int, kind: int = KIND_DATA) -> np.ndarray:
    """Plain frame bytes 1..9 of an address: version/kind, tag (2), group (4), symbol (2)."""
    return np.array([(FRAME_VERSION << 4) | kind, (tag >> 8) & 255, tag & 255, *int(group).to_bytes(4, "big"),
                     *int(symbol).to_bytes(2, "big")], dtype=np.uint8)


def encode_frames(lay: Layout, tag: int, group: int, symbol: int, payloads: np.ndarray, variant: int) -> np.ndarray:
    """Transmitted frames of (n, P) payload variants of one address with one scrambler variant (vectorised)."""
    n, P = payloads.shape
    plain = np.empty((n, 9 + P), dtype=np.uint8)
    plain[:, :9] = header_bytes(tag, group, symbol)
    plain[:, 9:] = payloads
    body = np.concatenate([plain, crc32_bytes_be(crc32_rows(plain))], axis=1)
    msg = np.empty((n, 1 + body.shape[1]), dtype=np.uint8)
    msg[:, 0] = variant
    msg[:, 1:] = body ^ keystreams(body.shape[1])[variant]
    return np.concatenate([msg, InnerRS(lay.inner_parity).parity(msg)], axis=1)


def frame_bytes(lay: Layout, tag: int, group: int, symbol: int, payload: np.ndarray, variant: int) -> np.ndarray:
    """The transmitted frame (variant, scrambled header + payload + CRC-32, inner RS parity) of one data symbol."""
    P = lay.payload_bytes
    plain = np.concatenate([header_bytes(tag, group, symbol), np.asarray(payload, np.uint8)])
    crc = crc32_bytes_be(crc32_rows(plain[None, :]))[0]
    body = np.concatenate([plain, crc])
    ks = keystreams(body.size)
    msg = np.concatenate([[variant], body ^ ks[variant]]).astype(np.uint8)
    par = InnerRS(lay.inner_parity).parity(msg[None, :])[0]
    out = np.concatenate([msg, par])
    assert out.size == lay.frame_bytes and P == payload.size
    return out


class OuterListDecoder:
    """Recover short rows of one archive (tag) from the stage's failed consensus frames. ``codec`` is the
    :class:`vnxdna.codec.codecs.CauchyRSCodec` of the archive (other outer codes are not list-decoded)."""

    def __init__(self, lay: Layout, cfg, codec, tag: int):
        self.lay, self.cfg, self.codec, self.tag = lay, cfg, codec, int(tag)
        self.counts: Counter = Counter()
        self._extra: dict = {}           # group -> {(symbol, frame index): variant} found by payload consistency

    def applicable(self) -> bool:
        return getattr(self.codec, "name", None) == "cauchy-rs" and self.cfg.outer_list == "on"

    def recover(self, rows: dict, frames: np.ndarray | None, erased: np.ndarray | None) -> dict:
        """``rows``: group -> (k, {symbol index: verified payload}) of short rows; ``frames``/``erased``: (n,
        frame_bytes) failed consensus frames and their erased-byte masks. Returns group -> (k, P) data of every row that
        reached k verified symbols (each new symbol passed inner RS + CRC-32 at its own address)."""
        out: dict = {}
        if not rows or not self.applicable() or frames is None or not len(frames):
            return out
        cfg, P = self.cfg, self.lay.payload_bytes
        rows = {g: (k, dict(syms)) for g, (k, syms) in rows.items()}
        self.counts["rows_attempted"] += len(rows)
        self.counts["candidates_offered"] += int(len(frames))
        span = 9 + P + 4
        ks = keystreams(span)
        frames = np.ascontiguousarray(frames, dtype=np.uint8)
        erased = np.asarray(erased, dtype=bool)
        for _ in range(cfg.list_rounds):
            open_rows = {g: v for g, v in rows.items() if g not in out}
            if not open_rows:
                break
            added = self._round(open_rows, frames, erased, ks)
            for g, (k, syms) in open_rows.items():
                data = self._consistent(k, syms)
                if data is None and len(syms) >= k:
                    data = self._confirmed(g, k, syms, frames)
                if data is not None:
                    out[g] = data
                    self.counts["rows_recovered"] += 1
            if not added:
                break
        return out

    def _consistent(self, k: int, syms: dict):
        """The row's data when it has k + ``list_check_symbols`` verified symbols and the codeword decoded from them
        reproduces every one of them (frames of another archive with a colliding tag, or a wrong repair, would make
        the symbols inconsistent); else None."""
        if len(syms) < k + self.cfg.list_check_symbols:
            return None
        P = self.lay.payload_bytes
        try:
            data = self.codec.decode(syms, k, P)
        except Exception:  # noqa: BLE001 - cannot happen with k verified symbols; the row stays failed
            self.counts["rows_decode_error"] += 1
            return None
        cw = self.codec.encode(data)
        if all(np.array_equal(cw[s], v) for s, v in syms.items()):
            return data
        self.counts["rows_inconsistent"] += 1
        return None

    def _confirmed(self, g: int, k: int, syms: dict, frames: np.ndarray):
        """A row with exactly the k verified symbols it needs is accepted when the codeword they determine predicts the
        full transmitted frame of a missing symbol that some failed frame matches in ``list_min_frame_match_percent``
        of its bytes (an independent observation; chance agreement of half a 70-byte frame is ~256^-35). Its verified
        symbols must also be consistent with that codeword."""
        P, M = self.lay.payload_bytes, self.codec.M
        try:
            data = self.codec.decode(syms, k, P)
        except Exception:  # noqa: BLE001 - cannot happen with k verified symbols
            return None
        cw = self.codec.encode(data)
        if not all(np.array_equal(cw[s], v) for s, v in syms.items()):
            self.counts["rows_inconsistent"] += 1
            return None
        need = -(-self.lay.frame_bytes * self.cfg.list_min_frame_match_percent // 100)
        v0 = frames[:, 0]
        for s in range(k + M):
            if s in syms:
                continue
            for v in np.unique(v0).tolist():
                sel = np.flatnonzero(v0 == v)
                pred = encode_frames(self.lay, self.tag, g, s, cw[s][None, :], int(v))[0]
                if ((frames[sel] == pred[None, :]).sum(axis=1) >= need).any():
                    self.counts["rows_confirmed_by_prediction"] += 1
                    return data
        return None

    def _round(self, rows: dict, frames: np.ndarray, erased: np.ndarray, ks: np.ndarray) -> int:
        """One round over the open short rows; returns the number of new verified symbols (added to ``rows``)."""
        cfg, P, M = self.cfg, self.lay.payload_bytes, self.codec.M
        self.counts["rounds"] += 1
        targets, owner = [], []
        for g, (k, syms) in sorted(rows.items()):
            for s in range(k + M):
                if s not in syms:
                    targets.append(header_bytes(self.tag, g, s))
                    owner.append((g, s))
        if not targets:
            return 0
        T = np.stack(targets)
        # 1. candidates per row: header-assigned (exact group + symbol bytes) plus those found by payload consistency
        #    in earlier rounds; each (symbol, scrambler variant, frame index)
        variants = np.arange(min(cfg.list_variants, ks.shape[0]), dtype=np.int16)
        tgt, var, _, _ = assign(frames, ks, variants, T, cfg.list_max_header_distance, EXACT_MASK)
        cands: dict = {}
        for i in np.flatnonzero(tgt >= 0).tolist():
            g, s = owner[int(tgt[i])]
            cands.setdefault(g, {})[(s, i)] = int(var[i])
        for g, lst in self._extra.items():
            if g in rows:
                for (s, i), v in lst.items():
                    if s not in rows[g][1]:
                        cands.setdefault(g, {})[(s, i)] = v
        # 2. proposals: per-column outer list decoding of each row from its candidates
        proposal = self._proposals(rows, cands, frames, erased, ks)
        # 3. payload-consistency assignment: frames whose payload agrees with a row's proposal for a missing symbol
        self._consistency_assign(rows, proposal, frames, ks, owner)
        added = 0
        # 4. inner RS + CRC-32 on hypotheses (header forced, payload repaired from confirmed proposal columns)
        added += self._rs_hypotheses(rows, proposal, frames, erased, ks, T, owner)
        # 5. tail-verified repair of every candidate of rows with proposals
        for g, lst in cands.items():
            k, syms = rows[g]
            for (s, i), v in sorted(lst.items()):
                if s in syms or g not in proposal:
                    continue
                res, ok, has, pos = proposal[g]
                base = frames[i, 10:10 + P] ^ ks[v][9:9 + P]
                pay = self._tail_repair(g, s, v, frames[i], base, np.where(has, res[pos(s)], base))
                if pay is None:
                    pay = self._tail_repair_any(g, s, v, frames[i], base)
                if pay is not None:
                    syms[s] = pay
                    added += 1
                    self.counts["symbols_tail_verified"] += 1
        return added

    def _proposals(self, rows, cands, frames, erased, ks) -> dict:
        cfg, P, K, M = self.cfg, self.lay.payload_bytes, self.codec.K, self.codec.M
        parity_full = np.asarray(CauchyErasureCode(K, M)._parity_matrix, dtype=np.uint8)
        proposal: dict = {}
        for g, lst in sorted(cands.items()):
            k, syms = rows[g]
            f = k - len(syms)
            items = sorted((s, i, v) for (s, i), v in lst.items() if s not in syms)
            if not 1 <= f <= MAX_F or len({s for s, _, _ in items}) <= f:
                continue
            pos = _pos(k, K)
            known = np.zeros(K + M, dtype=bool)
            sym = np.zeros((K + M, P), dtype=np.uint8)
            known[k:K] = True                                           # shortened positions: known zeros
            for s, v in syms.items():
                known[pos(s)] = True
                sym[pos(s)] = v
            res, acc, best, second = decode_row(
                parity_full, known.astype(np.uint8), sym, np.array([pos(s) for s, _, _ in items], dtype=np.int32),
                np.stack([frames[i, 10:10 + P] ^ ks[v][9:9 + P] for _, i, v in items]),
                np.stack([~erased[i, 10:10 + P] for _, i, _ in items]), cfg.list_min_extra, cfg.list_margin,
                cfg.list_max_subsets)
            ok = (best >= f + cfg.list_min_extra) & (best - np.maximum(second, 0) >= cfg.list_margin)
            proposal[g] = (res, ok, best >= 0, pos)
            self.counts["columns_accepted"] += int(acc)
            self.counts["rows_with_proposals"] += 1
        return proposal

    def _consistency_assign(self, rows, proposal, frames, ks, owner) -> None:
        """Assign failed frames (any header) to the missing symbol whose proposed payload they agree with in at least
        ``list_min_consistent`` columns, uniquely (by 3 columns over the runner-up); random agreement is ~1/256."""
        cfg, P = self.cfg, self.lay.payload_bytes
        tg = [(g, s) for g, s in owner if g in proposal]
        if not tg:
            return
        pred = np.stack([proposal[g][0][proposal[g][3](s)] for g, s in tg])          # (t, P)
        has = np.stack([proposal[g][2] for g, s in tg])
        v0 = frames[:, 0].astype(np.int64)
        pay = frames[:, 10:10 + P] ^ ks[v0][:, 9:9 + P]
        used = {i for lst in self._extra.values() for (_, i) in lst}
        for a in range(0, len(frames), 2048):
            m = ((pay[a:a + 2048, None, :] == pred[None, :, :]) & has[None, :, :]).sum(axis=2)     # (n, t)
            order = np.argsort(-m, axis=1)
            top = np.take_along_axis(m, order[:, :2], axis=1) if m.shape[1] > 1 else np.c_[m, np.zeros_like(m)]
            for r in np.flatnonzero(top[:, 0] >= cfg.list_min_consistent).tolist():
                i = a + r
                if i in used or top[r, 0] - top[r, 1] < 3:
                    continue
                g, s = tg[int(order[r, 0])]
                if s in rows[g][1]:
                    continue
                self._extra.setdefault(g, {})[(s, i)] = int(v0[i])
                used.add(i)
                self.counts["candidates_by_consistency"] += 1

    def _rs_hypotheses(self, rows, proposal, frames, erased, ks, T, owner) -> int:
        lay, cfg, P = self.lay, self.cfg, self.lay.payload_bytes
        v0 = frames[:, 0].astype(np.int64)
        hdr = frames[:, 1:10] ^ ks[v0][:, :9]
        pairs_c, pairs_t = [], []
        for t0 in range(0, len(T), 512):
            d = (hdr[:, None, :] != T[None, t0:t0 + 512, :]).sum(axis=2)
            ci, ti = np.nonzero(d <= cfg.list_max_header_distance)
            pairs_c.append(ci)
            pairs_t.append(ti + t0)
        ci = np.concatenate(pairs_c)[: cfg.list_max_hypotheses]
        ti = np.concatenate(pairs_t)[: cfg.list_max_hypotheses]
        self.counts["hypotheses"] += int(ci.size)
        if not ci.size:
            return 0
        trial = frames[ci].copy()
        er = erased[ci].copy()
        for j in range(ci.size):
            g, s = owner[int(ti[j])]
            v = int(trial[j, 0])
            trial[j, 1:10] = header_bytes(self.tag, g, s) ^ ks[v][:9]
            er[j, :10] = False
            prop = proposal.get(g)
            if prop is None:
                continue
            res, ok, has, pos = prop
            x = res[pos(s)] ^ ks[v][9:9 + P]                            # proposed payload, scrambled as transmitted
            cur = trial[j, 10:10 + P]
            er[j, 10:10 + P] |= has & ~ok & (cur != x)                  # disagreement with an unconfirmed proposal
            trial[j, 10:10 + P] = np.where(ok, x, cur)
            er[j, 10:10 + P] &= ~ok
        er[er.sum(axis=1) > lay.inner_parity] = False                   # errors-only for those
        parsed = decode_frames(lay, trial, er)
        added = 0
        for j in np.flatnonzero(parsed.ok).tolist():
            g, s = owner[int(ti[j])]
            if (int(parsed.kind[j]), int(parsed.tag[j]), int(parsed.group[j]), int(parsed.symbol[j])) != \
                    (KIND_DATA, self.tag, g, s):
                self.counts["verified_other_address"] += 1
                continue
            k, syms = rows[g]
            if s in syms:
                continue
            syms[s] = np.array(parsed.payload[j], dtype=np.uint8)
            added += 1
        self.counts["symbols_verified"] += added
        return added

    def _tail_repair_any(self, g: int, s: int, v: int, frame: np.ndarray, base: np.ndarray):
        """One payload byte replaced by any value (P x 255 variants), tail-verified as in :meth:`_tail_repair`."""
        P = self.lay.payload_bytes
        pays = np.repeat(base[None, :], P * 256, axis=0)
        cols = np.repeat(np.arange(P), 256)
        pays[np.arange(P * 256), cols] = np.tile(np.arange(256, dtype=np.uint8), P)
        return self._tail_pick(g, s, v, frame, pays)

    def _tail_repair(self, g: int, s: int, v: int, frame: np.ndarray, base: np.ndarray, prop: np.ndarray):

        cfg = self.cfg
        diff = np.flatnonzero(base != prop).tolist()
        subsets = [()]
        for r in range(1, min(cfg.list_repair_bytes, len(diff)) + 1):
            for c in combinations(diff, r):
                subsets.append(c)
                if len(subsets) >= cfg.list_max_repairs:
                    break
            if len(subsets) >= cfg.list_max_repairs:
                break
        pays = np.repeat(base[None, :], len(subsets), axis=0)
        for j, c in enumerate(subsets):
            if c:
                pays[j, list(c)] = prop[list(c)]
        return self._tail_pick(g, s, v, frame, pays)

    def _tail_pick(self, g: int, s: int, v: int, frame: np.ndarray, pays: np.ndarray):
        """The payload variant whose re-encoded CRC-32 + inner parity tail matches the candidate's best, if it matches
        in ``list_min_tail_matches`` bytes and leads every other distinct variant by 2."""
        cfg, P = self.cfg, self.lay.payload_bytes
        pays = np.unique(pays, axis=0)
        enc = encode_frames(self.lay, self.tag, g, s, pays, v)
        tail = slice(10 + P, self.lay.frame_bytes)
        m = (enc[:, tail] == frame[tail][None, :]).sum(axis=1)
        self.counts["repair_trials"] += len(pays)
        j = int(m.argmax())
        second = int(np.sort(m)[-2]) if len(m) > 1 else -1
        if m[j] >= cfg.list_min_tail_matches and m[j] - second >= 2:
            return pays[j].copy()
        return None


def _pos(k: int, K: int):
    """Row symbol index -> position in the full [K + M] code (a short block's parity follows the K data positions)."""
    return lambda s: s if s < k else K + s - k
