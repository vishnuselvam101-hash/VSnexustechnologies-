"""WP2: capability probe, deterministic path selection, differential self-check and fault injection."""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

from vnxdna import dispatch
from vnxdna.native import integrity, native_status
from vnxdna.native._loader import NativeKernelError

KERNELS = tuple(native_status()["kernels"])


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("INTEGRITY", "STRAND", "OUTERLIST"):
        monkeypatch.delenv(f"VNXDNA_{k}_BACKEND", raising=False)
    monkeypatch.delenv(dispatch.DIFFERENTIAL_ENV, raising=False)
    yield
    for k in dispatch._LOADER_KERNELS:
        dispatch._kernel_obj(k).quarantine_reason = None


def _native_integrity():
    return native_status()["kernels"]["integrity"]["backend"] == "native"


def test_probe_lists_every_kernel():
    caps = dispatch.probe()["kernels"]
    assert tuple(caps) == KERNELS
    assert all({"backend", "abi_version", "load_error"} <= set(c) for c in caps.values())


def test_selection_is_deterministic_and_complete():
    a, b = dispatch.select_path(), dispatch.select_path()
    assert a == b and tuple(a["kernels"]) == KERNELS
    assert all(e["path"] in ("native", "reference") and e["reason"] for e in a["kernels"].values())


def test_reference_request_selected(monkeypatch):
    monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", "reference")
    e = dispatch.select_path()["kernels"]["integrity"]
    assert e == {"path": "reference", "reason": "requested"}


@pytest.mark.parametrize("bad", [{"input_bytes": -1}, {"workers": 1.5}, {"reads": True, "x_reads": "3"}])
def test_invalid_workload_rejected(bad):
    with pytest.raises(dispatch.DispatchError):
        dispatch.select_path(bad)


@pytest.mark.skipif(not _native_integrity(), reason="integrity kernel not built")
def test_differential_pass():
    e = dispatch.select_path(differential=True)["kernels"]["integrity"]
    assert e["path"] == "native" and e["differential"] == "pass"


def test_backend_missing_falls_back(monkeypatch):
    kern = dispatch._kernel_obj("integrity")
    monkeypatch.setattr(kern, "load", lambda: None)
    e = dispatch.select_path()["kernels"]["integrity"]
    assert e["path"] == "reference"
    rows = np.arange(60, dtype=np.uint8).reshape(6, 10)
    assert integrity.crc32_rows(rows).tobytes() == dispatch.__dict__["np"].asarray(integrity.crc32_rows_reference(rows)).tobytes()


def test_backend_missing_explicit_native_fails(monkeypatch):
    monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", "native")
    monkeypatch.setattr(dispatch._kernel_obj("integrity"), "load", lambda: None)
    with pytest.raises(dispatch.DispatchError):
        dispatch.select_path()


@pytest.mark.skipif(not _native_integrity(), reason="integrity kernel not built")
class TestFaultInjection:
    def _bad_output(self, monkeypatch):
        real = integrity.crc32_rows_native
        monkeypatch.setattr(integrity, "crc32_rows_native", lambda rows, threads=1: real(rows, threads) ^ np.uint32(1))

    def test_wrong_output_falls_back_never_wrong_data(self, monkeypatch):
        self._bad_output(monkeypatch)
        e = dispatch.select_path(differential=True)["kernels"]["integrity"]
        assert e["path"] == "reference" and e["differential"] == "fail" and e["reason"] == "differential-fail-fallback"
        rows = np.arange(120, dtype=np.uint8).reshape(12, 10)
        assert integrity.crc32_rows(rows).tobytes() == np.asarray(integrity.crc32_rows_reference(rows)).tobytes()

    def test_wrong_output_explicit_native_raises(self, monkeypatch):
        self._bad_output(monkeypatch)
        monkeypatch.setenv("VNXDNA_INTEGRITY_BACKEND", "native")
        with pytest.raises(dispatch.DispatchError, match="differential"):
            dispatch.select_path(differential=True)

    def test_raising_backend_falls_back(self, monkeypatch):
        def boom(rows, threads=1):
            raise NativeKernelError("injected")
        monkeypatch.setattr(integrity, "crc32_rows_native", boom)
        e = dispatch.select_path(differential=True)["kernels"]["integrity"]
        assert e["path"] == "reference" and "injected" in e["detail"]

    def test_env_opt_in(self, monkeypatch):
        self._bad_output(monkeypatch)
        monkeypatch.setenv(dispatch.DIFFERENTIAL_ENV, "1")
        assert dispatch.path_report()["kernels"]["integrity"]["path"] == "reference"

    def test_default_has_no_check(self, monkeypatch):
        self._bad_output(monkeypatch)
        assert "differential" not in dispatch.select_path()["kernels"]["integrity"]


def _container(tmp_path):
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import datagen
    (tmp_path / "ds").mkdir()
    datagen.generate(tmp_path / "ds" / "f.bin", 12_000, "random", 7)
    ar.build_archive([tmp_path / "ds"], tmp_path / "a.vnx", ar.ArchiveOptions())
    return tmp_path / "a.vnx"


def test_reports_record_path_and_outputs_byte_identical(tmp_path, monkeypatch):
    from vnxdna.pipeline.encode import encode_container
    c = _container(tmp_path)
    r1 = encode_container(c, tmp_path / "a.fa")
    for k in ("INTEGRITY", "STRAND", "OUTERLIST", "ALIGN", "READS", "RS", "CLUSTER"):
        monkeypatch.setenv(f"VNXDNA_{k}_BACKEND", "reference")
    r2 = encode_container(c, tmp_path / "b.fa")
    assert r1["path_selection"]["schema"] == dispatch.SCHEMA
    assert all(e["path"] == "reference" for e in r2["path_selection"]["kernels"].values())
    h = [hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in ("a.fa", "b.fa")]
    assert h[0] == h[1]


def test_decode_report_records_path_and_data_roundtrips(tmp_path):
    from vnxdna.v4 import channel as ch
    from vnxdna.v4 import decoder as de
    from vnxdna.v4 import encoder as en
    c = _container(tmp_path)
    en.encode_container(c, tmp_path / "s.fa")
    ch.simulate_file(tmp_path / "s.fa", tmp_path / "r.fq", ch.ChannelConfig(coverage=5, seed=3))
    res = de.decode_reads(tmp_path / "r.fq", tmp_path / "o.vnx", de.DecodeOptions())
    assert res.status == "SUCCESS"
    assert res.report["path_selection"]["schema"] == dispatch.SCHEMA
    assert (tmp_path / "o.vnx").read_bytes() == c.read_bytes()
