"""Reads → verified container (or verified files): the decode stage graph of spec §5.2 (D0–D14).

Pass 1 (streaming over read batches, parallel, bounded memory; :mod:`vnxdna.recovery.pass1`)::

    read validation (ACGTN, length window, quality → erasures)
      → fast path: exact-length reads, markers stripped, inner RS + CRC
      → sync path: marker-template alignment (indels → erasures), inner RS + CRC
      → reverse-complement retry for reads that fit neither orientation
      → accepted symbols ............................. spilled to bucket files by group
      → failed but aligned reads with a readable header → spilled as *pending* (for consensus)

Pass 2 (per bucket; :mod:`vnxdna.recovery.outer`)::

    superblock (kind 1) → geometry, archive ID, container size/SHA-256
    duplicate resolution (strict majority of identical verified payloads)
    consensus of pending reads per address (soft vote → erasures) → inner RS + CRC
    outer decoding per group (Cauchy RS / LT fountain) → container bytes
    whole-container SHA-256 (superblock) + full structural validation → publish

Fail-closed rule: the output container is published only if its SHA-256 equals the one recorded in the superblock
*and* it opens as a valid VNX4 container. If groups cannot be decoded, the status is PARTIAL: individual files whose
every chunk verifies can be extracted (``partial_dir``); nothing unverified is ever written as a result.
Formerly ``vnxdna.v4.decoder`` (V6 Phase 2, M4)."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from vnxdna.core.errors import VNXFormatError
from vnxdna.native.reads import iter_reads
from vnxdna.recovery.options import DecodeOptions, DecodeResult
from vnxdna.recovery.outer import _pass2
from vnxdna.recovery.pass1 import _p_init, _process
from vnxdna.recovery.probe import check_unsupported_after_pass1, detect_layout
from vnxdna.recovery.schedule import _deferred_recovery, _plan_pass1
from vnxdna.recovery.spill import Spill, _bucket_count


def decode_reads(reads_path: str | os.PathLike, output: str | os.PathLike | None, options: DecodeOptions | None = None, *,
                 overwrite: bool = False, partial_dir: str | os.PathLike | None = None, select: list[str] | None = None,
                 select_dir: str | os.PathLike | None = None, key: bytes | None = None, passphrase: str | None = None,
                 progress=None, workdir: str | os.PathLike | None = None, observer=None,
                 task_id: str | None = None, allow_unencrypted: bool = False) -> DecodeResult:
    """``observer``: optional callable receiving structured events (vnxdna.v6.observe); observability only.
    ``allow_unencrypted``: accept an unencrypted archive although ``key``/``passphrase`` was given (otherwise the
    selected or partial files are refused, see :func:`vnxdna.v4.container.open_container`)."""
    opt = options or DecodeOptions()
    opt.validate()
    t0 = time.perf_counter()
    from vnxdna.core.observe import Events
    from vnxdna.recovery.planner import RecoveryPlanner
    from vnxdna.native import backend_summary
    ev = Events(observer, task_id, t0)
    backends = backend_summary()        # provenance: which kernels run natively (never raises; additive report field)
    try:
        res = _decode_reads(reads_path, output, opt, t0, ev, RecoveryPlanner(opt.recovery_budget, t0), overwrite=overwrite,
                            partial_dir=partial_dir, select=select, select_dir=select_dir, key=key, passphrase=passphrase,
                            progress=progress, workdir=workdir, allow_unencrypted=allow_unencrypted, backends=backends)
    except Exception as error:
        ev.emit("error", getattr(error, "stage", "unknown"), error_class=type(error).__name__, message=str(error)[:500])
        raise
    rep_ = res.report
    rep_["native_backends"] = backends
    ev.emit("decode_end", "output", status=res.status, seconds=round(rep_.get("seconds", 0.0), 4),
            groups_decoded=rep_.get("groups_decoded"), groups_failed=rep_.get("groups_failed"),
            peak_rss_bytes=rep_.get("peak_rss_bytes"))
    return res


def _decode_reads(reads_path, output, opt: DecodeOptions, t0: float, ev, planner, *, overwrite, partial_dir, select,
                  select_dir, key, passphrase, progress, workdir, allow_unencrypted=False, backends=None) -> DecodeResult:
    stage: dict = {}
    reads_path = Path(reads_path)
    lay = detect_layout(reads_path, opt)
    if ev:
        try:
            size = reads_path.stat().st_size
        except OSError:
            size = None
        ev.emit("decode_start", "input", reads_bytes=size, workers=opt.workers, batch_reads=opt.batch_reads,
                strand_nt=lay.strand_nt, indel_recovery=opt.indel_recovery, soft_decoding=opt.soft_decoding,
                recovery_budget=opt.recovery_budget.to_dict(), native_backends=backends)
    try:
        est_reads = max(1, reads_path.stat().st_size // (lay.strand_nt + 20))
    except OSError as error:
        raise VNXFormatError(f"cannot read {reads_path}: {error.strerror or error}") from None
    buckets = _bucket_count(est_reads)
    tmp_root = tempfile.mkdtemp(prefix="vnx4-decode-", dir=workdir)
    try:
        smart = opt.indel_recovery == "smart"
        softm = opt.soft_decoding != "off"
        qw = opt.consensus_weighting == "quality"
        spill = Spill(Path(tmp_root), buckets, lay.payload_bytes, lay.frame_nt,
                      lay.strand_nt + max(opt.band, opt.retry_band) if (smart or softm) else 0, lay.frame_nt if qw else 0)
        stats = Counter()
        indel_stats: Counter = Counter()
        t1 = time.perf_counter()
        deferred = (smart or softm) and opt.recovery_schedule == "deferred"
        base_args = (lay, opt.band, opt.sync_costs, opt.min_quality, opt.reverse_complement, opt.indel_config if smart else None,
                     opt.soft_config if softm else None)
        initargs = base_args + (deferred, qw, opt.retry_band)
        recovery_args = base_args + (False, False, opt.retry_band)      # the deferred stage: no defer, no qualities

        cpu = {"seconds": 0.0, "batches": 0}

        def take(res, depth=0):
            planner.checkpoint("pass 1")
            spill.write(res)
            stats.update(res["stats"])
            indel_stats.update(res.get("indel", {}))
            cpu["seconds"] += res.get("cpu_seconds", 0.0)
            cpu["batches"] += 1
            if progress:
                progress({"stage": "pass1", "reads": stats["reads"], "elapsed": time.perf_counter() - t0})
            if ev and (cpu["batches"] % 16 == 1):
                wall = max(1e-9, time.perf_counter() - t1)
                ev.emit("pass1_progress", "pass1", reads_processed=stats["reads"],
                        reads_accepted=stats["fast"] + stats["sync"], reads_pending=stats["pending"],
                        reads_rejected=stats["unaligned"], batches=cpu["batches"], queue_depth=depth,
                        worker_cpu_seconds=round(cpu["seconds"], 3),
                        worker_utilisation=round(cpu["seconds"] / (wall * opt.workers), 4))

        def batches():
            n = 0
            for batch in iter_reads(reads_path, opt.batch_reads, max_reads=opt.max_reads):
                n += batch.count
                q = batch.quals if (opt.min_quality or smart or softm or qw) else None
                yield batch.codes, batch.lengths, q

        if opt.workers == 1:
            _p_init(*initargs)
            for b in batches():
                take(_process(*b))
        else:
            with ProcessPoolExecutor(max_workers=opt.workers, initializer=_p_init, initargs=initargs) as pool:
                window: deque = deque()
                for b in batches():
                    window.append(pool.submit(_process, *b))
                    if len(window) >= 2 * opt.workers:
                        take(window.popleft().result(), len(window))
                while window:
                    take(window.popleft().result(), len(window))
        spill.close()
        stage["pass1_reads"] = time.perf_counter() - t1
        stage["pass1_worker_cpu"] = cpu["seconds"]
        if stats["reads"] == 0:
            raise VNXFormatError("the read file contains no reads", stage="input")
        if stats["fast"] + stats["sync"] + stats.get("smart", 0) + stats.get("soft", 0) == 0:
            # spec §3.10 step 7: no frame of the chosen version verified in pass 1. An unsupported or legacy frame
            # version is refused here (exit 6, not retryable), before any superblock error (exit 5, retryable).
            check_unsupported_after_pass1(reads_path)
        ev.emit("pass1_end", "pass1", reads_processed=stats["reads"], fast=stats["fast"], sync=stats["sync"],
                reverse_complement=stats["reverse_complement"], reads_pending=stats["pending"],
                reads_rejected=stats["unaligned"], orphans=stats["orphans"], seconds=round(stage["pass1_reads"], 4),
                worker_cpu_seconds=round(cpu["seconds"], 3),
                worker_utilisation=round(cpu["seconds"] / max(1e-9, stage["pass1_reads"] * opt.workers), 4))
        _plan_pass1(planner, opt, stats, deferred)
        recover_groups = None
        if deferred and select:
            # random access: smart/soft recovery only for the index groups, then for the stripes of the selected files.
            # The first stage runs before pass 2 decodes the superblock, so round S precedes it as in a full decode.
            ra_state: dict = {}

            def recover_groups(groups) -> dict:
                tr = time.perf_counter()
                info = _deferred_recovery(spill, lay, opt, stats, indel_stats, recovery_args, vote=False,
                                          planner=planner, groups=groups if callable(groups) else set(groups),
                                          state=ra_state)
                stage["deferred_recovery"] = stage.get("deferred_recovery", 0.0) + time.perf_counter() - tr
                return info
            recover_groups.state = ra_state
        elif deferred:
            t1b = time.perf_counter()
            stats["_schedule"] = _deferred_recovery(spill, lay, opt, stats, indel_stats, recovery_args,
                                                    vote=not select, planner=planner)
            stage["deferred_recovery"] = time.perf_counter() - t1b
            for rnd, r in stats["_schedule"].get("rounds", {}).items():
                ev.emit("recovery_round", "recovery", round=rnd, recovery_attempts=r.get("reads", 0),
                        recovered=r.get("recovered", 0), seconds=r.get("seconds"))
        if smart or softm:
            stats["_indel"] = indel_stats          # pass 2 adds its consensus counters; reported as report["indel_recovery"]
        planner.checkpoint("pass 2 start")
        result = _pass2(spill, lay, opt, stats, stage, t0, output, overwrite, partial_dir, select, select_dir, key, passphrase,
                        tmp_root, planner, ev, recover_groups, allow_unencrypted)
        return result
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
