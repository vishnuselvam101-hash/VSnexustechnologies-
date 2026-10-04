"""V6 Phase 1 items 6+7: profile of the inner Reed-Solomon decoder inside a representative decode. SIMULATED data.

Workload: 4 MiB random input (seed 42), profile v4-balanced, EXP-0011 channel (0.2 % substitutions, 0.05 %
insertions, 0.05 % deletions, 2 % dropout, Poisson coverage 3, seed 1011) -- the same case as the V5 Phase 2
re-profile (docs/V5_PHASE2_NATIVE_ALIGNMENT.md section 10). Single worker so that everything runs in this process.

Three views:
  1. cProfile of the whole decode: share of ``decode_batch`` (the inner-RS stage) and of the functions inside it.
  2. Workload capture: every inner-RS call is recorded (batch size, n, nsym, erasure counts, outcome), and the
     codewords/erasure masks are saved to an .npz (outside the repository) for the micro-benchmark.
  3. Phase timing inside ``rs_fast._decode_block`` on the captured workload: the module's own code split into
     syndromes / erasure locator / Berlekamp-Massey / Chien / Forney / re-check, timed with perf_counter on the
     captured calls (rs_fast is not modified; the split is a copy of its body with timers, checked to give the
     identical output).

With ``--native`` (the AFTER profile) ``codecs.InnerRS.decode`` is patched to ``vnxdna.v6.native_rs.decode_batch``
inside this process only (the decoder itself is not modified); views 2 and 3 are skipped and the stage share is the
cumulative time of ``native_rs.decode_batch``.

usage: python benchmarks/v6/native_rs/profile_decode.py [--workdir DIR] [--out profile-before.txt] [--native]
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pstats
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "v5"))
from provenance import provenance  # noqa: E402

from vnxdna.ecc.rs_batch import MAX_BATCH, _INV, _MUL, _alpha_powers  # noqa: E402
from vnxdna.v4 import rs_fast  # noqa: E402

NOISY_CHANNEL = {"substitution_rate": 0.002, "insertion_rate": 0.0005, "deletion_rate": 0.0005, "dropout_rate": 0.02,
                 "coverage": 3, "coverage_model": "poisson", "seed": 1011}
SIZE = 4 << 20


def prepare(work: Path) -> Path:
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import channel as ch
    from vnxdna.v4 import datagen
    from vnxdna.v4 import encoder as en
    reads = work / "r.fastq"
    if not reads.exists():
        datagen.generate(work / "input.bin", SIZE, "random", 42)
        ar.build_archive([work / "input.bin"], work / "a.vnx", ar.ArchiveOptions(workers=1))
        en.encode_container(work / "a.vnx", work / "s.fasta", en.DNAOptions(workers=1))
        ch.simulate_file(work / "s.fasta", reads, ch.ChannelConfig.from_dict(NOISY_CHANNEL), workers=1)
    return reads


def timed_block(cw, nsym, erase, t: Counter):
    """rs_fast._decode_block with perf_counter split points (body copied verbatim between the timers)."""
    pc = time.perf_counter
    t0 = pc()
    n_words, n = cw.shape
    width = nsym + 1
    x_of = _alpha_powers(n)
    tabs = rs_fast._tables(n, nsym)
    s = rs_fast._syndromes_fast(cw, tabs)
    clean = ~s.any(axis=1)
    f = erase.sum(axis=1).astype(np.int64)
    result = cw.copy()
    ok = clean & (f <= nsym)
    errata = np.zeros(n_words, dtype=np.int64)
    todo = np.flatnonzero(~clean & (f <= nsym))
    t1 = pc()
    t["1 syndromes + triage"] += t1 - t0
    if todo.size == 0:
        return result, ok, errata
    s = s[todo]
    er = erase[todo]
    f = f[todo]
    m = todo.size
    gamma = np.zeros((m, width), dtype=np.uint8)
    gamma[:, 0] = 1
    for j in np.flatnonzero(er.any(axis=0)).tolist():
        rows = np.flatnonzero(er[:, j])
        g = gamma[rows]
        shifted = np.zeros_like(g)
        shifted[:, 1:] = _MUL[x_of[j], g[:, :-1]]
        gamma[rows] = g ^ shifted
    t2 = pc()
    t["2 erasure locator"] += t2 - t1
    lam = gamma.copy()
    b = gamma.copy()
    big_l = f.copy()
    for r in range(1, nsym + 1):
        active = r > f
        if not active.any():
            continue
        idx = r - 1 - np.arange(width)
        valid = idx >= 0
        s_sel = np.zeros((m, width), dtype=np.uint8)
        s_sel[:, valid] = s[:, idx[valid]]
        delta = np.bitwise_xor.reduce(_MUL[lam, s_sel], axis=1)
        xb = np.zeros_like(b)
        xb[:, 1:] = b[:, :-1]
        nonzero = active & (delta != 0)
        tt = lam ^ _MUL[delta[:, None], xb]
        grow = nonzero & (2 * big_l <= r - 1 + f)
        b_new = np.where(grow[:, None], _MUL[_INV[delta][:, None], lam], xb)
        b = np.where(active[:, None], b_new, b)
        big_l = np.where(grow, r + f - big_l, big_l)
        lam = np.where(nonzero[:, None], tt, lam)
    degree = np.where(lam.any(axis=1), width - 1 - np.argmax(lam[:, ::-1] != 0, axis=1), 0)
    feasible = (degree == big_l) & (2 * (big_l - f) + f <= nsym) & (big_l <= n)
    t3 = pc()
    t["3 Berlekamp-Massey"] += t3 - t2
    values = rs_fast._eval_fixed(lam, tabs)
    roots = values == 0
    nroots = roots.sum(axis=1)
    feasible &= nroots == big_l
    feasible &= ~(er & ~roots).any(axis=1)
    t4 = pc()
    t["4 Chien search"] += t4 - t3
    omega = np.zeros((m, nsym), dtype=np.uint8)
    for k in range(width):
        coeff = lam[:, k]
        if not coeff.any():
            continue
        span = nsym - k
        if span <= 0:
            break
        omega[:, k:] ^= _MUL[coeff[:, None], s[:, :span]]
    deriv = np.zeros((m, width), dtype=np.uint8)
    deriv[:, 0:width - 1:2] = lam[:, 1::2]
    om_val = rs_fast._eval_fixed(omega, tabs)
    de_val = rs_fast._eval_fixed(deriv, tabs)
    feasible &= ~(roots & (de_val == 0)).any(axis=1)
    safe_de = np.where(de_val == 0, 1, de_val)
    magnitude = _MUL[_MUL[x_of[None, :], om_val], _INV[safe_de]]
    magnitude = np.where(roots, magnitude, 0).astype(np.uint8)
    candidate = cw[todo] ^ magnitude
    t5 = pc()
    t["5 Forney (omega, evaluations, magnitudes)"] += t5 - t4
    feasible &= ~rs_fast._syndromes_fast(candidate, tabs).any(axis=1)
    rows = todo[feasible]
    result[rows] = candidate[feasible]
    ok[rows] = True
    errata[rows] = nroots[feasible]
    t["6 re-check syndromes + scatter"] += pc() - t5
    return result, ok, errata


def native_profile(args) -> None:
    from vnxdna.v4 import codecs
    from vnxdna.v4 import decoder as de
    from vnxdna.v5 import native_alignment as na
    from vnxdna.v6 import native_rs as nr
    work = Path(args.workdir or tempfile.mkdtemp(prefix="vnx6-rsprof-"))
    work.mkdir(parents=True, exist_ok=True)
    reads = prepare(work)
    orig = codecs.InnerRS.decode
    codecs.InnerRS.decode = lambda self, codewords, erasures=None: nr.decode_batch(codewords, self.r, erasures)
    try:
        walls = []
        for k in range(3):
            t = time.perf_counter()
            res = de.decode_reads(reads, work / f"out-n{k}.vnx", de.DecodeOptions(workers=1), overwrite=True)
            walls.append(time.perf_counter() - t)
            assert res.status == "SUCCESS", res.status
        prof = cProfile.Profile()
        prof.enable()
        res = de.decode_reads(reads, work / "out-nprof.vnx", de.DecodeOptions(workers=1), overwrite=True)
        prof.disable()
    finally:
        codecs.InnerRS.decode = orig
    assert res.status == "SUCCESS"
    raw = pstats.Stats(prof).stats
    total = next(ct for (fn, _, name), (_, _, _, ct, _) in raw.items() if fn.endswith("decoder.py") and name == "decode_reads")
    rs_cum = sum(ct for (fn, _, name), (_, _, _, ct, _) in raw.items() if fn.endswith("native_rs.py") and name == "decode_batch")
    buf = io.StringIO()
    pstats.Stats(prof, stream=buf).sort_stats("cumulative").print_stats(25)
    cfg = {"input_size": SIZE, "pattern": "random", "seed": 42, "profile": "v4-balanced", "workers": 1, "channel": NOISY_CHANNEL}
    prov = provenance(cfg)
    st = nr.status()
    lines = [
        "V6 Phase 1 items 6+7 -- inner RS profile AFTER: native RS patched into InnerRS.decode in this process (SIMULATED channel data)",
        f"case: {json.dumps(cfg, sort_keys=True)}",
        f"commit {prov.get('commit_under_test')}  dirty={prov.get('worktree_dirty')}  cpu {prov.get('cpu_model')}  python {prov.get('python')}",
        f"alignment backend: {na.status()['active_backend']}  RS backend: {st['active_backend']} ({st['library']})",
        "",
        f"unprofiled decode wall, median of 3: {sorted(walls)[1]:.3f} s  (all: {', '.join(f'{w:.3f}' for w in walls)})",
        f"cProfile decode_reads cumulative: {total:.3f} s",
        f"inner RS stage (native_rs.decode_batch cumulative): {rs_cum:.3f} s = {rs_cum / total:.1%} of the profiled decode",
        "",
        "top 25 by cumulative time (cProfile):", buf.getvalue()]
    Path(args.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:9]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--out", default=str(HERE / "profile-before.txt"))
    ap.add_argument("--capture", default=None, help="where to save the captured RS workload (.npz)")
    ap.add_argument("--native", action="store_true", help="AFTER profile: inner RS through vnxdna.v6.native_rs")
    args = ap.parse_args()
    if args.native:
        native_profile(args)
        return
    work = Path(args.workdir or tempfile.mkdtemp(prefix="vnx6-rsprof-"))
    work.mkdir(parents=True, exist_ok=True)
    reads = prepare(work)
    from vnxdna.v4 import decoder as de
    from vnxdna.v5 import native_alignment as na

    calls: list[tuple[np.ndarray, int, np.ndarray | None, np.ndarray]] = []
    orig = rs_fast.decode_batch

    def capturing(codewords, nsym, erasures=None):
        out = orig(codewords, nsym, erasures)
        calls.append((np.array(codewords, dtype=np.uint8), nsym, None if erasures is None else np.array(erasures, bool), out[1].copy()))
        return out

    # unprofiled wall (median of 3), with capture off
    walls = []
    for k in range(3):
        t = time.perf_counter()
        res = de.decode_reads(reads, work / f"out-{k}.vnx", de.DecodeOptions(workers=1), overwrite=True)
        walls.append(time.perf_counter() - t)
        assert res.status == "SUCCESS", res.status
    rs_fast.decode_batch = capturing
    try:
        prof = cProfile.Profile()
        prof.enable()
        res = de.decode_reads(reads, work / "out-prof.vnx", de.DecodeOptions(workers=1), overwrite=True)
        prof.disable()
    finally:
        rs_fast.decode_batch = orig
    assert res.status == "SUCCESS"
    st = pstats.Stats(prof)
    raw = st.stats
    total = next(ct for (fn, _, name), (_, _, _, ct, _) in raw.items() if fn.endswith("decoder.py") and name == "decode_reads")
    rs_cum = sum(ct for (fn, _, name), (_, _, _, ct, _) in raw.items() if fn.endswith("rs_fast.py") and name == "decode_batch")
    rs_funcs = {f"{name} ({Path(fn).name})": {"calls": nc, "tottime": round(tt, 4), "cumtime": round(ct, 4)}
                for (fn, _, name), (_, nc, tt, ct, _) in raw.items() if fn.endswith(("rs_fast.py", "rs_batch.py"))}
    buf = io.StringIO()
    pstats.Stats(prof, stream=buf).sort_stats("cumulative").print_stats(25)

    # workload statistics
    words = sum(c[0].shape[0] for c in calls)
    shapes = Counter((c[0].shape[1], c[1]) for c in calls)
    fcount = Counter()
    okc = 0
    for cw, nsym, er, okv in calls:
        f = np.zeros(cw.shape[0], int) if er is None else er.sum(axis=1)
        fcount.update(np.minimum(f, 99).tolist())
        okc += int(okv.sum())
    batch_sizes = [c[0].shape[0] for c in calls]

    # phase split on the captured workload (rs_fast body with timers), verified identical
    phase: Counter = Counter()
    t_whole = 0.0
    for cw, nsym, er, _ in calls:
        e = np.zeros(cw.shape, bool) if er is None else er
        t = time.perf_counter()
        ref = orig(cw, nsym, er)
        t_whole += time.perf_counter() - t
        for start in range(0, cw.shape[0], MAX_BATCH):
            got = timed_block(np.ascontiguousarray(cw[start:start + MAX_BATCH]), nsym, e[start:start + MAX_BATCH], phase)
            for a, b in zip(got, ref):
                assert np.array_equal(a, b[start:start + MAX_BATCH])
    if args.capture:
        flat = {}
        for i, (cw, nsym, er, _) in enumerate(calls):
            flat[f"cw{i}"] = cw
            flat[f"nsym{i}"] = np.array(nsym)
            if er is not None:
                flat[f"er{i}"] = er
        np.savez_compressed(args.capture, **flat)

    cfg = {"input_size": SIZE, "pattern": "random", "seed": 42, "profile": "v4-balanced", "workers": 1, "channel": NOISY_CHANNEL}
    prov = provenance(cfg)
    lines = [
        "V6 Phase 1 items 6+7 -- inner RS profile BEFORE the native kernel (SIMULATED channel data)",
        f"case: {json.dumps(cfg, sort_keys=True)}",
        f"commit {prov.get('commit_under_test')}  dirty={prov.get('worktree_dirty')}  cpu {prov.get('cpu_model')}  python {prov.get('python')}",
        f"alignment backend: {na.status()['active_backend']}  RS module: vnxdna.v4.rs_fast (VNX_RS_REFERENCE unset)",
        "",
        f"unprofiled decode wall, median of 3: {sorted(walls)[1]:.3f} s  (all: {', '.join(f'{w:.3f}' for w in walls)})",
        f"cProfile decode_reads cumulative: {total:.3f} s",
        f"inner RS stage (rs_fast.decode_batch cumulative): {rs_cum:.3f} s = {rs_cum / total:.1%} of the profiled decode",
        "",
        "functions inside the inner RS stage (cProfile):",
    ]
    for k, v in sorted(rs_funcs.items(), key=lambda kv: -kv[1]["cumtime"]):
        lines.append(f"  {k:45s} calls {v['calls']:6d}  tottime {v['tottime']:8.4f} s  cumtime {v['cumtime']:8.4f} s")
    lines += ["", "captured workload:",
              f"  RS calls {len(calls)}, codewords {words}, (n, nsym) {dict(shapes)}, decoded ok {okc} ({okc / max(words, 1):.1%})",
              f"  batch size min/median/max {min(batch_sizes)}/{int(np.median(batch_sizes))}/{max(batch_sizes)}",
              "  flagged erasures per codeword (count: codewords): "
              + ", ".join(f"{k}: {v}" for k, v in sorted(fcount.items())),
              "",
              f"phase split inside _decode_block, captured workload replayed outside cProfile (whole decode_batch: {t_whole:.3f} s):"]
    tp = sum(phase.values())
    for k in sorted(phase):
        lines.append(f"  {k:45s} {phase[k]:8.4f} s  {phase[k] / tp:6.1%}")
    lines += ["", "top 25 by cumulative time (cProfile):", buf.getvalue()]
    Path(args.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    os.environ.pop("VNX_RS_REFERENCE", None)
    main()
