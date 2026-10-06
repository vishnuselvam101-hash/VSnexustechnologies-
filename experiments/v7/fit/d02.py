"""DT4DDS (D02) specifics: read -> design assignment (k-mer seeds, then edlib verification), PhiX mapping, stage split.

Assignment (fitting plan 3.2): exact 12-mers at three positions of every design (5', middle, 3'), both strands, vote for
candidates, verify by edlib HW alignment of the design inside the read; accept if best <= 0.3 * L and best < second best - 3,
else the read is unassigned (counted, never forced). Reads are handed to the guard's assigner contract, so only reads of
references of the requested split leave the guard."""
from __future__ import annotations

import numpy as np

import fitlib as FL

K = 12
SEED_POS = (0, 48, 96)
LUT = np.full(256, 4, dtype=np.uint8)
for _i, _c in enumerate(b"ACGT"):
    LUT[_c] = _i
_POW = 4 ** np.arange(K - 1, -1, -1, dtype=np.int64)


def kmer_codes(codes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(B, W) base codes -> (B, W-K+1) 12-mer integers and a validity mask (no N inside)."""
    B, W = codes.shape
    n = W - K + 1
    if n <= 0:
        return np.zeros((B, 0), dtype=np.int64), np.zeros((B, 0), dtype=bool)
    idx = np.arange(n)[:, None] + np.arange(K)[None, :]
    win = codes[:, idx]                                        # (B, n, K)
    bad = (win > 3).any(axis=2)
    return (np.minimum(win, 3).astype(np.int64) * _POW).sum(axis=2), ~bad


class Assigner:
    """``assigner(list of read bytes) -> list of design ID or None`` (the guard's ``assign_batch`` contract). ``orient`` maps a
    read's bytes to True if it matches the reverse-complemented design (valid for the reads of the last call)."""

    def __init__(self, designs: list, margin: int = 3, max_frac: float = 0.30):
        import edlib
        self.edlib = edlib
        self.ids = [d for d, _ in designs]
        self.seqs = [s for _, s in designs]
        self.L = len(self.seqs[0])
        self.margin, self.max_frac = margin, max_frac
        keys, owner = [], []
        for di, s in enumerate(self.seqs):
            for strand, seq in ((0, s), (1, FL.rc(s))):
                codes = LUT[np.frombuffer(seq, dtype=np.uint8)]
                for p in SEED_POS:
                    # seeds of the minus strand sit at mirrored positions; index its own prefix/middle/suffix k-mers
                    k = int((np.minimum(codes[p:p + K], 3).astype(np.int64) * _POW).sum())
                    keys.append(k)
                    owner.append(di * 2 + strand)
        order = np.argsort(keys, kind="stable")
        self.keys = np.asarray(keys, dtype=np.int64)[order]
        self.owner = np.asarray(owner, dtype=np.int64)[order]
        self.orient: dict = {}
        self.stats = {"reads": 0, "assigned": 0, "no_seed": 0, "too_distant": 0, "ambiguous": 0, "reverse_strand": 0}

    def _verify(self, read: bytes, di: int, strand: int):
        ref = self.seqs[di] if strand == 0 else FL.rc(self.seqs[di])
        r = self.edlib.align(ref, read, mode="HW", task="distance")
        return r["editDistance"]

    def __call__(self, reads: list) -> list:
        self.orient = {}
        W = max(len(r) for r in reads)
        arr = np.full((len(reads), W), 4, dtype=np.uint8)
        for i, r in enumerate(reads):
            arr[i, :len(r)] = LUT[np.frombuffer(r, dtype=np.uint8)]
        km, ok = kmer_codes(arr)
        out: list = []
        pos = np.searchsorted(self.keys, km)
        pos = np.minimum(pos, self.keys.size - 1)
        hit = (self.keys[pos] == km) & ok
        # a k-mer may belong to several owners (identical seeds); take the run of equal keys
        for i, r in enumerate(reads):
            self.stats["reads"] += 1
            cand: dict = {}
            for j in np.flatnonzero(hit[i]):
                lo = np.searchsorted(self.keys, km[i, j], side="left")
                hi = np.searchsorted(self.keys, km[i, j], side="right")
                for o in self.owner[lo:hi]:
                    cand[int(o)] = cand.get(int(o), 0) + 1
            if not cand:
                self.stats["no_seed"] += 1
                out.append(None)
                continue
            top = sorted(cand.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            dists = sorted((self._verify(r, o // 2, o % 2), o) for o, _ in top)
            best_d, best_o = dists[0]
            if best_d > self.max_frac * self.L:
                self.stats["too_distant"] += 1
                out.append(None)
                continue
            if len(dists) > 1 and dists[1][1] // 2 != best_o // 2 and dists[1][0] - best_d < self.margin + 1:
                self.stats["ambiguous"] += 1
                out.append(None)
                continue
            self.stats["assigned"] += 1
            self.orient[r] = bool(best_o % 2)
            self.stats["reverse_strand"] += int(best_o % 2)
            out.append(self.ids[best_o // 2])
        return out


# ================================================================================================================ PhiX
PHIX_BLOCKS = 100
PHIX_CYCLES = 108             # reads are cut to the first 108 cycles, the length of the design region the model is applied to


class PhiX:
    """Sequencing-only control: map reads to NC_001422.1 (k-mer seeds, diagonal vote, edlib HW realignment inside the window
    +-10 nt) and count events per aligned reference base (plan 3.2, PhiX row)."""

    def __init__(self, genome: bytes):
        import edlib
        self.edlib = edlib
        self.genome = genome
        self.fwd, self.rev = genome, FL.rc(genome)
        keys, pos, strand = [], [], []
        for s, seq in enumerate((self.fwd, self.rev)):
            codes = LUT[np.frombuffer(seq, dtype=np.uint8)].astype(np.int64)
            idx = np.arange(len(seq) - K + 1)[:, None] + np.arange(K)[None, :]
            kk = (np.minimum(codes[idx], 3) * _POW).sum(axis=1)
            keys.append(kk)
            pos.append(np.arange(len(kk)))
            strand.append(np.full(len(kk), s))
        keys, pos, strand = np.concatenate(keys), np.concatenate(pos), np.concatenate(strand)
        order = np.argsort(keys, kind="stable")
        self.keys, self.pos, self.strand = keys[order], pos[order], strand[order]
        self.blocks = np.zeros((PHIX_BLOCKS, 8 + 16 + 4 + 16 + 16), dtype=np.int64)
        self.stats = {"reads": 0, "mapped": 0, "unmapped": 0, "too_distant": 0}

    def map_read(self, read: bytes):
        codes = LUT[np.frombuffer(read, dtype=np.uint8)][None, :]
        km, ok = kmer_codes(codes)
        votes: dict = {}
        for j in np.flatnonzero(ok[0]):
            lo = np.searchsorted(self.keys, km[0, j], side="left")
            hi = np.searchsorted(self.keys, km[0, j], side="right")
            for q in range(lo, hi):
                key = (int(self.strand[q]), int(self.pos[q]) - int(j))
                votes[key] = votes.get(key, 0) + 1
        if not votes:
            return None
        (s, start), n = max(votes.items(), key=lambda kv: (kv[1], -kv[0][1], kv[0][0]))
        return (s, start) if n >= 2 else None

    def add(self, read: bytes) -> None:
        from vnxdna.simulation.fit.align import left_normalise, parse_cigar
        from vnxdna.simulation.fit.tally import read_events
        read = read[:PHIX_CYCLES]
        self.stats["reads"] += 1
        m = self.map_read(read)
        if m is None:
            self.stats["unmapped"] += 1
            return
        s, start = m
        g = self.fwd if s == 0 else self.rev
        lo, hi = max(0, start - 10), min(len(g), start + len(read) + 10)
        window = g[lo:hi]
        res = self.edlib.align(read, window, mode="HW", task="path")
        if res["editDistance"] < 0 or res["editDistance"] > 0.10 * len(read):
            self.stats["too_distant"] += 1
            return
        a, b = res["locations"][0]
        seg = window[a:b + 1]
        runs = parse_cigar(res["cigar"])                   # query = read: I = extra read base, D = reference base missing
        if any(op in "ID" for op, _ in runs):
            runs = left_normalise(seg, read, runs)
        ev = read_events(seg, read, runs, 0)
        fwd_start = lo + a if s == 0 else len(g) - (lo + b + 1)
        blk = min(PHIX_BLOCKS - 1, fwd_start * PHIX_BLOCKS // len(g))
        row = self.blocks[blk]
        row[0] += len(seg)
        row[1] += 1
        row[2] += len(ev["sub_sites"])
        row[3] += len(ev["ins_len"])
        row[4] += sum(ev["ins_len"])
        row[5] += len(ev["del_starts"])
        row[6] += sum(ev["del_len"])
        for p in ev["sub_pairs"]:
            row[8 + p] += 1
        for bse in ev["ins_bases"]:
            row[24 + min(bse, 3)] += 1
        for n in ev["ins_len"]:
            row[28 + min(n, 16) - 1] += 1
        for n in ev["del_len"]:
            row[44 + min(n, 16) - 1] += 1
        self.stats["mapped"] += 1

    def estimates(self, totals: np.ndarray) -> dict:
        from vnxdna.simulation.fit.estimate import deletion_from_observed
        nb = float(totals[0])
        mat = totals[8:24].reshape(4, 4).astype(float)
        matrix = np.array([mat[i] / mat[i].sum() if mat[i].sum() > 0 else np.where(np.arange(4) == i, 0.0, 1.0 / 3.0) for i in range(4)])
        ib = totals[24:28].astype(float)
        d, m = deletion_from_observed(totals[5] / nb, totals[6] / nb)
        return {"substitution_rate": float(totals[2] / nb), "insertion_rate": float(totals[3] / nb),
                "insertion_bases_rate": float(totals[4] / nb), "deletion_rate": float(d), "deletion_run_mean": float(m),
                "deletion_bases_rate": float(totals[6] / nb), "matrix": matrix.tolist(),
                "base_weights": (ib / ib.sum()).tolist() if ib.sum() else None}

    def fit(self, bootstrap: int, seed: int) -> dict:
        tot = self.blocks.sum(axis=0)
        point = self.estimates(tot)
        reps = []
        for b in range(bootstrap):
            w = np.bincount(np.random.default_rng([seed, b]).integers(0, PHIX_BLOCKS, PHIX_BLOCKS), minlength=PHIX_BLOCKS)
            reps.append(self.estimates((w[:, None] * self.blocks).sum(axis=0)))
        ci = {}
        for k, v in point.items():
            if v is None:
                continue
            arr = np.asarray([r[k] for r in reps], dtype=float)
            lo, hi = np.percentile(arr, 2.5, axis=0), np.percentile(arr, 97.5, axis=0)
            ci[k] = [float(lo), float(hi)] if arr.ndim == 1 else {"lo": lo.tolist(), "hi": hi.tolist()}
        return {"values": point, "ci95": ci, "mapped_reads": self.stats["mapped"], "reference_bases": int(tot[0]), "stats": dict(self.stats)}
