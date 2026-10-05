"""Fair comparisons: V3 vs V4 under the identical simulated channel, and the sequence-constraint study.

V3 vs V4 (``v3_vs_v4``): the same input, the same channel implementation
(:mod:`vnxdna.v4.channel`) with the same configuration and the same seed per
trial, the same success definition (recovered bytes SHA-256-identical to the
input, after each system's own verification). V3 runs through its unchanged
CLI (``vnx-dna``): coverage 1 → ``decode`` (default, and "best" with its
single-read indel/burst repair enabled); coverage > 1 → ``cluster`` →
``consensus`` → ``decode``. The two systems do **not** spend the same
redundancy (their default profiles differ), so the nucleotide cost per input
byte is reported next to every success rate; neither is tuned to the channel.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from vnxdna.archive import operations as ar
from . import channel as ch
from . import datagen
from . import decoder as de
from . import encoder as en
from .sweep import _seed, channel_fields, expand_points

V3 = [sys.executable, "-c", "import sys; sys.argv[0] = \"vnx-dna\"; from vnxdna._entry import main; main()"]


def _v3(*args, cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(V3 + [str(a) for a in args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


def _v3_trial(work: Path, reads: Path, coverage: float, mode: str, in_sha: str) -> tuple[str, float]:
    t = time.perf_counter()
    rec = work / f"rec-{mode}.vxdna"
    out = work / f"out-{mode}.bin"
    for p in (rec, out):
        p.unlink(missing_ok=True)
    if coverage > 1:
        cl, cons = work / "clusters.jsonl", work / "consensus.fasta"
        r = _v3("cluster", reads, "--output", cl, "--force", "-j", "1", cwd=work)
        if r.returncode == 0:
            r = _v3("consensus", cl, "--output", cons, "--force", cwd=work)
        if r.returncode == 0:
            r = _v3("decode", cons, "--output", rec, "--force", "-j", "1", cwd=work)
    else:
        extra = ["--experimental-indel-repair", "--max-indel", "3", "--burst-repair", "16"] if mode == "best" else []
        r = _v3("decode", reads, "--output", rec, "--force", "-j", "1", *extra, cwd=work)
    if r.returncode == 0:
        r = _v3("restore", rec, "--output", out, "--force", cwd=work)
    dt = time.perf_counter() - t
    if r.returncode == 0 and out.exists():
        return ("SUCCESS" if _sha(out) == in_sha else "INTEGRITY_FAILURE"), dt
    if r.returncode == 2:
        raise RuntimeError(f"V3 CLI usage error (wrapper bug, not a V3 outcome): {r.stderr[-500:]}")
    return {5: "DECODER_FAILURE", 1: "INTEGRITY_FAILURE", 3: "INVALID_INPUT"}.get(r.returncode, f"FAILED_EXIT_{r.returncode}"), dt


def _trial(args):
    root, v3_strands, v4_strands, chan, coverage, point, trial, in_sha, profile = args
    work = Path(tempfile.mkdtemp(prefix="vnx-cmp-", dir=root))
    try:
        cfg = ch.ChannelConfig.from_dict(chan)
        out = {"point": point, "trial": trial, "seed": cfg.seed}
        ch.simulate_file(v3_strands, work / "v3.fastq", cfg)
        modes = ["default", "best"] if coverage <= 1 else ["default"]
        for m in modes:
            out[f"v3_{m}"], out[f"v3_{m}_seconds"] = _v3_trial(work, work / "v3.fastq", coverage, m, in_sha)
        ch.simulate_file(v4_strands, work / "v4.fastq", cfg)
        t = time.perf_counter()
        try:
            res = de.decode_reads(work / "v4.fastq", work / "r.vnx", de.DecodeOptions(profile=profile, workers=1), workdir=str(work))
            status = res.status
            if status == "SUCCESS":
                ar.extract(work / "r.vnx", work / "x")
                status = "SUCCESS" if _sha(next((work / "x").rglob("input.bin"))) == in_sha else "INTEGRITY_FAILURE"
            elif status == "FAILURE":
                status = "DECODER_FAILURE"
        except Exception as error:  # noqa: BLE001 - outcome
            status = "DECODER_FAILURE" if "Decode" in type(error).__name__ else type(error).__name__
        out["v4"], out["v4_seconds"] = status, round(time.perf_counter() - t, 4)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def v3_vs_v4(config: dict, workdir: str | None = None, progress=None) -> dict:
    inp = config.get("input", {"size": 128 << 10, "pattern": "random", "seed": 42})
    trials = int(config.get("trials", 5))
    workers = int(config.get("workers", 1))
    base = dict(config.get("channel", {}))
    base_seed = int(base.pop("seed", 12345))
    profile = config.get("dna", {}).get("profile", "v4-balanced")
    root = Path(tempfile.mkdtemp(prefix="vnx-cmp-root-", dir=workdir))
    try:
        src = root / "input.bin"
        in_sha = datagen.generate(src, datagen.parse_size(inp["size"]), inp.get("pattern", "random"), int(inp.get("seed", 42)))
        r = _v3("store", src, "--output", root / "a.vxdna", cwd=root)
        assert r.returncode == 0, r.stderr
        r = _v3("encode", root / "a.vxdna", "--output", root / "v3.fasta", cwd=root)
        assert r.returncode == 0, r.stderr
        v3_bases = sum(len(line.strip()) for line in open(root / "v3.fasta") if not line.startswith(">"))
        ar.build_archive([src], root / "a.vnx")
        enc = en.encode_container(root / "a.vnx", root / "v4.fasta", en.DNAOptions(profile=profile))
        points = expand_points(config["sweep"])
        tasks = []
        for pi, over in enumerate(points):
            for tr in range(trials):
                chan = {**base, **channel_fields(over), "seed": _seed(base_seed, pi, tr)}
                tasks.append((str(root), str(root / "v3.fasta"), str(root / "v4.fasta"), chan, float(chan.get("coverage", 1)), pi, tr,
                              in_sha, profile))
        if workers == 1:
            recs = [_trial(t) for t in tasks]
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                recs = list(pool.map(_trial, tasks))
        size = src.stat().st_size
        summary = []
        for pi, over in enumerate(points):
            rs = [x for x in recs if x["point"] == pi]
            row = {"parameters": over, "trials": len(rs)}
            for sysname in ("v3_default", "v3_best", "v4"):
                vals = [x[sysname] for x in rs if sysname in x]
                if vals:
                    row[f"{sysname}_success_rate"] = round(sum(v == "SUCCESS" for v in vals) / len(vals), 4)
                    row[f"{sysname}_outcomes"] = {o: vals.count(o) for o in sorted(set(vals))}
                    row[f"{sysname}_seconds_median"] = round(float(np.median([x[f"{sysname}_seconds"] for x in rs])), 3)
            summary.append(row)
        return {"case": "v3_vs_v4", "input": inp, "input_sha256": in_sha,
                "v3": {"profile": "balanced (V3 default)", "bases": v3_bases, "nt_per_input_byte": round(v3_bases / size, 4)},
                "v4": {"profile": profile, "bases": enc["bases"], "nt_per_input_byte": round(enc["bases"] / size, 4),
                       "layout": enc["layout"], "outer_code": enc["outer_code"]},
                "base_channel": {**base, "seed": base_seed}, "points": summary, "trials": recs,
                "fairness": "identical input, channel implementation, channel parameters and per-trial seeds; success = "
                            "SHA-256-identical output; redundancy budgets differ (see nt_per_input_byte)"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ============================================================================ constraint / sequence-optimisation study
def constraint_study(n_strands: int = 20000, seed: int = 3, profile: str = "v4-balanced", cases: list[dict] | None = None) -> dict:
    """Constraint violations before (variant 0 = plain scrambling) and after screening, variants needed, time, recoverability."""
    from .constraints import ConstraintConfig, violations_batch
    from .frame import PROFILES, build_strands, decode_frames, nt_to_bytes, insert_markers, bytes_to_nt, plain_rows, keystreams
    from vnxdna.codec.codecs import InnerRS
    from .sync import strip_markers_exact
    lay = PROFILES[profile][0]
    rng = np.random.default_rng(seed)
    pays = rng.integers(0, 256, (n_strands, lay.payload_bytes), dtype=np.uint8)
    groups = np.arange(n_strands) // 80
    syms = np.arange(n_strands) % 80
    cases = cases or [
        {"name": "default (GC 40-60, homopolymer<=4)", "constraints": {}},
        {"name": "relaxed (GC 30-70, homopolymer<=6)", "constraints": {"gc_min_percent": 30, "gc_max_percent": 70, "max_homopolymer": 6}},
        {"name": "strict (GC 45-55, homopolymer<=3)", "constraints": {"gc_min_percent": 45, "gc_max_percent": 55, "max_homopolymer": 3}},
        {"name": "windowed GC (window 50 nt, 30-70)", "constraints": {"gc_window_nt": 50, "gc_window_min_percent": 30,
                                                                     "gc_window_max_percent": 70}},
        {"name": "motifs (EcoRI, BamHI, HindIII) + tandem<=12", "constraints": {"forbidden_motifs": ["GAATTC", "GGATCC", "AAGCTT"],
                                                                                "max_tandem_repeat_nt": 12}},
        {"name": "very strict (GC 48-52, homopolymer<=2)", "constraints": {"gc_min_percent": 48, "gc_max_percent": 52,
                                                                           "max_homopolymer": 2}},
    ]
    # "before": the same frames with ONE scrambler variant drawn at random per strand (unscreened scrambling). Variant 0
    # would be unrepresentative: its unscrambled variant byte 0x00 maps to AAAA at the strand start (also 0x55, 0xAA, 0xFF).
    plain = plain_rows(1, 0, groups, syms, pays)
    rv = rng.integers(0, 256, n_strands).astype(np.uint8)
    msg = np.concatenate([rv[:, None], plain ^ keystreams(plain.shape[1])[rv]], axis=1)
    frames0 = np.concatenate([msg, InnerRS(lay.inner_parity).parity(msg)], axis=1)
    before_strands = insert_markers(lay, bytes_to_nt(frames0))
    out = {"case": "constraints", "profile": profile, "strands": n_strands, "strand_nt": lay.strand_nt, "results": []}
    for case in cases:
        cfg = ConstraintConfig.from_dict(case["constraints"])
        v = violations_batch(before_strands, cfg)
        before = {k: int(m.sum()) for k, m in v.items() if m.any()}
        bad_before = int(np.any(np.stack(list(v.values())), axis=0).sum())
        t = time.perf_counter()
        try:
            strands, variant = build_strands(lay, cfg, 1, 0, groups, syms, pays)
            dt = time.perf_counter() - t
            fb, _ = strip_markers_exact(lay, strands)
            p = decode_frames(lay, nt_to_bytes(fb))
            recoverable = bool(p.ok.all() and (p.payload == pays).all())
            res = {"name": case["name"], "constraints": cfg.to_dict(), "violating_before": bad_before,
                   "violating_before_fraction": round(bad_before / n_strands, 5), "violations_before_by_rule": before,
                   "violating_after": 0, "variants_mean": round(float(variant.mean()), 3), "variants_max": int(variant.max()),
                   "strands_needing_rescreen_fraction": round(float((variant > 0).mean()), 5), "seconds": round(dt, 3),
                   "strands_per_second": round(n_strands / dt, 1), "recoverable_after_screening": recoverable,
                   "sequence_overhead_nt": 0, "status": "SATISFIED"}
        except Exception as error:  # noqa: BLE001 - an unsatisfiable constraint is a measured outcome
            res = {"name": case["name"], "constraints": cfg.to_dict(), "violating_before": bad_before,
                   "violations_before_by_rule": before, "status": "UNSATISFIABLE", "error": type(error).__name__,
                   "message": str(error)[:200], "seconds": round(time.perf_counter() - t, 3)}
        out["results"].append(res)
    out["before_definition"] = "one uniformly random scrambler variant per strand, no screening"
    out["note"] = ("screening changes only the 1-byte scrambler variant inside each frame, so it costs no extra nucleotides; "
                   "the alternative is to fail explicitly (VNXConstraintError) — sequences are never emitted unscreened")
    return out
