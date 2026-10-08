from __future__ import annotations

import hashlib

import pytest

from vnxdna.secure.control import ControlPlane
from vnxdna.secure.events import ManualClock
from vnxdna.secure.simulator import Lab

MASTER = hashlib.sha256(b"vnx-secure test master").digest()


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


@pytest.fixture
def cp(tmp_path):
    comp = tmp_path / "comp"
    comp.mkdir()
    (comp / "a.py").write_text("A = 1\n")
    c = ControlPlane(None, clock=ManualClock(), master_key=MASTER, integrity_roots=[comp])
    for name, roles in (("alice", ("reader",)), ("bob", ("writer",)), ("ops", ("admin",)), ("eve", ("guest",))):
        c.add_principal(name, f"{name}-pw", roles, scrypt_n=1 << 10)
    c.check_integrity()
    c.comp = comp
    return c


class Nonces:
    def __init__(self):
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"nonce-{self.n:026d}"


@pytest.fixture
def nonce():
    return Nonces()
