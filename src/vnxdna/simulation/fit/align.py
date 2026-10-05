"""Global alignment of a read to its reference with edlib and leftmost indel normalisation (V7 fitting plan 3.2).

Operations (always from the reference's point of view): ``=`` match, ``X`` substitution, ``I`` extra base in the read,
``D`` reference base missing from the read. ``NW`` aligns the whole read to the whole reference (pre-trimmed segments:
CNR, D03). ``HW`` aligns the whole reference inside the read (reads that carry primers or adapters: D02); the returned
runs then describe the reference against the read window ``locations``.

Unit-cost alignments are not unique, so every indel is shifted to its leftmost equivalent position before it is counted
(inside homopolymers and repeats); position profiles and homopolymer statistics would otherwise depend on edlib's
tie-break. The optional dependency ``edlib`` (``pip install 'vnx-dna[fit]'``) is imported on first use only.
"""
from __future__ import annotations

import re

_CIGAR = re.compile(r"(\d+)([=XID])")
Runs = list  # list of [op, n]


def edlib_module():
    try:
        import edlib
    except ImportError as error:  # pragma: no cover - exercised only without the extra
        raise ImportError("fitting needs the optional dependency edlib: pip install 'vnx-dna[fit]'") from error
    return edlib


def parse_cigar(cigar: str) -> Runs:
    return [[op, int(n)] for n, op in _CIGAR.findall(cigar)]


def left_normalise(ref: bytes, read: bytes, runs: Runs) -> Runs:
    """Shift every indel run to its leftmost equivalent position; merge runs that become adjacent (repeated until nothing
    moves, so the result is a fixed point). ``read`` is the aligned read (or read window). Mismatches are never moved."""
    cur = [list(r) for r in runs]
    for _ in range(64):
        nxt = _normalise_pass(ref, read, cur)
        if nxt == cur:
            return nxt
        cur = nxt
    return cur


def _normalise_pass(ref: bytes, read: bytes, runs: Runs) -> Runs:
    out: Runs = []
    ri = qi = 0
    for op, n in runs:
        if op in "ID" and out and out[-1][0] == "=":
            prev = out[-1]
            s = 0
            if op == "D":
                while s < prev[1] and ref[ri - 1 - s] == ref[ri + n - 1 - s]:
                    s += 1
            else:
                while s < prev[1] and read[qi - 1 - s] == read[qi + n - 1 - s]:
                    s += 1
            if s:
                prev[1] -= s
                if prev[1] == 0:
                    out.pop()
                out.append([op, n])
                out.append(["=", s])
            else:
                out.append([op, n])
        else:
            out.append([op, n])
        if op in "=XD":
            ri += n
        if op in "=XI":
            qi += n
    merged: Runs = []
    for op, n in out:
        if merged and merged[-1][0] == op:
            merged[-1][1] += n
        else:
            merged.append([op, n])
    return merged


def right_normalise(ref: bytes, read: bytes, runs: Runs) -> Runs:
    """The rightmost equivalent placement of every indel (sensitivity check): left-normalise the reversed problem."""
    rv = [list(r) for r in reversed(runs)]
    out = left_normalise(ref[::-1], read[::-1], rv)
    return [list(r) for r in reversed(out)]


_DP_LUT = None


def dp_align_batch(ref: bytes, reads: list) -> list:
    """Unit-cost global alignment of each read to ``ref`` by dynamic programming with the tie-break of P4-EXP-03
    (traceback: diagonal, then deletion, then insertion). Vectorised over the reads of one reference. Returns, per read,
    (edit distance, runs, (0, len(read)))."""
    import numpy as np
    global _DP_LUT
    if _DP_LUT is None:
        _DP_LUT = np.full(256, 4, dtype=np.uint8)
        for i, c in enumerate(b"ACGT"):
            _DP_LUT[c] = i
    if not reads:
        return []
    L = len(ref)
    cent = _DP_LUT[np.frombuffer(ref, dtype=np.uint8)]
    lens = np.array([len(r) for r in reads], dtype=np.int64)
    B, W = len(reads), int(lens.max())
    R = np.full((B, W), 5, dtype=np.uint8)
    for k, r in enumerate(reads):
        R[k, :len(r)] = _DP_LUT[np.frombuffer(r, dtype=np.uint8)]
    J = np.arange(W + 1)
    ptr = np.zeros((L + 1, B, W + 1), dtype=np.int8)           # 0 diagonal, 1 deletion (reference base missing), 2 insertion
    prev = np.broadcast_to(J, (B, W + 1)).astype(np.int32).copy()
    ptr[0, :, 1:] = 2
    for i in range(1, L + 1):
        mism = (R != cent[i - 1]).astype(np.int32)
        diag = prev[:, :-1] + mism
        up = prev + 1
        cur = up.copy()
        p = np.ones((B, W + 1), dtype=np.int8)
        better = diag <= up[:, 1:]
        cur[:, 1:] = np.where(better, diag, up[:, 1:])
        p[:, 1:] = np.where(better, 0, 1)
        ins = J + np.minimum.accumulate(cur - J, axis=1)
        take = ins < cur
        cur = np.where(take, ins, cur)
        p = np.where(take, 2, p)
        ptr[i] = p
        prev = cur
    out = []
    for k in range(B):
        i, j = L, int(lens[k])
        ops = []
        while i > 0 or j > 0:
            op = 2 if i == 0 else 1 if j == 0 else int(ptr[i, k, j])
            if op == 0:
                ops.append("=" if R[k, j - 1] == cent[i - 1] else "X")
                i -= 1
                j -= 1
            elif op == 1:
                ops.append("D")
                i -= 1
            else:
                ops.append("I")
                j -= 1
        ops.reverse()
        runs: Runs = []
        for o in ops:
            if runs and runs[-1][0] == o:
                runs[-1][1] += 1
            else:
                runs.append([o, 1])
        out.append((int(prev[k, lens[k]]), runs, (0, int(lens[k]))))
    return out


def align(read: bytes, ref: bytes, mode: str = "NW", normalise: bool = True, shift: str = "left"):
    """(edit distance, runs, read window (start, end exclusive)) of ``read`` against ``ref``; runs are normalised.

    Returns ``None`` if edlib finds no alignment (HW with a read shorter than the reference allows none only if the
    distance is unbounded; kept for safety)."""
    ed = edlib_module()
    if mode == "NW":
        res = ed.align(read, ref, mode="NW", task="path")
        if res["editDistance"] < 0:
            return None
        runs = parse_cigar(res["cigar"])
        window = (0, len(read))
        win = read
    elif mode == "HW":
        res = ed.align(ref, read, mode="HW", task="path")
        if res["editDistance"] < 0 or not res["locations"]:
            return None
        s, e = res["locations"][0]
        window = (s, e + 1)
        win = read[s:e + 1]
        swap = {"I": "D", "D": "I", "=": "=", "X": "X"}      # edlib's query is the reference here
        runs = [[swap[op], n] for op, n in parse_cigar(res["cigar"])]
    else:
        raise ValueError(f"mode must be NW or HW, not {mode!r}")
    if normalise and any(op in "ID" for op, _ in runs):
        runs = left_normalise(ref, win, runs) if shift == "left" else right_normalise(ref, win, runs)
    return res["editDistance"], runs, window
