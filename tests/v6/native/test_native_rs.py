"""Native inner Reed-Solomon decoder (vnxdna.v6.native_rs) == reference (vnxdna.v4.rs_fast): golden vectors,
round trips, the correction bound, erasures, every dispatch level, fallbacks and the boundary contract.

Every comparison is bit for bit on all three results (corrected words, ok flags, errata counts) and on dtypes.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from vnxdna.ecc import rs_batch
from vnxdna.v4 import rs_fast
from vnxdna.v4.codecs import InnerRS
from vnxdna.v6 import native_rs as nr

HERE = Path(__file__).resolve().parent
GOLDEN = json.loads((HERE / "native_rs_golden.json").read_text())
NATIVE_LEVELS = ("scalar", "avx2", "avx512")


@pytest.fixture(scope="module", autouse=True)
def _native(tmp_path_factory):
    if not nr.available():
        try:
            lib = nr.build(tmp_path_factory.mktemp("native_rs") / "libvnx_rs.so")
        except nr.NativeRSError as error:
            pytest.skip(f"native RS decoder unavailable and cannot be built here: {error}")
        os.environ[nr.ENV_LIB] = str(lib)
        nr._reset_for_tests()
    assert nr.available(), nr.status()
    yield
    nr._restrict_levels_for_tests(None)


def levels() -> list[str]:
    """Every native level this CPU can run (the others are covered by the fallback tests)."""
    return [lv for lv in NATIVE_LEVELS if lv in nr.supported_levels()]


@pytest.fixture(params=NATIVE_LEVELS)
def level(request):
    if request.param not in nr.supported_levels():
        pytest.skip(f"{request.param} not supported by this CPU/OS")
    return request.param


def assert_same(got, ref):
    assert len(got) == len(ref) == 3
    for a, b, name in zip(got, ref, ("corrected", "ok", "errata")):
        assert a.dtype == b.dtype, name
        assert a.shape == b.shape, name
        assert np.array_equal(a, b), name


def encode(msgs: np.ndarray, nsym: int) -> np.ndarray:
    return np.concatenate([msgs, InnerRS(nsym).parity(msgs)], axis=1)


# ---------------------------------------------------------------------------------------------------- golden vectors
def test_golden_fixture_integrity():
    digest = hashlib.sha256(json.dumps([[v["corrected"], v["ok"], v["errata"]] for v in GOLDEN["cases"]]).encode()).hexdigest()
    assert digest == GOLDEN["expected_sha256"]
    assert len(GOLDEN["cases"]) == GOLDEN["vectors"] >= 150
    assert GOLDEN["reedsolo_cross_checked_within_bound"] >= 80
    # the fixture exercises successes, failures and beyond-bound successes (miscorrections or lucky erasures)
    outcomes = {(c["ok"], c["within_bound"]) for c in GOLDEN["cases"]}
    assert outcomes == {(True, True), (False, False), (True, False)}


@pytest.mark.parametrize("backend", ["reference", *NATIVE_LEVELS])
def test_golden_vectors(backend):
    if backend != "reference" and backend not in nr.supported_levels():
        pytest.skip(f"{backend} not supported here")
    for c in GOLDEN["cases"]:
        recv = np.frombuffer(bytes.fromhex(c["received"]), dtype=np.uint8)[None, :]
        er = np.zeros(recv.shape, dtype=bool)
        er[0, c["erasures"]] = True
        out, ok, errata = nr.decode_batch(recv, c["nsym"], er, backend=backend)
        assert out[0].tobytes().hex() == c["corrected"], (c["n"], c["nsym"], c["label"])
        assert bool(ok[0]) == c["ok"] and int(errata[0]) == c["errata"], (c["n"], c["nsym"], c["label"])


def test_golden_vectors_as_one_batch_per_code(level):
    by_code: dict = {}
    for c in GOLDEN["cases"]:
        by_code.setdefault((c["n"], c["nsym"]), []).append(c)
    for (n, nsym), cs in by_code.items():
        recv = np.stack([np.frombuffer(bytes.fromhex(c["received"]), dtype=np.uint8) for c in cs])
        er = np.zeros(recv.shape, dtype=bool)
        for i, c in enumerate(cs):
            er[i, c["erasures"]] = True
        got = nr.decode_batch(recv, nsym, er, backend=level)
        assert_same(got, rs_batch.decode_batch(recv, nsym, er))
        assert [o.tobytes().hex() for o in got[0]] == [c["corrected"] for c in cs]


# ---------------------------------------------------------------------------------------------------- round trips and bounds
@pytest.mark.parametrize("n,nsym", [(70, 16), (70, 12), (70, 20), (255, 64), (30, 6), (64, 2), (17, 16)])
def test_round_trip_within_bound(level, n, nsym):
    rng = np.random.default_rng(n * 1000 + nsym)
    words = 600
    sent = encode(rng.integers(0, 256, (words, n - nsym), dtype=np.uint8), nsym)
    recv = sent.copy()
    er = np.zeros(recv.shape, dtype=bool)
    expect = np.zeros(words, dtype=np.int64)
    for i in range(words):
        f = int(rng.integers(0, nsym + 1))
        e = int(rng.integers(0, (nsym - f) // 2 + 1))
        pos = rng.permutation(n)
        recv[i, pos[:e]] ^= rng.integers(1, 256, e, dtype=np.uint8)
        er[i, pos[e:e + f]] = True
        recv[i, pos[e:e + f]] = rng.integers(0, 256, f, dtype=np.uint8)
        # errata counts every located symbol: errors plus flagged erasures (even those that happen to hold the right byte)
        expect[i] = e + f if (e or (recv[i, pos[e:e + f]] != sent[i, pos[e:e + f]]).any()) else 0
    out, ok, errata = got = nr.decode_batch(recv, nsym, er, backend=level)
    assert ok.all()
    assert np.array_equal(out, sent)
    assert_same(got, rs_fast.decode_batch(recv, nsym, er))
    # a word whose syndromes vanish is accepted as is (errata 0); otherwise every errata symbol is located
    dirty = expect > 0
    assert np.array_equal(errata[dirty], expect[dirty])


@pytest.mark.parametrize("n,nsym", [(70, 16), (70, 20), (40, 7), (255, 32)])
def test_corruption_beyond_capacity_same_decisions(level, n, nsym):
    """At, just beyond and far beyond the bound, failures and miscorrections are the reference's, word for word."""
    rng = np.random.default_rng(7 + n + nsym)
    words = 1500
    sent = encode(rng.integers(0, 256, (words, n - nsym), dtype=np.uint8), nsym)
    recv = sent.copy()
    er = np.zeros(recv.shape, dtype=bool)
    for i in range(words):
        excess = int(rng.integers(0, 6))
        f = int(rng.integers(0, nsym + 2))
        e = max(0, (nsym - f) // 2 + excess)
        pos = rng.permutation(n)
        e = min(e, n)
        recv[i, pos[:e]] ^= rng.integers(1, 256, e, dtype=np.uint8)
        f = min(f, n - e)
        er[i, pos[e:e + f]] = True
    got = nr.decode_batch(recv, nsym, er, backend=level)
    ref = rs_fast.decode_batch(recv, nsym, er)
    assert_same(got, ref)
    assert not got[1].all() and got[1].any()     # both outcomes occur
    assert np.array_equal(got[0][~got[1]], recv[~got[1]])   # failed rows are returned unchanged


def test_more_erasures_than_parity_fail_unchanged(level):
    rng = np.random.default_rng(3)
    sent = encode(rng.integers(0, 256, (50, 54), dtype=np.uint8), 16)
    er = np.zeros(sent.shape, dtype=bool)
    er[:, :17] = True
    out, ok, errata = got = nr.decode_batch(sent, 16, er, backend=level)
    assert not ok.any() and not errata.any() and np.array_equal(out, sent)   # even though the words are clean
    assert_same(got, rs_fast.decode_batch(sent, 16, er))


def test_clean_words_with_erasure_flags_are_accepted(level):
    rng = np.random.default_rng(4)
    sent = encode(rng.integers(0, 256, (50, 54), dtype=np.uint8), 16)
    er = np.zeros(sent.shape, dtype=bool)
    er[:, 3:19] = True
    got = nr.decode_batch(sent, 16, er, backend=level)
    assert got[1].all() and not got[2].any()
    assert_same(got, rs_fast.decode_batch(sent, 16, er))


def test_errors_only_and_erasures_only(level):
    rng = np.random.default_rng(5)
    for nsym in (2, 8, 16, 33):
        n = 90
        sent = encode(rng.integers(0, 256, (300, n - nsym), dtype=np.uint8), nsym)
        a = sent.copy()
        for i in range(300):
            p = rng.permutation(n)[: nsym // 2]
            a[i, p] ^= rng.integers(1, 256, p.size, dtype=np.uint8)
        assert_same(nr.decode_batch(a, nsym, backend=level), rs_fast.decode_batch(a, nsym))
        assert np.array_equal(nr.decode_batch(a, nsym, backend=level)[0], sent)
        b = sent.copy()
        er = np.zeros(b.shape, dtype=bool)
        for i in range(300):
            p = rng.permutation(n)[:nsym]
            er[i, p] = True
            b[i, p] = 0
        assert_same(nr.decode_batch(b, nsym, er, backend=level), rs_fast.decode_batch(b, nsym, er))
        assert np.array_equal(nr.decode_batch(b, nsym, er, backend=level)[0], sent)


def test_large_batch_crosses_reference_blocks(level):
    """More rows than rs_fast.MAX_BATCH: the reference splits into blocks, the kernel does not; results must agree."""
    rng = np.random.default_rng(6)
    words = rs_batch.MAX_BATCH + 1234
    sent = encode(rng.integers(0, 256, (words, 54), dtype=np.uint8), 16)
    recv = sent.copy()
    recv[np.arange(words), rng.integers(0, 70, words)] ^= rng.integers(1, 256, words, dtype=np.uint8)
    recv[::3, 5] ^= 0x5A
    recv[::7] = rng.integers(0, 256, recv[::7].shape, dtype=np.uint8)
    er = rng.random(recv.shape) < 0.05
    assert_same(nr.decode_batch(recv, 16, er, backend=level), rs_fast.decode_batch(recv, 16, er))


# ---------------------------------------------------------------------------------------------------- boundary contract
def test_trivial_shapes_and_parameters(level):
    for shape, nsym in [((0, 70), 16), ((0, 10), 12), ((5, 70), 0), ((0, 0), 0), ((3, 1), 0), ((4, 2), 1), ((2, 255), 254)]:
        cw = np.random.default_rng(1).integers(0, 256, shape, dtype=np.uint8)
        assert_same(nr.decode_batch(cw, nsym, backend=level), rs_fast.decode_batch(cw, nsym))
        er = np.ones(shape, dtype=bool)
        assert_same(nr.decode_batch(cw, nsym, er, backend=level), rs_fast.decode_batch(cw, nsym, er))


@pytest.mark.parametrize("args", [
    (np.zeros(70, np.uint8), 16, None),                 # 1-D
    (np.zeros((2, 300), np.uint8), 6, None),            # n > 255
    (np.zeros((2, 30), np.uint8), -1, None),            # negative nsym
    (np.zeros((2, 30), np.uint8), 30, None),            # nsym >= n
    (np.zeros((2, 0), np.uint8), 0, None),              # empty words
    (np.zeros((5, 30), np.uint8), 6, np.zeros((5, 29), bool)),   # erasure shape
    (np.zeros((5, 30), np.uint8), 6, np.zeros((4, 30), bool)),
])
def test_invalid_arguments_raise_like_the_reference(level, args):
    with pytest.raises(ValueError) as ref_err:
        rs_fast.decode_batch(*args)
    with pytest.raises(ValueError) as nat_err:
        nr.decode_batch(*args, backend=level)
    assert str(nat_err.value) == str(ref_err.value)


def test_input_conversions_match_the_reference(level):
    """Non-contiguous, wider-integer and non-bool inputs are converted exactly as rs_fast converts them."""
    rng = np.random.default_rng(8)
    sent = encode(rng.integers(0, 256, (200, 54), dtype=np.uint8), 16)
    recv = sent.copy()
    recv[:, 10] ^= 7
    wide = np.zeros((200, 140), dtype=np.int64)
    wide[:, ::2] = recv                                  # strided view, int64
    er = np.zeros((200, 70), dtype=np.int32)
    er[:, 20] = 5                                        # any non-zero flags an erasure
    er_view = np.asfortranarray(er)
    for cw, e in [(wide[:, ::2], None), (wide[:, ::2], er_view), (recv.tolist(), er.tolist()), (recv[::-1], er[::-1])]:
        assert_same(nr.decode_batch(cw, 16, e, backend=level), rs_fast.decode_batch(cw, 16, e))
    # values above 255 wrap exactly like np.ascontiguousarray(dtype=uint8)
    big = recv.astype(np.int64) + 256
    assert_same(nr.decode_batch(big, 16, backend=level), rs_fast.decode_batch(big, 16))


def test_inputs_are_not_modified_and_outputs_are_fresh(level):
    rng = np.random.default_rng(9)
    recv = encode(rng.integers(0, 256, (100, 54), dtype=np.uint8), 16)
    recv[:, 3] ^= 1
    er = np.zeros(recv.shape, dtype=bool)
    er[:, 4] = True
    keep, keep_er = recv.copy(), er.copy()
    out, ok, errata = nr.decode_batch(recv, 16, er, backend=level)
    assert np.array_equal(recv, keep) and np.array_equal(er, keep_er)
    assert not np.shares_memory(out, recv) and out.flags.c_contiguous and out.flags.writeable


def test_inplace_variant(level):
    rng = np.random.default_rng(10)
    recv = encode(rng.integers(0, 256, (500, 54), dtype=np.uint8), 16)
    recv[:, 3] ^= 1
    recv[::4, 9:20] ^= 3                                  # beyond the bound for a quarter of the rows
    er = rng.random(recv.shape) < 0.03
    ref = rs_fast.decode_batch(recv, 16, er)
    buf = recv.copy()
    ok, errata = nr.decode_batch_inplace(buf, 16, er, backend=level)
    assert_same((buf, ok, errata), ref)
    for bad in (recv[:, ::2], recv.astype(np.int64), recv[0], np.asfortranarray(recv)):
        with pytest.raises(ValueError):
            nr.decode_batch_inplace(bad, 16, backend=level)
    ro = recv.copy()
    ro.flags.writeable = False
    with pytest.raises(ValueError):
        nr.decode_batch_inplace(ro, 16, backend=level)


def test_inplace_reference_backend():
    rng = np.random.default_rng(11)
    recv = encode(rng.integers(0, 256, (50, 54), dtype=np.uint8), 16)
    recv[:, 0] ^= 9
    ref = rs_fast.decode_batch(recv, 16)
    buf = recv.copy()
    ok, errata = nr.decode_batch_inplace(buf, 16, backend="reference")
    assert_same((buf, ok, errata), ref)


def test_kernel_rejects_bad_calls_directly():
    """The C entry point validates its own arguments (defence in depth behind the Python checks)."""
    lib = nr._load()
    cw = np.zeros((4, 70), np.uint8)
    out = np.zeros_like(cw)
    ok = np.zeros(4, np.uint8)
    errata = np.zeros(4, np.int64)
    p = nr._ptr
    good = (4, 70, 16, p(cw), None, p(out), p(ok), p(errata), 1)
    assert lib.vnx_rs_decode_batch(*good) == 0
    for i, bad in [(1, 256), (1, 0), (2, 0), (2, 70), (2, -3), (0, -1), (8, 4), (8, -1)]:
        args = list(good)
        args[i] = bad
        assert lib.vnx_rs_decode_batch(*args) == -1, (i, bad)
    for i in (3, 5, 6, 7):
        args = list(good)
        args[i] = None
        assert lib.vnx_rs_decode_batch(*args) == -1, i
    # partial overlap of the output with the input is refused; exact aliasing (in place) is allowed
    big = np.zeros(4 * 70 + 1, np.uint8)
    args = list(good)
    args[3], args[5] = p(big[1:]), p(big[:-1])
    assert lib.vnx_rs_decode_batch(*args) == -4
    args[3] = args[5] = p(big[:-1])
    assert lib.vnx_rs_decode_batch(*args) == 0
    # ok flags must not overlap the output
    args = list(good)
    args[6] = p(out)
    assert lib.vnx_rs_decode_batch(*args) == -4


# ---------------------------------------------------------------------------------------------------- dispatch and fallback
def test_status_reports_backend_for_provenance(monkeypatch):
    monkeypatch.delenv(nr.ENV, raising=False)
    st = nr.status()
    assert st["native_available"] and st["active_backend"] in NATIVE_LEVELS and st["library"]
    assert st["active_backend"] == nr.active_backend()
    assert set(st["supported_levels"]) <= set(st["cpu_levels"]) and "scalar" in st["supported_levels"]
    json.dumps(st)


def test_cpu_detection_matches_proc_cpuinfo():
    lib = nr._load()
    flags = set()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("flags"):
                flags = set(line.split(":", 1)[1].split())
                break
    except OSError:
        pytest.skip("no /proc/cpuinfo")
    if not flags:
        pytest.skip("no flags line")
    mask = lib.vnx_rs_cpu_levels()
    # the kernel also requires the OS to save the register state (xgetbv); Linux lists flags only when it does
    assert bool(mask & (1 << nr.LEVELS["avx2"])) == ("avx2" in flags)
    assert bool(mask & (1 << nr.LEVELS["avx512"])) == ({"avx512f", "avx512bw", "avx2"} <= flags)


@pytest.mark.parametrize("name", ["auto", "native", *NATIVE_LEVELS, "reference", " AVX2 "])
def test_env_override_selects_backend(monkeypatch, name):
    monkeypatch.setenv(nr.ENV, name)
    want = name.strip().lower()
    active = nr.active_backend()
    if want in ("auto", "native"):
        assert active == nr.LEVEL_NAMES[nr._load().vnx_rs_best_level()]
    elif want == "reference":
        assert active == "reference"
    elif want in nr.supported_levels():
        assert active == want
    rng = np.random.default_rng(12)
    recv = rng.integers(0, 256, (300, 70), dtype=np.uint8)
    assert_same(nr.decode_batch(recv, 16), rs_fast.decode_batch(recv, 16))


def test_auto_policy_prefers_avx2_and_never_picks_avx512(monkeypatch):
    """Measured decision (bench.json): AVX-512 was not faster than AVX2, so auto/native use AVX2, else scalar."""
    for name in ("auto", "native"):
        monkeypatch.setenv(nr.ENV, name)
        assert nr.active_backend() == ("avx2" if "avx2" in nr.supported_levels() else "scalar")
    nr._reset_for_tests()
    try:
        nr._restrict_levels_for_tests([])
        monkeypatch.setenv(nr.ENV, "auto")
        assert nr.active_backend() == "scalar" and nr.status()["fallback_reason"] is None
    finally:
        nr._restrict_levels_for_tests(None)


def test_unknown_backend_is_rejected(monkeypatch):
    monkeypatch.setenv(nr.ENV, "gpu")
    with pytest.raises(ValueError):
        nr.decode_batch(np.zeros((1, 70), np.uint8), 16)
    assert nr.status()["error"]


@pytest.mark.parametrize("allowed,forced,expect", [
    ([], "avx512", "scalar"), (["avx2"], "avx512", "avx2"), ([], "avx2", "scalar"), (["avx512"], "avx2", "scalar"),
])
def test_forcing_an_unsupported_level_falls_back_safely(monkeypatch, caplog, allowed, forced, expect):
    cpu = nr.LEVELS
    detected = nr._load().vnx_rs_cpu_levels()
    if any(not detected & (1 << cpu[a]) for a in allowed):
        pytest.skip("this CPU cannot run the levels the test keeps")
    nr._reset_for_tests()
    try:
        nr._restrict_levels_for_tests(allowed)
        monkeypatch.setenv(nr.ENV, forced)
        with caplog.at_level(logging.WARNING, logger=nr.__name__):
            assert nr.active_backend() == expect
        assert any("not supported" in r.getMessage() for r in caplog.records)
        st = nr.status()
        assert st["active_backend"] == expect and forced in st["fallback_reason"]
        # the C kernel itself refuses the unsupported level instead of executing it
        cw = np.zeros((2, 70), np.uint8)
        out, ok, errata = np.zeros_like(cw), np.zeros(2, np.uint8), np.zeros(2, np.int64)
        p = nr._ptr
        rc = nr._load().vnx_rs_decode_batch(2, 70, 16, p(cw), None, p(out), p(ok), p(errata), cpu[forced])
        assert rc == -2
        rng = np.random.default_rng(13)
        recv = rng.integers(0, 256, (400, 70), dtype=np.uint8)
        er = rng.random(recv.shape) < 0.1
        assert_same(nr.decode_batch(recv, 16, er), rs_fast.decode_batch(recv, 16, er))
    finally:
        nr._restrict_levels_for_tests(None)


def test_missing_library_falls_back_to_reference(monkeypatch, caplog, tmp_path):
    monkeypatch.setattr(nr, "_candidates", lambda: [tmp_path / "nope.so"])
    nr._reset_for_tests()
    try:
        rng = np.random.default_rng(14)
        recv = rng.integers(0, 256, (100, 70), dtype=np.uint8)
        for name in ("auto", "avx512", "avx2", "scalar"):
            monkeypatch.setenv(nr.ENV, name)
            with caplog.at_level(logging.WARNING, logger=nr.__name__):
                assert nr.active_backend() == "reference"
                assert_same(nr.decode_batch(recv, 16), rs_fast.decode_batch(recv, 16))
            st = nr.status()
            assert st["active_backend"] == "reference" and not st["native_available"] and st["fallback_reason"]
        assert any("unavailable" in r.getMessage() for r in caplog.records)
        monkeypatch.setenv(nr.ENV, "native")
        with pytest.raises(nr.NativeRSError):
            nr.decode_batch(recv, 16)
        assert nr.status()["error"]
    finally:
        monkeypatch.undo()
        nr._reset_for_tests()
        assert nr.available()


def test_abi_mismatch_is_not_loaded(monkeypatch, tmp_path):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    src = tmp_path / "fake.c"
    src.write_text("int vnx_rs_abi_version(void){return 999;}\n")
    lib = tmp_path / "libfake.so"
    subprocess.run([cc, "-shared", "-fPIC", str(src), "-o", str(lib)], check=True)
    monkeypatch.setattr(nr, "_candidates", lambda: [lib])
    nr._reset_for_tests()
    try:
        assert not nr.available()
        assert "ABI 999 != 1" in nr.status()["load_error"]
        ctypes.CDLL(str(lib))   # loadable, but rejected by the binding
    finally:
        monkeypatch.undo()
        nr._reset_for_tests()
        assert nr.available()


def test_build_command_has_no_march_and_strict_warnings():
    cmd = nr.build_command("x.so", compiler="cc", strict=True)          # CI / sanitizer builds
    assert not any(a.startswith(("-march", "-mavx", "-mtune")) for a in cmd)
    assert {"-O3", "-std=c11", "-fPIC", "-shared", "-Wall", "-Wextra", "-Werror"} <= set(cmd)
    # install/development builds: same flags without -Werror, so a new compiler warning cannot disable the kernel
    plain = nr.build_command("x.so", compiler="cc", strict=False)
    assert not any(a.startswith(("-march", "-mavx", "-mtune")) for a in plain)
    assert set(cmd) - set(plain) == {"-Werror"} and "-Werror" not in plain
