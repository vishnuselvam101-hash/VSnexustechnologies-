"""PHASE 3: the VNX-DNA resource governor.

* **Containment.** Governed jobs run as transient systemd scopes inside ``vnxdna.slice`` (CPU quota, MemoryHigh/Max,
  swap cap, TasksMax from the hardware profile). A runaway job hits its own cgroup limit, never the host's.
* **Admission.** A job starts only if MemAvailable − estimate stays above the floor, disk stays above its floor, and
  its class is below its (pressure-scaled) concurrency limit. Otherwise it waits, then fails with exit 75.
* **Watchdog.** ``vnxdna governor watch`` evaluates pressure levels (OK/ELEVATED/DANGER/CRITICAL) and acts only on
  units in ``vnxdna.slice``: halve concurrency, freeze non-essential jobs (cgroup freeze keeps their state), unload
  local models LAYA loaded, kill non-essential jobs, and thaw everything once pressure falls (with hysteresis).

Production services that share the host are never signalled. Every decision is appended to logs/governor.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from . import paths, profile
from .sysinfo import disk, meminfo, pressure

EX_TEMPFAIL = 75
LEVELS = ("CRITICAL", "DANGER", "ELEVATED", "OK")
_UNITS = {"KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40, "KB": 10**3, "MB": 10**6, "GB": 10**9, "B": 1}


def parse_size(v: Any) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    m = re.fullmatch(r"\s*([\d.]+)\s*([KMGT]i?B|B)?\s*", str(v))
    if not m:
        raise ValueError(f"bad size: {v!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2) or "B"])


# --------------------------------------------------------------------------------------------- policy


@dataclass
class Policy:
    raw: dict[str, Any]
    limits: dict[str, Any]

    @classmethod
    def load(cls, resources: Path | None = None, hw: dict[str, Any] | None = None) -> "Policy":
        raw = yaml.safe_load((resources or paths.LAYA_CONFIG / "resources.yaml").read_text())
        prof = hw if hw is not None else profile.load()
        limits = {k: v.get("value") for k, v in prof.get("limits", {}).items()}
        return cls(raw, limits)

    def lim(self, name: str, default: int) -> int:
        v = self.limits.get(name)
        return int(v) if v is not None else default

    def class_max(self, cls: str) -> int:
        c = self.raw["job_classes"].get(cls) or self.raw["job_classes"]["misc"]
        return int(c["max"]) if "max" in c else self.lim(c["max_from"], 1)

    def class_essential(self, cls: str) -> bool:
        return bool((self.raw["job_classes"].get(cls) or {}).get("essential", False))

    def class_mem(self, cls: str) -> int:
        return parse_size((self.raw["job_classes"].get(cls) or self.raw["job_classes"]["misc"]).get("default_mem", 0))

    @property
    def min_free(self) -> int:
        return self.lim(self.raw["admission"]["min_free_ram_from"], 6 * 2**30)

    @property
    def disk_floor(self) -> int:
        return self.lim(self.raw["admission"]["disk_free_floor_from"], 15 * 2**30)

    @property
    def swap_limit(self) -> int:
        return self.lim("SAFE_SWAP_LIMIT", 2 * 2**30)


# --------------------------------------------------------------------------------------------- metrics + levels


@dataclass
class Snapshot:
    mem_available: int
    mem_total: int
    swap_used: int
    disk_free: int
    psi_mem_some: float
    psi_mem_full: float
    psi_cpu_some: float
    loadavg: float
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def snapshot() -> Snapshot:
    m = meminfo()
    pm, pc = pressure("memory"), pressure("cpu")
    full = 0.0
    try:
        full = float(Path("/proc/pressure/memory").read_text().splitlines()[1].split()[1].split("=")[1])
    except (OSError, IndexError, ValueError):
        pass
    return Snapshot(mem_available=m["MemAvailable"], mem_total=m["MemTotal"], swap_used=m.get("SwapTotal", 0) - m.get("SwapFree", 0),
                    disk_free=disk(str(paths.HOME))["free"], psi_mem_some=pm.get("avg10", 0.0), psi_mem_full=full,
                    psi_cpu_some=pc.get("avg10", 0.0), loadavg=os.getloadavg()[0])


def evaluate(s: Snapshot, pol: Policy) -> tuple[str, list[str]]:
    """Pure: the first level (most severe first) whose any trigger fires, plus the triggers that fired."""
    lv = pol.raw["levels"]
    for name in LEVELS[:-1]:
        rule, fired = lv[name], []
        if "mem_available_below_x_floor" in rule and s.mem_available < rule["mem_available_below_x_floor"] * pol.min_free:
            fired.append(f"mem_available {s.mem_available >> 20} MiB < {rule['mem_available_below_x_floor']}×floor")
        if "swap_used_above_x_limit" in rule and s.swap_used > rule["swap_used_above_x_limit"] * pol.swap_limit:
            fired.append(f"swap_used {s.swap_used >> 20} MiB > {rule['swap_used_above_x_limit']}×limit")
        if "psi_memory_some_avg10_above" in rule and s.psi_mem_some > rule["psi_memory_some_avg10_above"]:
            fired.append(f"psi_memory_some {s.psi_mem_some}")
        if "psi_memory_full_avg10_above" in rule and s.psi_mem_full > rule["psi_memory_full_avg10_above"]:
            fired.append(f"psi_memory_full {s.psi_mem_full}")
        if "psi_cpu_some_avg10_above" in rule and s.psi_cpu_some > rule["psi_cpu_some_avg10_above"]:
            fired.append(f"psi_cpu_some {s.psi_cpu_some}")
        if "disk_free_below_x_floor" in rule and s.disk_free < rule["disk_free_below_x_floor"] * pol.disk_floor:
            fired.append(f"disk_free {s.disk_free >> 30} GiB < {rule['disk_free_below_x_floor']}×floor")
        if fired:
            return name, fired
    return "OK", []


def concurrency_factor(level: str) -> float:
    return {"OK": 1.0, "ELEVATED": 0.5, "DANGER": 0.0, "CRITICAL": 0.0}[level]


# --------------------------------------------------------------------------------------------- state


class State:
    """SQLite job table + key/value state, shared by `vnxdna run`, the watchdog and LAYA."""

    def __init__(self, db: Path | None = None):
        self.path = db or paths.RUN / "governor.db"
        paths.ensure(self.path.parent)
        self.db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, unit TEXT, pid INTEGER, cls TEXT, essential INTEGER,
                mem_est INTEGER, cmd TEXT, started REAL, ended REAL, status TEXT, exit_code INTEGER, frozen INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);""")

    def get(self, k: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, k: str, v: Any) -> None:
        self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    def active(self) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id,unit,pid,cls,essential,mem_est,cmd,started,frozen FROM jobs WHERE status='running'").fetchall()
        out = []
        for r in rows:
            job = dict(zip(("id", "unit", "pid", "cls", "essential", "mem_est", "cmd", "started", "frozen"), r))
            if job["pid"] and not _alive(job["pid"]):
                self.finish(job["id"], "lost", None)
                continue
            out.append(job)
        return out

    def add(self, job: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO jobs(id,unit,pid,cls,essential,mem_est,cmd,started,status) VALUES(?,?,?,?,?,?,?,?, 'running')",
                        (job["id"], job["unit"], job.get("pid"), job["cls"], int(job["essential"]), job["mem_est"],
                         job["cmd"], time.time()))

    def set_pid(self, job_id: str, pid: int) -> None:
        self.db.execute("UPDATE jobs SET pid=? WHERE id=?", (pid, job_id))

    def finish(self, job_id: str, status: str, code: int | None) -> None:
        self.db.execute("UPDATE jobs SET status=?, exit_code=?, ended=? WHERE id=?", (status, code, time.time(), job_id))

    def mark_frozen(self, job_id: str, frozen: bool) -> None:
        self.db.execute("UPDATE jobs SET frozen=? WHERE id=?", (int(frozen), job_id))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def log_event(event: str, **data: Any) -> None:
    paths.ensure(paths.LOGS)
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"), "event": event, **data}
    with open(paths.LOGS / "governor.jsonl", "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


# --------------------------------------------------------------------------------------------- admission + run


@dataclass
class Decision:
    admitted: bool
    reason: str
    level: str
    snapshot: dict[str, Any]


def admit(cls: str, mem_est: int, pol: Policy, state: State, snap: Snapshot | None = None,
          disk_est: int = 0) -> Decision:
    s = snap or snapshot()
    level = state.get("level", "OK")
    lv_now, fired = evaluate(s, pol)
    worst = min(LEVELS.index(level), LEVELS.index(lv_now))  # be conservative: watchdog's view or ours
    level = LEVELS[worst]
    limit = int(pol.class_max(cls) * concurrency_factor(level))
    if pol.class_essential(cls) and level == "ELEVATED":
        limit = max(limit, 1)  # essential work still progresses one at a time under moderate pressure
    running = [j for j in state.active() if j["cls"] == cls]
    if len(running) >= limit:
        return Decision(False, f"{cls}: {len(running)} running, limit {limit} at level {level}", level, s.as_dict())
    reserved = sum(j["mem_est"] or 0 for j in state.active())  # admitted but maybe not yet resident
    if s.mem_available - mem_est - reserved * 0.5 < pol.min_free:
        return Decision(False, f"RAM: available {s.mem_available >> 20} MiB − job {mem_est >> 20} MiB would cross floor "
                               f"{pol.min_free >> 20} MiB", level, s.as_dict())
    if s.disk_free - disk_est < pol.disk_floor:
        return Decision(False, f"disk: {s.disk_free >> 30} GiB free − {disk_est >> 30} GiB < floor {pol.disk_floor >> 30} GiB",
                        level, s.as_dict())
    return Decision(True, "ok", level, s.as_dict())


def _systemd_available() -> bool:
    return os.geteuid() == 0 and shutil.which("systemd-run") is not None and Path("/run/systemd/system").exists()


def scope_argv(unit: str, cmd: list[str], pol: Policy, mem_max: int | None, nice: int = 10) -> list[str]:
    slice_name = pol.raw["slice"]["name"]
    argv = ["systemd-run", "--scope", "--quiet", "--collect", f"--unit={unit}", f"--slice={slice_name}", f"--nice={nice}",
            "-p", f"TasksMax={pol.lim('MAX_PROCESSES', 256)}"]
    if mem_max:
        argv += ["-p", f"MemoryMax={mem_max}", "-p", f"MemoryHigh={int(mem_max * 0.9)}", "-p", f"MemorySwapMax={min(pol.swap_limit, mem_max // 8)}"]
    return argv + ["--", *cmd]


def run(cmd: list[str], cls: str = "misc", mem: int | None = None, timeout: float | None = None,
        wait: bool = True, pol: Policy | None = None, state: State | None = None,
        env: dict[str, str] | None = None, cwd: str | None = None,
        stdout: Any = None, stderr: Any = None, stdin_text: str | None = None) -> dict[str, Any]:
    """Run ``cmd`` under governance. Returns a record with exit code, status, timings and the admission reason."""
    pol = pol or Policy.load()
    state = state or State()
    mem_est = mem if mem is not None else pol.class_mem(cls)
    max_wait = pol.raw["admission"]["max_wait_seconds"] if wait else 0
    t_wait = time.time()
    while True:
        d = admit(cls, mem_est, pol, state)
        if d.admitted or time.time() - t_wait >= max_wait:
            break
        log_event("admission_wait", cls=cls, reason=d.reason)
        time.sleep(pol.raw["admission"]["poll_seconds"])
    if not d.admitted:
        log_event("admission_refused", cls=cls, reason=d.reason, cmd=cmd)
        return {"status": "refused", "exit_code": EX_TEMPFAIL, "reason": d.reason, "level": d.level}
    job_id = uuid.uuid4().hex[:12]
    unit = f"vnxdna-{cls}-{job_id}"
    governed = _systemd_available()
    # Per-job hard cap: twice the estimate (at least 512 MiB), never above the slice limit.
    mem_max = min(max(2 * mem_est, 512 * 2**20), pol.lim("SAFE_RAM_LIMIT", 8 * 2**30)) if mem_est else None
    argv = scope_argv(unit, cmd, pol, mem_max) if governed else cmd
    state.add({"id": job_id, "unit": f"{unit}.scope" if governed else "", "cls": cls, "essential": pol.class_essential(cls),
               "mem_est": mem_est, "cmd": " ".join(cmd)[:500]})
    log_event("job_start", id=job_id, cls=cls, unit=unit, governed=governed, mem_est=mem_est, mem_max=mem_max, cmd=cmd)
    t0 = time.time()
    status, code = "done", None
    try:
        proc = subprocess.Popen(argv, env=env, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True,
                                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                                text=stdin_text is not None)
        state.set_pid(job_id, proc.pid)
        try:
            if stdin_text is not None:
                proc.communicate(stdin_text, timeout=timeout)
                code = proc.returncode
            else:
                code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            status = "timeout"
            _terminate(proc, f"{unit}.scope" if governed else None)
            code = proc.wait()
        except KeyboardInterrupt:
            status = "interrupted"
            _terminate(proc, f"{unit}.scope" if governed else None)
            code = proc.wait()
            raise
    finally:
        if status == "done" and code not in (0, None):
            # A signal we did not send means the cgroup limit (kernel OOM killer / systemd-oomd) stopped the job.
            status = "killed_limit" if governed and code in (-9, -15, 137, 143) else "failed"
        state.finish(job_id, status, code)
        log_event("job_end", id=job_id, cls=cls, status=status, exit_code=code, seconds=round(time.time() - t0, 3))
    return {"status": status, "exit_code": code, "id": job_id, "unit": unit, "governed": governed,
            "seconds": round(time.time() - t0, 3), "level": d.level, "mem_max": mem_max}


def _terminate(proc: subprocess.Popen, unit: str | None, grace: float = 10.0) -> None:
    if unit:
        subprocess.run(["systemctl", "kill", "--signal=SIGTERM", unit], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        if unit:
            subprocess.run(["systemctl", "kill", "--signal=SIGKILL", unit], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)


# --------------------------------------------------------------------------------------------- watchdog


def _systemctl(*args: str) -> bool:
    return subprocess.run(["systemctl", *args], capture_output=True).returncode == 0


class Watchdog:
    def __init__(self, pol: Policy | None = None, state: State | None = None,
                 snap_fn: Callable[[], Snapshot] = snapshot, act: bool = True):
        self.pol = pol or Policy.load()
        self.state = state or State()
        self.snap_fn = snap_fn
        self.act = act
        self.level = self.state.get("level", "OK")
        self.calm = 0

    def tick(self) -> dict[str, Any]:
        s = self.snap_fn()
        target, fired = evaluate(s, self.pol)
        prev = self.level
        if LEVELS.index(target) < LEVELS.index(self.level):  # more severe: escalate at once
            self.level, self.calm = target, 0
        elif target != self.level:  # less severe: step down one level after N calm intervals
            self.calm += 1
            if self.calm >= self.pol.raw["levels"]["hysteresis_intervals"]:
                self.level, self.calm = LEVELS[LEVELS.index(self.level) + 1], 0
        else:
            self.calm = 0
        self.state.set("level", self.level)
        self.state.set("concurrency_factor", concurrency_factor(self.level))
        self.state.set("last_snapshot", s.as_dict())
        actions: list[str] = []
        if self.level != prev:
            log_event("level_change", frm=prev, to=self.level, triggers=fired, snapshot=s.as_dict())
            actions = self.apply(self.level)
        return {"level": self.level, "target": target, "triggers": fired, "actions": actions}

    def apply(self, level: str) -> list[str]:
        done = []
        jobs = self.state.active()
        for a in self.pol.raw["levels"][level]["action"]:
            if a == "freeze_nonessential" or a == "freeze_essential":
                want_ess = a == "freeze_essential"
                for j in jobs:
                    if bool(j["essential"]) == want_ess and j["unit"] and not j["frozen"]:
                        if not self.act or _systemctl("freeze", j["unit"]):
                            self.state.mark_frozen(j["id"], True)
                            done.append(f"freeze {j['unit']}")
            elif a == "kill_nonessential":
                for j in jobs:
                    if not j["essential"] and j["unit"]:
                        if self.act:
                            _systemctl("thaw", j["unit"])
                            _systemctl("kill", "--signal=SIGTERM", j["unit"])
                        done.append(f"kill {j['unit']}")
            elif a == "thaw_all":
                for j in jobs:
                    if j["frozen"] and j["unit"]:
                        if not self.act or _systemctl("thaw", j["unit"]):
                            self.state.mark_frozen(j["id"], False)
                            done.append(f"thaw {j['unit']}")
            elif a == "unload_llm":
                from . import models
                for name in self.state.get("llm_loaded_by_laya", []):
                    if not self.act or models.unload(name):
                        done.append(f"unload {name}")
                self.state.set("llm_loaded_by_laya", [])
        for d in done:
            log_event("action", level=level, action=d)
        return done

    def loop(self, interval: float | None = None, iterations: int | None = None) -> None:
        interval = interval or self.pol.raw["levels"]["interval_seconds"]
        log_event("watchdog_start", pid=os.getpid(), level=self.level)
        n = 0
        while iterations is None or n < iterations:
            try:
                self.tick()
            except Exception as exc:  # the watchdog must survive a bad read; log and keep going
                log_event("watchdog_error", error=repr(exc))
            n += 1
            time.sleep(interval)


# --------------------------------------------------------------------------------------------- install


def slice_unit(pol: Policy) -> str:
    cpu = pol.lim(pol.raw["slice"]["cpu_quota_from"], 2) * 100
    return f"""# Generated by `vnxdna governor install` from config/laya/resources.yaml + hardware-capability.yaml.
[Unit]
Description=VNX-DNA governed workloads (builds, tests, benchmarks, agents)
Before=slices.target

[Slice]
CPUQuota={cpu}%
CPUWeight={pol.raw['slice']['cpu_weight']}
IOWeight={pol.raw['slice']['io_weight']}
MemoryAccounting=yes
MemoryHigh={pol.lim(pol.raw['slice']['memory_high_from'], 4 * 2**30)}
MemoryMax={pol.lim(pol.raw['slice']['memory_max_from'], 6 * 2**30)}
MemorySwapMax={pol.lim(pol.raw['slice']['swap_max_from'], 2**30)}
TasksMax={pol.lim(pol.raw['slice']['tasks_max_from'], 256)}
"""


def watchdog_unit() -> str:
    py = paths.VENV / "bin" / "python"
    return f"""# Generated by `vnxdna governor install`.
[Unit]
Description=VNX-DNA resource governor watchdog
After=network.target

[Service]
Type=simple
WorkingDirectory={paths.REPO / 'ops'}
Environment=PYTHONPATH={paths.REPO / 'ops'}
ExecStart={py} -m vnxops governor watch
Restart=on-failure
RestartSec=5
Nice=-5
MemoryMax=256M
CPUQuota=20%

[Install]
WantedBy=multi-user.target
"""


def install(enable_watchdog: bool = True) -> dict[str, Any]:
    pol = Policy.load()
    etc = Path("/etc/systemd/system")
    rb = paths.HOME / "rollback"
    paths.ensure(rb)
    written = []
    for name, text in (("vnxdna.slice", slice_unit(pol)), ("vnxdna-governor.service", watchdog_unit())):
        target = etc / name
        if target.exists():
            shutil.copy2(target, rb / f"{name}.{int(time.time())}.bak")
        target.write_text(text)
        written.append(str(target))
    (rb / "governor-uninstall.sh").write_text(
        "#!/bin/sh\n# Roll back `vnxdna governor install`\nsystemctl disable --now vnxdna-governor.service\n"
        "rm -f /etc/systemd/system/vnxdna-governor.service /etc/systemd/system/vnxdna.slice\nsystemctl daemon-reload\n")
    os.chmod(rb / "governor-uninstall.sh", 0o750)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    if enable_watchdog:
        subprocess.run(["systemctl", "enable", "--now", "vnxdna-governor.service"], check=True, capture_output=True)
    log_event("installed", files=written)
    return {"written": written, "rollback": str(rb / "governor-uninstall.sh")}


def status() -> dict[str, Any]:
    st = State()
    pol = Policy.load()
    s = snapshot()
    lvl, fired = evaluate(s, pol)
    return {"level": st.get("level", "OK"), "instant_level": lvl, "triggers": fired,
            "watchdog": subprocess.run(["systemctl", "is-active", "vnxdna-governor.service"], capture_output=True, text=True).stdout.strip(),
            "concurrency_factor": st.get("concurrency_factor", 1.0), "active_jobs": st.active(),
            "snapshot": s.as_dict(), "floors": {"min_free_ram": pol.min_free, "disk_floor": pol.disk_floor}}
