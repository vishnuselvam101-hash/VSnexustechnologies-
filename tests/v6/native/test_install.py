"""Install test: a normal ``pip install`` builds and uses all three native kernels; without a compiler it still installs
and every kernel reports the reference backend. ``pip wheel`` packages the three extensions.

Opt-in (fresh virtual environments, dependency downloads or the pip cache, a few minutes): ``VNXDNA_INSTALL_TEST=1``.
CI runs it in its own job. Each install builds from a clean copy of the packaging inputs, so local in-place libraries
and build directories cannot leak into the result.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
KERNELS = {"align", "reads", "rs"}

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(os.environ.get("VNXDNA_INSTALL_TEST") != "1",
                                 reason="install test is opt-in: set VNXDNA_INSTALL_TEST=1 (creates virtual environments)")]


def _source_copy(dest: Path) -> Path:
    dest.mkdir()
    for name in ("pyproject.toml", "setup.py", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, dest / name)
    shutil.copytree(ROOT / "src", dest / "src",
                    ignore=shutil.ignore_patterns("*.so", "__pycache__", "*.egg-info", "*.pyc"))
    return dest


def _venv(path: Path) -> Path:
    subprocess.run([sys.executable, "-m", "venv", str(path)], check=True)
    return path / "bin" / "python"


def _env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VNXDNA_", "PYTHON")) and k != "VNX_RS_REFERENCE"}
    env.update(extra)
    return env


def _status(py: Path, cwd: Path) -> dict:
    code = "import json, vnxdna; print(json.dumps({'file': vnxdna.__file__, **vnxdna.native_status()}))"
    out = subprocess.run([str(py), "-c", code], cwd=cwd, env=_env(), capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_pip_install_uses_all_native_kernels(tmp_path):
    src = _source_copy(tmp_path / "src-copy")
    py = _venv(tmp_path / "venv")
    subprocess.run([str(py), "-m", "pip", "install", "-q", str(src)], env=_env(), check=True)
    st = _status(py, tmp_path)
    site = (tmp_path / "venv").resolve()
    assert Path(st["file"]).resolve().is_relative_to(site), st["file"]        # the installed package, not the worktree
    assert st["all_native"] is True, st
    for name in KERNELS:
        k = st["kernels"][name]
        assert k["backend"] == "native" and k["library_origin"] == "packaged", k
        assert Path(k["library"]).resolve().is_relative_to(site) and Path(k["library"]).name.startswith("_vnx_"), k
    assert st["kernels"]["rs"]["simd_level"] in ("scalar", "avx2", "avx512")
    rc = subprocess.run([str(py), "-m", "vnxdna.native", "--require-native"], cwd=tmp_path, env=_env(), capture_output=True)
    assert rc.returncode == 0


def test_pip_install_without_compiler_falls_back_to_reference(tmp_path):
    src = _source_copy(tmp_path / "src-copy")
    py = _venv(tmp_path / "venv")
    res = subprocess.run([str(py), "-m", "pip", "install", str(src)], env=_env(CC="/bin/false"), capture_output=True,
                         text=True)
    assert res.returncode == 0, res.stdout[-3000:] + res.stderr[-3000:]
    st = _status(py, tmp_path)
    assert Path(st["file"]).resolve().is_relative_to((tmp_path / "venv").resolve())
    assert st["all_native"] is False
    for name in KERNELS:
        k = st["kernels"][name]
        assert k["backend"] == "reference" and k["library"] is None and k["load_error"], k
    rc = subprocess.run([str(py), "-m", "vnxdna.native", "--require-native"], cwd=tmp_path, env=_env(), capture_output=True)
    assert rc.returncode == 1


def test_pip_wheel_contains_the_three_extensions(tmp_path):
    src = _source_copy(tmp_path / "src-copy")
    py = _venv(tmp_path / "venv")
    subprocess.run([str(py), "-m", "pip", "wheel", "-q", "--no-deps", "-w", str(tmp_path / "wheels"), str(src)], env=_env(),
                   check=True)
    (whl,) = (tmp_path / "wheels").glob("vnx_dna-*.whl")
    names = zipfile.ZipFile(whl).namelist()
    for prefix in ("vnxdna/v5/_vnx_align", "vnxdna/v6/_vnx_reads", "vnxdna/v6/_vnx_rs"):
        assert sum(n.startswith(prefix) and n.endswith(".so") for n in names) == 1, (prefix, names)
    assert not any(n.endswith(".so") and "/native/" in n for n in names)     # no in-place development libraries
