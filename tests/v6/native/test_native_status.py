"""vnxdna.native_status(): which backend each native kernel uses, and that it is recorded in reports and events.

Also the packaging contract: setup.py builds all four kernels as optional extensions with the explicit builds' flags
(minus -Werror), under the names the loaders look for; and the RS level-restriction hook stays test-only.
"""
from __future__ import annotations

import json
import os
import re
import runpy
import sysconfig
from pathlib import Path

import pytest
from typer.testing import CliRunner

import vnxdna
from vnxdna import _native_build as nb
from vnxdna import native
from vnxdna.native import cluster as ncl
from vnxdna.v5 import native_alignment as na
from vnxdna.v6 import native_reads as nr
from vnxdna.v6 import native_rs as nrs

ROOT = Path(__file__).resolve().parents[3]
MODULES = {"align": (na, "VNXDNA_NATIVE_LIB", "libvnx_align.so"), "reads": (nr, "VNXDNA_READS_LIB", "libvnx_reads.so"),
           "rs": (nrs, "VNXDNA_RS_LIB", "libvnx_rs.so"), "cluster": (ncl, "VNXDNA_CLUSTER_LIB", "libvnx_cluster.so")}
BACKEND_ENV = ("VNXDNA_ALIGN_BACKEND", "VNXDNA_READS_BACKEND", "VNXDNA_RS_BACKEND", "VNXDNA_CLUSTER_BACKEND")


def _reset() -> None:
    for m, _, _ in MODULES.values():
        m._reset_for_tests()


@pytest.fixture(scope="module", autouse=True)
def _libraries(tmp_path_factory):
    """Every kernel loadable: an installed/in-place library, else one built here (skip without a compiler)."""
    saved = {env: os.environ.get(env) for _, env, _ in MODULES.values()}
    for kernel, (m, env, name) in MODULES.items():
        if not m.available():
            try:
                lib = m.build(tmp_path_factory.mktemp(kernel) / name)
            except RuntimeError as error:
                pytest.skip(f"{kernel} kernel unavailable and cannot be built here: {error}")
            os.environ[env] = str(lib)
            m._reset_for_tests()
    yield
    for env, v in saved.items():
        if v is None:
            os.environ.pop(env, None)
        else:
            os.environ[env] = v
    _reset()


@pytest.fixture(autouse=True)
def _clean_backend_env(monkeypatch):
    for env in BACKEND_ENV:
        monkeypatch.delenv(env, raising=False)
    yield
    _reset()


# ---------------------------------------------------------------------------------------------------- status function
def test_all_kernels_native_when_libraries_load():
    st = vnxdna.native_status()
    assert st["all_native"] is True and set(st["kernels"]) == {"align", "reads", "rs", "cluster"}
    for kernel, (m, _, _) in MODULES.items():
        k = st["kernels"][kernel]
        assert k["backend"] == "native" and k["library"] and Path(k["library"]).is_file(), k
        assert k["abi_version"] == m.ABI_VERSION and k["load_error"] is None and k["error"] is None
        assert k["library_origin"] in ("env", "packaged", "in-place")
    rs = st["kernels"]["rs"]
    assert rs["simd_level"] == nrs.active_backend() and rs["simd_level"] in rs["supported_levels"]
    assert rs["levels_restricted"] is False
    assert st["kernels"]["align"]["simd_lanes"] >= 1
    assert st["kernels"]["cluster"]["simd_level"] in ("scalar", "avx2")
    assert st["kernels"]["cluster"]["reference"] == "vnxdna.recovery.cluster"
    assert json.loads(json.dumps(st)) == st        # JSON-serialisable as is


def test_reference_when_libraries_are_missing(monkeypatch):
    for m, _, _ in MODULES.values():
        monkeypatch.setattr(m, "_candidates", lambda: [])
    _reset()
    st = native.native_status()
    assert st["all_native"] is False
    for kernel in MODULES:
        k = st["kernels"][kernel]
        assert k["backend"] == "reference" and k["library"] is None and k["library_origin"] is None and k["load_error"], k
    assert st["kernels"]["rs"]["simd_level"] is None
    assert {k: v["backend"] for k, v in native.backend_summary().items()} == dict.fromkeys(MODULES, "reference")
    assert native.main(["--require-native"]) == 1 and native.main([]) == 0


def test_requested_reference_is_reported(monkeypatch):
    for env in BACKEND_ENV:
        monkeypatch.setenv(env, "reference")
    st = native.native_status()
    for k in st["kernels"].values():
        assert k["backend"] == "reference" and k["requested"] == "reference" and k["library"]   # loaded but not used


def test_forced_simd_level_is_reported(monkeypatch):
    monkeypatch.setenv("VNXDNA_RS_BACKEND", "scalar")
    assert native.native_status()["kernels"]["rs"]["simd_level"] == "scalar"
    assert native.backend_summary()["rs"] == {"backend": "native", "simd_level": "scalar", "abi_version": nrs.ABI_VERSION,
                                              "library_origin": native.native_status()["kernels"]["rs"]["library_origin"]}


def test_vnx_rs_reference_override_is_reported(monkeypatch):
    """VNX_RS_REFERENCE=1 makes InnerRS use the V3 decoder (vnxdna.ecc.rs_batch) whatever VNXDNA_RS_BACKEND says."""
    from vnxdna.v4 import codecs
    monkeypatch.setattr(codecs, "_REFERENCE_RS", True)
    k = native.native_status()["kernels"]["rs"]
    assert k["backend"] == "reference" and k["simd_level"] is None and k["reference"] == "vnxdna.ecc.rs_batch"
    assert k["requested"] == "VNX_RS_REFERENCE=1"


def test_invalid_backend_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("VNXDNA_RS_BACKEND", "bogus")
    monkeypatch.setenv("VNXDNA_READS_BACKEND", "bogus")
    st = native.native_status()
    for kernel in ("rs", "reads"):
        assert st["kernels"][kernel]["backend"] is None and "bogus" in st["kernels"][kernel]["error"]
    assert native.backend_summary()["rs"]["error"]
    assert st["all_native"] is False


def test_library_origin():
    assert native._origin("rs", None) is None
    assert native._origin("rs", "/x/vnxdna/v6/_vnx_rs.cpython-312-x86_64-linux-gnu.so") == "packaged"
    assert native._origin("reads", "/x/vnxdna/v6/native/libvnx_reads.so") == "in-place"


def test_library_origin_env(monkeypatch, tmp_path):
    lib = tmp_path / "custom.so"
    monkeypatch.setenv("VNXDNA_RS_LIB", str(lib))
    assert native._origin("rs", str(lib)) == "env"


def test_stale_in_place_library_is_flagged(tmp_path, monkeypatch):
    src, lib = tmp_path / "rs.c", tmp_path / "libvnx_rs.so"
    lib.write_bytes(b"")
    src.write_text("")
    os.utime(lib, (1_000_000, 1_000_000))
    monkeypatch.setattr(nrs, "_SOURCE", src)
    assert native._stale(nrs, "in-place", str(lib)) is True
    assert native._stale(nrs, "packaged", str(lib)) is False     # installed files: mtimes are not meaningful


def test_level_restriction_is_visible():
    if len(nrs.supported_levels()) < 2:
        pytest.skip("only the scalar level on this CPU")
    try:
        nrs._restrict_levels_for_tests(["scalar"])
        k = native.native_status()["kernels"]["rs"]
        assert k["levels_restricted"] is True and k["simd_level"] == "scalar"
    finally:
        nrs._restrict_levels_for_tests(None)
    assert native.native_status()["kernels"]["rs"]["levels_restricted"] is False


def test_restrict_hook_is_test_only():
    """vnx_rs_restrict_levels changes process-wide dispatch: only the test hook in native_rs may reach it."""
    hits = []
    for p in sorted((ROOT / "src" / "vnxdna").rglob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"restrict_levels", line):
                hits.append((p.relative_to(ROOT).as_posix(), line.strip()))
    # the RS binding's file (src/vnxdna/v6/native_rs.py before V6 Phase 2; the old path is now an alias of it)
    assert {f for f, _ in hits} == {Path(nrs.__file__).resolve().relative_to(ROOT).as_posix()}, hits
    calls = [line for _, line in hits if "restrict_levels(" in line and not line.startswith("def ")]
    assert calls == ["lib.vnx_rs_restrict_levels(-1 if names is None else sum(1 << LEVELS[k] for k in names))"], calls


# ---------------------------------------------------------------------------------------------------- packaging contract
def test_setup_builds_all_kernels_as_optional_extensions(monkeypatch):
    import sys
    import types

    class Extension:                     # stand-in: setuptools is a build-time dependency only
        def __init__(self, name, sources, **kw):
            self.name, self.sources = name, sources
            self.extra_compile_args, self.optional = kw.pop("extra_compile_args", []), kw.pop("optional", False)
            assert not kw, kw
    captured = {}
    fake = types.ModuleType("setuptools")
    fake.Extension, fake.setup = Extension, lambda **kw: captured.update(kw)
    monkeypatch.setitem(sys.modules, "setuptools", fake)
    monkeypatch.chdir(ROOT)
    runpy.run_path(str(ROOT / "setup.py"), run_name="__main__")
    exts = {e.name: e for e in captured["ext_modules"]}
    assert set(exts) == {"vnxdna.v5._vnx_align", "vnxdna.v6._vnx_reads", "vnxdna.v6._vnx_rs",
                         "vnxdna._vnx_cluster"} == set(nb.KERNELS)
    for name, ext in exts.items():
        assert ext.optional is True and ext.sources == [nb.KERNELS[name]] and (ROOT / ext.sources[0]).is_file()
        flags = ext.extra_compile_args
        assert flags == nb.COMPILE_FLAGS and "-Werror" not in flags
        assert not any(f.startswith(("-march", "-mavx", "-mtune", "-mcpu")) for f in flags)
    # the explicit builds use the same flags (+ -fPIC -shared, which setuptools adds itself)
    for m, _, _ in MODULES.values():
        assert set(m.CFLAGS) == set(nb.COMPILE_FLAGS) | {"-fPIC", "-shared"}
        assert set(m.STRICT_CFLAGS) == set(m.CFLAGS) | {"-Werror"}


@pytest.mark.parametrize("kernel", sorted(MODULES))
def test_loaders_find_the_packaged_extension_name(kernel, monkeypatch, tmp_path):
    """setup.py's module name ``vnxdna.vX._vnx_<k>`` lands at ``vnxdna/vX/_vnx_<k><EXT_SUFFIX>``: the loader must try it,
    after an explicit *_LIB path and before the in-place library."""
    m, env, _ = MODULES[kernel]
    ext_name = {"align": "vnxdna.v5._vnx_align", "reads": "vnxdna.v6._vnx_reads", "rs": "vnxdna.v6._vnx_rs",
                "cluster": "vnxdna._vnx_cluster"}[kernel]
    assert ext_name in nb.KERNELS
    # the loader searches the directory setup.py builds the extension into (since V6 Phase 2 the loader module itself
    # lives in vnxdna.native, while the C source, library and extension stay in vnxdna/v5 and vnxdna/v6)
    assert Path(*ext_name.split(".")[:-1]) == m._HERE.relative_to(Path(vnxdna.__file__).resolve().parents[1])
    ext = tmp_path / (ext_name.rsplit(".", 1)[1] + (sysconfig.get_config_var("EXT_SUFFIX") or ".so"))
    ext.write_bytes(b"")
    monkeypatch.setattr(m, "_HERE", tmp_path)
    monkeypatch.setattr(m, "_INPLACE", tmp_path / "native" / "lib.so")
    monkeypatch.setenv(env, str(tmp_path / "explicit.so"))
    c = m._candidates()
    assert c[0] == tmp_path / "explicit.so" and ext in c and c.index(ext) < c.index(m._INPLACE)


# ---------------------------------------------------------------------------------------------------- reports / events / CLI
@pytest.fixture(scope="module")
def reads(tmp_path_factory):
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import channel as ch
    from vnxdna.v4 import datagen
    from vnxdna.v4 import encoder as en
    d = tmp_path_factory.mktemp("status_decode")
    datagen.generate(d / "in.bin", 20_000, "random", 6201)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    ch.simulate_file(d / "s.fasta", d / "r.fastq", ch.ChannelConfig(substitution_rate=0.003, coverage=3, seed=6202))
    return d


def test_decode_report_and_start_event_record_backends(reads, monkeypatch, tmp_path):
    from vnxdna.v4 import decoder as de
    got = []
    res = de.decode_reads(reads / "r.fastq", tmp_path / "o.vnx", de.DecodeOptions(), observer=got.append)
    assert res.status == "SUCCESS"
    nb_ = res.report["native_backends"]
    assert nb_ == native.backend_summary() and {v["backend"] for v in nb_.values()} == {"native"}
    start = got[0]
    assert start["event"] == "decode_start" and start["native_backends"] == nb_
    monkeypatch.setenv("VNXDNA_RS_BACKEND", "reference")
    monkeypatch.setenv("VNXDNA_READS_BACKEND", "reference")
    res2 = de.decode_reads(reads / "r.fastq", tmp_path / "o2.vnx", de.DecodeOptions())
    assert res2.report["native_backends"]["rs"] == {"backend": "reference", "simd_level": None,
                                                    "abi_version": nrs.ABI_VERSION,
                                                    "library_origin": nb_["rs"]["library_origin"]}
    assert res2.report["native_backends"]["reads"]["backend"] == "reference"
    # bit-identical result either way; only the provenance field differs
    assert (tmp_path / "o.vnx").read_bytes() == (tmp_path / "o2.vnx").read_bytes()


def test_cli_native_and_version_list_every_kernel():
    from vnxdna.v4.cli import app
    r = CliRunner().invoke(app, ["native"])
    assert r.exit_code == 0, r.output
    st = json.loads(r.output)
    assert st["active_backend"] == "native" and st["all_native"] is True     # V5 fields kept, kernels added
    assert {k: v["backend"] for k, v in st["kernels"].items()} == dict.fromkeys(MODULES, "native")
    v = json.loads(CliRunner().invoke(app, ["version"]).output)
    assert v["alignment_backend"] == "native" and set(v["native_backends"]) == set(MODULES)
