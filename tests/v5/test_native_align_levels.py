"""V11 WP1: runtime-dispatched SIMD levels of the native aligner (4 / 8 / 16 lanes in one library, ABI 3; ABI 4 since V13 adds the selected-reads entry).

Every level must be bit-exact with the V4 NumPy reference (TemplateAligner._align); a level the CPU/OS cannot run is
refused by the library (VNX_E_UNSUPPORTED); forced levels degrade and record why; a stale ABI-2 library is rejected;
the dispatcher self-check quarantines a failing level in auto and raises for an explicit request. The per-level run
of the whole differential suite is in test_native_alignment_equivalence.py (level fixture). All data is SIMULATED.
"""
from __future__ import annotations

import ctypes
import os
import subprocess

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vnxdna import dispatch
from vnxdna.native import align as na
from vnxdna.v4.frame import Layout
from vnxdna.v4.sync import SyncCosts, TemplateAligner

from .native_support import FIELDS, LAYOUTS, assert_identical, strand

pytestmark = pytest.mark.usefixtures("native_ready")
LEVELS = tuple(na.LEVELS)
E_UNSUPPORTED = -6


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("VNXDNA_ALIGN_BACKEND", raising=False)
    monkeypatch.setenv(na.QUARANTINE_ENV, "")   # restored on teardown, so quarantine_level() never leaks
    monkeypatch.delenv(dispatch.DIFFERENTIAL_ENV, raising=False)


def _need(level):
    if level not in na.supported_levels():
        pytest.skip(f"SIMD level {level} not supported on this CPU/OS")


def _both(layout, reads, quals=None, minq=0, band=6, costs=None, level="scalar"):
    ref = TemplateAligner(layout, band, costs, backend="reference").project(reads, quals, minq)
    al = TemplateAligner(layout, band, costs, backend=level)
    assert al.backend == "native" and al.level_request == level
    return ref, al.project(reads, quals, minq)


def _edited(layout, rng, n):
    out = []
    for k in range(n):
        s = strand(layout, rng)
        for j in range(k % 4):
            pos = int(rng.integers(0, s.size))
            s = np.insert(s, pos, np.uint8(rng.integers(0, 6))) if (k + j) % 3 == 0 else (
                np.delete(s, pos) if (k + j) % 3 == 1 else np.where(np.arange(s.size) == pos, (s + 1) % 4, s).astype(np.uint8))
        out.append(s.astype(np.uint8))
    return out


# ============================================================================ library: levels, ABI, unsupported code
def test_levels_reported_and_portable_level_always_present():
    sup = na.supported_levels()
    assert "scalar" in sup and set(sup) <= set(LEVELS)
    mask = na._load().vnx_align_levels()
    assert mask & (1 << 4) and not mask & ~((1 << 4) | (1 << 8) | (1 << 16))
    assert na._load().vnx_align_abi_version() == 4 == na.ABI_VERSION
    assert na._load().vnx_align_lanes() == 4                   # ABI-2 entry points keep 4 lanes


def _c_args(T=1, band=0, reads=(np.array([2], np.uint8),), lanes=4):
    """Direct C call on a synthetic geometry: T frame positions, no markers, one segment."""
    p = na._ptr
    tpl = np.full(T, -1, np.int16)
    z = np.zeros(T, np.int32)
    frame_pos = np.arange(T, dtype=np.int32)
    n = len(reads)
    codes = np.concatenate(reads) if n else np.zeros(0, np.uint8)
    offs = np.concatenate([[0], np.cumsum([r.size for r in reads])]).astype(np.int64)
    outs = [np.zeros((n, T), np.uint8), np.zeros((n, T), np.uint8), np.zeros(n, np.uint8)] + [np.zeros(n, np.int64) for _ in range(4)]
    keep = (tpl, z, frame_pos, codes, offs, outs)
    args = (n, p(codes), p(offs), int(offs[-1]), None, None, T, p(tpl), p(z), p(z), p(z), 1, T, p(frame_pos), p(z), band,
            4, 6, 6, 1, 0, 0, *(p(o) for o in outs))
    return args, outs, keep


@pytest.mark.parametrize("lanes", [0, 1, 2, 3, 5, 12, 32, -4])
def test_invalid_level_is_unsupported(lanes):
    args, outs, _keep = _c_args()
    assert na._load().vnx_align_batch_lv(*args, lanes) == E_UNSUPPORTED
    assert not outs[2].any()                                    # nothing executed, nothing written


def test_level_missing_on_cpu_is_refused():
    mask = na._load().vnx_align_levels()
    missing = [lanes for lanes in (8, 16) if not mask & (1 << lanes)]
    if not missing:
        pytest.skip("this CPU/OS supports every level; test_masked_library_refuses_missing_levels covers the refusal")
    args, outs, _keep = _c_args()
    assert na._load().vnx_align_batch_lv(*args, missing[0]) == E_UNSUPPORTED and not outs[2].any()


@pytest.mark.parametrize("mask,supported", [(0x10, ["scalar"]), (0x110, ["scalar", "avx2"])])
def test_masked_library_refuses_missing_levels(tmp_path, monkeypatch, mask, supported):
    """A test build that hides levels (-DVNX_ALIGN_LEVEL_MASK, as on an older CPU) refuses them with VNX_E_UNSUPPORTED
    before writing any output, for every entry point; Python then downgrades a forced level and records why."""
    try:
        lib_path = na.build(tmp_path / "libmasked.so", extra_flags=[f"-DVNX_ALIGN_LEVEL_MASK={mask:#x}"], strict=True)
    except na.NativeAlignmentError as error:
        pytest.skip(f"no C compiler: {error}")
    lib = ctypes.CDLL(str(lib_path))
    na._bind(lib)
    have = [n for n, lanes in na.LEVELS.items() if lib.vnx_align_levels() & (1 << lanes)]
    expect = [n for n in supported if n in na.supported_levels()]
    assert have == expect
    for name in set(LEVELS) - set(have):
        args, outs, _keep = _c_args()
        rpos = np.full(1, 7, np.int16)
        timings = np.zeros(4)
        assert lib.vnx_align_batch_lv(*args, na.LEVELS[name]) == E_UNSUPPORTED
        assert lib.vnx_align_batch_path_lv(*args, na._ptr(rpos), na.LEVELS[name]) == E_UNSUPPORTED
        assert lib.vnx_align_batch_profiled_lv(*args, na._ptr(timings), na.LEVELS[name]) == E_UNSUPPORTED
        assert not any(o.any() for o in outs) and rpos[0] == 7 and not timings.any()   # nothing executed
    args, outs, _keep = _c_args()
    assert lib.vnx_align_batch_lv(*args, 4) == 0 and outs[2][0] == 1
    monkeypatch.setattr(na, "_candidates", lambda: [lib_path])
    na._reset_for_tests()
    try:
        assert na.supported_levels() == expect
        monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", "avx512")
        st_ = na.status()
        assert st_["simd_level"] == expect[-1] and "not supported" in st_["fallback_reason"]
        layout = LAYOUTS["default-313nt"]
        ref, nat = _both(layout, _edited(layout, np.random.default_rng(3), 9), level="avx512")
        assert_identical(ref, nat, "masked library, avx512 requested")
    finally:
        monkeypatch.undo()
        na._reset_for_tests()
    assert na.available()


@pytest.mark.parametrize("T", [1, 2, 17, 8192])
def test_synthetic_template_lengths_identical_across_levels(T):
    """T = 1 and T = 8192 (domain edges) on a synthetic marker-free geometry: every level writes the same bytes."""
    rng = np.random.default_rng(T)
    band = min(64, T)
    reads = [rng.integers(0, 4, max(0, T + d)).astype(np.uint8) for d in (0, -band, band, 1 - (T > 1), 0)]
    results = []
    for level in na.supported_levels():
        args, outs, _keep = _c_args(T, band, reads)
        assert na._load().vnx_align_batch_lv(*args, na.LEVELS[level]) == 0
        results.append([o.tobytes() for o in outs])
    assert all(r == results[0] for r in results)
    if T == 1:
        args, outs, _keep = _c_args(1, 0, [np.array([2], np.uint8)])
        assert na._load().vnx_align_batch_lv(*args, 4) == 0
        assert outs[0][0, 0] == 2 and outs[2][0] == 1 and outs[6][0] == 0


def test_abi2_library_rejected(tmp_path, monkeypatch):
    """A stale ABI-2 library (no level entry points) is rejected on its ABI number, before any symbol is bound."""
    src = tmp_path / "old.c"
    src.write_text("int vnx_align_abi_version(void) { return 2; }\n")
    lib = tmp_path / "libold.so"
    cc = subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", str(src), "-o", str(lib)], capture_output=True)
    if cc.returncode:
        pytest.skip("no C compiler")
    monkeypatch.setattr(na, "_candidates", lambda: [lib])
    na._reset_for_tests()
    try:
        assert not na.available()
        assert "ABI 2 != 4" in na.status()["load_error"]
        assert na.resolve_backend() == "reference"
        with pytest.raises(na.NativeAlignmentError):
            na.resolve_backend("native")
    finally:
        monkeypatch.undo()
        na._reset_for_tests()
    assert na.available()


# ============================================================================ selection
def test_backend_names():
    for name in ("auto", "native", "avx512", "avx2", "scalar", "reference"):
        assert na.requested_backend(name) == name
    with pytest.raises(ValueError):
        na.requested_backend("sse4")


def test_auto_follows_policy():
    level = na.active_level()
    assert level == next(n for n in na.AUTO_ORDER if n in na.supported_levels())
    st_ = na.status()
    assert st_["simd_level"] == level and st_["simd_lanes"] == na.LEVELS[level] and st_["auto_policy"] == na.AUTO_POLICY


@pytest.mark.parametrize("want", LEVELS)
def test_forced_level_downgrades_and_records(want, monkeypatch):
    monkeypatch.setattr(na, "supported_levels", lambda: ["scalar"])
    monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", want)
    st_ = na.status()
    assert st_["simd_level"] == "scalar" and st_["active_backend"] == "native"
    assert (st_["fallback_reason"] is None) == (want == "scalar")
    if want != "scalar":
        assert "not supported" in st_["fallback_reason"]


def test_forced_downgrade_skips_quarantined_levels(monkeypatch):
    monkeypatch.setattr(na, "supported_levels", lambda: ["scalar", "avx2"])
    na.quarantine_level("avx2")
    level, reason = na._resolve("avx512")
    assert level == "scalar" and "quarantined skipped: avx2" in reason
    na.quarantine_level("scalar")
    level, reason = na._resolve("avx512")
    assert level == "avx2" and "although it is quarantined" in reason
    assert na._resolve("avx2") == ("avx2", None)               # a supported forced level runs despite the quarantine


_NOMEM_CHILD = r"""
import ctypes, resource, sys
import numpy as np
from vnxdna.native import align as na
lib = ctypes.CDLL(sys.argv[1]); na._bind(lib)
T, B = 8192, 64
tpl = np.full(T, -1, np.int16); z = np.zeros(T, np.int32); fp = np.arange(T, dtype=np.int32)
codes = np.zeros(T, np.uint8); offs = np.array([0, T], np.int64)
outs = [np.zeros(T, np.uint8), np.zeros(T, np.uint8), np.zeros(1, np.uint8)] + [np.zeros(1, np.int64) for _ in range(4)]
p = na._ptr
args = (1, p(codes), p(offs), T, None, None, T, p(tpl), p(z), p(z), p(z), 1, T, p(fp), p(z), B, 4, 6, 6, 1, 0, 0, *(p(o) for o in outs))
vm = int(next(l for l in open("/proc/self/status") if l.startswith("VmSize")).split()[1]) * 1024
resource.setrlimit(resource.RLIMIT_AS, (vm + 12 * 2**20, resource.RLIM_INFINITY))   # 16.9 MB scratch fails, 8.5 MB fits
print(lib.vnx_align_batch_lv(*args, 16), lib.vnx_align_batch_lv(*args, 4), int(outs[2][0]))
"""


def test_out_of_memory_retry_never_runs_a_missing_level(tmp_path):
    """LANES 16 scratch (16.9 MB at T 8192, band 64) fails under an address-space limit; the retry one level narrower
    must not run LANES 8 when the library reports it missing (masked test build: levels 4 and 16 only)."""
    import os
    import sys
    try:
        lib_path = na.build(tmp_path / "lib416.so", extra_flags=["-DVNX_ALIGN_LEVEL_MASK=0x10010"], strict=True)
    except na.NativeAlignmentError as error:
        pytest.skip(f"no C compiler: {error}")
    if "avx512" not in na.supported_levels():
        pytest.skip("needs a CPU/OS with AVX-512 (the masked build keeps level 16 only if the CPU has it)")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    env.pop("LD_PRELOAD", None)                                 # sanitizer runtimes reserve far more address space
    res = subprocess.run([sys.executable, "-c", _NOMEM_CHILD, str(lib_path)], capture_output=True, text=True, env=env,
                         timeout=120)
    assert res.returncode == 0, res.stderr[-2000:]
    rc16, rc4, ok4 = (int(x) for x in res.stdout.split())
    assert rc16 == -4                                           # VNX_E_NOMEM, not a silent run at the refused level 8
    assert rc4 == 0 and ok4 == 1                                # 4.2 MB scratch still fits


def test_quarantine_steps_down_in_auto(monkeypatch):
    sup = na.supported_levels()
    order = [n for n in na.AUTO_ORDER if n in sup]
    na.quarantine_level(order[0])
    if len(order) > 1:
        assert na.active_level() == order[1]
        assert "quarantined" in na.status()["fallback_reason"]
    for name in order:
        na.quarantine_level(name)
    assert na.active_level() == "reference" and na.resolve_backend() == "reference"
    with pytest.raises(na.NativeAlignmentError):
        na.active_level("native")
    assert na.active_level(order[0]) == order[0]               # an explicit level ignores the auto quarantine


def test_native_status_reports_level():
    from vnxdna.native import backend_summary, kernel_status
    k = kernel_status("align")
    assert k["simd_level"] == na.active_level() and k["simd_lanes"] == na.LEVELS[k["simd_level"]]
    assert backend_summary()["align"]["simd_level"] == k["simd_level"]


@pytest.mark.parametrize("explicit", ["native", "auto", "scalar", "Native", " AVX2 "])
def test_explicit_native_aligner_ignores_reference_environment(explicit, monkeypatch):
    """An aligner constructed with an explicit native backend runs the kernel even if VNXDNA_ALIGN_BACKEND=reference
    (as before ABI 3): the environment never silently turns a native comparison into reference-vs-reference."""
    monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", "reference")
    calls = []
    real = na.align_usable

    def spy(*a, **k):
        out = real(*a, **k)
        calls.append(out is not None)
        return out
    monkeypatch.setattr(na, "align_usable", spy)
    layout = LAYOUTS["default-313nt"]
    al = TemplateAligner(layout, 6, backend=explicit, retry_band=12)
    assert al.backend == "native" and al.retry.backend == "native"
    assert al.level_request == na.requested_backend(explicit)
    reads = _edited(layout, np.random.default_rng(9), 6)
    ref = TemplateAligner(layout, 6, backend="reference", retry_band=12).project(reads)
    assert_identical(ref, al.project(reads), explicit)
    assert calls and all(calls)


def test_reference_request_returns_none_from_align_usable():
    al = TemplateAligner(LAYOUTS["default-313nt"], 6, backend="reference")
    assert na.align_usable(al, [strand(al.layout, np.random.default_rng(0))], None, 0, backend="reference") is None


# ============================================================================ differential per level
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("n", [0, 1, 3, 4, 5, 7, 8, 9, 15, 16, 17, 33])
def test_group_boundaries(level, n):
    """n around every LANES (4, 8, 16) ± 1: ragged final groups never change a read's result."""
    _need(level)
    layout = LAYOUTS["default-313nt"]
    reads = _edited(layout, np.random.default_rng(100 + n), n)
    ref, nat = _both(layout, reads, level=level)
    assert_identical(ref, nat, f"{level} n={n}")


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("band", [0, 64])
def test_band_edges(level, band):
    _need(level)
    layout = LAYOUTS["short-178nt"]
    rng = np.random.default_rng(band)
    T = layout.strand_nt
    base = strand(layout, rng)
    reads = [base]
    for L in (T - band, T - band + 1, T + band - 1, T + band, T - band - 1, T + band + 1):
        reads.append(base[:L].copy() if L <= T else np.concatenate([base, rng.integers(0, 4, L - T).astype(np.uint8)]))
    reads += _edited(layout, rng, 9)
    ref, nat = _both(layout, reads, band=band, level=level)
    assert_identical(ref, nat, f"{level} band={band}")


@pytest.mark.parametrize("level", LEVELS)
def test_max_template_and_band(level):
    """T = 8192 (contract maximum) with band 64 against the reference."""
    _need(level)
    layout = Layout(2003, 0, 256, 4)
    assert layout.strand_nt == 8192
    rng = np.random.default_rng(8192)
    reads = [strand(layout, rng) for _ in range(3)]
    reads += [np.delete(reads[0], [5, 100, 7000]), reads[1][:-64], np.concatenate([reads[2], np.zeros(64, np.uint8)])]
    ref, nat = _both(layout, reads, band=64, level=level)
    assert_identical(ref, nat, f"{level} T=8192")


@pytest.mark.parametrize("level", LEVELS)
def test_qualities_and_max_cost(level):
    _need(level)
    layout = LAYOUTS["indel-profile"]
    rng = np.random.default_rng(77)
    reads = _edited(layout, rng, 21)
    quals = [None if k % 3 == 0 else rng.integers(0, 42, r.size).astype(np.uint8) for k, r in enumerate(reads)]
    for costs in (SyncCosts(), SyncCosts(65536, 65536, 65536, 65536, 1024), SyncCosts(0, 0, 0, 0)):
        for minq in (0, 20):
            ref, nat = _both(layout, reads, quals, minq, costs=costs, level=level)
            assert_identical(ref, nat, f"{level} {costs} minq={minq}")


@pytest.mark.parametrize("level", LEVELS)
def test_readpos_and_profiled_entry_points(level):
    """The path (readpos) and profiled entry points give the same arrays at every level as the portable level."""
    _need(level)
    layout = LAYOUTS["default-313nt"]
    al = TemplateAligner(layout, 6, backend="native")
    reads = [r for r in _edited(layout, np.random.default_rng(5), 23) if abs(r.size - al.T) <= 6]
    base = na.align_usable(al, reads, None, 0, readpos=True, backend="scalar")
    got = na.align_usable(al, reads, None, 0, readpos=True, backend=level)
    assert len(got) == 8 and all(a.tobytes() == b.tobytes() for a, b in zip(base, got))
    tm: dict = {}
    prof = na.align_usable(al, reads, None, 0, timings=tm, backend=level)
    assert all(a.tobytes() == b.tobytes() for a, b in zip(base[:7], prof)) and tm["dp"] >= 0
    ref = al._align(reads, np.array([r.size for r in reads]), None, 0)
    assert all(np.asarray(a).tobytes() == np.asarray(b).tobytes() for a, b in zip(ref, got[:7]))


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(seed=st.integers(0, 2**32 - 1), n=st.integers(0, 40), band=st.sampled_from([0, 1, 2, 5, 8, 16]),
       costs=st.tuples(*(st.integers(0, 12) for _ in range(4)), st.integers(0, 2)), with_q=st.booleans(),
       name=st.sampled_from(sorted(LAYOUTS)))
def test_property_every_level_matches_reference(seed, n, band, costs, with_q, name):
    layout = LAYOUTS[name]
    rng = np.random.default_rng(seed)
    reads = _edited(layout, rng, n)
    quals = [rng.integers(0, 45, r.size).astype(np.uint8) if rng.random() < 0.8 else None for r in reads] if with_q else None
    c = SyncCosts(*costs)
    ref = TemplateAligner(layout, band, c, backend="reference").project(reads, quals, 15)
    for level in na.supported_levels():
        nat = TemplateAligner(layout, band, c, backend=level).project(reads, quals, 15)
        for f in FIELDS:
            assert np.array_equal(getattr(ref, f), getattr(nat, f)), f"{level} {f}"


# ============================================================================ dispatcher self-check
def test_selftest_every_level_passes():
    for level in na.supported_levels():
        assert na.selftest(level) is True


def test_selftest_detects_wrong_output(monkeypatch):
    real = na.align_usable

    def corrupt(*a, **k):
        out = real(*a, **k)
        out[6][0] += 1                                          # one cost differs
        return out
    monkeypatch.setattr(na, "align_usable", corrupt)
    assert na.selftest("scalar") is False


def test_path_report_records_level():
    e = dispatch.select_path()["kernels"]["align"]
    assert e["path"] == "native" and e["simd_level"] == na.active_level()
    e = dispatch.select_path(differential=True)["kernels"]["align"]
    assert e["differential"] == "pass" and e["simd_level"] == na.active_level()


def test_selftest_workload_registered_by_the_sync_layer_only():
    # the native layer never imports the reference aligner (tests/architecture R1/R7); sync.template registers it
    import sys
    code = ("import sys; from vnxdna.native import align as na; a = na.selftest_available(); "
            "import vnxdna.sync.template; print(a, na.selftest_available(), 'vnxdna.sync.template' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["False", "True", "True"]


def test_unregistered_workload_is_not_available_never_a_pass_or_quarantine(monkeypatch):
    monkeypatch.setattr(na, "_SELFTEST_WORKLOAD", None)
    with pytest.raises(na.NativeAlignmentError, match="not registered"):
        na.selftest("scalar")
    e = dispatch.select_path(differential=True)["kernels"]["align"]
    assert e["differential"] == "not-available" and e["path"] == "native" and e["simd_level"] == na.active_level()
    assert dispatch._run_selftest("align")[0] == "not-available"


def _fail_levels(monkeypatch, bad):
    real = na.selftest
    monkeypatch.setattr(na, "selftest", lambda level: False if level in bad else real(level))


def test_failing_level_quarantined_in_auto(monkeypatch):
    order = [n for n in na.AUTO_ORDER if n in na.supported_levels()]
    _fail_levels(monkeypatch, {order[0]})
    e = dispatch.select_path(differential=True)["kernels"]["align"]
    assert order[0] in na.quarantined_levels()
    if len(order) > 1:
        assert e["path"] == "native" and e["simd_level"] == order[1] and e["reason"] == "differential-level-fallback"
    else:
        assert e["path"] == "reference"


def test_every_level_failing_falls_back_to_reference(monkeypatch):
    _fail_levels(monkeypatch, set(LEVELS))
    e = dispatch.select_path(differential=True)["kernels"]["align"]
    assert e["path"] == "reference" and e["differential"] == "fail" and e["simd_level"] is None
    assert na.resolve_backend() == "reference"


@pytest.mark.parametrize("want", ["native", "scalar"])
def test_failing_explicit_request_raises(monkeypatch, want):
    monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", want)
    _fail_levels(monkeypatch, set(LEVELS))
    with pytest.raises(dispatch.DispatchError):
        dispatch.select_path(differential=True)


def test_selftest_exception_is_a_failure(monkeypatch):
    def boom(level):
        raise RuntimeError("injected")
    monkeypatch.setattr(na, "selftest", boom)
    e = dispatch.select_path(differential=True)["kernels"]["align"]
    assert e["path"] == "reference" and e["differential"] == "fail"


def test_ctypes_signature_has_level_argument():
    lib = na._load()
    for name in ("vnx_align_batch_lv", "vnx_align_batch_path_lv", "vnx_align_batch_profiled_lv"):
        assert getattr(lib, name).argtypes[-1] is ctypes.c_int32
