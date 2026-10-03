import pytest

from vnxops import governor as g

GiB = 2**30


def snap(**kw):
    base = dict(mem_available=20 * GiB, mem_total=32 * GiB, swap_used=0, disk_free=80 * GiB, psi_mem_some=0.0,
                psi_mem_full=0.0, psi_cpu_some=0.0, loadavg=0.1)
    base.update(kw)
    return g.Snapshot(**base)


@pytest.fixture
def pol():
    hw = {"limits": {k: {"value": v} for k, v in dict(MIN_FREE_RAM=6 * GiB, DISK_FREE_FLOOR=20 * GiB, SAFE_SWAP_LIMIT=2 * GiB,
                                                      SAFE_PARALLEL_BUILDS=3, SAFE_TEST_PARALLELISM=2, SAFE_LLM_CONCURRENCY=1,
                                                      SAFE_RAM_LIMIT=20 * GiB, MAX_PROCESSES=256, SAFE_CPU_THREADS=6).items()}}
    return g.Policy.load(hw=hw)


@pytest.fixture
def state(tmp_path):
    return g.State(tmp_path / "gov.db")


def test_parse_size():
    assert g.parse_size("2GiB") == 2 * GiB
    assert g.parse_size("512MiB") == 512 * 2**20
    assert g.parse_size("1GB") == 10**9
    assert g.parse_size(123) == 123
    with pytest.raises(ValueError):
        g.parse_size("lots")


@pytest.mark.parametrize("kw,level", [
    ({}, "OK"),
    ({"mem_available": 8 * GiB}, "ELEVATED"),          # < 1.5 × 6 GiB floor
    ({"mem_available": 5 * GiB}, "DANGER"),            # < floor
    ({"mem_available": 2 * GiB}, "CRITICAL"),          # < 0.5 × floor
    ({"swap_used": 3 * GiB}, "DANGER"),
    ({"swap_used": 5 * GiB}, "CRITICAL"),
    ({"psi_mem_some": 15.0}, "ELEVATED"),
    ({"psi_mem_full": 30.0}, "CRITICAL"),
    ({"psi_cpu_some": 70.0}, "ELEVATED"),
    ({"disk_free": 15 * GiB}, "ELEVATED"),
    ({"disk_free": 9 * GiB}, "CRITICAL"),
])
def test_levels(pol, kw, level):
    assert g.evaluate(snap(**kw), pol)[0] == level


def test_admission_respects_ram_floor_and_class_limits(pol, state):
    assert g.admit("build", 2 * GiB, pol, state, snap()).admitted
    d = g.admit("build", 15 * GiB, pol, state, snap())          # 20 − 15 < 6 GiB floor
    assert not d.admitted and "RAM" in d.reason
    for i in range(3):
        state.add({"id": f"b{i}", "unit": "", "cls": "build", "essential": True, "mem_est": 0, "cmd": "x"})
        state.set_pid(f"b{i}", 1)                              # pid 1 is always alive
    d = g.admit("build", 1, pol, state, snap())
    assert not d.admitted and "limit 3" in d.reason


def test_admission_halves_under_pressure_and_stops_at_danger(pol, state):
    state.add({"id": "t0", "unit": "", "cls": "test", "essential": True, "mem_est": 0, "cmd": "x"})
    state.set_pid("t0", 1)
    assert not g.admit("test", 1, pol, state, snap(psi_mem_some=15.0)).admitted   # 2 × 0.5 = 1 slot, taken
    assert not g.admit("bench", 1, pol, state, snap(mem_available=5 * GiB)).admitted


def test_admission_refuses_when_disk_low(pol, state):
    d = g.admit("misc", 1, pol, state, snap(), disk_est=70 * GiB)
    assert not d.admitted and "disk" in d.reason


def test_dead_jobs_are_reaped(state):
    state.add({"id": "dead", "unit": "", "cls": "misc", "essential": False, "mem_est": 0, "cmd": "x"})
    state.set_pid("dead", 2**22 - 1)
    assert state.active() == []


def test_watchdog_escalates_immediately_and_steps_down_with_hysteresis(pol, state):
    seq = iter([snap(mem_available=2 * GiB)] + [snap()] * 20)
    w = g.Watchdog(pol, state, snap_fn=lambda: next(seq), act=False)
    assert w.tick()["level"] == "CRITICAL"
    levels = [w.tick()["level"] for _ in range(9)]
    assert levels[:2] == ["CRITICAL", "CRITICAL"]      # 3 calm ticks before the first step down
    assert levels[2] == "DANGER" and levels[-1] == "OK"


def test_watchdog_actions_only_touch_vnxdna_units(pol, state):
    state.add({"id": "n1", "unit": "vnxdna-bench-n1.scope", "cls": "bench", "essential": False, "mem_est": 0, "cmd": "x"})
    state.add({"id": "e1", "unit": "vnxdna-test-e1.scope", "cls": "test", "essential": True, "mem_est": 0, "cmd": "x"})
    for j in ("n1", "e1"):
        state.set_pid(j, 1)
    w = g.Watchdog(pol, state, act=False)
    assert w.apply("DANGER") == ["freeze vnxdna-bench-n1.scope"]
    assert w.apply("OK") == ["thaw vnxdna-bench-n1.scope"]
    acts = w.apply("CRITICAL")
    assert "kill vnxdna-bench-n1.scope" in acts and "freeze vnxdna-test-e1.scope" in acts
    assert all("vnxdna-" in a for a in acts)


def test_scope_argv_caps_memory_and_swap(pol):
    argv = g.scope_argv("vnxdna-x", ["true"], pol, 2 * GiB)
    assert "--slice=vnxdna.slice" in argv and f"MemoryMax={2 * GiB}" in argv
    assert f"MemorySwapMax={2 * GiB // 8}" in argv and argv[-2:] == ["--", "true"]


def test_run_unsystemd_timeout_and_exit_codes(pol, state, no_systemd):
    assert g.run(["true"], pol=pol, state=state)["status"] == "done"
    assert g.run(["false"], pol=pol, state=state)["status"] == "failed"
    r = g.run(["sleep", "5"], timeout=0.3, pol=pol, state=state)
    assert r["status"] == "timeout"
    assert state.active() == []


def test_run_refuses_when_no_capacity(pol, state, no_systemd, monkeypatch):
    monkeypatch.setattr(g, "snapshot", lambda: snap(mem_available=1 * GiB))
    r = g.run(["true"], pol=pol, state=state, wait=False)
    assert r["status"] == "refused" and r["exit_code"] == g.EX_TEMPFAIL


def test_slice_unit_uses_profile(pol):
    unit = g.slice_unit(pol)
    assert "CPUQuota=600%" in unit and f"MemoryMax={20 * GiB}" in unit and "TasksMax=256" in unit
