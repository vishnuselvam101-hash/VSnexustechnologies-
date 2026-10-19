"""V14 Phase 6 workstream B: the linear pool path's per-extent pass 2 in parallel worker processes (workers > 1).

:func:`vnxdna.pipeline.stream.decode_stream` runs each pooled extent's pass 2 (PoolDecoder.decode) in up to ``workers``
forked processes and writes the results in manifest order. Differential tests against the serial linear path
(workers = 1, the reference for this change) and the legacy whole-pool path:

- output bytes and the stream report identical for workers in {1, 2, 3, 4} (timing fields aside);
- per-extent pass-2 reports identical whether pass 2 runs in a forked worker or in-process;
- a failing task, a task that writes wrong bytes, and a crashing worker all fall back (never a wrong byte), with the
  same linear/fallback accounting as the serial loop;
- the fallback ladder (serial PoolDecoder.decode, then the whole-pool decode_extent) is preserved.

Every read here is SIMULATED (vnxdna.simulation.channel).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
from pathlib import Path
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import numpy as np
import pytest

from vnxdna.pipeline import decode as dc
from vnxdna.pipeline import stream as st
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.outer import _pass2
from vnxdna.v4 import channel as ch

KIB = 1 << 10
_TIMING = ("seconds", "mb_s", "output", "pass1_seconds", "partition_seconds", "pool_pass2_seconds",
           "pool_pass2_task_extents")


def _data(n: int, seed: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


def _pool(tmp_path, data: bytes, cfg: ch.ChannelConfig, extent_bytes: int = 8 * KIB):
    sink = tmp_path / "s.fasta"
    st.encode_stream(data, sink, options=st.StreamOptions(extent_bytes=extent_bytes))
    reads = tmp_path / "s.fastq"
    ch.simulate_file(sink, reads, cfg)
    return reads, sink.with_name("s.fasta.stream.json")


def _strip(rep: dict) -> dict:
    return {k: v for k, v in rep.items() if k not in _TIMING}


@pytest.fixture(scope="module")
def noisy_pool(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pp")
    data = _data(7 * 8 * KIB - 321, 4242)                   # seven extents, the last one partial
    cfg = ch.ChannelConfig(seed=17, coverage=4, substitution_rate=0.015, insertion_rate=0.003, deletion_rate=0.003,
                           duplication_rate=0.1, reverse_complement_rate=0.4, dropout_rate=0.02, shuffle_window=100_000)
    reads, man = _pool(tmp, data, cfg)
    return data, reads, man


# ============================================================================ output + report equivalence
def test_parallel_matches_serial_and_legacy(tmp_path, noisy_pool):
    data, reads, man = noisy_pool
    reps, outs = {}, {}
    for workers in (1, 2, 3, 4):
        out = tmp_path / f"w{workers}.bin"
        reps[workers] = st.decode_stream(reads, man, out, options=DecodeOptions(workers=workers))
        outs[workers] = out.read_bytes()
        assert reps[workers]["by_pool_linear"] == reps[workers]["by_pool"] == 7
        assert reps[workers]["by_pool_fallback"] == 0
        # the worker processes actually carried the extents at w > 1 (w1 keeps the serial loop: no such field)
        assert reps[workers].get("pool_pass2_task_extents") == (7 if workers > 1 else None)
        # partition sizes come from the single pass 1 and do not depend on the worker count
        assert reps[workers]["pool_partition_records"] == reps[1]["pool_partition_records"]
    assert outs[1] == outs[2] == outs[3] == outs[4] == data
    assert _strip(reps[1]) == _strip(reps[2]) == _strip(reps[3]) == _strip(reps[4])

    leg = tmp_path / "leg.bin"
    with pytest.MonkeyPatch.context() as m:
        m.setattr(dc, "pool_decode_refusal", lambda opt: "forced legacy (differential test)")
        rep_l = st.decode_stream(reads, man, leg, options=DecodeOptions(workers=4))
    assert leg.read_bytes() == data
    drop = _TIMING + ("by_pool_linear", "by_pool_fallback", "pool_records", "pool_partition_records")
    assert {k: v for k, v in reps[4].items() if k not in drop} == {k: v for k, v in rep_l.items() if k not in drop}


def _no_timing(x):
    """A report without its measurements of the process (timings, the process's peak RSS) and the output path."""
    if isinstance(x, dict):
        return {k: _no_timing(v) for k, v in x.items()
                if "seconds" not in k and k not in ("elapsed", "output", "peak_rss_bytes")}
    if isinstance(x, list):
        return [_no_timing(v) for v in x]
    return x


_PD = None


def _pass2_report(args):
    """One extent's pass 2 against its partition, as PoolDecoder.decode runs it; returns (report, container bytes)."""
    rec, out, wdir = args
    from vnxdna.recovery.planner import RecoveryPlanner
    tag = int(rec["archive_tag"], 16)
    opt_e = replace(_PD.opt, expect_archive_id=rec["archive_id"], archive_tag=tag)
    os.makedirs(wdir, exist_ok=True)
    res = _pass2(_PD.parts[tag], _PD.lay, opt_e, Counter(_PD.stats), {}, 0.0, out, True, None, None, None, None, None,
                 wdir, RecoveryPlanner(opt_e.recovery_budget, 0.0), None, None, False, pool=None)
    with open(out, "rb") as f:
        return res.status, _no_timing(res.report), f.read()


def test_per_extent_report_identical_in_worker_processes(tmp_path, noisy_pool):
    global _PD
    _, reads, man = noisy_pool
    doc = json.loads(man.read_text())
    opt = DecodeOptions(workers=4)
    opt.validate()
    _PD = dc.PoolDecoder(reads, opt, [int(e["archive_tag"], 16) for e in doc["extents"]], workdir=str(tmp_path))
    try:
        local = [_pass2_report((e, str(tmp_path / f"l{e['index']}.vnx"), str(tmp_path / f"lw{e['index']}")))
                 for e in doc["extents"]]
        with ProcessPoolExecutor(3, mp_context=mp.get_context("fork")) as ex:
            forked = list(ex.map(_pass2_report, [(e, str(tmp_path / f"f{e['index']}.vnx"), str(tmp_path / f"fw{e['index']}"))
                                                 for e in doc["extents"]]))
    finally:
        _PD.close()
        _PD = None
    assert [r[0] for r in local] == ["SUCCESS"] * len(doc["extents"])
    for a, b in zip(local, forked):
        assert a[0] == b[0] and a[2] == b[2]            # status and container bytes
        assert a[1] == b[1]                             # the whole per-extent report, timing fields aside


# ============================================================================ failures fall back, never a wrong byte
def test_parallel_task_failure_falls_back(tmp_path, noisy_pool, monkeypatch):
    data, reads, man = noisy_pool
    real = dc.PoolDecoder.decode

    def flaky(self, rec, consume):
        if rec["index"] == 2:                           # raised in the worker (fork inherits the patch) and serially
            raise RuntimeError("simulated linear-path failure")
        return real(self, rec, consume)

    monkeypatch.setattr(dc.PoolDecoder, "decode", flaky)
    out = tmp_path / "o.bin"
    rep = st.decode_stream(reads, man, out, options=DecodeOptions(workers=4))
    assert out.read_bytes() == data
    assert rep["by_pool"] == 7 and rep["by_pool_linear"] == 6 and rep["by_pool_fallback"] == 1
    assert rep["pool_pass2_task_extents"] == 6       # the pool paused for the fallback, then carried the rest


def test_parallel_wrong_bytes_rejected(tmp_path, noisy_pool, monkeypatch):
    # a task whose bytes do not match the manifest is never written: the parent re-checks length + SHA-256 and the
    # extent falls back, as in the serial loop (the patch is inherited by the forked workers; the parent is unaffected)
    data, reads, man = noisy_pool
    real = dc.PoolDecoder.decode
    parent = os.getpid()

    def corrupt(self, rec, consume):
        out = real(self, rec, consume)
        if rec["index"] == 4 and os.getpid() != parent:
            out = bytes([out[0] ^ 0x01]) + out[1:]
        return out

    monkeypatch.setattr(dc.PoolDecoder, "decode", corrupt)
    out = tmp_path / "o.bin"
    rep = st.decode_stream(reads, man, out, options=DecodeOptions(workers=3))
    assert out.read_bytes() == data
    assert rep["by_pool"] == 7 and rep["by_pool_linear"] == 6 and rep["by_pool_fallback"] == 1


def test_parallel_worker_crash_recovers(tmp_path, noisy_pool, monkeypatch):
    # a worker process dying (broken pool) loses no extent: the rest are redone serially in the parent, in order
    data, reads, man = noisy_pool
    real = dc.PoolDecoder.decode
    parent = os.getpid()

    def crash(self, rec, consume):
        if rec["index"] == 1 and os.getpid() != parent:
            os._exit(3)
        return real(self, rec, consume)

    monkeypatch.setattr(dc.PoolDecoder, "decode", crash)
    out = tmp_path / "o.bin"
    rep = st.decode_stream(reads, man, out, options=DecodeOptions(workers=2))
    assert out.read_bytes() == data
    assert rep["by_pool"] == 7 and rep["by_pool_linear"] == 7 and rep["by_pool_fallback"] == 0


def test_single_pooled_extent_stays_serial(tmp_path, monkeypatch):
    # one pooled extent: no task pool is started (nothing to overlap)
    data = _data(5000, 3)
    reads, man = _pool(tmp_path, data, ch.ChannelConfig(seed=2, coverage=4, substitution_rate=0.01))

    def boom(*a, **k):
        raise AssertionError("the task pool must not start for a single pooled extent")

    monkeypatch.setattr(st, "_ParallelPass2", boom)
    out = tmp_path / "o.bin"
    rep = st.decode_stream(reads, man, out, options=DecodeOptions(workers=4), by_range=False)
    assert out.read_bytes() == data and rep["by_pool_linear"] == 1


def test_mixed_range_and_pool_stays_serial(tmp_path, monkeypatch):
    # a stream with by-range AND pooled extents keeps the serial pool loop (no task pool next to by-range decodes)
    data = _data(3 * 8 * KIB - 5, 31)
    sink = tmp_path / "s.fasta"
    st.encode_stream(data, sink, options=st.StreamOptions(extent_bytes=8 * KIB))
    man = sink.with_name("s.fasta.stream.json")
    monkeypatch.setattr(st, "_range_matches", lambda strands, rec: rec["index"] == 0)

    def boom(*a, **k):
        raise AssertionError("the task pool must not start for a mixed by-range / pooled stream")

    monkeypatch.setattr(st, "_ParallelPass2", boom)
    out = tmp_path / "o.bin"
    rep = st.decode_stream(sink, man, out, options=DecodeOptions(workers=4))
    assert out.read_bytes() == data
    assert rep["by_range"] == 1 and rep["by_pool"] == 2 and rep["by_pool_linear"] == 2


# ============================================================================ review fixes (B1, S2, S3)
def test_noisy_fallbacks_keep_the_pool(tmp_path, noisy_pool, monkeypatch):
    # S3: a legacy fallback pauses the pool only around itself; later extents are still decoded by worker processes
    data, reads, man = noisy_pool
    real = dc.PoolDecoder.decode

    def flaky(self, rec, consume):
        if rec["index"] in (1, 4):
            raise RuntimeError("simulated linear-path failure")
        return real(self, rec, consume)

    monkeypatch.setattr(dc.PoolDecoder, "decode", flaky)
    out = tmp_path / "o.bin"
    rep = st.decode_stream(reads, man, out, options=DecodeOptions(workers=2))
    assert out.read_bytes() == data
    assert rep["by_pool_linear"] == 5 and rep["by_pool_fallback"] == 2
    assert rep["pool_pass2_task_extents"] == 5


def test_worker_crash_after_a_consumed_result(tmp_path, noisy_pool, monkeypatch):
    # B1: a worker dies while the parent is busy with an earlier result; the next submit meets a broken pool. The
    # decode must not raise: the unfinished extents and the rest run serially in the parent.
    import time
    data, reads, man = noisy_pool
    real = dc.PoolDecoder.decode
    parent = os.getpid()

    def crash(self, rec, consume):
        if rec["index"] == 1 and os.getpid() != parent:
            time.sleep(1.0)
            os._exit(3)
        return real(self, rec, consume)

    def progress(ev):
        if ev["extents"] == 1:
            time.sleep(2.5)                     # extent 0 consumed; extent 1's worker dies meanwhile

    monkeypatch.setattr(dc.PoolDecoder, "decode", crash)
    out = tmp_path / "o.bin"
    rep = st.decode_stream(reads, man, out, options=DecodeOptions(workers=2), progress=progress)
    assert out.read_bytes() == data
    assert rep["by_pool_linear"] == 7 and rep["by_pool_fallback"] == 0


@pytest.mark.parametrize("failure", ["fork", "context"])
def test_pool_start_failure_runs_serially(tmp_path, noisy_pool, monkeypatch, failure):
    # B1: fork failing (EAGAIN / RLIMIT_NPROC) or no "fork" start method: no exception, serial linear path, same bytes
    import errno
    import multiprocessing
    data, reads, man = noisy_pool
    if failure == "fork":
        def no_fork():
            raise OSError(errno.EAGAIN, "Resource temporarily unavailable (injected)")
        monkeypatch.setattr(os, "fork", no_fork)
    else:
        def no_context(method=None):
            raise ValueError(f"cannot find context for {method!r} (injected)")
        monkeypatch.setattr(multiprocessing, "get_context", no_context)
    out = tmp_path / "o.bin"
    rep = st.decode_stream(reads, man, out, options=DecodeOptions(workers=4))
    assert out.read_bytes() == data
    assert rep["by_pool_linear"] == 7 and rep["by_pool_fallback"] == 0
    assert rep["pool_pass2_task_extents"] == 0


_SIGNAL_SCRIPT = r"""
import json, os, signal, sys, time
from pathlib import Path
from vnxdna.pipeline import decode as dc, stream as st
from vnxdna.recovery.options import DecodeOptions
mode, reads, man, out, wd = sys.argv[1:6]
if mode == "term-cli":
    from vnxdna import cli
    signal.signal(signal.SIGTERM, cli._terminate)          # what the CLI installs
real = dc.PoolDecoder.decode
parent = os.getpid()
def slow(self, rec, consume):
    if rec["index"] >= 1 and os.getpid() != parent:
        time.sleep(120)                                     # keep the workers busy until the signal
    return real(self, rec, consume)
dc.PoolDecoder.decode = slow
def progress(ev):
    if ev["extents"] == 1:
        import multiprocessing
        print(json.dumps({"ready": [p.pid for p in multiprocessing.active_children()]}), flush=True)
st.decode_stream(reads, man, out, options=DecodeOptions(workers=3), progress=progress, workdir=wd)
print("finished", flush=True)
"""


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(") ")[1][0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.parametrize("mode", ["int", "term-cli", "term-default"])
def test_interrupt_leaves_no_workers(tmp_path, noisy_pool, mode):
    # S2: SIGINT (KeyboardInterrupt) and SIGTERM through the CLI's handler stop the parallel pass 2 without a hang, kill
    # the workers and remove the work directories and the partial output. A plain SIGTERM (default disposition: no
    # Python cleanup can run, on any path) still leaves no worker behind: workers exit when their parent is gone.
    import signal
    import subprocess
    import sys
    import time
    _, reads, man = noisy_pool
    script = tmp_path / "run.py"
    script.write_text(_SIGNAL_SCRIPT)
    wd = tmp_path / "wd"
    wd.mkdir()
    out = tmp_path / "o.bin"
    env = {**os.environ, "PYTHONPATH": str(Path(dc.__file__).resolve().parents[2])}
    p = subprocess.Popen([sys.executable, str(script), mode, str(reads), str(man), str(out), str(wd)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        line = p.stdout.readline()
        assert line.startswith("{"), (line, p.stderr.read() if p.poll() is not None else "")
        workers = json.loads(line)["ready"]
        assert workers, "the parallel pass 2 is running"
        time.sleep(0.5)
        p.send_signal(signal.SIGINT if mode == "int" else signal.SIGTERM)
        p.wait(timeout=60)                      # no hang on the interrupt
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    deadline = time.time() + 10                 # parent-watch threads poll every 0.5 s
    while time.time() < deadline and any(_alive(w) for w in workers):
        time.sleep(0.2)
    assert not any(_alive(w) for w in workers), "worker processes outlived the decode"
    assert not out.exists()
    if mode != "term-default":
        assert p.returncode != 0
        assert sorted(os.listdir(wd)) == [], "work directories left behind"
