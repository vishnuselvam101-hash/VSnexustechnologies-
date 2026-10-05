"""Fresh-process measurement of one `vnx` command: wall time, the child's own peak RSS, worker peaks and an RSS timeline.

Shared by experiments/v7/g-memory and experiments/v7/g-scaling. Time and memory are MEASURED on the host that runs it.

Peak RSS method (the V6 lesson, docs/V6_DEFERRED.md §4): the measured command runs in a fresh interpreter that executes
the CLI in-process and, at exit, writes its own ``VmHWM`` from /proc/self/status: the high-water mark of the address
space created by exec. The parent's ``wait4`` ru_maxrss is NOT used for the result: for a posix_spawn child Linux folds
the spawning parent's high-water mark into it. It is still recorded (``wait4_ru_maxrss_bytes``) to show the difference.

Worker processes (``--workers N > 1``: ProcessPoolExecutor children of the measured process) are not covered by its
VmHWM. A sampler thread polls /proc every ``interval`` seconds for the whole process tree and records:
  * ``tree_rss_peak_bytes``  the largest sampled sum of VmRSS over the tree. Not a strict bound either way: sampling can
                             miss short spikes, and pages shared copy-on-write between the parent and its forked
                             workers are counted once per process;
  * ``tree_hwm_sum_bytes``   the sum over every process ever seen of its last sampled VmHWM (an upper bound of the
                             simultaneous peak: per-process peaks need not coincide, and a forked worker's VmHWM starts
                             at the parent's high-water mark at fork time; it misses growth in a worker's last
                             ``interval``);
  * ``timeline``             (seconds since spawn, main-process VmRSS, tree VmRSS) samples, thinned to <= 600 points.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

CHILD = """
import sys
sys.argv = ["vnx"] + sys.argv[2:]
try:
    from vnxdna.commands import main
    main()
finally:
    hwm = [l for l in open("/proc/self/status") if l.startswith("VmHWM:")][0].split()[1]
    open(HWM_PATH, "w").write(hwm)
"""


def _status(pid: int) -> dict:
    out = {}
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith(("VmRSS:", "VmHWM:")):
                    out[line[:5]] = int(line.split()[1]) * 1024
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
    return out


def _children(pid: int) -> list[int]:
    kids = []
    try:
        for tid in os.listdir(f"/proc/{pid}/task"):
            try:
                with open(f"/proc/{pid}/task/{tid}/children") as f:
                    kids += [int(x) for x in f.read().split()]
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
    return kids


def _tree(pid: int) -> list[int]:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        todo += _children(p)
    return out


class Sampler(threading.Thread):
    def __init__(self, pid: int, interval: float):
        super().__init__(daemon=True)
        self.pid, self.interval = pid, interval
        self.t0 = time.time()
        self.stop = threading.Event()
        self.samples: list[tuple[float, int, int]] = []   # (wall time, main rss, tree rss)
        self.hwm: dict[int, int] = {}
        self.tree_peak = 0
        self.max_procs = 0

    def run(self) -> None:
        while not self.stop.is_set():
            pids = _tree(self.pid)
            total = main = 0
            for p in pids:
                st = _status(p)
                rss = st.get("VmRSS", 0)
                total += rss
                if p == self.pid:
                    main = rss
                if "VmHWM" in st:
                    self.hwm[p] = max(self.hwm.get(p, 0), st["VmHWM"])
            if main:
                self.samples.append((time.time(), main, total))
                self.tree_peak = max(self.tree_peak, total)
                self.max_procs = max(self.max_procs, len(pids))
            self.stop.wait(self.interval)


def run_vnx(args: list[str], work: Path, *, interval: float = 0.05, env: dict | None = None) -> dict:
    """Run ``vnx <args>`` in a fresh interpreter; return exit code, wall seconds, the memory measurements and the
    command's JSON result (stdout; stderr is kept in ``work``/last-stderr.txt)."""
    hwm_path = work / f"hwm-{os.getpid()}-{time.monotonic_ns()}.txt"
    code = CHILD.replace("HWM_PATH", repr(str(hwm_path)))
    child_env = dict(os.environ, **(env or {}))
    t_wall0 = time.time()
    t = time.perf_counter()
    out_path, err_path = work / "last-stdout.json", work / "last-stderr.txt"
    actions = [(os.POSIX_SPAWN_OPEN, 1, str(out_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644),
               (os.POSIX_SPAWN_OPEN, 2, str(err_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)]
    pid = os.posix_spawn(sys.executable, [sys.executable, "-c", code, "-", *args], child_env, file_actions=actions)
    sampler = Sampler(pid, interval)
    sampler.start()
    _, status, ru = os.wait4(pid, 0)
    secs = time.perf_counter() - t
    sampler.stop.set()
    sampler.join()
    hwm = int(hwm_path.read_text()) * 1024 if hwm_path.exists() else None
    if hwm_path.exists():
        hwm_path.unlink()
    samples = sampler.samples
    step = max(1, len(samples) // 600)
    timeline = [(round(w - t_wall0, 3), m, tr) for w, m, tr in samples[::step]]
    workers_hwm = {p: h for p, h in sampler.hwm.items() if p != pid}
    try:
        result = json.loads(out_path.read_text())
    except ValueError:
        result = None
    return {"result": result, "exit": os.waitstatus_to_exitcode(status), "wall_seconds": round(secs, 3), "peak_rss_bytes": hwm,
            "wait4_ru_maxrss_bytes": ru.ru_maxrss * 1024, "tree_rss_peak_bytes": sampler.tree_peak,
            "tree_hwm_sum_bytes": (hwm or 0) + sum(workers_hwm.values()), "worker_processes_seen": len(workers_hwm),
            "max_worker_hwm_bytes": max(workers_hwm.values(), default=0), "max_processes": sampler.max_procs,
            "samples": len(samples), "sample_interval_s": interval, "spawn_wall_time": t_wall0, "timeline": timeline,
            "_raw_samples": samples}


def stage_of_peak(samples: list[tuple[float, int, int]], events_path: Path, key: int = 1) -> dict | None:
    """Attribute the largest sample (main RSS: key=1, tree RSS: key=2) to the decode stage active at that time, using
    the wall-clock ``ts`` (millisecond resolution) of the `--events` stream."""
    from datetime import datetime

    if not samples or not events_path.exists():
        return None
    events = []
    for line in events_path.read_text().splitlines():
        e = json.loads(line)
        events.append((datetime.fromisoformat(e["ts"]).timestamp(), e.get("stage"), e.get("event"), e.get("rss_bytes")))
    events.sort()
    best = max(samples, key=lambda s: s[key])

    def stage_at(w):
        # a sample belongs to the stage of the first event at or after it: progress and *_end events are emitted by
        # the stage that just ran (pass1_progress, pass2_end -> "outer", verify -> "integrity", ...)
        for ts, stage, ev, _ in events:
            if ts >= w:
                return stage, ev
        return "exit", None

    per_stage: dict[str, int] = {}
    for s in samples:
        st = stage_at(s[0])[0] or "?"
        per_stage[st] = max(per_stage.get(st, 0), s[key])
    stage, ev = stage_at(best[0])
    return {"peak_sample_bytes": best[key], "stage": stage, "last_event": ev,
            "peak_by_stage_bytes": per_stage,
            "event_rss_bytes": [{"stage": s, "event": e, "rss_bytes": r} for _, s, e, r in events]}
