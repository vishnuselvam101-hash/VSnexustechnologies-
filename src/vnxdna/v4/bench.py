"""Benchmark framework: end-to-end, per-stage throughput, worker scaling, memory scaling and codec comparison.

Every measurement runs in a fresh child process (``multiprocessing`` spawn), so
peak RSS is that case's own maximum (child + its worker processes, from
``getrusage``), not an artefact of earlier cases. Results are JSON with the
environment and the resolved configuration; :func:`markdown_report` renders a
human-readable table.

The headline system metric is **recoverable bytes per second**: original
content bytes divided by the decode time, counted only when the decode
SUCCEEDED (verified container). Bases per second are reported too but are not
the figure of merit.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import resource
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from . import archive as ar
from . import channel as ch
from . import datagen
from . import decoder as de
from . import encoder as en
from .util import environment


def _rss_now() -> dict:
    self_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    kids_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return {"peak_rss_mb": round(max(self_kb, kids_kb) / 1024, 1), "peak_rss_self_mb": round(self_kb / 1024, 1),
            "peak_rss_workers_mb": round(kids_kb / 1024, 1)}


def _child(fn_name: str, kwargs: dict, q) -> None:
    try:
        res = globals()[fn_name](**kwargs)
        res.update(_rss_now())
        q.put(("ok", res))
    except BaseException as error:  # noqa: BLE001 - reported to the parent
        q.put(("error", {"error": type(error).__name__, "message": str(error)[:2000]}))


def isolated(fn_name: str, **kwargs) -> dict:
    """Run a benchmark function in a fresh process; returns its result dict (+ peak RSS)."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_child, args=(fn_name, kwargs, q))
    p.start()
    import queue
    while True:                      # never block forever: a child that dies without reporting is an ERROR result
        try:
            status, res = q.get(timeout=1.0)
            break
        except queue.Empty:
            if not p.is_alive():
                p.join()
                return {"status": "ERROR", "error": "ChildDied", "message": f"benchmark process exited with code {p.exitcode}",
                        "case": fn_name}
    p.join()
    if status != "ok":
        res["status"] = "ERROR"
    return res


# ============================================================================ end-to-end
def end_to_end(input_size: int, pattern: str = "mixed", seed: int = 42, profile: str = "v4-balanced", workers: int = 1,
               channel: dict | None = None, outer_code: str = "cauchy-rs", data_symbols: int | None = None,
               parity_symbols: int | None = None, chunk_size: int = 1 << 20, workdir: str | None = None, keep: bool = False,
               decode_overrides: dict | None = None) -> dict:
    """Generate → archive → DNA → (channel) → decode → verify. Returns timings, sizes and the outcome."""
    tmp = Path(tempfile.mkdtemp(prefix="vnx4-bench-", dir=workdir))
    try:
        src = tmp / "input.bin"
        t = time.perf_counter()
        in_sha = datagen.generate(src, input_size, pattern, seed)
        gen_s = time.perf_counter() - t
        arc = ar.build_archive([src], tmp / "a.vnx", ar.ArchiveOptions(chunk_size=chunk_size, workers=workers))
        dna = en.encode_container(tmp / "a.vnx", tmp / "s.fasta",
                                  en.DNAOptions(profile=profile, workers=workers, outer_code=outer_code, data_symbols=data_symbols,
                                                parity_symbols=parity_symbols, experimental=True))
        reads = tmp / "s.fasta"
        chan = None
        if channel:
            cfg = ch.ChannelConfig.from_dict(channel)
            chan = ch.simulate_file(tmp / "s.fasta", tmp / "r.fastq", cfg, workers=workers)
            reads = tmp / "r.fastq"
        status, dec_report, out_sha, err = "FAILURE", {}, None, None
        t = time.perf_counter()
        try:
            res = de.decode_reads(reads, tmp / "r.vnx", de.DecodeOptions(profile=profile, workers=workers, **(decode_overrides or {})))
            status, dec_report = res.status, res.report
        except Exception as error:  # noqa: BLE001 - every failure is a measured outcome
            err = {"error": type(error).__name__, "message": str(error)[:500]}
            dec_report = getattr(error, "details", {}) or {}
        decode_s = time.perf_counter() - t
        extract_s = None
        if status == "SUCCESS":
            t = time.perf_counter()
            ar.extract(tmp / "r.vnx", tmp / "out")
            extract_s = time.perf_counter() - t
            out_sha = _sha_file(tmp / "out" / "input.bin")      # streamed: must not inflate the measured peak RSS
            if out_sha != in_sha:
                status = "INTEGRITY_FAILURE"      # must never happen: the container SHA-256 was verified
        mb = input_size / 1e6
        return {
            "case": "end_to_end", "input_size": input_size, "pattern": pattern, "seed": seed, "profile": profile, "workers": workers,
            "outer_code": dna["outer_code"], "layout": dna["layout"], "container_bytes": arc.container_bytes,
            "stored_bytes": arc.stored_bytes, "strands": dna["strands"], "bases": dna["bases"],
            "nt_per_input_byte": round(dna["bases"] / max(1, input_size), 4), "overhead": dna["overhead"],
            "generate_seconds": round(gen_s, 4), "archive_seconds": round(arc.seconds, 4), "encode_seconds": round(dna["seconds"], 4),
            "channel_seconds": round(chan["seconds"], 4) if chan else None, "decode_seconds": round(decode_s, 4),
            "extract_seconds": round(extract_s, 4) if extract_s is not None else None,
            "archive_mb_s": round(mb / arc.seconds, 3) if arc.seconds else None,
            "encode_mb_s": round(mb / (arc.seconds + dna["seconds"]), 3),
            "decode_mb_s": round(mb / decode_s, 3) if decode_s else None,
            "recoverable_mb_s": round(mb / decode_s, 3) if status == "SUCCESS" and decode_s else 0.0,
            "recoverable_tb_per_day": round(mb / decode_s * 86400 / 1e6, 6) if status == "SUCCESS" and decode_s else 0.0,
            "status": status, "error": err, "input_sha256": in_sha, "output_sha256": out_sha,
            "channel": chan["config"] if chan else None,
            "channel_stats": {k: v for k, v in (chan or {}).items() if isinstance(v, int)},
            "decode_reads": dec_report.get("reads"), "groups_failed": dec_report.get("groups_failed"),
            "reads_per_second": round(dec_report["reads"]["reads"] / decode_s, 1) if dec_report.get("reads") and decode_s else None,
        }
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()


# ============================================================================ per-stage throughput
def stages(size: int = 4 << 20, seed: int = 1, profile: str = "v4-balanced") -> dict:
    """Throughput of each stage on the same deterministic data (single core)."""
    import zstandard
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from . import merkle
    from .codecs import CauchyRSCodec, InnerRS
    from .constraints import ConstraintConfig, satisfied_batch
    from .frame import PROFILES, build_strands, decode_frames, insert_markers, bytes_to_nt, nt_to_bytes
    from .sync import TemplateAligner, frame_erasures_to_bytes

    rng = np.random.default_rng(seed)
    tmp = Path(tempfile.mkdtemp(prefix="vnx4-stages-"))
    try:
        src = tmp / "x.bin"
        datagen.generate(src, size, "mixed", seed)
        data = src.read_bytes()
        mb = size / 1e6
        out: dict = {"case": "stages", "input_size": size, "profile": profile}

        def timed(fn, reps=1):
            t = time.perf_counter()
            for _ in range(reps):
                r = fn()
            return (time.perf_counter() - t) / reps, r

        cctx, dctx = zstandard.ZstdCompressor(level=3), zstandard.ZstdDecompressor()
        chunks = [data[i:i + (1 << 20)] for i in range(0, size, 1 << 20)]
        s, comp = timed(lambda: [cctx.compress(c) for c in chunks])
        out["compression_mb_s"] = round(mb / s, 1)
        out["compression_ratio"] = round(size / sum(len(c) for c in comp), 3)
        s, _ = timed(lambda: [dctx.decompress(c) for c in comp])
        out["decompression_mb_s"] = round(mb / s, 1)
        aead = AESGCM(os.urandom(32))
        s, _ = timed(lambda: [aead.encrypt(i.to_bytes(12, "big"), c, b"") for i, c in enumerate(chunks)])
        out["encryption_mb_s"] = round(mb / s, 1)
        s, _ = timed(lambda: [hashlib.sha256(c).digest() for c in chunks])
        out["chunk_hash_mb_s"] = round(mb / s, 1)
        t0 = time.perf_counter()
        ar.build_archive([src], tmp / "a.vnx", ar.ArchiveOptions())
        out["archive_mb_s"] = round(mb / (time.perf_counter() - t0), 1)
        lay, K, M = PROFILES[profile]
        P = lay.payload_bytes
        groups = size // (K * P)
        blocks = np.frombuffer(data[: groups * K * P], dtype=np.uint8).reshape(groups, K, P)
        code = CauchyRSCodec(K, M)
        s, coded = timed(lambda: code.encode_many(blocks))
        out["outer_ecc_encode_mb_s"] = round(groups * K * P / 1e6 / s, 1)
        present = np.ones((min(groups, 2000), K + M), dtype=bool)
        for g in range(present.shape[0]):
            present[g, rng.permutation(K + M)[:M]] = False
        sh = coded[: present.shape[0]]
        s, _ = timed(lambda: code._code.decode(sh, present))
        out["outer_ecc_decode_mb_s_max_erasures"] = round(present.shape[0] * K * P / 1e6 / s, 1)
        n = min(groups * (K + M), 60_000)
        pays = coded.reshape(-1, P)[:n]
        cfg = ConstraintConfig()
        s, (strands, _) = timed(lambda: build_strands(lay, cfg, 1, 0, np.arange(n), np.zeros(n), pays))
        out["dna_encoding_mb_s"] = round(n * P / 1e6 / s, 2)
        out["dna_encoding_strands_s"] = round(n / s, 1)
        frames = np.zeros((n, lay.frame_bytes), dtype=np.uint8)
        s, _ = timed(lambda: insert_markers(lay, bytes_to_nt(frames)))
        out["dna_mapping_mb_s"] = round(n * lay.frame_bytes / 1e6 / s, 1)
        s, _ = timed(lambda: satisfied_batch(strands, cfg))
        out["dna_validation_mbases_s"] = round(strands.size / 1e6 / s, 1)
        inner = InnerRS(lay.inner_parity)
        s, _ = timed(lambda: inner.parity(frames[:, : lay.frame_bytes - lay.inner_parity]))
        out["inner_rs_encode_mb_s"] = round(n * lay.frame_bytes / 1e6 / s, 1)
        from .sync import strip_markers_exact
        fb, _ = strip_markers_exact(lay, strands)
        fr = nt_to_bytes(fb)
        noisy = fr.copy()
        for i in range(n):
            pos = rng.permutation(lay.frame_bytes)[: lay.inner_parity // 4]
            noisy[i, pos] ^= 1
        s, p = timed(lambda: decode_frames(lay, fr))
        out["frame_check_clean_frames_s"] = round(n / s, 1)
        s, p = timed(lambda: decode_frames(lay, noisy))
        out["inner_rs_decode_noisy_frames_s"] = round(n / s, 1)
        cfgc = ch.ChannelConfig(substitution_rate=0.002, insertion_rate=0.001, deletion_rate=0.001, coverage=1).validate()
        s, sim = timed(lambda: ch.simulate_batch(strands[:20000], cfgc, 0))
        out["channel_simulation_mbases_s"] = round(sim["stats"]["bases"] / 1e6 / s, 2)
        offs = np.concatenate([[0], np.cumsum(sim["lengths"])])
        reads = [sim["codes"][offs[i]:offs[i + 1]] for i in range(min(5000, sim["lengths"].size))]
        al = TemplateAligner(lay)
        s, pr = timed(lambda: al.project(reads))
        out["sync_alignment_reads_s"] = round(len(reads) / s, 1)
        s, _ = timed(lambda: decode_frames(lay, nt_to_bytes(np.minimum(pr.bases, 3)), frame_erasures_to_bytes(pr.erased)))
        out["sync_inner_decode_reads_s"] = round(len(reads) / s, 1)
        leaves = [hashlib.sha256(i.to_bytes(8, "big")).digest() for i in range(100_000)]
        s, _ = timed(lambda: merkle.root_from_leaves(leaves))
        out["merkle_leaves_s"] = round(len(leaves) / s, 1)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================ codec comparison (fair: same budget, same channel)
def codec_compare(k: int = 256, budget: float = 0.25, symbol_bytes: int = 40, loss_rates=(0.0, 0.05, 0.1, 0.15, 0.18, 0.2, 0.22),
                  trials: int = 200, seed: int = 7, rs_groups=((64, 16), (200, 50))) -> dict:
    """Erasure-only comparison of outer codes at the same redundancy budget under i.i.d. symbol loss.

    The same total data (``k`` × rs-group multiples) and the same parity budget are used for every code; a trial
    succeeds when all data symbols are recovered. Reports success rates, encode/decode time per MB.
    """
    from .codecs import CauchyRSCodec, LTFountainCodec
    rng_master = np.random.default_rng(seed)
    total_k = int(np.lcm.reduce([k] + [g[0] for g in rs_groups]))
    while total_k < 1000:
        total_k *= 2
    data = rng_master.integers(0, 256, (total_k, symbol_bytes), dtype=np.uint8)
    schemes = []
    for gk, gm in rs_groups:
        schemes.append((f"cauchy-rs({gk}+{gm})", "rs", gk, gm))
    m_lt = int(round(k * budget))
    schemes.append((f"fountain-dense({k}+{m_lt})", "lt-dense", k, m_lt))
    schemes.append((f"fountain-soliton({k}+{m_lt})", "lt-soliton", k, m_lt))
    out = {"case": "codec_compare", "total_data_symbols": total_k, "symbol_bytes": symbol_bytes, "budget": budget, "trials": trials,
           "loss_rates": list(loss_rates), "seed": seed, "schemes": {}}
    for name, kind, gk, gm in schemes:
        if kind == "rs":
            codec = CauchyRSCodec(gk, gm)
        else:
            codec = LTFountainCodec(gk, gm, distribution="dense" if kind == "lt-dense" else "robust-soliton")
        nblocks = total_k // gk
        t = time.perf_counter()
        coded = [codec.encode_block(data[b * gk:(b + 1) * gk], b) if kind != "rs" else codec.encode(data[b * gk:(b + 1) * gk])
                 for b in range(nblocks)]
        enc_s = time.perf_counter() - t
        res = {"data_symbols_per_block": gk, "parity_symbols_per_block": gm, "blocks": nblocks,
               "overhead": round(gm / gk, 4), "encode_mb_s": round(total_k * symbol_bytes / 1e6 / enc_s, 2), "success_rate": {},
               "decode_seconds_per_trial": {}}
        for p in loss_rates:
            ok = 0
            dt = 0.0
            for tr in range(trials):
                rng = np.random.default_rng([seed, int(p * 1e6), tr])
                success = True
                t = time.perf_counter()
                for b in range(nblocks):
                    n = coded[b].shape[0]
                    keep = np.flatnonzero(rng.random(n) >= p)
                    syms = {int(i): coded[b][i] for i in keep}
                    try:
                        got = codec.decode_block(syms, gk, symbol_bytes, b) if kind != "rs" else codec.decode(syms, gk, symbol_bytes)
                        if not (got == data[b * gk:(b + 1) * gk]).all():
                            raise AssertionError("wrong decode")     # would be a correctness bug, never a statistic
                    except AssertionError:
                        raise
                    except Exception:  # noqa: BLE001 - decode failure is the measured outcome
                        success = False
                        break
                dt += time.perf_counter() - t
                ok += success
            res["success_rate"][str(p)] = round(ok / trials, 4)
            res["decode_seconds_per_trial"][str(p)] = round(dt / trials, 5)
        out["schemes"][name] = res
    return out


# ============================================================================ reports
def markdown_report(results: list[dict] | dict, title: str = "VNX-DNA V4 benchmark") -> str:
    rows = results if isinstance(results, list) else [results]
    lines = [f"# {title}", "", "All results are software measurements on simulated data; nothing was synthesised or sequenced.", ""]
    e2e = [r for r in rows if r.get("case") == "end_to_end"]
    if e2e:
        lines += ["| input | pattern | workers | channel | status | nt/B | encode MB/s | decode MB/s | recoverable MB/s | peak RSS MB |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in e2e:
            chn = "clean" if not r.get("channel") else ", ".join(f"{k.split('_')[0]}={v}" for k, v in r["channel"].items()
                                                               if k in ("substitution_rate", "insertion_rate", "deletion_rate",
                                                                        "dropout_rate", "coverage") and v)
            lines.append(f"| {r['input_size']:,} | {r['pattern']} | {r['workers']} | {chn} | {r['status']} | {r['nt_per_input_byte']} | "
                         f"{r['encode_mb_s']} | {r['decode_mb_s']} | {r['recoverable_mb_s']} | {r.get('peak_rss_mb')} |")
        lines.append("")
    for r in rows:
        if r.get("case") == "stages":
            lines += ["## Stage throughput (single core)", "", "| stage | value |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in r.items() if k not in ("case",)]
            lines.append("")
        if r.get("case") == "codec_compare":
            lines += [f"## Outer codes at {r['budget']:.0%} redundancy, i.i.d. symbol loss ({r['trials']} trials)", "",
                      "| scheme | " + " | ".join(f"loss {p}" for p in r["loss_rates"]) + " | encode MB/s |",
                      "|---|" + "---|" * (len(r["loss_rates"]) + 1)]
            for name, s in r["schemes"].items():
                lines.append(f"| {name} | " + " | ".join(str(s["success_rate"][str(p)]) for p in r["loss_rates"]) +
                             f" | {s['encode_mb_s']} |")
            lines.append("")
    return "\n".join(lines) + "\n"


def run_suite(profile_name: str = "balanced", sizes=(1 << 20,), output: str | None = None) -> dict:
    """The default ``vnx benchmark`` suite: stage throughput + clean and noisy end-to-end runs."""
    from .config import performance
    perf = performance(profile_name)
    results = [isolated("stages")]
    for size in sizes:
        results.append(isolated("end_to_end", input_size=size, workers=perf["workers"]))
        results.append(isolated("end_to_end", input_size=size, workers=perf["workers"],
                                channel={"substitution_rate": 0.002, "insertion_rate": 0.0005, "deletion_rate": 0.0005,
                                         "dropout_rate": 0.02, "coverage": 5, "coverage_model": "poisson", "seed": 12345}))
    doc = {"vnx_benchmark": 1, "environment": environment(), "performance_profile": profile_name, "performance": perf,
           "results": results}
    if output:
        Path(output).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        Path(output).with_suffix(".md").write_text(markdown_report(results))
    return doc
