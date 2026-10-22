"""A stale packaged extension must not shadow a newer in-place build of the same kernel.

Two builds of one kernel can sit in the tree: the extension ``pip install`` produced
(``vnxdna[_vnx_<name><EXT_SUFFIX>]``) and the in-place development build
(``python -m vnxdna.native.<name> build`` writes ``libvnx_<name>.so``). The extension is compiled from the
source as it was at install time; the in-place build is compiled from the source as it is now. When the source
has moved on, the extension is stale, and a loader that always prefers the extension runs the old kernel and
ignores the change without a word.

This is the V15 CC-3 outer-list fix being invisible: the extension built on 2026-10-19 shadowed the in-place
build of 2026-10-21, so the suite tested the old C++ kernel and the new one was never exercised.

The rule the loaders implement: the in-place build comes first when it is at least as new as the packaged
extension; the extension keeps priority when it is the newer of the two (the usual case right after an
install, where no in-place build exists); an explicit ``<PREFIX>_LIB`` override is never reordered.
SYNTHETIC SOFTWARE TEST.
"""
from __future__ import annotations

import importlib
import os
import sys
import sysconfig
import time
import types

import pytest

#: every loader with its own candidate list: the module, the environment variable that overrides it, and the
#: name of the extension the packaging builds (the file name is the module's own short name).
MODULES = {
    "align": ("vnxdna.native.align", "VNXDNA_NATIVE_LIB"),
    "reads": ("vnxdna.native.reads", "VNXDNA_READS_LIB"),
    "rs": ("vnxdna.native.rs", "VNXDNA_RS_LIB"),
    "cluster": ("vnxdna.native.cluster", "VNXDNA_CLUSTER_LIB"),
    "strand": ("vnxdna.native.strand", "VNXDNA_STRAND_LIB"),
    "orient": ("vnxdna.native.orient", "VNXDNA_ORIENT_LIB"),
    "outerlist": ("vnxdna.native.outerlist", "VNXDNA_OUTERLIST_LIB"),
    "integrity": ("vnxdna.native.integrity", "VNXDNA_INTEGRITY_LIB"),
    "engine": ("vnxdna.native.engine", "VNXDNA_ENGINE_LIB"),
}


def _freshest_first(candidates, inplace, pinned=None):
    """The shared helper under test (imported inside the test, so a missing helper is a failure, not a collection error)."""
    from vnxdna.native._loader import freshest_first
    return freshest_first(candidates, inplace, pinned)


def _ext_name(kernel: str) -> str:
    """The file name ``setup.py`` builds the packaged extension of this kernel to.

    The loaders look for ``_vnx_<kernel><EXT_SUFFIX>`` and for any ``_vnx_<kernel>*.so`` beside it, so the planted
    file has to carry the interpreter's extension suffix to be found at all.
    """
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
    return f"_vnx_{kernel}{suffix}"


def test_freshest_first_puts_the_newer_in_place_build_first(tmp_path):
    """The in-place build wins when it is newer than the extension."""
    ext = tmp_path / "ext.so"
    inplace = tmp_path / "libvnx.so"
    ext.write_bytes(b"")
    inplace.write_bytes(b"")
    os.utime(ext, (1000, 1000))
    os.utime(inplace, (2000, 2000))
    assert _freshest_first([ext, inplace], inplace) == [inplace, ext]


def test_freshest_first_keeps_the_newer_extension_first(tmp_path):
    """The extension keeps priority when it is the newer of the two (the case right after an install)."""
    ext = tmp_path / "ext.so"
    inplace = tmp_path / "libvnx.so"
    ext.write_bytes(b"")
    inplace.write_bytes(b"")
    os.utime(ext, (2000, 2000))
    os.utime(inplace, (1000, 1000))
    assert _freshest_first([ext, inplace], inplace) == [ext, inplace]


def test_freshest_first_keeps_an_explicit_override_first(tmp_path):
    """An explicit path is an override, not a candidate to be compared by date."""
    ext = tmp_path / "ext.so"
    inplace = tmp_path / "libvnx.so"
    override = tmp_path / "explicit.so"
    for p in (ext, inplace, override):
        p.write_bytes(b"")
    os.utime(ext, (1000, 1000))
    os.utime(inplace, (2000, 2000))
    assert _freshest_first([override, ext, inplace], inplace, override)[0] == override


def test_freshest_first_never_moves_the_in_place_build_in_front_of_the_override(tmp_path):
    """The in-place build moves in front of the extensions, but never in front of the explicit override."""
    ext = tmp_path / "ext.so"
    inplace = tmp_path / "libvnx.so"
    override = tmp_path / "explicit.so"
    for p in (ext, inplace, override):
        p.write_bytes(b"")
    os.utime(ext, (1000, 1000))
    os.utime(inplace, (2000, 2000))        # the in-place build is the newest of the three
    order = _freshest_first([override, ext, inplace], inplace, override)
    assert order[0] == override
    assert order.index(inplace) < order.index(ext)


def test_freshest_first_leaves_a_missing_in_place_build_alone(tmp_path):
    """With no in-place build there is nothing to compare against, so the order is unchanged."""
    ext = tmp_path / "ext.so"
    ext.write_bytes(b"")
    os.utime(ext, (1000, 1000))
    assert _freshest_first([ext, tmp_path / "absent.so"], tmp_path / "absent.so") == [ext, tmp_path / "absent.so"]


@pytest.mark.parametrize("kernel", sorted(MODULES))
def test_the_loader_does_not_load_a_stale_extension(kernel, tmp_path, monkeypatch):
    """The loader's own candidate list prefers the newer in-place build over the older packaged extension.

    The real library files are not needed: only their dates decide the order, so the test plants two empty
    files with a known age and checks which one the loader would reach first.
    """
    module, env = MODULES[kernel]
    mod = importlib.import_module(module)
    monkeypatch.delenv(env, raising=False)

    ext = tmp_path / _ext_name(kernel)
    inplace = tmp_path / "libvnx.so"
    ext.write_bytes(b"")
    inplace.write_bytes(b"")
    os.utime(ext, (1000, 1000))            # the packaged extension, older
    os.utime(inplace, (2000, 2000))         # the in-place build, newer

    here = tmp_path
    monkeypatch.setattr(mod, "_HERE", here, raising=False)
    monkeypatch.setattr(mod, "_INPLACE", inplace, raising=False)

    candidates = mod._candidates()
    assert candidates.index(inplace) < candidates.index(ext), candidates


@pytest.mark.parametrize("kernel", sorted(MODULES))
def test_the_loader_still_prefers_the_extension_when_it_is_newer(kernel, tmp_path, monkeypatch):
    """The packaged extension keeps priority when it is the newer of the two."""
    module, env = MODULES[kernel]
    mod = importlib.import_module(module)
    monkeypatch.delenv(env, raising=False)

    ext = tmp_path / _ext_name(kernel)
    inplace = tmp_path / "libvnx.so"
    ext.write_bytes(b"")
    inplace.write_bytes(b"")
    os.utime(ext, (2000, 2000))            # the packaged extension, newer
    os.utime(inplace, (1000, 1000))         # the in-place build, older

    monkeypatch.setattr(mod, "_HERE", tmp_path, raising=False)
    monkeypatch.setattr(mod, "_INPLACE", inplace, raising=False)

    candidates = mod._candidates()
    assert candidates.index(ext) < candidates.index(inplace), candidates


def test_the_shared_kernel_contract_orders_its_candidates(tmp_path, monkeypatch):
    """The kernels built on :class:`vnxdna.native._loader.Kernel` follow the same rule."""
    from vnxdna.native import _loader

    kernel = _loader.Kernel("demo", "VNXDNA_DEMO", 1, "vnx_demo_abi_version", lambda lib: None, "demo reference",
                             "vnxdna.native.demo")
    ext = tmp_path / "_vnx_demo.so"
    inplace = tmp_path / "libvnx_demo.so"
    ext.write_bytes(b"")
    inplace.write_bytes(b"")
    os.utime(ext, (1000, 1000))
    os.utime(inplace, (2000, 2000))

    module = types.ModuleType("vnxdna.native.demo")    # a stand-in module with the contract's globals
    module._HERE = tmp_path
    module._INPLACE = inplace
    monkeypatch.setitem(sys.modules, "vnxdna.native.demo", module)
    candidates = kernel.candidates()
    assert candidates.index(inplace) < candidates.index(ext), candidates


def test_two_libraries_of_the_same_age_put_the_in_place_build_first(tmp_path):
    """An equal age counts as "the in-place build is not older", so it comes first.

    The comparison is ``>=`` and not ``>``: the in-place build is compiled from the source in the tree, so when
    the two dates are the same the in-place build is the one that matches the tree, and it is the one to run.
    """
    ext = tmp_path / "ext.so"
    inplace = tmp_path / "libvnx.so"
    ext.write_bytes(b"")
    inplace.write_bytes(b"")
    stamp = time.time()
    os.utime(ext, (stamp, stamp))
    os.utime(inplace, (stamp, stamp))
    assert _freshest_first([ext, inplace], inplace) == [inplace, ext]


@pytest.mark.parametrize("kernel", sorted(MODULES))
def test_the_loader_keeps_an_explicit_override_first(kernel, tmp_path, monkeypatch):
    """An explicit ``<PREFIX>_LIB`` override is tried first whatever the dates of the other candidates are."""
    module, env = MODULES[kernel]
    mod = importlib.import_module(module)

    ext = tmp_path / _ext_name(kernel)
    inplace = tmp_path / "libvnx.so"
    override = tmp_path / "explicit.so"
    for p in (ext, inplace, override):
        p.write_bytes(b"")
    os.utime(ext, (1000, 1000))
    os.utime(inplace, (2000, 2000))        # the in-place build is the newest of the three

    monkeypatch.setenv(env, str(override))
    monkeypatch.setattr(mod, "_HERE", tmp_path, raising=False)
    monkeypatch.setattr(mod, "_INPLACE", inplace, raising=False)

    candidates = mod._candidates()
    assert candidates[0] == override, candidates
