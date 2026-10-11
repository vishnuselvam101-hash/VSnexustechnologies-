"""V11-RT-08 (LOW): a native library override (``VNXDNA_*_LIB``) must be an absolute path. A relative value was checked
with ``is_file()`` against the current directory but handed to ``dlopen``, which resolves a name without a slash
through the library search path (and ``./x.so`` through the current directory): the file checked and the file loaded
could differ. Relative overrides are refused (named in ``load_error``); the package libraries still load, or the
reference runs. SYNTHETIC SOFTWARE TEST."""
from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest

MODULES = {
    "align": ("vnxdna.native.align", "VNXDNA_NATIVE_LIB"),
    "reads": ("vnxdna.native.reads", "VNXDNA_READS_LIB"),
    "cluster": ("vnxdna.native.cluster", "VNXDNA_CLUSTER_LIB"),
    "rs": ("vnxdna.native.rs", "VNXDNA_RS_LIB"),
    "strand": ("vnxdna.native.strand", "VNXDNA_STRAND_LIB"),
}


def _reset(mod):
    if hasattr(mod, "KERNEL"):
        mod.KERNEL.reset()
    else:
        mod._reset_for_tests()


def _loaded(mod):
    if hasattr(mod, "KERNEL"):
        mod.KERNEL.load()
        return mod.KERNEL._path, mod.KERNEL._load_error
    mod._load()
    return mod._lib_path, mod._load_error


@pytest.mark.parametrize("name", sorted(MODULES))
@pytest.mark.parametrize("form", ["bare", "dot"])
def test_relative_override_is_refused(name, form, tmp_path, monkeypatch):
    mod = importlib.import_module(MODULES[name][0])
    _reset(mod)
    real, _ = _loaded(mod)
    if real is None:
        pytest.skip(f"native {name} library not built")
    planted = tmp_path / Path(real).name
    shutil.copy(real, planted)                               # a library in the current directory
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(MODULES[name][1], planted.name if form == "bare" else f"./{planted.name}")
    _reset(mod)
    import ctypes
    opened = []
    real_cdll = ctypes.CDLL

    def spy(name, *a, **k):
        opened.append(str(name))
        return real_cdll(name, *a, **k)
    monkeypatch.setattr(ctypes, "CDLL", spy)
    try:
        path, err = _loaded(mod)
        assert all(Path(o).is_absolute() for o in opened), opened       # dlopen never searches for a bare name
        assert path is None or path == real
    finally:
        monkeypatch.delenv(MODULES[name][1])
        _reset(mod)


@pytest.mark.parametrize("name", sorted(MODULES))
def test_absolute_override_still_loads(name, tmp_path, monkeypatch):
    mod = importlib.import_module(MODULES[name][0])
    _reset(mod)
    real, _ = _loaded(mod)
    if real is None:
        pytest.skip(f"native {name} library not built")
    copy = tmp_path / Path(real).name
    shutil.copy(real, copy)
    monkeypatch.setenv(MODULES[name][1], str(copy))
    _reset(mod)
    try:
        path, _ = _loaded(mod)
        assert path == str(copy)
    finally:
        monkeypatch.delenv(MODULES[name][1])
        _reset(mod)
