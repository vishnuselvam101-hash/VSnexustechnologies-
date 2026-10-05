"""Native aligner: backend selection, fallback, domain routing, kernel argument validation, CLI diagnostics, and
decoder-level equivalence (identical decode with either backend). Channel data is SIMULATED."""
from __future__ import annotations

import json

import numpy as np
import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.sync import SyncCosts, TemplateAligner
from vnxdna.v5 import native_alignment as na

from .native_support import LAYOUTS, assert_identical, project_both, strand

LAY = LAYOUTS["default-313nt"]


# ============================================================================ backend selection and fallback
def test_backend_names(monkeypatch, native_ready):
    monkeypatch.delenv("VNXDNA_ALIGN_BACKEND", raising=False)
    assert na.requested_backend() == "auto"
    assert na.resolve_backend() == "native"
    assert na.resolve_backend("reference") == "reference"
    assert TemplateAligner(LAY).backend == "native"
    monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", "reference")
    assert TemplateAligner(LAY).backend == "reference"
    assert TemplateAligner(LAY, backend="native").backend == "native"     # explicit argument wins over the environment
    with pytest.raises(ValueError, match="alignment backend"):
        na.requested_backend("gpu")


@pytest.fixture
def no_native(monkeypatch):
    """Simulate an installation where the native library is missing."""
    monkeypatch.setattr(na, "_candidates", lambda: [])
    na._reset_for_tests()
    yield
    monkeypatch.undo()
    na._reset_for_tests()


def test_fallback_when_library_missing(no_native, monkeypatch):
    monkeypatch.delenv("VNXDNA_ALIGN_BACKEND", raising=False)
    assert not na.available()
    st = na.status()
    assert st["active_backend"] == "reference" and st["native_available"] is False and "not built" in st["load_error"]
    al = TemplateAligner(LAY)                                      # auto -> reference, no error
    assert al.backend == "reference"
    reads = [strand(LAY, np.random.default_rng(1))]
    assert al.project(reads).ok.all()
    with pytest.raises(na.NativeAlignmentError, match="unavailable"):
        TemplateAligner(LAY, backend="native")                     # explicit native fails loudly, never silently
    monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", "native")
    assert na.status()["active_backend"] is None and "unavailable" in na.status()["error"]


def test_abi_mismatch_is_rejected(monkeypatch, native_ready):
    monkeypatch.setattr(na, "ABI_VERSION", 999)
    na._reset_for_tests()
    try:
        assert not na.available()
        assert "ABI" in na.status()["load_error"]
    finally:
        monkeypatch.undo()
        na._reset_for_tests()
    assert na.available()


# ============================================================================ domain routing (outside -> reference, same result)
@pytest.mark.parametrize("case", ["band65", "negative_cost", "huge_cost", "guard_too_large", "int64_reads", "2d_reads",
                                  "int16_quals"])
def test_outside_domain_uses_reference(case, native_ready, monkeypatch):
    calls = []
    lib = na._load()
    orig = lib.vnx_align_batch

    def spy(*a):
        calls.append(1)
        return orig(*a)

    class Proxy:
        vnx_align_batch = staticmethod(spy)
    monkeypatch.setattr(na, "_lib", Proxy())
    rng = np.random.default_rng(4)
    reads = [strand(LAY, rng) for _ in range(5)]
    band, costs, quals = 6, SyncCosts(), None
    if case == "band65":
        band = 65
    elif case == "negative_cost":
        costs = SyncCosts(insertion=-1)
    elif case == "huge_cost":
        costs = SyncCosts(deletion=70000)
    elif case == "guard_too_large":
        costs = SyncCosts(guard_segments=5000)
    elif case == "int64_reads":
        reads = [r.astype(np.int64) for r in reads]
    elif case == "2d_reads":
        reads = [r.reshape(1, -1) for r in reads]
    elif case == "int16_quals":
        quals = [np.full(r.size, 30, dtype=np.int16) for r in reads]
    ref = TemplateAligner(LAY, band, costs, backend="reference")
    nat = TemplateAligner(LAY, band, costs, backend="native")
    assert_identical(ref.project(reads, quals, 0), nat.project(reads, quals, 0), case)   # 2-D (1, L) reads broadcast in both
    assert calls == [], "out-of-domain input reached the native kernel"


def test_in_domain_reaches_kernel(native_ready, monkeypatch):
    calls = []
    orig = na._load().vnx_align_batch

    class Proxy:
        @staticmethod
        def vnx_align_batch(*a):
            calls.append(1)
            return orig(*a)
    monkeypatch.setattr(na, "_lib", Proxy())
    TemplateAligner(LAY, backend="native").project([strand(LAY, np.random.default_rng(2))])
    assert calls == [1]


def test_quality_length_mismatch_raises_like_reference(native_ready):
    reads = [strand(LAY, np.random.default_rng(3))]
    quals = [np.full(reads[0].size - 1, 30, dtype=np.uint8)]
    for backend in ("reference", "native"):
        with pytest.raises(ValueError):
            TemplateAligner(LAY, backend=backend).project(reads, quals, 10)


# ============================================================================ kernel argument validation (direct C calls)
def _call(lib, n, codes, offsets, total, T=None, band=6, costs=(4, 6, 6, 1, 0), min_q=0, geom=None, frame_nt=None, nseg=None):
    al = TemplateAligner(LAY, 6, backend="native")
    g = geom or na.geometry(al)
    T = al.T if T is None else T
    frame_nt = LAY.frame_nt if frame_nt is None else frame_nt
    nseg = al.n_segments if nseg is None else nseg
    m = max(n, 1)
    outs = [np.zeros(m * max(frame_nt, 1), np.uint8), np.zeros(m * max(frame_nt, 1), np.uint8), np.zeros(m, np.uint8)] + \
           [np.zeros(m, np.int64) for _ in range(4)]
    p = na._ptr
    return lib.vnx_align_batch(n, p(codes) if codes is not None else None, p(offsets) if offsets is not None else None, total, None,
                               None, T, p(g["tpl"]), p(g["seg_of"]), p(g["prev_seg"]), p(g["next_seg"]), nseg, frame_nt,
                               p(g["frame_pos"]), p(g["seg_frame"]), band, *costs, min_q, *(p(o) for o in outs))


def test_kernel_rejects_bad_arguments(native_ready):
    lib = na._load()
    T = LAY.strand_nt
    read = strand(LAY, np.random.default_rng(9))
    off = np.array([0, T], np.int64)
    assert _call(lib, 1, read, off, T) == 0                                         # control: valid call
    assert _call(lib, 0, None, None, 0) == 0                                        # empty batch
    assert _call(lib, -1, read, off, T) == -1                                       # negative count
    assert _call(lib, 1, read, None, T) == -1                                       # missing offsets
    assert _call(lib, 1, None, off, T) == -1                                        # missing codes
    assert _call(lib, 1, read, np.array([1, T], np.int64), T) == -3                 # offsets[0] != 0
    assert _call(lib, 1, read, np.array([0, T - 1], np.int64), T) == -3             # offsets[n] != total
    short = np.array([0, T - 7], np.int64)
    assert _call(lib, 1, read, short, T - 7) == -3                                  # read outside the band
    assert _call(lib, 1, read, off, T, band=65) == -2                               # band outside the domain
    assert _call(lib, 1, read, off, T, costs=(4, -1, 6, 1, 0)) == -2                # negative cost
    assert _call(lib, 1, read, off, T, costs=(4, 6, 70000, 1, 0)) == -2             # cost too large
    assert _call(lib, 1, read, off, T, costs=(4, 6, 6, 1, 2000)) == -2              # guard too large
    assert _call(lib, 1, read, off, T, T=0) == -2                                   # empty template
    assert _call(lib, 1, read, off, T, T=9000) == -2                                # template too long
    assert _call(lib, 1, read, off, T, nseg=10_000) == -2                           # more segments than frame bases
    al = TemplateAligner(LAY, 6, backend="native")
    bad = dict(na.geometry(al))
    bad["frame_pos"] = bad["frame_pos"].copy()
    bad["frame_pos"][3] = T + 5                                                     # index outside the template
    assert _call(lib, 1, read, off, T, geom=bad) == -1
    bad = dict(na.geometry(al))
    bad["seg_of"] = bad["seg_of"].copy()
    bad["seg_of"][0] = 999                                                          # segment id out of range
    assert _call(lib, 1, read, off, T, geom=bad) == -1


def test_kernel_empty_read_with_quality_flag(native_ready):
    """T <= B makes an empty read usable. Its quality flag may be set with no quality buffer (there are no bases to
    read); the kernel must accept that. Not reachable through Layout (T >= 67 > MAX_BAND), so tested at the C ABI."""
    lib = na._load()
    T, band = 6, 6
    tpl = np.full(T, -1, np.int16)
    zeros = np.zeros(T, np.int32)
    frame_pos = np.arange(T, dtype=np.int32)
    offsets = np.zeros(2, np.int64)
    has_q = np.ones(1, np.uint8)
    bases, erased, ok = np.zeros(T, np.uint8), np.zeros(T, np.uint8), np.zeros(1, np.uint8)
    ins, dele, mm, cost = (np.zeros(1, np.int64) for _ in range(4))
    p = na._ptr
    rc = lib.vnx_align_batch(1, None, p(offsets), 0, None, p(has_q), T, p(tpl), p(zeros), p(zeros), p(zeros), 1, T, p(frame_pos),
                             p(zeros), band, 4, 6, 6, 1, 0, 10, p(bases), p(erased), p(ok), p(ins), p(dele), p(mm), p(cost))
    assert rc == 0
    assert ok[0] == 1 and dele[0] == T and ins[0] == 0 and cost[0] == 6 * T and erased.all() and (bases == 4).all()


def test_profiled_entry_point_is_identical(native_ready):
    """The benchmark-only profiled entry point returns the same arrays and non-negative stage times."""
    rng = np.random.default_rng(21)
    reads = [strand(LAY, rng) for _ in range(9)] + [np.insert(strand(LAY, rng), 50, np.uint8(2))]
    al = TemplateAligner(LAY, 6, backend="native")
    plain = na.align_usable(al, reads, None, 0)
    tm: dict = {}
    prof = na.align_usable(al, reads, None, 0, timings=tm)
    for a, b in zip(plain, prof):
        assert a.dtype == b.dtype and np.array_equal(a, b)
    assert set(tm) == {"pack", "dp", "traceback", "projection", "c_call", "wrapper"} and min(tm.values()) >= 0


def test_kernel_error_raises_not_ignored(native_ready, monkeypatch):
    class Proxy:
        @staticmethod
        def vnx_align_batch(*a):
            return -5
    monkeypatch.setattr(na, "_lib", Proxy())
    with pytest.raises(na.NativeAlignmentError, match="traceback"):
        TemplateAligner(LAY, backend="native").project([strand(LAY, np.random.default_rng(1))])


# ============================================================================ CLI diagnostics
def test_cli_reports_backend(native_ready, monkeypatch):
    from typer.testing import CliRunner

    from vnxdna.v4.cli import app
    monkeypatch.delenv("VNXDNA_ALIGN_BACKEND", raising=False)
    r = CliRunner().invoke(app, ["native"])
    assert r.exit_code == 0, r.output
    st = json.loads(r.output)
    assert st["active_backend"] == "native" and st["native_available"] and st["library"]
    r = CliRunner().invoke(app, ["version"])
    assert json.loads(r.output)["alignment_backend"] == "native"
    monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", "reference")
    assert json.loads(CliRunner().invoke(app, ["version"]).output)["alignment_backend"] == "reference"


# ============================================================================ decoder-level equivalence
CHANNELS = {
    # recoverable: must decode to SUCCESS with the original bytes
    "mild": (dict(substitution_rate=0.003, insertion_rate=0.001, deletion_rate=0.001, dropout_rate=0.01, reverse_complement_rate=0.3,
                  coverage=5, coverage_model="poisson", quality_informative=0.5, seed=77), 15),
    # beyond recovery for this archive (one outer group fails): both backends must fail identically
    "harsh": (dict(substitution_rate=0.004, insertion_rate=0.002, deletion_rate=0.002, dropout_rate=0.02, reverse_complement_rate=0.3,
                   coverage=3, coverage_model="poisson", quality_informative=0.5, seed=77), 15),
}


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("channel", sorted(CHANNELS))
def test_decode_identical_with_either_backend(tmp_path, monkeypatch, native_ready, workers, channel):
    """A full noisy decode (orientation pre-pass, RC retry, pending reads, snapping, consensus, outer decode) gives the
    same status, the same report (every count) and the same container bytes with either backend."""
    import hashlib
    src = tmp_path / "in.bin"
    sha = datagen.generate(src, 300_000, "random", 5)
    ar.build_archive([src], tmp_path / "a.vnx", ar.ArchiveOptions())
    en.encode_container(tmp_path / "a.vnx", tmp_path / "s.fasta", en.DNAOptions())
    kw, min_q = CHANNELS[channel]
    ch.simulate_file(tmp_path / "s.fasta", tmp_path / "r.fastq", ch.ChannelConfig(**kw))
    reports = {}
    for backend in ("reference", "native"):
        monkeypatch.setenv("VNXDNA_ALIGN_BACKEND", backend)
        res = de.decode_reads(tmp_path / "r.fastq", tmp_path / f"{backend}.vnx", de.DecodeOptions(workers=workers, min_quality=min_q))
        # native_backends records which aligner ran: it must name this backend (and is the only field allowed to differ)
        assert res.report["native_backends"]["align"]["backend"] == backend
        rep = {k: v for k, v in res.report.items()
               if k not in ("stage_seconds", "seconds", "peak_rss_bytes", "output", "native_backends")}
        reports[backend] = (res.status, json.dumps(rep, sort_keys=True, default=str))
    assert reports["reference"] == reports["native"]
    status = reports["native"][0]
    if channel == "mild":
        assert status == "SUCCESS"
        assert (tmp_path / "reference.vnx").read_bytes() == (tmp_path / "native.vnx").read_bytes()
        ar.extract(tmp_path / "native.vnx", tmp_path / "out")
        assert hashlib.sha256((tmp_path / "out" / "in.bin").read_bytes()).hexdigest() == sha
    else:
        assert status != "SUCCESS"


def test_reads_with_many_lengths_in_one_simd_group(native_ready):
    """Lanes of one SIMD group with different lengths (the only per-lane control quantity besides the bases)."""
    rng = np.random.default_rng(12)
    base = strand(LAY, rng)
    reads = [base[:LAY.strand_nt - k] if k >= 0 else np.concatenate([base, rng.integers(0, 4, -k).astype(np.uint8)])
             for k in (6, -6, 0, 3, -2, 5, -5, 1, -1)]
    ref, nat = project_both(LAY, reads)
    assert_identical(ref, nat, "ragged lanes")
