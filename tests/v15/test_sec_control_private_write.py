"""F1: the VNX-Secure home is never written through a symlink.

``secure/control.py:_write_private`` used a fixed temporary name (``<name>.tmp``) opened with ``O_CREAT|O_TRUNC``:
anyone who may write in the home directory could plant a symlink there and redirect the write - the master key
into a file of their choosing, and the control plane left holding a symlink instead of its own key file. The same
fixed name also kept the permissions of whatever was planted there, so the key could end up world-readable.

The write must go through a private temporary file (a unique name, created with ``O_EXCL`` and mode 0600) and be
published with a rename, and a path that is a symlink must be refused rather than written through.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from vnxdna.secure.control import ControlPlane
from vnxdna.secure.policy import Policy


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def _regular(path: Path) -> bool:
    return stat.S_ISREG(os.lstat(path).st_mode)


@pytest.mark.parametrize("name", ["secure.key", "policy.json", "policy.good.json", "baseline.json"])
def test_init_never_writes_through_a_symlink_planted_at_a_temporary_name(tmp_path, name):
    home = _home(tmp_path)
    victim = tmp_path / f"victim-{name}"
    victim.write_bytes(b"precious")
    (home / f"{name}.tmp").symlink_to(victim)
    ControlPlane.init(home, integrity_roots=[])
    assert victim.read_bytes() == b"precious", "the write went through the planted symlink"
    assert (home / f"{name}.tmp").is_symlink(), "the planted name was consumed"


def test_init_leaves_a_regular_private_key_file_and_never_a_symlink(tmp_path):
    home = _home(tmp_path)
    ControlPlane.init(home, integrity_roots=[])
    key = home / "secure.key"
    assert not key.is_symlink()
    assert _regular(key)
    assert key.stat().st_size == 32
    assert key.stat().st_mode & 0o777 == 0o600


def test_init_never_reuses_a_planted_temporary_name_with_wider_permissions(tmp_path):
    home = _home(tmp_path)
    planted = home / "secure.key.tmp"
    planted.write_bytes(b"old")
    planted.chmod(0o644)
    ControlPlane.init(home, integrity_roots=[])
    key = home / "secure.key"
    assert key.stat().st_mode & 0o777 == 0o600, "the key kept the permissions of the planted file"


def test_init_refuses_a_symlinked_key_path(tmp_path):
    home = _home(tmp_path)
    victim = tmp_path / "victim.bin"
    (home / "secure.key").symlink_to(victim)
    with pytest.raises(FileExistsError):
        ControlPlane.init(home, integrity_roots=[])
    assert not victim.exists(), "the key was written through the symlink"


def test_save_never_writes_through_a_symlink_planted_at_a_temporary_name(tmp_path):
    home = _home(tmp_path)
    cp = ControlPlane.init(home, integrity_roots=[])
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"precious")
    (home / "state.json.tmp").symlink_to(victim)
    cp.save()
    assert victim.read_bytes() == b"precious", "the state was written through the planted symlink"
    assert _regular(home / "state.json")


def test_policy_restore_never_writes_through_a_symlink_planted_at_a_temporary_name(tmp_path):
    home = _home(tmp_path)
    cp = ControlPlane.init(home, integrity_roots=[])
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"precious")
    (home / "policy.json.tmp").symlink_to(victim)
    cp.policy = Policy([])          # the current policy no longer matches the known-good MAC
    cp.recover()
    assert victim.read_bytes() == b"precious", "the policy was written through the planted symlink"
