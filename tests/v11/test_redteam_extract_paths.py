"""V11-RT-02 (LOW, V6-SEC-10): extraction must keep every file inside the output directory. The containment check was a
string prefix test (``/out2`` passes for ``/out``); a directory swapped for a symlink to a sibling with the same
prefix after the component check let a file land outside. Symlinks already in the output tree are refused.
SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import os

import pytest

from vnxdna.archive import operations as ar
from vnxdna.core.errors import VNXOutputError


@pytest.fixture()
def archive(tmp_path):
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "f.txt").write_text("inner")
    (src / "t.txt").write_text("top")
    ar.build_archive([src], tmp_path / "a.vnx", ar.ArchiveOptions())
    return tmp_path / "a.vnx"


def test_plain_extract_still_works(archive, tmp_path):
    res = ar.extract(archive, tmp_path / "out", allow_unencrypted=True)
    assert res["status"] == "EXTRACTED" and (tmp_path / "out" / "src" / "d" / "f.txt").read_text() == "inner"


def test_symlink_component_to_a_sibling_with_the_same_prefix_is_refused(archive, tmp_path, monkeypatch):
    out, sibling = tmp_path / "out", tmp_path / "out2"
    sibling.mkdir()
    real = ar._safe_target

    def racing(root, rel):
        target = real(root, rel)              # the component checks pass ...
        if rel == "src/d/f.txt":              # ... then the parent becomes a symlink to the sibling (TOCTOU)
            (root / "src").mkdir(exist_ok=True)
            os.symlink(sibling, root / "src" / "d")
        return target
    monkeypatch.setattr(ar, "_safe_target", racing)
    with pytest.raises(VNXOutputError):
        ar.extract(archive, out, allow_unencrypted=True)
    assert list(sibling.iterdir()) == []


def test_existing_symlink_component_is_refused(archive, tmp_path):
    out, elsewhere = tmp_path / "out", tmp_path / "elsewhere"
    (out / "src").mkdir(parents=True)
    elsewhere.mkdir()
    os.symlink(elsewhere, out / "src" / "d")
    with pytest.raises(VNXOutputError, match="symlink"):
        ar.extract(archive, out, allow_unencrypted=True)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("overwrite", [False, True])
def test_symlink_target_file_is_never_followed(archive, tmp_path, overwrite):
    out, victim = tmp_path / "out", tmp_path / "victim.txt"
    victim.write_text("keep")
    (out / "src").mkdir(parents=True)
    os.symlink(victim, out / "src" / "t.txt")
    os.symlink(tmp_path / "missing", out / "src" / "dangling")       # dangling links are symlinks too
    with pytest.raises(VNXOutputError):
        ar.extract(archive, out, overwrite=overwrite, allow_unencrypted=True)
    assert victim.read_text() == "keep"


def test_output_directory_given_as_a_symlink_is_resolved(archive, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "link")
    ar.extract(archive, tmp_path / "link", allow_unencrypted=True)
    assert (real / "src" / "t.txt").read_text() == "top"
