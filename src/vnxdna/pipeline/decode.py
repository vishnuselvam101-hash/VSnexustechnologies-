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
import queue
import shutil
import tempfile
import threading
import time
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

from vnxdna.core.errors import VNXConfigurationError, VNXFormatError, VNXIntegrityError
from vnxdna.native.heap import trim_heap
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
    from vnxdna.native import backend_summary
    from vnxdna.dispatch import path_report
    # path selection (and the opt-in differential self-check / quarantine) BEFORE any kernel runs or worker starts,
    # so a failing native kernel is bypassed for this whole run and the report states the path that actually ran
    path_selection = path_report()
    ev = Events(observer, task_id, t0)
    backends = backend_summary()        # provenance: which kernels run natively (never raises; additive report field)
    cluster_box: dict = {}              # V7 read clustering (opt-in): the stage object, for its report block
    sc = None
    if opt.stage_counters:
        from vnxdna.recovery.stagecount import StageCounters
        sc = StageCounters()
    try:
        res = _decode_ladder(reads_path, output, opt, t0, ev, overwrite=overwrite, partial_dir=partial_dir, select=select,
                             select_dir=select_dir, key=key, passphrase=passphrase, progress=progress, workdir=workdir,
                             allow_unencrypted=allow_unencrypted, backends=backends, stage_counters=sc,
                             cluster_box=cluster_box)
    except Exception as error:
        if "stage" in cluster_box and isinstance(getattr(error, "details", None), dict):
            error.details["clustering"] = cluster_box["stage"].report()
        if sc is not None and isinstance(getattr(error, "details", None), dict):
            error.details["stage_counters"] = sc.block(report=error.details, error=error)
        ev.emit("error", getattr(error, "stage", "unknown"), error_class=type(error).__name__, message=str(error)[:500])
        raise
    rep_ = res.report
    if "stage" in cluster_box:
        rep_["clustering"] = cluster_box["stage"].report()
    if sc is not None:
        rep_["stage_counters"] = sc.block(status=res.status, report=rep_)
    rep_["native_backends"] = backends
    rep_["path_selection"] = path_selection
    ev.emit("decode_end", "output", status=res.status, seconds=round(rep_.get("seconds", 0.0), 4),
            groups_decoded=rep_.get("groups_decoded"), groups_failed=rep_.get("groups_failed"),
            peak_rss_bytes=rep_.get("peak_rss_bytes"))
    return res


# ============================================================================ V13 engine ladder
#: errors that are about the request or the environment, not about what pass 1 recovered: never retried on a lower rung
_NO_FALLBACK = ("VNXKeyError", "VNXConfigurationError", "VNXOutputError")
#: kernel backend variables the reference rung pins to the Python references
_REFERENCE_ENV = ("VNXDNA_ALIGN_BACKEND", "VNXDNA_RS_BACKEND", "VNXDNA_READS_BACKEND", "VNXDNA_ORIENT_BACKEND",
                  "VNXDNA_INTEGRITY_BACKEND", "VNXDNA_CLUSTER_BACKEND", "VNXDNA_STRAND_BACKEND",
                  "VNXDNA_OUTERLIST_BACKEND")


def stream_refusal(opt: DecodeOptions, reads_path, select=None) -> str | None:
    """Why the streaming engine cannot run this decode (None = it can): the engine reproduces the default recovery
    configuration of pass 1 on FASTQ/FASTA input."""
    if opt.indel_recovery != "segment" or opt.soft_decoding != "off":
        return "smart indel recovery / soft decoding run on the V13 path"
    if opt.consensus_weighting != "count":
        return "quality-weighted consensus runs on the V13 path"
    if opt.read_clustering != "off":
        return "read clustering runs on the V13 path"
    if opt.stage_counters:
        return "stage counters are produced by the V13 path"
    if opt.retry_band:
        return "the retry band runs on the V13 path"
    try:
        from vnxdna.dnaenc.reads import detect
        fmt = detect(reads_path)
    except Exception as error:  # noqa: BLE001 - the V13 path raises the proper error
        return f"input not detected ({type(error).__name__})"
    if fmt not in ("fastq", "fasta"):
        return f"{fmt} input runs on the V13 path"
    from vnxdna.recovery import engine as ne
    try:
        if ne.resolve_backend() != "native":
            return "VNXDNA_ENGINE_BACKEND=reference"
        ne.kernel_levels()
    except Exception as error:  # noqa: BLE001 - unavailable library, reference backends: the V13 path runs
        return f"engine unavailable: {error}"
    return None


class _reference_backends:
    """Pin every kernel to its Python reference for the duration (environment, inherited by worker processes)."""

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in _REFERENCE_ENV}
        for k in _REFERENCE_ENV:
            os.environ[k] = "reference"
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _decode_ladder(reads_path, output, opt: DecodeOptions, t0: float, ev, **kw) -> DecodeResult:
    """The fallback ladder: stream engine -> V13 path -> Python references (docs/V13_ENGINE_ARCHITECTURE.md)."""
    from vnxdna.core.errors import VNXError
    from vnxdna.recovery import engine as ne
    from vnxdna.recovery.planner import RecoveryPlanner, VNXBudgetExceeded
    info: dict = {"requested": opt.engine, "rung": None, "fallbacks": []}
    rung = {"auto": "stream", "stream": "stream", "v13": "v13", "reference": "reference"}[opt.engine]
    if rung == "stream":
        why = stream_refusal(opt, reads_path, kw.get("select"))
        if why is not None:
            if opt.engine == "stream":
                raise VNXConfigurationError(f"engine='stream' cannot run this decode: {why}")
            info["fallbacks"].append({"rung": "stream", "reason": why})
            rung = "v13"
    # the hard wall-time budget (RecoveryBudget.max_wall_seconds) also stops the engine, at its next block or batch
    cancel = ne.Cancel()
    timer = None
    limit = getattr(opt.recovery_budget, "max_wall_seconds", None)
    if limit is not None:
        timer = threading.Timer(max(0.0, float(limit) - (time.perf_counter() - t0)), cancel.set)
        timer.daemon = True
        timer.start()

    planner = RecoveryPlanner(opt.recovery_budget, t0)

    def over_budget():
        # the planner records the exhaustion and raises with its plan (the same error and details as the V13 path);
        # the fallback below is for a cancel that lands exactly at the limit (the timer fired, the clock disagrees)
        try:
            planner.checkpoint("pass 1 (engine stopped)")
        except VNXBudgetExceeded as error:
            return error
        planner.exhausted.append({"limit": "max_wall_seconds", "at": "pass 1 (engine stopped)", "used": None,
                                  "allowed": limit, "effect": "decode stopped"})
        return VNXBudgetExceeded(f"wall-time budget of {limit} s exceeded in pass 1 (engine stopped)",
                                 details={"recovery_plan": planner.report()},
                                 hint="raise the budget, or decode on a larger machine")
    try:
        if rung == "stream":
            t = time.perf_counter()
            try:
                res = _decode_reads(reads_path, output, opt, t0, ev, planner,
                                    **({**kw, "partial_dir": None} if opt.engine == "auto" else kw), stream=cancel,
                                    engine_info=info)
            except ne.EngineCancelled:
                raise over_budget() from None
            except (ne.EngineUnsupported, ne.EngineError) as error:
                if opt.engine == "stream":
                    raise
                info["fallbacks"].append({"rung": "stream", "reason": f"{type(error).__name__}: {error}"[:300],
                                          "seconds": round(time.perf_counter() - t, 4)})
                res = None
            except VNXError as error:
                if type(error).__name__ in _NO_FALLBACK or opt.engine == "stream":
                    raise
                info["fallbacks"].append({"rung": "stream", "reason": f"{type(error).__name__}: {error}"[:300],
                                          "seconds": round(time.perf_counter() - t, 4)})
                res = None
            if res is not None:
                if res.status == "SUCCESS" or opt.engine == "stream":
                    info["rung"] = "stream"
                    res.report["engine"] = info
                    return res
                info["fallbacks"].append({"rung": "stream", "reason": f"status {res.status}",
                                          "seconds": round(time.perf_counter() - t, 4)})
            rung = "v13"
            if ev:
                ev.emit("engine_fallback", "pass1", rung="v13", reason=info["fallbacks"][-1]["reason"])
        if cancel.is_set:
            raise over_budget()
        planner = RecoveryPlanner(opt.recovery_budget, t0)
        if rung == "reference":
            with _reference_backends():
                res = _decode_reads(reads_path, output, opt, t0, ev, planner, **kw)
        else:
            res = _decode_reads(reads_path, output, opt, t0, ev, planner, **kw)
        info["rung"] = rung
        res.report["engine"] = info
        return res
    finally:
        if timer is not None:
            timer.cancel()


_PREFETCH = 2       # V13 S4: batches the parser thread runs ahead of pass 1 (bounded: that many extra batches in memory)


def _prefetch(gen, depth: int = _PREFETCH):
    """Yield the items of ``gen`` produced by a background thread, ``depth`` items ahead at most.

    The native parser and the file reads release the GIL, so parsing overlaps the pass-1 work of the consumer; the
    items, their order and any exception (raised at the consumer's position in the stream, after the items parsed
    before it) are those of ``gen`` itself. Closing the generator early stops the thread."""
    q: queue.Queue = queue.Queue(depth)
    stop = threading.Event()
    done = object()

    def run() -> None:
        try:
            for item in gen:
                while not stop.is_set():
                    try:
                        q.put(item, timeout=0.05)
                        break
                    except queue.Full:
                        continue
                if stop.is_set():
                    return
            q.put(done)
        except BaseException as error:  # noqa: BLE001 - re-raised in the consumer
            q.put(error)

    t = threading.Thread(target=run, name="vnx-reads-prefetch", daemon=True)
    t.start()
    try:
        while True:
            item = q.get()
            if item is done:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        t.join()


def _decode_reads(reads_path, output, opt: DecodeOptions, t0: float, ev, planner, *, overwrite, partial_dir, select,
                  select_dir, key, passphrase, progress, workdir, allow_unencrypted=False, backends=None,
                  stage_counters=None, cluster_box=None, stream=None, engine_info=None) -> DecodeResult:
    """``stream``: a :class:`vnxdna.native.engine.Cancel` = run pass 1 in the streaming engine (V13); ``engine_info``
    receives its statistics."""
    cluster_box = {} if cluster_box is None else cluster_box
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
    spill = cluster = pool = None
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
        clustering = opt.read_clustering != "off"
        initargs = base_args + (deferred, qw, opt.retry_band, stage_counters is not None, clustering, opt.orientation_retry,
                                opt.sync_speculation)
        if clustering:
            # V7 item A (opt-in): unplaced-read store in pass 1; the stage itself runs lazily in pass 2 (fill-only)
            from vnxdna.recovery.cluster.stage import ClusterStage
            cluster = ClusterStage(Path(tmp_root), lay, opt.cluster_config, buckets, spill.acc_dtype,
                                   opt.sync_costs.marker_mismatch, stage_counters, planner, threads=opt.workers)
            cluster_box["stage"] = cluster
        recovery_args = base_args + (False, False, opt.retry_band)      # the deferred stage: no defer, no qualities

        cpu = {"seconds": 0.0, "batches": 0}

        def take(res, depth=0):
            planner.checkpoint("pass 1")
            spill.write(res)
            if cluster is not None:
                cluster.write(res)
            stats.update(res["stats"])
            indel_stats.update(res.get("indel", {}))
            if stage_counters is not None:
                stage_counters.update(res["diag"]["counts"])
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

        if stream is not None:
            # V13 engine: pass 1 in one native call that writes the spill bucket files itself
            from vnxdna.dnaenc.reads import detect
            from vnxdna.recovery import engine as ne
            spill.close()
            es = ne.run_pass1(reads_path, tmp_root, lay, opt, buckets, fmt=detect(reads_path), threads=opt.threads,
                              dedup=opt.engine_dedup, cancel=stream)
            for k in ("reads", "fast", "sync", "speculative", "reverse_complement", "pending", "orphans", "unaligned",
                      "rc_retry_skipped"):
                stats[k] += es[k]
            if es["skipped"]:
                stats["skipped_redundant"] += es["skipped"]
            cpu["seconds"] = es["ns_total"] / 1e9
            cpu["batches"] = es["batches"]
            if engine_info is not None:
                engine_info["threads"] = opt.threads
                engine_info["dedup"] = opt.engine_dedup
                engine_info["pass1"] = {k: (round(v / 1e9, 4) if k.startswith("ns_") else v) for k, v in es.items()
                                        if not k.startswith("reserved")}
            if progress:
                progress({"stage": "pass1", "reads": stats["reads"], "elapsed": time.perf_counter() - t0})
        elif opt.workers == 1:
            _p_init(*initargs)
            for b in _prefetch(batches()):
                take(_process(*b))
        else:
            # the pool outlives pass 1: V12 parallel pass 2 runs bucket consensus on the same workers (shut down below)
            pool = ProcessPoolExecutor(max_workers=opt.workers, initializer=_p_init, initargs=initargs)
            window: deque = deque()
            for b in _prefetch(batches()):
                window.append(pool.submit(_process, *b))
                if len(window) >= 2 * opt.workers:
                    take(window.popleft().result(), len(window))
            while window:
                take(window.popleft().result(), len(window))
            if deferred or clustering or select or stage_counters is not None:
                # pass 2 keeps its serial loop here (V11 behaviour); later stages start their own pools
                pool.shutdown(wait=True)
                pool = None
        spill.close()
        trim_heap()                     # the pass-1 result window is gone; return its pages before pass 2 (memory only)
        if cluster is not None:
            cluster.close_pass1()
        if stage_counters is not None:
            stats["_stage"] = stage_counters        # pass 2 adds its counters; popped before report["reads"] is built
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
                        tmp_root, planner, ev, recover_groups, allow_unencrypted,
                        **({"cluster": cluster} if cluster is not None else {}), pool=pool)
        return result
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        # a stop in pass 1 leaves the spill files open; close them before removing the directory, or the removed files
        # keep their disk blocks for as long as the caller holds the exception (its traceback keeps this frame alive)
        if spill is not None:
            spill.close()
        if cluster is not None:
            cluster.store.close()
        shutil.rmtree(tmp_root, ignore_errors=True)


# ============================================================================ linear-time shuffled-pool decode (V14 P6-A)
# A streamed pool (vnxdna.pipeline.stream) holds one ordinary archive per extent, each with its own content-derived
# archive ID and 16-bit archive tag. Decoding the pool one extent at a time (stream.decode_extent) re-ran a WHOLE-POOL
# decode_reads for every extent: layout probe + pass 1 rescanned every read E times (O(E x pool)). PoolDecoder runs the
# layout probe and pass 1 ONCE, partitions the spill by archive tag (acc by its verified tag, pend by its raw header
# tag), and runs pass 2 per extent against its own partition, so the work is ~O(pool + extents). The decoded container
# is still verified against the superblock SHA-256 and the caller re-checks it against the manifest; any per-extent
# failure falls back to the whole-pool decode_extent, so the result is byte-for-byte that of the legacy path.
#
# Equivalence of the partition: pass 1 does not depend on the tag options, so one pass 1 produces the same spill a
# per-extent decode would. Pass 2 keeps only acc whose verified tag == the extent's tag, so partitioning acc by tag is
# exact. Pass-2 consensus groups pending reads by their raw (kind, tag, group, symbol) key and snaps them only against
# the missing addresses of the SAME tag, so a read of tag Y never contributes to tag Z's consensus EXCEPT through a
# cross-tag address snap (a read whose raw header, or alternate reading, lands within one byte of another tag's missing
# address). Partitioning pend by raw tag drops those rare cross-tag snaps; when one would have mattered the extent
# simply recovers fewer symbols and, if it then fails, falls back to the whole-pool path, so correctness is preserved.
# evidence_refusal sees the whole-pool record count (Spill.view(records=...)), matching the legacy denominator.
#
# Restricted to the default recovery configuration: smart indel recovery / soft decoding keep unaddressed *orphan*
# reads that have no tag to partition on and rewrite the spill per archive, read clustering and stage counters add
# per-decode state, and random access is never used by the stream path. Any of these falls back to the legacy path.
def pool_decode_refusal(opt: DecodeOptions) -> str | None:
    """Why the linear pool path cannot decode this configuration (None = it can)."""
    if opt.indel_recovery != "segment":
        return "smart indel recovery keeps unaddressed orphan reads"
    if opt.soft_decoding != "off":
        return "soft decoding keeps unaddressed orphan reads"
    if opt.read_clustering != "off":
        return "read clustering adds per-decode state"
    if opt.stage_counters:
        return "stage counters add per-decode state"
    if getattr(opt.recovery_budget, "max_wall_seconds", None) is not None:
        # the single pass 1 and the per-extent planners do not share one wall-clock; the legacy single-planner path
        # enforces max_wall_seconds exactly, so defer to it rather than weaken the budget
        return "a wall-time recovery budget is enforced on the single-planner path"
    return None


class PoolDecoder:
    """One layout probe + one pass 1 over a shuffled pool, then pass 2 per extent against a per-archive-tag partition.

    ``options`` must already be validated and must pass :func:`pool_decode_refusal`. ``tags`` are the distinct 16-bit
    archive tags of the pool's extents (the manifest's duplicate-tag refusal has already run)."""

    def __init__(self, reads_path, options: DecodeOptions, tags, *, key=None, passphrase=None, workdir=None):
        self.opt = options
        self.key = key
        self.passphrase = passphrase
        self.tags = [int(t) for t in tags]
        self._root = Path(tempfile.mkdtemp(prefix="vnx-pool-", dir=workdir))
        self._src_dir = self._root / "pass1"
        self._parts_dir = self._root / "parts"
        self._work_dir = self._root / "work"
        for d in (self._src_dir, self._parts_dir, self._work_dir):
            d.mkdir()
        self.pass1_seconds = 0.0
        self.partition_seconds = 0.0
        self.pass2_seconds = 0.0            # summed over the extents decoded so far
        self.pass2_extents = 0
        self.partition_records: dict[int, int] = {}
        try:
            t = time.perf_counter()
            self._pass1(Path(reads_path))
            self.pass1_seconds = time.perf_counter() - t
            t = time.perf_counter()
            self._partition()
            self.partition_seconds = time.perf_counter() - t
        except BaseException:
            self.close()
            raise

    # -------------------------------------------------------------------- pass 1 (once over the whole pool)
    def _pass1(self, reads_path: Path) -> None:
        opt = self.opt
        lay = detect_layout(reads_path, opt)
        self.lay = lay
        try:
            est_reads = max(1, reads_path.stat().st_size // (lay.strand_nt + 20))
        except OSError as error:
            raise VNXFormatError(f"cannot read {reads_path}: {error.strerror or error}") from None
        buckets = _bucket_count(est_reads)
        self.B = buckets
        qw = opt.consensus_weighting == "quality"
        # default recovery configuration only (pool_decode_refusal has excluded smart/soft/clustering/stage counters)
        src = Spill(self._src_dir, buckets, lay.payload_bytes, lay.frame_nt, 0, lay.frame_nt if qw else 0)
        self._src = src
        stats: Counter = Counter()
        base_args = (lay, opt.band, opt.sync_costs, opt.min_quality, opt.reverse_complement, None, None)
        #            (deferred, qw, retry_band, stage_counters, clustering, orientation_retry, sync_speculation)
        initargs = base_args + (False, qw, opt.retry_band, False, False, opt.orientation_retry, opt.sync_speculation)

        def batches():
            for batch in iter_reads(reads_path, opt.batch_reads, max_reads=opt.max_reads):
                q = batch.quals if (opt.min_quality or qw) else None
                yield batch.codes, batch.lengths, q

        def take(res) -> None:
            src.write(res)
            stats.update(res["stats"])

        rung = {"auto": "stream", "stream": "stream", "v13": "v13", "reference": "reference"}[opt.engine]
        used_engine = False
        if rung == "stream" and stream_refusal(opt, reads_path) is None:
            from vnxdna.dnaenc.reads import detect
            from vnxdna.recovery import engine as ne
            src.close()
            try:
                es = ne.run_pass1(reads_path, str(self._src_dir), lay, opt, buckets, fmt=detect(reads_path),
                                  threads=opt.threads, dedup=opt.engine_dedup, cancel=None)
                for k in ("reads", "fast", "sync", "speculative", "reverse_complement", "pending", "orphans",
                          "unaligned", "rc_retry_skipped"):
                    stats[k] += es[k]
                if es["skipped"]:
                    stats["skipped_redundant"] += es["skipped"]
                used_engine = True
            except (ne.EngineUnsupported, ne.EngineError):
                used_engine = False
        if not used_engine:
            # Python pass 1 (V13 path), optionally with every kernel pinned to its reference
            src = self._src = Spill(self._src_dir, buckets, lay.payload_bytes, lay.frame_nt, 0, lay.frame_nt if qw else 0)
            ctx = _reference_backends() if rung == "reference" else _nullctx()
            with ctx:
                if opt.workers == 1:
                    _p_init(*initargs)
                    for b in _prefetch(batches()):
                        take(_process(*b))
                else:
                    pool = ProcessPoolExecutor(max_workers=opt.workers, initializer=_p_init, initargs=initargs)
                    try:
                        window: deque = deque()
                        for b in _prefetch(batches()):
                            window.append(pool.submit(_process, *b))
                            if len(window) >= 2 * opt.workers:
                                take(window.popleft().result())
                        while window:
                            take(window.popleft().result())
                    finally:
                        pool.shutdown(wait=True, cancel_futures=True)
            src.close()
        trim_heap()
        if stats["reads"] == 0:
            raise VNXFormatError("the read file contains no reads", stage="input")
        if stats["fast"] + stats["sync"] == 0:
            check_unsupported_after_pass1(reads_path)
        self.stats = stats
        self.records_total = src.records()

    # -------------------------------------------------------------------- partition the spill by archive tag
    def _partition(self) -> None:
        src = self._src
        tagset = set(self.tags)
        roots = {t: (self._parts_dir / f"{t:04x}") for t in self.tags}
        for d in roots.values():
            d.mkdir()
        counts: Counter = Counter()
        for b in range(self.B):
            acc, pend = src.load(b)
            self._write_bucket(acc, "acc", b, roots, tagset, counts)
            self._write_bucket(pend, "pend", b, roots, tagset, counts)
        self.partition_records = {t: int(counts.get(t, 0)) for t in self.tags}
        self.parts = {t: Spill.view(roots[t], self.B, src.acc_dtype, src.pend_dtype, self.records_total)
                      for t in self.tags}

    @staticmethod
    def _write_bucket(arr: np.ndarray, name: str, b: int, roots: dict, tagset: set, counts: Counter) -> None:
        """Stable-partition one bucket's records by their 16-bit tag; one file per manifest tag (empty where none),
        so every partition has the full bucket set and keeps the records in their original (pass-1) order."""
        slices: dict = {}
        if arr.size:
            order = np.argsort(arr["tag"], kind="stable")
            srt = arr[order]
            t = srt["tag"]
            change = np.flatnonzero(t[1:] != t[:-1]) + 1 if len(srt) > 1 else np.zeros(0, dtype=np.int64)
            starts = np.concatenate([[0], change])
            ends = np.concatenate([change, [len(srt)]])
            for s, e in zip(starts.tolist(), ends.tolist()):
                tg = int(t[s])
                if tg in tagset:
                    slices[tg] = srt[s:e].tobytes()
                    counts[tg] += int(e - s)
        for tg, d in roots.items():
            (d / f"{name}{b}.bin").write_bytes(slices.get(tg, b""))

    # -------------------------------------------------------------------- pass 2, one extent, from its partition
    def decode(self, rec: dict, consume):
        """Decode the extent ``rec`` (manifest record) from its tag partition and pass the verified container path to
        ``consume`` (which returns the extent's bytes). Raises on any failure; the caller falls back to the legacy
        whole-pool :func:`vnxdna.pipeline.stream.decode_extent`."""
        from vnxdna.recovery.planner import RecoveryBudget, RecoveryPlanner
        tag = int(rec["archive_tag"], 16)
        part = self.parts.get(tag)
        if part is None:
            raise VNXFormatError(f"pool extent {rec.get('index', '?')}: archive tag {tag:04x} is not in the pool partition",
                                 stage="stream")
        opt_e = replace(self.opt, expect_archive_id=rec["archive_id"], archive_tag=tag)
        work = Path(tempfile.mkdtemp(prefix="vnx-pool-extent-", dir=self._work_dir))
        try:
            t0 = time.perf_counter()
            budget = opt_e.recovery_budget          # a RecoveryBudget after validate(); narrowed for the type checker
            planner = RecoveryPlanner(budget if isinstance(budget, RecoveryBudget) else None, t0)
            out = work / "e.vnx"
            res = _pass2(part, self.lay, opt_e, Counter(self.stats), {}, t0, out, True, None, None, None,
                         self.key, self.passphrase, str(work), planner, None, None, False, pool=None)
            if res.status != "SUCCESS":
                raise VNXIntegrityError(f"pool extent {rec.get('index', '?')} did not decode: status {res.status}",
                                        stage="stream", details={"extent": rec.get("index"), "status": res.status})
            data = consume(out)
            self.pass2_seconds += time.perf_counter() - t0
            self.pass2_extents += 1
            return data
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def close(self) -> None:
        src = getattr(self, "_src", None)
        if src is not None:
            src.close()
        shutil.rmtree(self._root, ignore_errors=True)


class _nullctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
