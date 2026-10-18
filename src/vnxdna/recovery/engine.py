"""The V13 streaming engine seen from the recovery layer: builds the engine's parameters from a layout and the decode
options (:func:`params_for`) and runs pass 1 (:func:`run_pass1`); the ctypes binding itself is
:mod:`vnxdna.native.engine` (layer 1, which imports nothing above ``vnxdna.native``)."""
from __future__ import annotations

import os

import numpy as np

from vnxdna.core.version import FRAME_VERSION
from vnxdna.dnaenc.layout import CRC_BYTES, HEADER_BYTES, Layout
from vnxdna.dnaenc.scrambler import keystreams
from vnxdna.native import engine as _ne
from vnxdna.native.align import geometry
from vnxdna.native.engine import (Cancel, EngineCancelled, EngineError, EngineParams, EngineUnsupported, STAT_NAMES,  # noqa: F401
                                  available, kernel_levels, resolve_backend)
from vnxdna.recovery.options import DecodeOptions
from vnxdna.sync.template import TemplateAligner


def params_for(lay: Layout, opt: DecodeOptions) -> EngineParams:
    """The engine parameters of ``lay`` under ``opt`` (default recovery configuration; ``retry_band`` is refused)."""
    if opt.retry_band:
        raise EngineUnsupported("retry_band")
    al = TemplateAligner(lay, opt.band, opt.sync_costs)
    g = geometry(al)
    tpl, fpos = lay.template()
    mpos = np.ascontiguousarray(np.flatnonzero(tpl >= 0), dtype=np.int64)
    want = np.ascontiguousarray(tpl[mpos], dtype=np.uint8)
    starts = np.flatnonzero(np.diff(mpos, prepend=-2) > 1)
    block = np.zeros(mpos.size, dtype=np.int64)
    if mpos.size:
        block[starts[1:]] = 1
        block = np.ascontiguousarray(np.cumsum(block), dtype=np.int64)
    span = HEADER_BYTES - 1 + lay.payload_bytes + CRC_BYTES
    c = opt.sync_costs
    return EngineParams(t=int(tpl.size), frame_nt=lay.frame_nt, frame_bytes=lay.frame_bytes, payload=lay.payload_bytes,
                        parity=lay.inner_parity, mpos=mpos, want=want, block=block, nblocks=int(starts.size),
                        frame_pos64=np.ascontiguousarray(fpos, dtype=np.int64), tpl16=g["tpl"], seg_of=g["seg_of"],
                        prev_seg=g["prev_seg"], next_seg=g["next_seg"], n_segments=int(al.n_segments),
                        frame_pos32=g["frame_pos"], seg_frame=g["seg_frame"], band=int(opt.band),
                        c_mm=int(c.marker_mismatch), c_ins=int(c.insertion), c_del=int(c.deletion),
                        c_x=int(c.marker_deletion_extra), guard=int(c.guard_segments), min_q=int(opt.min_quality),
                        rc=bool(opt.reverse_complement), rc_bounded=opt.orientation_retry == "bounded",
                        spec=opt.sync_speculation == "markers", ks=np.ascontiguousarray(keystreams(span), dtype=np.uint8),
                        span=span, max_reads=int(opt.max_reads), frame_version=int(FRAME_VERSION),
                        orient_margin=max(2, int(mpos.size) // 8))


def run_pass1(reads_path: str | os.PathLike, spill_dir: str | os.PathLike, lay: Layout, opt: DecodeOptions, buckets: int, *,
              fmt: str, threads: int, dedup: bool = True, batch: int | None = None, cancel: Cancel | None = None,
              scheduler: str | None = None) -> dict:
    """Pass 1 of a decode in the engine (see :func:`vnxdna.native.engine.run_pass1`); ``scheduler`` None = the
    option's ``engine_scheduler`` ("auto" runs the V13 rule; the strand retry is the ladder's, pipeline.decode)."""
    return _ne.run_pass1(reads_path, spill_dir, params_for(lay, opt), buckets, fmt=fmt, threads=threads, dedup=dedup,
                         batch=batch, cancel=cancel, scheduler=scheduler_of(scheduler or opt.engine_scheduler))


def scheduler_of(name: str) -> str:
    """The engine scheduler an option value runs: "auto" -> "corroborated"."""
    return "corroborated" if name == "auto" else name
