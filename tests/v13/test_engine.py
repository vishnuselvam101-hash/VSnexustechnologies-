"""V13 streaming decode engine (native/rust/engine, docs/V13_ENGINE_ARCHITECTURE.md).

- the scanner returns exactly the reads of the reference parser (dnaenc.reads.iter_reads), or refuses the input;
- with the address scheduler off, the engine's pass 1 writes spill files byte-identical to the V13 pass 1
  (recovery.pass1._process) on every layout, option and thread count tested;
- with the scheduler on, decodes are byte-identical to the archive, deterministic across thread counts, and attempt
  fewer reads than they scan;
- the fallback ladder: refusals, engine errors, non-SUCCESS results and pinned reference backends drop to the V13
  path (recorded in report["engine"]); engine="stream" refuses what it cannot run; the timeout cancels the engine;
  request errors propagate unchanged. SIMULATED reads only.
"""
from __future__ import annotations

import filecmp
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError, VNXFormatError
from vnxdna.dnaenc import reads as ref_reads
from vnxdna.native import engine as ne
from vnxdna.recovery import engine as re_
from vnxdna.native.reads import iter_reads
from vnxdna.recovery import pass1
from vnxdna.recovery.options import DecodeOptions
from vnxdna.recovery.probe import detect_layout
from vnxdna.recovery.spill import Spill
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

pytestmark = pytest.mark.skipif(not ne.available(), reason="engine library not built (python -m vnxdna.native.engine build)")

INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002}


@pytest.fixture(scope="module")
def pools(tmp_path_factory):
    d = tmp_path_factory.mktemp("v13engine")
    datagen.generate(d / "in.bin", 150_000, "random", 13101)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    out = {"dir": d, "archive": d / "a.vnx"}
    for profile in ("v4-balanced", "v4-dense", "v6-high-dropout"):
        en.encode_container(d / "a.vnx", d / f"s-{profile}.fasta", en.DNAOptions(profile=profile))
    for name, profile, cfg, cov in (("clean", "v4-balanced", {}, 3), ("indel", "v4-balanced", INDEL, 4),
                                    ("indel_rc", "v4-balanced", {**INDEL, "reverse_complement_rate": 0.5}, 4),
                                    ("heavy", "v4-balanced", {"substitution_rate": 0.03, "insertion_rate": 0.01,
                                                              "deletion_rate": 0.01}, 3),
                                    ("dense", "v4-dense", {"substitution_rate": 0.01}, 3),
                                    ("hd", "v6-high-dropout", {"substitution_rate": 0.005}, 3)):
        path = d / f"{name}.fastq"
        ch.simulate_file(d / f"s-{profile}.fasta", path, ch.ChannelConfig(seed=17, coverage=cov, **cfg))
        out[name] = path
    return out


# ------------------------------------------------------------------------------------------------------- scanner
def _reference_reads(path):
    codes, lens, quals = [], [], []
    for b in ref_reads.iter_reads(path, 1000):
        codes.append(b.codes)
        lens.append(b.lengths)
        if b.quals is not None:
            quals.append(b.quals)
    if not lens:
        return np.zeros(0, np.uint8), np.zeros(0, np.int64), None
    return np.concatenate(codes), np.concatenate(lens), (np.concatenate(quals) if quals else None)


def _random_text(rng, fastq: bool) -> bytes:
    alphabet = b"ACGTNacgtnX- "
    out = bytearray()
    if rng.random() < 0.2:
        out += b"\n" * int(rng.integers(1, 3))
    for _ in range(int(rng.integers(0, 40))):
        n = int(rng.integers(0, 60))
        seq = bytes(alphabet[int(i)] for i in rng.integers(0, 4 if rng.random() < 0.7 else len(alphabet), n))
        eol = b"\r\n" if rng.random() < 0.2 else b"\n"
        if fastq:
            qual = bytes(int(q) for q in rng.integers(33, 75, n))
            out += b"@r" + eol + seq + eol + b"+" + eol + qual + eol
            if rng.random() < 0.1:
                out += b"\n"                                    # blank line before the next header
        else:
            out += b">r" + eol
            for k in range(0, max(1, n), int(rng.integers(5, 80))):
                out += seq[k:k + 80] + (b"  " if rng.random() < 0.1 else b"") + eol
    if out and rng.random() < 0.3:
        out = out.rstrip(b"\n")                                 # no newline at the end
    if rng.random() < 0.05 and out:
        cut = int(rng.integers(0, len(out)))
        out = out[:cut]                                         # truncated (reference error or a shorter file)
    return bytes(out)


@pytest.mark.parametrize("fmt", ["fastq", "fasta"])
def test_scanner_matches_reference_parser_or_refuses(tmp_path, fmt):
    rng = np.random.default_rng(31 if fmt == "fastq" else 32)
    agreed = refused = 0
    for case in range(150):
        p = tmp_path / f"c{case}.{fmt}"
        text = _random_text(rng, fmt == "fastq")
        p.write_bytes(text)
        try:
            detected = ref_reads.detect(p)
        except Exception:  # noqa: BLE001 - undetectable input never reaches the engine (detection is in Python)
            continue
        if detected != fmt:
            continue
        try:
            want = _reference_reads(p)
            ref_error = None
        except Exception as error:  # noqa: BLE001 - the reference rejects it: the engine must refuse too
            want, ref_error = None, error
        for block, use_map in ((1 << 16, True), (7, False), (1, False), (64, False)):
            try:
                got = ne.scan_file(p, fmt, block=block, use_map=use_map)
            except ne.EngineUnsupported:
                refused += 1
                continue
            assert ref_error is None, f"engine accepted input the reference rejects: {ref_error!r}"
            assert np.array_equal(got[1], want[1])
            assert np.array_equal(got[0], want[0])
            if fmt == "fastq" and want[2] is not None:
                assert np.array_equal(got[2], want[2])
            agreed += 1
    assert agreed > 300                                         # most generated files are inside the domain


def test_scanner_on_simulated_pool(pools):
    want = _reference_reads(pools["indel"])
    for use_map in (True, False):
        got = ne.scan_file(pools["indel"], "fastq", block=1 << 16, use_map=use_map)
        assert np.array_equal(got[1], want[1]) and np.array_equal(got[0], want[0]) and np.array_equal(got[2], want[2])


# ------------------------------------------------------------------------------------------------- pass 1 equality
def _v13_spill(path, lay, opt, d: Path, buckets: int) -> Counter:
    sp = Spill(d, buckets, lay.payload_bytes, lay.frame_nt)
    pass1._p_init(lay, opt.band, opt.sync_costs, opt.min_quality, opt.reverse_complement, None, None, False, False,
                  opt.retry_band, False, False, opt.orientation_retry, opt.sync_speculation)
    stats: Counter = Counter()
    for b in iter_reads(path, 1500):
        res = pass1._process(b.codes, b.lengths, b.quals if opt.min_quality else None)
        sp.write(res)
        stats.update(res["stats"])
    sp.close()
    return stats


def _engine_spill(path, lay, opt, d: Path, buckets: int, threads: int, dedup: bool) -> dict:
    Spill(d, buckets, lay.payload_bytes, lay.frame_nt).close()
    return re_.run_pass1(path, d, lay, opt, buckets, fmt="fastq", threads=threads, dedup=dedup, batch=700)


def _same_dirs(a: Path, b: Path) -> bool:
    names = sorted(p.name for p in a.iterdir())
    return names == sorted(p.name for p in b.iterdir()) and all(
        filecmp.cmp(a / n, b / n, shallow=False) for n in names)


@pytest.mark.parametrize("pool,opts,threads", [
    ("clean", {}, 1), ("indel", {}, 1), ("indel", {}, 3), ("indel_rc", {}, 2), ("heavy", {}, 1),
    ("indel", {"min_quality": 30}, 2), ("indel_rc", {"orientation_retry": "always"}, 1),
    ("indel", {"sync_speculation": "off"}, 1), ("indel_rc", {"reverse_complement": False}, 1),
    ("dense", {}, 2), ("hd", {}, 1),
])
def test_pass1_without_scheduler_equals_v13(pools, tmp_path, pool, opts, threads):
    path = pools[pool]
    opt = DecodeOptions(**opts)
    lay = detect_layout(path, opt)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    want = _v13_spill(path, lay, opt, tmp_path / "a", 2)
    got = _engine_spill(path, lay, opt, tmp_path / "b", 2, threads, dedup=False)
    assert _same_dirs(tmp_path / "a", tmp_path / "b")
    for k in ("reads", "fast", "sync", "speculative", "reverse_complement", "pending", "orphans", "unaligned",
              "rc_retry_skipped"):
        assert got[k] == want[k], k
    assert got["skipped"] == 0 and got["attempted"] == want["reads"]


@pytest.mark.parametrize("pool", ["indel", "indel_rc", "dense"])
def test_scheduler_is_deterministic_across_threads(pools, tmp_path, pool):
    path = pools[pool]
    opt = DecodeOptions()
    lay = detect_layout(path, opt)
    stats = []
    for t in (1, 2, 5):
        d = tmp_path / f"t{t}"
        d.mkdir()
        stats.append(_engine_spill(path, lay, opt, d, 3, t, dedup=True))
    assert _same_dirs(tmp_path / "t1", tmp_path / "t2") and _same_dirs(tmp_path / "t1", tmp_path / "t5")
    keys = ("reads", "fast", "sync", "pending", "attempted", "skipped")
    assert all({k: s[k] for k in keys} == {k: stats[0][k] for k in keys} for s in stats)
    assert stats[0]["skipped"] > 0 and stats[0]["attempted"] < stats[0]["reads"]


# ------------------------------------------------------------------------------------------------ whole decodes
def _decode(pools, tmp_path, pool, **opts):
    out = tmp_path / f"o-{pool}.vnx"
    res = de.decode_reads(pools[pool], out, DecodeOptions(**opts), overwrite=True)
    return res, out


@pytest.mark.parametrize("pool,workers", [("clean", 1), ("indel", 1), ("indel", 3), ("indel_rc", 2), ("dense", 1),
                                          ("hd", 2)])
def test_stream_decode_is_byte_identical(pools, tmp_path, pool, workers):
    res, out = _decode(pools, tmp_path, pool, workers=workers)
    assert res.status == "SUCCESS"
    assert out.read_bytes() == pools["archive"].read_bytes()
    eng = res.report["engine"]
    assert eng["rung"] == "stream" and eng["fallbacks"] == [] and eng["threads"] == workers
    assert res.report["reads"]["skipped_redundant"] == eng["pass1"]["skipped"] > 0


def test_v13_rung_is_selectable_and_identical(pools, tmp_path):
    a, out_a = _decode(pools, tmp_path, "indel", engine="v13")
    assert a.report["engine"]["rung"] == "v13" and a.report["engine"]["fallbacks"] == []
    data = out_a.read_bytes()
    b, out_b = _decode(pools, tmp_path, "indel", engine="stream")
    assert b.report["engine"]["rung"] == "stream" and out_b.read_bytes() == data


def test_reference_rung(pools, tmp_path, monkeypatch):
    res, out = _decode(pools, tmp_path, "clean", engine="reference")
    assert res.status == "SUCCESS" and res.report["engine"]["rung"] == "reference"
    assert out.read_bytes() == pools["archive"].read_bytes()


def test_fallback_when_engine_is_deselected(pools, tmp_path, monkeypatch):
    monkeypatch.setenv("VNXDNA_ENGINE_BACKEND", "reference")
    res, out = _decode(pools, tmp_path, "indel")
    eng = res.report["engine"]
    assert res.status == "SUCCESS" and eng["rung"] == "v13"
    assert "VNXDNA_ENGINE_BACKEND" in eng["fallbacks"][0]["reason"]
    with pytest.raises(VNXConfigurationError):
        _decode(pools, tmp_path, "indel", engine="stream")


def test_fallback_on_engine_error(pools, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise ne.EngineError("injected")
    monkeypatch.setattr(re_, "run_pass1", boom)
    res, out = _decode(pools, tmp_path, "indel")
    eng = res.report["engine"]
    assert res.status == "SUCCESS" and eng["rung"] == "v13" and "injected" in eng["fallbacks"][0]["reason"]
    assert out.read_bytes() == pools["archive"].read_bytes()
    with pytest.raises(ne.EngineError):
        _decode(pools, tmp_path, "indel", engine="stream")


def test_fallback_on_non_success(pools, tmp_path, monkeypatch):
    """An engine pass 1 that recovers too little (simulated: it writes nothing) never yields a result: the V13 path
    decodes, and the output is the archive."""
    def nothing(reads_path, spill_dir, lay, opt, buckets, **kw):
        return {k: 0 for k in ne.STAT_NAMES} | {"reads": 1}
    monkeypatch.setattr(re_, "run_pass1", nothing)
    res, out = _decode(pools, tmp_path, "indel")
    eng = res.report["engine"]
    assert res.status == "SUCCESS" and eng["rung"] == "v13" and eng["fallbacks"][0]["rung"] == "stream"
    assert out.read_bytes() == pools["archive"].read_bytes()


def test_out_of_domain_options_use_v13(pools, tmp_path):
    res, _ = _decode(pools, tmp_path, "clean", stage_counters=True)
    assert res.report["engine"]["rung"] == "v13" and "stage counters" in res.report["engine"]["fallbacks"][0]["reason"]
    with pytest.raises(VNXConfigurationError):
        _decode(pools, tmp_path, "clean", engine="stream", indel_recovery="smart")


def test_malformed_input_gives_the_reference_error(tmp_path):
    bad = tmp_path / "bad.fastq"
    bad.write_bytes(b"@r1\nACGT\n+\nII\n")                    # quality length differs
    errors = []
    for engine in ("auto", "v13"):
        with pytest.raises(VNXFormatError) as exc:
            de.decode_reads(bad, tmp_path / "o.vnx", DecodeOptions(engine=engine, profile="v4-balanced"))
        errors.append(str(exc.value))
    assert errors[0] == errors[1]


def test_wall_time_budget_cancels_the_engine(pools, tmp_path):
    from vnxdna.recovery.planner import RecoveryBudget, VNXBudgetExceeded
    with pytest.raises(VNXBudgetExceeded) as exc:
        _decode(pools, tmp_path, "indel", recovery_budget=RecoveryBudget(max_wall_seconds=1e-9))
    assert exc.value.code == "BUDGET_EXCEEDED"
    assert not (tmp_path / "o-indel.vnx").exists()


def test_option_validation():
    for bad in ({"engine": "fast"}, {"engine_dedup": 1}):
        with pytest.raises(VNXConfigurationError):
            DecodeOptions(**bad).validate()


def test_abi_and_argument_checks(pools, tmp_path):
    import ctypes
    assert ctypes.sizeof(ne._Cfg) == 45 * 8
    assert ne.status()["active_backend"] == "native"
    opt = DecodeOptions(band=6)
    lay = detect_layout(pools["clean"], opt)
    Spill(tmp_path, 1, lay.payload_bytes, lay.frame_nt).close()
    with pytest.raises(ne.EngineError):
        re_.run_pass1(pools["clean"], tmp_path, lay, opt, 0, fmt="fastq", threads=1)      # 0 buckets: invalid argument
    with pytest.raises(ne.EngineUnsupported):
        re_.run_pass1(pools["clean"], tmp_path, lay, opt, 1, fmt="plain", threads=1)
    with pytest.raises(ne.EngineUnsupported):
        re_.params_for(lay, DecodeOptions(band=6, retry_band=12))


# ------------------------------------------------------------------------------------------ configuration surface
def test_engine_configuration_surface(monkeypatch):
    """One configuration surface: the environment sets the defaults, explicit options win, bad values are refused."""
    monkeypatch.delenv("VNXDNA_ENGINE", raising=False)
    monkeypatch.delenv("VNXDNA_ENGINE_THREADS", raising=False)
    o = DecodeOptions()
    assert o.engine == "auto" and o.engine_threads is None and o.threads == 1
    assert DecodeOptions(workers=4).threads == 4 and DecodeOptions(workers=4, engine_threads=2).threads == 2
    monkeypatch.setenv("VNXDNA_ENGINE", "v13")
    monkeypatch.setenv("VNXDNA_ENGINE_THREADS", "3")
    o = DecodeOptions(workers=2)
    o.validate()
    assert o.engine == "v13" and o.engine_threads == 3 and o.threads == 3
    assert DecodeOptions(engine="stream", engine_threads=1).engine == "stream"       # explicit wins over the environment
    monkeypatch.setenv("VNXDNA_ENGINE", "turbo")
    with pytest.raises(VNXConfigurationError, match="VNXDNA_ENGINE"):
        DecodeOptions().validate()
    monkeypatch.setenv("VNXDNA_ENGINE", "auto")
    monkeypatch.setenv("VNXDNA_ENGINE_THREADS", "many")
    with pytest.raises(VNXConfigurationError, match="VNXDNA_ENGINE_THREADS"):
        DecodeOptions().validate()
    monkeypatch.delenv("VNXDNA_ENGINE_THREADS")
    for bad in (0, 257, True, "2"):
        with pytest.raises(VNXConfigurationError):
            DecodeOptions(engine_threads=bad).validate()


def test_engine_threads_are_independent_of_workers(pools, tmp_path):
    a, out_a = _decode(pools, tmp_path, "indel", workers=1, engine_threads=3)
    assert a.report["engine"]["rung"] == "stream" and a.report["engine"]["threads"] == 3
    b, out_b = _decode(pools, tmp_path, "indel", workers=3, engine_threads=1)
    assert b.report["engine"]["threads"] == 1 and out_a.read_bytes() == out_b.read_bytes() == pools["archive"].read_bytes()
    assert a.report["engine"]["pass1"]["attempted"] == b.report["engine"]["pass1"]["attempted"]   # deterministic schedule


def test_native_status_lists_the_engine():
    import vnxdna
    from vnxdna import native
    st = vnxdna.native_status()
    k = st["kernels"]["engine"]
    assert k["backend"] == "native" and k["abi_version"] == ne.ABI_VERSION and k["reference"] == "vnxdna.recovery.pass1"
    assert k["library"].endswith(".so") and k["library_origin"] in ("env", "packaged", "in-place")
    assert native.backend_summary()["engine"]["backend"] == "native"
    assert "engine" in native.KERNELS and native.main([]) == 0


def test_cli_engine_flags(pools, tmp_path):
    import json
    from typer.testing import CliRunner
    from vnxdna.commands.cli import app
    runner = CliRunner()
    reads = str(pools["indel"])
    r = runner.invoke(app, ["decode", reads, "-o", str(tmp_path / "a.vnx"), "--engine", "v13", "--report",
                            str(tmp_path / "a.json")])
    assert r.exit_code == 0, r.output
    rep = json.loads((tmp_path / "a.json").read_text())["result"]
    assert rep["engine"]["rung"] == "v13" and rep["engine"]["requested"] == "v13"
    r = runner.invoke(app, ["decode", reads, "-o", str(tmp_path / "b.vnx"), "--engine", "stream", "--no-engine-dedup",
                            "--engine-threads", "2", "--report", str(tmp_path / "b.json")])
    assert r.exit_code == 0, r.output
    rep = json.loads((tmp_path / "b.json").read_text())["result"]
    assert rep["engine"] == {**rep["engine"], "rung": "stream", "dedup": False, "threads": 2}
    assert rep["engine"]["pass1"]["skipped"] == 0
    assert (tmp_path / "a.vnx").read_bytes() == (tmp_path / "b.vnx").read_bytes() == pools["archive"].read_bytes()
    r = runner.invoke(app, ["decode", reads, "-o", str(tmp_path / "c.vnx"), "--engine", "turbo"])
    assert r.exit_code == 7 and not (tmp_path / "c.vnx").exists()
