"""Container → DNA strands with the V6 outer code (superblock version 2; streaming, parallel, deterministic).

Same frames, constraint screening and superblock coding as V4 (vnxdna.v4.encoder); what changes is the outer layer
(column-parity groups per stripe, vnxdna.v6.outer) and, with ``strand_order="interleaved"``, the order in which
strands are written. Work is split into tasks of whole stripes; each worker reads its stripes' container bytes,
builds the full-position row codewords of the data rows and of the column-parity rows, and emits the stripe's
strands in file order.
"""
from __future__ import annotations

import os
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from ..v2.strandio import StrandWriter, format_for_output
from vnxdna.archive import container as ct
from vnxdna.codec.codecs import CauchyRSCodec
from ..v4.constraints import ConstraintConfig
from ..v4.encoder import _ASCII, DNAOptions, SB_VERSION_V6, Superblock, _labels
from vnxdna.core.errors import VNXConfigurationError
from ..v4.frame import KIND_DATA, KIND_SUPER, Layout, build_strands
from vnxdna.core.util import sha256_file
from vnxdna.codec.outer import Geometry, column_parity_rows, plan, row_codewords

_W: dict = {}


def resolve_geometry(opt: DNAOptions, lay: Layout, K: int, M: int, size: int) -> tuple[Geometry, dict | None]:
    """The archive's outer geometry from the options (adaptive plan or fixed K/M/D/Mc)."""
    P = lay.payload_bytes
    if opt.outer_plan == "adaptive":
        budget = opt.redundancy_budget if opt.redundancy_budget is not None else M / K
        p = plan(size, P, budget, order=opt.strand_order or "interleaved")
        return p.geometry, {"mode": "adaptive", "redundancy_budget": budget, **p.to_dict()}
    groups = -(-size // (K * P))
    depth = opt.stripe_depth or (min(groups, 65535) if opt.column_parity == 0 else min(groups, 128))
    geo = Geometry(K, M, depth, opt.column_parity, P, size, opt.strand_order or "sequential")
    try:
        geo.validate()
    except VNXConfigurationError as error:
        raise VNXConfigurationError(f"invalid V6 outer geometry: {error}") from None
    return geo, None


def _records(codes: np.ndarray, labels: list[bytes], fmt: str) -> list[bytes]:
    n, length = codes.shape
    seq = np.empty((n, length + 1), dtype=np.uint8)
    seq[:, :length] = _ASCII[codes]
    seq[:, length] = 10
    rows = seq.tobytes()
    w = length + 1
    if fmt == "fastq":
        q = b"+\n" + b"I" * length + b"\n"
        return [b"@" + lab + b"\n" + rows[i * w:(i + 1) * w] + q for i, lab in enumerate(labels)]
    return [b">" + lab + b"\n" + rows[i * w:(i + 1) * w] for i, lab in enumerate(labels)]


def _init(container: str, geo: Geometry, lay: Layout, cfg: dict, tag: int, fmt: str) -> None:
    _W.update(container=container, geo=geo, lay=lay, cfg=ConstraintConfig.from_dict(cfg), tag=tag, fmt=fmt)


def stripe_payloads(geo: Geometry, raw: bytes, s: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(groups, symbols, payloads) of stripe s in file order; ``raw`` holds the stripe's container bytes."""
    K, M, P = geo.K, geo.M, geo.P
    data_g, par_g = geo.stripe_rows(s)
    d = len(data_g)
    buf = np.zeros(d * K * P, dtype=np.uint8)
    buf[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
    blocks = buf.reshape(d, K, P)
    cw = row_codewords(blocks, K, M)                                    # (d, n, P)
    if geo.Mc:
        cw = np.concatenate([cw, row_codewords(column_parity_rows(blocks, geo.D, geo.Mc), K, M)])
    row = {g: r for r, g in enumerate(data_g + par_g)}
    order = geo.stripe_order(s)
    groups = np.fromiter((g for g, _ in order), dtype=np.int64, count=len(order))
    syms = np.fromiter((t for _, t in order), dtype=np.int64, count=len(order))
    rows = np.fromiter((row[g] for g, _ in order), dtype=np.int64, count=len(order))
    pos = np.fromiter((geo.position(g, t) for g, t in order), dtype=np.int64, count=len(order))
    return groups, syms, cw[rows, pos]


def _encode_task(task: tuple[int, int]) -> tuple[int, list[bytes], int]:
    s0, s1 = task
    geo: Geometry = _W["geo"]
    lay: Layout = _W["lay"]
    K, P = geo.K, geo.P
    gs, ss, ps = [], [], []
    with open(_W["container"], "rb") as f:
        for s in range(s0, s1):
            data_g, _ = geo.stripe_rows(s)
            raw = os.pread(f.fileno(), len(data_g) * K * P, data_g[0] * K * P)
            g, t, p = stripe_payloads(geo, raw, s)
            gs.append(g)
            ss.append(t)
            ps.append(p)
    groups, syms, payloads = np.concatenate(gs), np.concatenate(ss), np.concatenate(ps)
    strands, variants = build_strands(lay, _W["cfg"], _W["tag"], KIND_DATA, groups, syms, payloads)
    return s0, _records(strands, _labels(_W["tag"], KIND_DATA, groups, syms), _W["fmt"]), int(variants.max(initial=0))


def encode_container_v6(container_path: str | os.PathLike, output: str | os.PathLike, opt: DNAOptions, *,
                        overwrite: bool = False, progress=None) -> dict:
    lay, K, M = opt.resolve()
    t0 = time.perf_counter()
    container_path = Path(container_path)
    c = ct.open_container(container_path)
    size = c.size
    aid = c.archive_id
    tag = int.from_bytes(aid[:2], "big")
    P = lay.payload_bytes
    geo, plan_info = resolve_geometry(opt, lay, K, M, size)
    sb = Superblock("cauchy-rs", geo.K, geo.M, lay, "dense", 0, aid, size, bytes.fromhex(sha256_file(container_path)),
                    ct.HEADER_BYTES + c.manifest["counts"]["stored_bytes"], geo.G, SB_VERSION_V6, geo.D, geo.Mc, geo.order)
    fmt = format_for_output(output, opt.fmt)
    if fmt == "vxs":
        raise VNXConfigurationError("VXS output is not supported for V4 strands (use FASTA or FASTQ)")
    ks, ms = Superblock.symbols(P)
    sb_data = np.zeros((ks, P), dtype=np.uint8)
    raw = sb.pack()
    sb_data.reshape(-1)[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
    sb_coded = CauchyRSCodec(ks, ms).encode(sb_data)
    sb_strands, _ = build_strands(lay, opt.constraints, tag, KIND_SUPER, np.zeros(ks + ms, dtype=np.int64),
                                  np.arange(ks + ms, dtype=np.int64), sb_coded)
    sb_recs = _records(sb_strands, _labels(tag, KIND_SUPER, np.zeros(ks + ms, dtype=np.int64), np.arange(ks + ms)), fmt)
    slots = geo.superblock_slots(len(sb_recs), geo.strands())
    per_task = max(1, opt.groups_per_task // max(1, geo.D + geo.Mc))
    tasks = [(s, min(geo.stripes, s + per_task)) for s in range(0, geo.stripes, per_task)]
    initargs = (str(container_path), geo, lay, opt.constraints.to_dict(), tag, fmt)
    nt = lay.strand_nt
    state = {"written": 0, "next_sb": 0, "strands": 0, "max_variant": 0}

    with StrandWriter(output, fmt, overwrite=overwrite) as w:
        def put_sb_until(count: int, out: list[bytes]) -> None:
            while state["next_sb"] < len(sb_recs) and slots[state["next_sb"]] <= count:
                out.append(sb_recs[state["next_sb"]])
                state["next_sb"] += 1
                state["strands"] += 1

        def take(res):
            s0, recs, mv = res
            out: list[bytes] = []
            for r in recs:
                put_sb_until(state["written"], out)
                out.append(r)
                state["written"] += 1
                state["strands"] += 1
            w.write_bytes(b"".join(out), len(out), len(out) * nt)
            state["max_variant"] = max(state["max_variant"], mv)
            if progress:
                progress({"stage": "encode", "stripes_done": s0 + per_task, "stripes": geo.stripes,
                          "elapsed": time.perf_counter() - t0})

        if opt.workers == 1:
            _init(*initargs)
            for t in tasks:
                take(_encode_task(t))
        else:
            with ProcessPoolExecutor(max_workers=opt.workers, initializer=_init, initargs=initargs) as pool:
                window: deque = deque()
                for t in tasks:
                    window.append(pool.submit(_encode_task, t))
                    if len(window) >= 2 * opt.workers:
                        take(window.popleft().result())
                while window:
                    take(window.popleft().result())
        tail: list[bytes] = []
        put_sb_until(1 << 62, tail)
        if tail:
            w.write_bytes(b"".join(tail), len(tail), len(tail) * nt)
        info = w.commit()
    strands_total = state["strands"]
    bases_total = strands_total * nt
    data_symbols = geo.data_strands()
    seconds = time.perf_counter() - t0
    content = c.manifest["counts"]["content_bytes"]
    return {
        "output": str(output), "format": fmt, "strands": strands_total, "bases": bases_total, "strand_nt": nt,
        "container_bytes": size, "content_bytes": content, "archive_id": aid.hex(), "archive_tag": f"{tag:04x}",
        "layout": lay.to_dict(), "outer_code": {"name": "cauchy-rs", "data_symbols": geo.K, "parity_symbols": geo.M},
        "outer_capabilities": {"type": "erasure", "mds": True, "rows": f"any {geo.M} of {geo.n} symbols per row",
                               "columns": f"any {geo.Mc} of {geo.D + geo.Mc} rows per stripe and position"},
        "outer_v6": {"superblock_version": SB_VERSION_V6, **geo.to_dict(), "plan": plan_info},
        "groups": geo.G, "superblock_strands": ks + ms,
        "overhead": {
            "nt_per_container_byte": round(bases_total / size, 4), "nt_per_content_byte": round(bases_total / max(1, content), 4),
            "bits_per_nt_net": round(8 * content / bases_total, 4) if bases_total else 0,
            "outer_parity_fraction": round((strands_total - (ks + ms) - data_symbols) / max(1, strands_total), 5),
            "frame_payload_fraction": round(P / lay.frame_bytes, 5),
            "marker_fraction": round(lay.markers * lay.marker_len / lay.strand_nt, 5),
            "address_fraction": round(10 / lay.frame_bytes, 5),
        },
        "max_scrambler_variant": state["max_variant"], "file_sha256": info["file_sha256"], "seconds": seconds,
        "container_mb_s": round(size / seconds / 1e6, 3) if seconds else None, "workers": opt.workers,
    }
