"""V7 item D3: tooling for two public nanopore DNA-storage datasets whose reads are concatemers (D13, CAS9).

PUBLIC-DATA-DERIVED. Every read processed here was produced by another group; VNX-DNA has synthesised, stored and sequenced
nothing. Pre-registration: docs/V7_PROTOCOL_AMENDMENT_D3.md (sections cited as "PR-n" below).

* D13 (Lopez et al. 2019): each read holds several designed 150-nt oligos joined by assembly linkers. :func:`split_read`
  locates every reference oligo inside a read with a k-mer index plus an edlib semi-global (HW) alignment and applies the
  ambiguity rules of PR-4.
* CAS9 (Imburgia et al. 2025): reads are rolling-circle concatemers of one molecule; the designed strands are not published.
  :func:`find_addresses` locates the repeated file-address motif, :func:`units_between` cuts the repeat units, and
  :func:`star_consensus` builds the leave-one-out pseudo-reference of PR-5.
* :class:`ErrorTally` accumulates aggregate statistics only (rates, position and homopolymer profiles, substitution matrix,
  indel runs, quality calibration). Tallies are sums of integer counts, so the result does not depend on the order or the
  number of worker processes.

The optional dependency ``edlib`` is imported on first use. Nothing here is imported by the codec.
"""
from __future__ import annotations

import gzip
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import numpy as np

_COMP = bytes.maketrans(b"ACGTNacgtn", b"TGCANtgcan")
LUT = np.full(256, 4, dtype=np.uint8)
for _i, _c in enumerate(b"ACGT"):
    LUT[_c] = _i
    LUT[_c + 32] = _i
BASES = b"ACGT"

FIT, DEV, HELDOUT = "FIT", "DEV", "HELDOUT"


def _edlib() -> Any:
    try:
        import edlib
    except ImportError as error:  # pragma: no cover - environment dependent
        raise ImportError("the D3 tooling needs the optional dependency edlib") from error
    return edlib


# --------------------------------------------------------------------------------------------------------------------
# split rule (docs/V7_PROTOCOL.md section 4.1, Amendment 1; PR-3)
# --------------------------------------------------------------------------------------------------------------------

def revcomp(seq: bytes) -> bytes:
    return seq.translate(_COMP)[::-1]


def canonical(seq: bytes) -> bytes:
    """Lexicographically smaller of a sequence and its reverse complement, upper-case ASCII."""
    s = seq.strip().upper()
    return min(s, revcomp(s))


def bucket(seq: bytes) -> int:
    return int(hashlib.sha256(canonical(seq)).hexdigest(), 16) % 10


def bucket_split(b: int) -> str:
    if not 0 <= b <= 9:
        raise ValueError(b)
    return FIT if b <= 5 else DEV if b <= 7 else HELDOUT


def id_bucket(read_id: bytes) -> int:
    """Bucket of a read with no reference (CAS9, PR-3.2): SHA-256 of the read ID (first header token, without '@')."""
    return int(hashlib.sha256(read_id).hexdigest(), 16) % 10


def heldout_unit(dataset_id: str, units: list[str]) -> str:
    """Held-out unit: units sorted by name, index int(SHA-256('VNX-V7-HELDOUT/' + dataset_id), 16) mod n."""
    us = sorted(units)
    if not us:
        raise ValueError("no units")
    return us[int(hashlib.sha256(("VNX-V7-HELDOUT/" + dataset_id).encode()).hexdigest(), 16) % len(us)]


# --------------------------------------------------------------------------------------------------------------------
# input parsing
# --------------------------------------------------------------------------------------------------------------------

def read_fastq(path: str | Path) -> Iterator[tuple[bytes, bytes, bytes]]:
    """(read ID, sequence, quality) of every record of a FASTQ file (gzip by magic number). Raises ValueError on a
    malformed record instead of skipping it."""
    p = Path(path)
    with open(p, "rb") as raw:
        gz = raw.read(2) == b"\x1f\x8b"
    fh = gzip.open(p, "rb") if gz else open(p, "rb")
    with fh:
        n = 0
        while True:
            h = fh.readline()
            if not h:
                return
            seq, plus, q = fh.readline().rstrip(b"\r\n"), fh.readline(), fh.readline().rstrip(b"\r\n")
            n += 1
            if not h.startswith(b"@") or not plus.startswith(b"+") or len(seq) != len(q):
                raise ValueError(f"{p.name}: malformed FASTQ record {n}")
            yield h[1:].split()[0], seq, q


def parse_d13_refs(path: str | Path) -> list[bytes]:
    """Reference oligos of a D13 ``seqs_*.txt`` file: one ``5'-SEQ-3'`` per line, SEQ over ACGT."""
    out = []
    for n, line in enumerate(Path(path).read_bytes().splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if not (s.startswith(b"5'-") and s.endswith(b"-3'")):
            raise ValueError(f"line {n}: not a 5'-...-3' sequence")
        seq = s[3:-3]
        if not seq or seq.strip(b"ACGT"):
            raise ValueError(f"line {n}: non-ACGT sequence")
        out.append(seq)
    return out


def mean_phred(q: bytes) -> float:
    """Mean of the per-base error probabilities, expressed as a Phred value (the ONT convention for read quality)."""
    if not q:
        return 0.0
    a = np.frombuffer(q, dtype=np.uint8).astype(np.float64) - 33.0
    p = float(np.mean(10.0 ** (-a / 10.0)))
    return -10.0 * np.log10(max(p, 1e-12))


# --------------------------------------------------------------------------------------------------------------------
# k-mer index and concatemer splitting (D13; PR-4)
# --------------------------------------------------------------------------------------------------------------------

def kmer_codes(seq: bytes, k: int) -> tuple[np.ndarray, np.ndarray]:
    """(start positions, 2-bit k-mer codes) of every window of ``seq`` that holds only A/C/G/T. ``k`` <= 31."""
    if not 1 <= k <= 31:
        raise ValueError(k)
    c = LUT[np.frombuffer(seq, dtype=np.uint8)]
    n = len(c) - k + 1
    if n <= 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    bad = np.concatenate(([0], np.cumsum(c == 4)))
    ok = (bad[k:] - bad[:-k]) == 0
    win = np.lib.stride_tricks.sliding_window_view(c.astype(np.int64) & 3, k)
    codes = win @ (np.int64(4) ** np.arange(k - 1, -1, -1, dtype=np.int64))
    pos = np.nonzero(ok)[0]
    return pos.astype(np.int64), codes[ok]


class RefIndex:
    """k-mers of the (forward) references that occur at most ``max_occ`` times over all references, with their reference
    and offset. More frequent k-mers (shared primers, repeated motifs of the encoding) are dropped."""

    def __init__(self, refs: list[bytes], k: int = 16, max_occ: int = 8):
        if max_occ < 1:
            raise ValueError(max_occ)
        self.k = k
        self.max_occ = max_occ
        self.refs = refs
        cs, rs, ps = [], [], []
        for i, r in enumerate(refs):
            p, c = kmer_codes(r, k)
            cs.append(c)
            ps.append(p.astype(np.int32))
            rs.append(np.full(len(c), i, dtype=np.int32))
        codes = np.concatenate(cs) if cs else np.zeros(0, dtype=np.int64)
        ref = np.concatenate(rs) if rs else np.zeros(0, dtype=np.int32)
        pos = np.concatenate(ps) if ps else np.zeros(0, dtype=np.int32)
        order = np.lexsort((pos, ref, codes))
        codes, ref, pos = codes[order], ref[order], pos[order]
        _, inverse, count = np.unique(codes, return_inverse=True, return_counts=True)
        keep = count[inverse] <= max_occ
        self.codes, self.ref, self.pos = codes[keep], ref[keep], pos[keep]
        self.total_kmers = int(len(codes))
        self.dropped_kmers = int(len(codes) - keep.sum())

    def hits(self, seq: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(read offset, reference index, reference offset) of every indexed k-mer hit of ``seq`` (forward only)."""
        p, c = kmer_codes(seq, self.k)
        if not len(self.codes) or not len(c):
            z = np.zeros(0, dtype=np.int64)
            return z, z, z
        lo = np.searchsorted(self.codes, c, side="left")
        hi = np.searchsorted(self.codes, c, side="right")
        n = hi - lo
        m = n > 0
        if not m.any():
            z = np.zeros(0, dtype=np.int64)
            return z, z, z
        lo, n, p = lo[m], n[m], p[m]
        rep = np.repeat(np.arange(len(lo)), n)
        within = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
        j = lo[rep] + within
        return p[rep], self.ref[j].astype(np.int64), self.pos[j].astype(np.int64)


def edlib_ops(cigar: str) -> str:
    """Expand an edlib extended CIGAR computed with query = reference, target = read into one op per column, from the
    read's point of view: '=' match, 'X' substitution, 'D' reference base missing from the read, 'I' extra read base."""
    swap = {"=": "=", "X": "X", "I": "D", "D": "I"}
    out, num = [], 0
    for ch in cigar:
        if ch.isdigit():
            num = num * 10 + (ord(ch) - 48)
        else:
            out.append(swap[ch] * num)
            num = 0
    return "".join(out)


@dataclass(frozen=True)
class Segment:
    """One reference oligo found in a read. Coordinates are in the original read orientation, end exclusive; ``seq``,
    ``qual`` and ``ops`` are oriented to the reference strand."""

    ref: int
    strand: int
    start: int
    end: int
    ed: int
    hits: int
    ops: str
    seq: bytes
    qual: bytes


@dataclass
class SplitResult:
    segments: list[Segment]
    candidates: int
    rejected_error: int
    ambiguous: int


@dataclass(frozen=True)
class SplitParams:
    """Pre-registered constants (PR-4.2)."""

    k: int = 16
    max_occ: int = 8
    min_hits: int = 3
    diag_tol: int = 24
    pad: int = 30
    max_err: float = 0.30
    margin: int = 5
    overlap: float = 0.5


def _candidates(rp: np.ndarray, rid: np.ndarray, rpos: np.ndarray, p: SplitParams) -> list[tuple[int, int, int, int]]:
    """(ref, min diagonal, max diagonal, hits) of every hit cluster with at least ``min_hits`` hits."""
    if not len(rp):
        return []
    diag = rp - rpos
    order = np.lexsort((diag, rid))
    rid, diag = rid[order], diag[order]
    brk = np.nonzero((np.diff(rid) != 0) | (np.diff(diag) > p.diag_tol))[0] + 1
    out = []
    for a, b in zip(np.concatenate(([0], brk)), np.concatenate((brk, [len(rid)]))):
        if b - a >= p.min_hits:
            out.append((int(rid[a]), int(diag[a]), int(diag[b - 1]), int(b - a)))
    return out


def build_index(refs: list[bytes], params: SplitParams = SplitParams()) -> RefIndex:
    """The k-mer index :func:`split_read` expects for ``params``."""
    return RefIndex(refs, k=params.k, max_occ=params.max_occ)


def split_read(seq: bytes, qual: bytes, index: RefIndex, params: SplitParams = SplitParams()) -> SplitResult:
    """Every reference oligo in a concatemer read (PR-4). Both strands are searched. A candidate is aligned with edlib HW
    (the whole reference against a window of the read) and kept if its edit distance is at most ``max_err`` x the reference
    length. Candidates are taken in order of edit distance; a candidate overlapping an accepted segment by more than
    ``overlap`` of the shorter one is dropped, and if it belongs to a different reference and is within ``margin`` edits of
    the accepted one, the accepted segment is marked ambiguous and discarded (never assigned to the nearest reference)."""
    if index.k != params.k or index.max_occ != params.max_occ:
        raise ValueError("index built with other k / max_occ than params (use build_index)")
    if len(qual) != len(seq):
        raise ValueError("sequence and quality lengths differ")
    ed_mod = _edlib()
    n = len(seq)
    found = []
    rejected = 0
    ncand = 0
    for strand in (1, -1):
        s = seq if strand == 1 else revcomp(seq)
        q = qual if strand == 1 else qual[::-1]
        for ref_i, dmin, dmax, nh in _candidates(*index.hits(s), params):
            ncand += 1
            ref = index.refs[ref_i]
            L = len(ref)
            a, b = max(0, dmin - params.pad), min(len(s), dmax + L + params.pad)
            if b - a <= 0:
                continue
            r = ed_mod.align(ref, s[a:b], mode="HW", task="path")
            ed = r["editDistance"]
            if ed < 0 or ed > params.max_err * L:
                rejected += 1
                continue
            x, y = r["locations"][0]
            x, y = a + x, a + y + 1
            o0, o1 = (x, y) if strand == 1 else (n - y, n - x)
            found.append((ed, -nh, ref_i, strand, o0, o1, edlib_ops(r["cigar"]), s[x:y], q[x:y]))
    found.sort(key=lambda t: (t[0], t[1], t[2], t[3], t[4]))
    accepted: list[list] = []
    for ed, negh, ref_i, strand, o0, o1, ops, sseq, sq in found:
        clash = False
        for acc in accepted:
            ov = min(o1, acc[5]) - max(o0, acc[4])
            if ov > params.overlap * min(o1 - o0, acc[5] - acc[4]):
                clash = True
                if ref_i != acc[2] and ed <= acc[0] + params.margin:
                    acc[9] = True
        if not clash:
            accepted.append([ed, negh, ref_i, strand, o0, o1, ops, sseq, sq, False])
    segs = [Segment(ref=a[2], strand=a[3], start=a[4], end=a[5], ed=a[0], hits=-a[1], ops=a[6], seq=a[7], qual=a[8])
            for a in accepted if not a[9]]
    segs.sort(key=lambda g: g.start)
    return SplitResult(segs, ncand, rejected, sum(1 for a in accepted if a[9]))


# --------------------------------------------------------------------------------------------------------------------
# address motif search, repeat units and leave-one-out consensus (CAS9; PR-5)
# --------------------------------------------------------------------------------------------------------------------

_N_EQ = [("N", b) for b in "ACGT"]


def find_addresses(s: bytes, motif: bytes, max_ed: int, max_hits: int = 1000) -> list[tuple[int, int, int]]:
    """Every non-overlapping occurrence of ``motif`` (N = any base) in ``s`` with at most ``max_ed`` edits, best first:
    edlib HW finds the best occurrence, which is masked, and the search repeats. Returns sorted (start, end, ed)."""
    ed_mod = _edlib()
    t = bytearray(s)
    out = []
    for _ in range(max_hits):
        r = ed_mod.align(motif, bytes(t), mode="HW", task="locations", k=max_ed, additionalEqualities=_N_EQ)
        if r["editDistance"] < 0:
            break
        x, y = r["locations"][0]
        out.append((x, y + 1, r["editDistance"]))
        t[x:y + 1] = b"#" * (y + 1 - x)
    return sorted(out)


def classify_address(window: bytes, addresses: dict[str, bytes], max_ed: int, margin: int = 1) -> str | None:
    """Name of the address that matches ``window`` best (edlib HW, address inside window), or None if no address is within
    ``max_ed`` or the best two are less than ``margin`` apart."""
    ed_mod = _edlib()
    scored = sorted((ed_mod.align(a, window, mode="HW")["editDistance"], name) for name, a in addresses.items())
    scored = [(d, nm) for d, nm in scored if d >= 0]
    if not scored or scored[0][0] > max_ed:
        return None
    if len(scored) > 1 and scored[1][0] - scored[0][0] < margin:
        return None
    return scored[0][1]


def units_between(hits: list[tuple[int, int, int]], min_len: int, max_len: int) -> list[tuple[int, int]]:
    """Repeat units of a rolling-circle read: from the start of one address hit to the start of the next, kept only if the
    length is within [min_len, max_len] (a unit outside the range contains a missed or spurious hit)."""
    out = []
    for (a, _, _), (b, _, _) in zip(hits, hits[1:]):
        if min_len <= b - a <= max_len:
            out.append((a, b))
    return out


def pairwise_distances(seqs: list[bytes]) -> np.ndarray:
    """Symmetric matrix of global (NW) unit-cost edit distances."""
    ed_mod = _edlib()
    n = len(seqs)
    dist = np.zeros((n, n), dtype=np.int64)
    for i in range(n):
        for j in range(i + 1, n):
            dist[i, j] = dist[j, i] = ed_mod.align(seqs[i], seqs[j], mode="NW")["editDistance"]
    return dist


def star_consensus(seqs: list[bytes], dist: np.ndarray | None = None) -> bytes:
    """Consensus of repeat units by star alignment: the medoid (least total edit distance to the others; ties to the lowest
    index) is the backbone, every other unit is aligned to it globally, each backbone column takes the majority of
    {base, deletion} (ties keep the backbone base), and an insertion is added after a column when more than half of the
    units have one there (the most common inserted string, ties to the lexicographically smallest). ``dist`` may pass the
    precomputed :func:`pairwise_distances` of ``seqs``."""
    ed_mod = _edlib()
    if not seqs:
        raise ValueError("no units")
    if len(seqs) == 1:
        return seqs[0]
    n = len(seqs)
    if dist is None:
        dist = pairwise_distances(seqs)
    elif dist.shape != (n, n):
        raise ValueError("distance matrix does not match the units")
    m = int(np.argmin(dist.sum(axis=1)))
    bb = seqs[m]
    L = len(bb)
    votes = [Counter() for _ in range(L)]
    ins: list[Counter] = [Counter() for _ in range(L + 1)]
    for i, s in enumerate(seqs):
        if i == m:
            for c in range(L):
                votes[c][bb[c:c + 1]] += 1
            continue
        ops = edlib_ops(ed_mod.align(bb, s, mode="NW", task="path")["cigar"])
        c = j = 0
        pending = bytearray()
        for op in ops:
            if op == "I":
                pending.append(s[j])
                j += 1
                continue
            if pending:
                ins[c][bytes(pending)] += 1
                pending = bytearray()
            if op == "D":
                votes[c][b""] += 1
                c += 1
            else:
                votes[c][s[j:j + 1]] += 1
                c += 1
                j += 1
        if pending:
            ins[c][bytes(pending)] += 1
    out = bytearray()
    for c in range(L + 1):
        tot_ins = sum(ins[c].values())
        if tot_ins * 2 > n:
            best = sorted(ins[c].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            out += best
        if c == L:
            break
        top = max(votes[c].values())
        winners = sorted(k for k, v in votes[c].items() if v == top)
        out += bb[c:c + 1] if bb[c:c + 1] in winners else winners[0]
    return bytes(out)


# --------------------------------------------------------------------------------------------------------------------
# aggregate error statistics
# --------------------------------------------------------------------------------------------------------------------

MAX_HP = 8
MAX_RUN = 16
QBINS = 94
POS_BINS = 15
ED_BINS = 101
DRIFT_OFF = 50


def homopolymer_runs(ref: bytes) -> np.ndarray:
    """Length of the homopolymer run that contains each position (capped at MAX_HP)."""
    L = len(ref)
    out = np.ones(L, dtype=np.int64)
    i = 0
    while i < L:
        j = i
        while j < L and ref[j] == ref[i]:
            j += 1
        out[i:j] = min(j - i, MAX_HP)
        i = j
    return out


class ErrorTally:
    """Integer counts from (reference, read segment, op string, quality) alignments. Rates are derived on export."""

    def __init__(self) -> None:
        self.segments = 0
        self.sites = 0
        self.sub = 0
        self.dele = 0
        self.ins_events = 0
        self.ins_bases = 0
        self.end_ins_bases = 0
        self.submat = np.zeros((4, 4), dtype=np.int64)
        self.pos = np.zeros((4, POS_BINS), dtype=np.int64)          # sites, sub, del, ins events per relative bin
        self.hp = np.zeros((4, MAX_HP + 1), dtype=np.int64)          # sites, sub, del, ins (homopolymer extension) by run
        self.ins_other = 0
        self.del_runs = np.zeros(MAX_RUN + 1, dtype=np.int64)
        self.ins_runs = np.zeros(MAX_RUN + 1, dtype=np.int64)
        self.corr = np.zeros((2, 2), dtype=np.int64)                 # (event at site i, event at site i+1)
        self.q = np.zeros((2, QBINS), dtype=np.int64)                # read bases by Phred: correct, error (X or I)
        self.ed_hist = np.zeros(ED_BINS, dtype=np.int64)            # per segment, in percent of the reference length
        self.drift = np.zeros(2 * DRIFT_OFF + 1, dtype=np.int64)     # read segment length - reference length
        self.lengths: Counter = Counter()
        self.ed_moments = np.zeros(3, dtype=np.int64)               # segments, sum and sum of squares of edit counts

    def add(self, ref: bytes, seg: bytes, ops: str, qual: bytes | None = None) -> None:
        L = len(ref)
        if L == 0:
            raise ValueError("empty reference")
        if ops.count("=") + ops.count("X") + ops.count("D") != L or ops.count("=") + ops.count("X") + ops.count("I") != len(seg):
            raise ValueError("op string does not match the reference and segment lengths")
        hp = homopolymer_runs(ref)
        ev = np.zeros(L, dtype=np.int64)
        i = j = 0
        drun = irun = 0
        for op in ops:
            if op == "I":
                irun += 1
                if qual is not None:
                    self.q[1, min(qual[j] - 33, QBINS - 1)] += 1
                j += 1
                continue
            if irun:
                self._close_ins(ref, seg, i, j, irun, hp, ev)
                irun = 0
            if op != "D" and drun:
                self.del_runs[min(drun, MAX_RUN)] += 1
                drun = 0
            b = (i * POS_BINS) // L
            self.sites += 1
            self.pos[0, b] += 1
            self.hp[0, hp[i]] += 1
            rb = LUT[ref[i]]
            if op == "=":
                if qual is not None:
                    self.q[0, min(qual[j] - 33, QBINS - 1)] += 1
                j += 1
            elif op == "X":
                self.sub += 1
                self.pos[1, b] += 1
                self.hp[1, hp[i]] += 1
                if rb < 4 and LUT[seg[j]] < 4:
                    self.submat[rb, LUT[seg[j]]] += 1
                if qual is not None:
                    self.q[1, min(qual[j] - 33, QBINS - 1)] += 1
                ev[i] = 1
                j += 1
            elif op == "D":
                self.dele += 1
                self.pos[2, b] += 1
                self.hp[2, hp[i]] += 1
                ev[i] = 1
                drun += 1
            else:
                raise ValueError(f"unknown op {op!r}")
            i += 1
        if drun:
            self.del_runs[min(drun, MAX_RUN)] += 1
        if irun:
            self.end_ins_bases += irun
            self.ins_runs[min(irun, MAX_RUN)] += 1
            self.ins_events += 1
            self.ins_bases += irun
        nerr = ops.count("X") + ops.count("D") + ops.count("I")
        self.corr += np.array([[np.sum((ev[:-1] == a) & (ev[1:] == c)) for c in (0, 1)] for a in (0, 1)], dtype=np.int64)
        self.segments += 1
        self.ed_moments += np.array([1, nerr, nerr * nerr], dtype=np.int64)
        self.ed_hist[min(int(round(100 * nerr / L)), ED_BINS - 1)] += 1
        self.drift[min(max(len(seg) - L, -DRIFT_OFF), DRIFT_OFF) + DRIFT_OFF] += 1
        self.lengths[L] += 1

    def _close_ins(self, ref: bytes, seg: bytes, i: int, j: int, run: int, hp: np.ndarray, ev: np.ndarray) -> None:
        """An insertion of ``run`` read bases seg[j-run:j] before reference site i."""
        L = len(ref)
        self.ins_events += 1
        self.ins_bases += run
        self.ins_runs[min(run, MAX_RUN)] += 1
        b = (min(i, L - 1) * POS_BINS) // L
        self.pos[3, b] += 1
        if i < L:
            ev[i] = 1
        inserted = seg[j - run:j]
        nb = []
        if i > 0:
            nb.append((ref[i - 1], hp[i - 1]))
        if i < L:
            nb.append((ref[i], hp[i]))
        ext = [h for base, h in nb if inserted == bytes([base]) * run]
        if ext:
            self.hp[3, max(ext)] += 1
        else:
            self.ins_other += 1

    def merge(self, other: "ErrorTally") -> None:
        for name in ("segments", "sites", "sub", "dele", "ins_events", "ins_bases", "end_ins_bases", "ins_other"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for name in ("submat", "pos", "hp", "del_runs", "ins_runs", "corr", "q", "ed_hist", "drift", "ed_moments"):
            getattr(self, name).__iadd__(getattr(other, name))
        self.lengths.update(other.lengths)

    def _moments(self) -> dict:
        n, sm, sq = (int(x) for x in self.ed_moments)
        if n == 0:
            return {"segments": 0, "mean": None, "variance": None, "variance_over_mean": None}
        mean = sm / n
        var = sq / n - mean * mean
        return {"segments": n, "mean": round(mean, 4), "variance": round(var, 4),
                "variance_over_mean": round(var / mean, 4) if mean else None}

    def summary(self) -> dict:
        """Aggregate statistics only: no per-read or per-reference value is exported."""
        s = max(self.sites, 1)

        def ratio(a: np.ndarray, b: np.ndarray) -> list:
            return [round(float(x) / float(y), 6) if y else None for x, y in zip(a, b)]

        qc, qe = self.q
        occupied = [int(q) for q in range(QBINS) if qc[q] + qe[q]]
        calib = [{"phred": q, "bases": int(qc[q] + qe[q]), "empirical_error": round(float(qe[q] / (qc[q] + qe[q])), 6),
                  "predicted_error": round(10 ** (-q / 10), 6)} for q in occupied]
        nb = int(qc.sum() + qe.sum())
        ece = sum(c["bases"] * abs(c["empirical_error"] - c["predicted_error"]) for c in calib) / nb if nb else None
        emp_phred = [{"phred": c["phred"], "empirical_phred": round(-10 * np.log10(max(c["empirical_error"], 1e-6)), 3)}
                     for c in calib if c["bases"] >= 1000]
        dr = self.drift
        total_seg = max(int(dr.sum()), 1)
        cum = {f"abs_drift_le_{t}": round(float(dr[DRIFT_OFF - t:DRIFT_OFF + t + 1].sum()) / total_seg, 6) for t in (0, 3, 6)}
        c = self.corr
        p_ev = float(c[1].sum()) / max(float(c.sum()), 1.0)
        p_ev_after_ev = float(c[1, 1]) / max(float(c[1].sum()), 1.0)
        ed_pct = np.repeat(np.arange(ED_BINS), self.ed_hist)
        return {
            "segments": self.segments,
            "reference_sites": self.sites,
            "reference_lengths": dict(sorted((str(k), v) for k, v in self.lengths.items())),
            "rates_per_reference_site": {
                "substitution": round(self.sub / s, 6), "deletion": round(self.dele / s, 6),
                "insertion_events": round(self.ins_events / s, 6), "inserted_bases": round(self.ins_bases / s, 6),
                "total_edits": round((self.sub + self.dele + self.ins_bases) / s, 6)},
            "counts": {"substitutions": self.sub, "deleted_bases": self.dele, "insertion_events": self.ins_events,
                       "inserted_bases": self.ins_bases, "end_inserted_bases": self.end_ins_bases},
            "substitution_matrix": {"rows_ref_cols_read": "ACGT", "counts": self.submat.tolist(),
                                    "transition_share": round(float(self.submat[0, 2] + self.submat[2, 0] + self.submat[1, 3]
                                                                    + self.submat[3, 1]) / max(int(self.submat.sum()), 1), 6)},
            "position_profile": {"bins": POS_BINS, "basis": "relative position in the reference, equal-width bins",
                                 "substitution": ratio(self.pos[1], self.pos[0]), "deletion": ratio(self.pos[2], self.pos[0]),
                                 "insertion_events": ratio(self.pos[3], self.pos[0]), "sites": self.pos[0].tolist()},
            "homopolymer": {"run_length_index": "1..8 (8 = 8 or more)", "sites": self.hp[0, 1:].tolist(),
                            "substitution": ratio(self.hp[1, 1:], self.hp[0, 1:]),
                            "deletion": ratio(self.hp[2, 1:], self.hp[0, 1:]),
                            "extension_insertion_events": self.hp[3, 1:].tolist(),
                            "extension_insertions_per_site": ratio(self.hp[3, 1:], self.hp[0, 1:]),
                            "other_insertion_events": self.ins_other},
            "deletion_run_lengths": {"index": "1..16 (16 = 16 or more)", "counts": self.del_runs[1:].tolist()},
            "insertion_run_lengths": {"index": "1..16 (16 = 16 or more)", "counts": self.ins_runs[1:].tolist()},
            "error_correlation": {"p_event": round(p_ev, 6), "p_event_given_previous_event": round(p_ev_after_ev, 6),
                                  "pairs": c.tolist()},
            "segment_edit_count": self._moments(),
            "segment_edit_distance_percent": {
                "histogram_0_to_100": self.ed_hist.tolist(),
                "median": int(np.median(ed_pct)) if len(ed_pct) else None,
                "p90": int(np.percentile(ed_pct, 90, method="lower")) if len(ed_pct) else None},
            "length_drift": {"offset": DRIFT_OFF, "histogram": dr.tolist(), **cum},
            "quality_calibration": {"read_bases": nb, "bins": calib, "expected_calibration_error": None if ece is None else round(ece, 6),
                                    "empirical_phred_bins_ge_1000_bases": emp_phred},
        }
