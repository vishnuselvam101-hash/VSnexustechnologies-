"""AB-DIAG: ground-truth decode funnel per alignment band (SIMULATED; descriptive, not a criterion of PREREG.md).

    PYTHONPATH=src python experiments/v6/align-band/funnel.py --out experiments/v6/align-band/AB-DIAG/results.json

For each (model, seed) and each band B: every read is attributed to its source strand and orientation by shared 16-mers
(ground truth the decoder never sees), oriented, and aligned with ``TemplateAligner(B)``. Reported per band:

* ``aligned``: share of reads the band aligns;
* ``erased_bytes``: mean erased frame bytes per aligned read (inner RS: 2e + f ≤ r = 16 of 70 frame bytes);
* ``addr_exact`` / ``addr_within1``: share of aligned reads whose header (the projected reading or the raw-prefix
  reading, as pass 1 forms them) gives the true address exactly / within one header byte (pass 2 snaps one byte);
* ``oracle_strands_decoded``: share of strands whose count-vote consensus over ALL their aligned reads (grouped by the
  true strand, not by header) passes inner RS + CRC: what consensus could do with perfect address recovery;
* ``oracle_2e_f_fits``: share of strands whose oracle consensus frame has 2e + f ≤ r against the true frame.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "phase4"))
import phase4 as p4  # noqa: E402

from vnxdna.core.provenance import environment  # noqa: E402
from vnxdna.dnaenc.frame4 import decode_frames, tentative_address  # noqa: E402
from vnxdna.dnaenc.layout import HEADER_BYTES, PROFILES  # noqa: E402
from vnxdna.dnaenc.mapping import nt_to_bytes  # noqa: E402
from vnxdna.dnaenc.reads import iter_reads  # noqa: E402
from vnxdna.recovery.consensus import _addr_bytes, consensus_hard  # noqa: E402
from vnxdna.sync.template import TemplateAligner, frame_erasures_to_bytes  # noqa: E402

RC = np.array([3, 2, 1, 0, 4], dtype=np.uint8)
CELLS = [("nanopore-like", 10.0), ("nanopore-like", 15.0), ("deletion-heavy", 5.0)]
SEEDS = [80000, 80001, 80002, 80003, 80004]
BANDS = [6, 12, 16, 24]


def load_reads(path: Path) -> list:
    out = []
    for b in iter_reads(path, 1 << 17):
        off = np.concatenate([[0], np.cumsum(b.lengths)])
        out += [b.codes[off[k]:off[k + 1]].copy() for k in range(b.count)]
    return out


def attribute(reads, codes):
    n = codes.shape[0]
    sk, _ = p4._kmers(codes)
    keys = sk.reshape(-1)
    sid = np.repeat(np.arange(n), sk.shape[1])
    o = np.argsort(keys, kind="stable")
    keys, sid = keys[o], sid[o]
    origin, fwd = [], []
    for r in reads:
        best, hits, f_best = -1, 0, True
        for f, c in ((True, r), (False, RC[r[::-1]])):
            km, ok = p4._kmers(c)
            km = km[ok]
            if not km.size:
                continue
            pos = np.minimum(np.searchsorted(keys, km), keys.size - 1)
            hit = keys[pos] == km
            if not hit.any():
                continue
            ids, cnt = np.unique(sid[pos[hit]], return_counts=True)
            j = int(cnt.argmax())
            if cnt[j] > hits:
                best, hits, f_best = int(ids[j]), int(cnt[j]), f
        origin.append(best)
        fwd.append(f_best)
    return np.array(origin), fwd


def funnel(lay, reads, origin, order, truth_fb, band):
    al = TemplateAligner(lay, band)
    pr = al.project(reads)
    eb = frame_erasures_to_bytes(pr.erased)
    ta = tentative_address(lay, nt_to_bytes(np.minimum(pr.bases, 3)))
    _, fpos = lay.template()
    hpos = fpos[: 4 * HEADER_BYTES]
    raw = np.zeros((len(reads), lay.frame_nt), dtype=np.uint8)
    for j, r in enumerate(reads):
        t = hpos[hpos < r.size]
        raw[j, : t.size] = np.minimum(r[t], 3)
    alt = tentative_address(lay, nt_to_bytes(raw))
    ex = near = tot = 0
    for j in np.flatnonzero(pr.ok & (origin >= 0)).tolist():
        tot += 1
        tk = _addr_bytes(order[origin[j]])
        best = 9
        for a in (ta[j], alt[j]):
            if a[0] >= 0:
                best = min(best, sum(x != y for x, y in zip(_addr_bytes(tuple(int(v) for v in a)), tk)))
        ex += best == 0
        near += best <= 1
    n = len(order)
    fr = np.zeros((n, lay.frame_nt), dtype=np.uint8)
    er = np.ones((n, lay.frame_nt), dtype=bool)
    for s in range(n):
        sel = np.flatnonzero((origin == s) & pr.ok)
        if sel.size:
            fr[s], er[s] = consensus_hard(np.where(pr.erased[sel], 4, pr.bases[sel]), 0.6)
    ebs = frame_erasures_to_bytes(er)
    cb, tb = nt_to_bytes(np.minimum(fr, 3)), nt_to_bytes(truth_fb)
    wrong = ((cb != tb) & ~ebs).sum(axis=1)
    P = decode_frames(lay, cb, ebs)
    return {"aligned": round(float(pr.ok.mean()), 4), "erased_bytes": round(float(eb[pr.ok].sum(1).mean()), 2) if pr.ok.any() else None,
            "addr_exact": round(ex / max(tot, 1), 4), "addr_within1": round(near / max(tot, 1), 4),
            "oracle_strands_decoded": round(float(P.ok.mean()), 4),
            "oracle_2e_f_fits": round(float(((2 * wrong + ebs.sum(1)) <= lay.inner_parity).mean()), 4)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    rows = []
    with tempfile.TemporaryDirectory(prefix="vnx-ab-diag-") as d:
        work = Path(d)
        info = p4.prepare(work, {"size": 20000}, 6201)
        lay = PROFILES["v4-balanced"][0]
        codes = p4.chn.read_strand_codes(work / "strands.fasta")
        truth, order = p4.truth_frames(work / "strands.fasta", lay)
        truth_fb = np.stack([truth[k] for k in order])
        for model, cov in CELLS:
            spec = {"base": model, "name": f"{model}-cov{int(cov)}", "channel": {"coverage": cov}}
            mpath = p4.materialise_model(spec, work / "models")
            m = p4.chn.load_model(mpath)
            for seed in SEEDS:
                rp = work / "reads.fastq"
                p4.chn.simulate_with_sidecar(m, work / "strands.fasta", rp, seed, workers=1)
                reads = load_reads(rp)
                origin, fwd = attribute(reads, codes)
                oriented = [r if f else RC[r[::-1]] for r, f in zip(reads, fwd)]
                row = {"model": model, "coverage": cov, "seed": seed, "reads": len(reads),
                       "unattributed": int((origin < 0).sum()), "bands": {}}
                for band in BANDS:
                    row["bands"][str(band)] = funnel(lay, oriented, origin, order, truth_fb, band)
                rows.append(row)
                print(json.dumps(row), flush=True)
    summary = {}
    for model, cov in CELLS:
        rs = [r for r in rows if r["model"] == model and r["coverage"] == cov]
        summary[f"{model}/cov{int(cov)}"] = {
            str(b): {k: round(float(np.mean([r["bands"][str(b)][k] for r in rs])), 4)
                     for k in rs[0]["bands"][str(b)]} for b in BANDS}
    doc = {"experiment": "AB-DIAG", "classification": "SIMULATED (ground-truth diagnostic, descriptive only)",
           "statement": p4.STATEMENT, "setup": info, "cells": CELLS, "seeds": SEEDS, "bands": BANDS,
           "git": p4.chn.git_info(), "environment": environment(), "rows": rows, "mean_over_seeds": summary,
           "seconds": round(time.perf_counter() - t0, 1)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
