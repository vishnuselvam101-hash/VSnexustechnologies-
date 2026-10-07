"""Process sandbox: resource limits, timeouts and crashes end the child, not the caller; errors keep their type."""
from __future__ import annotations

import os
import time

import pytest

from vnxdna.core.errors import VNXFormatError
from vnxdna.secure.control import SecurityFailure
from vnxdna.secure.gate import SecureArchive
from vnxdna.secure.sandbox import SandboxError, run_limited


def _alloc():
    return len(bytearray(2 << 30))


def _spin():
    while True:
        pass


def _sleep():
    time.sleep(30)


def _crash():
    os._exit(9)


def _fmt():
    raise VNXFormatError("bad header")


def test_result_is_returned():
    assert run_limited(sum, [1, 2, 3]) == 6


@pytest.mark.parametrize("fn,reason,kw", [(_alloc, "memory_limit", {"mem_bytes": 512 << 20}), (_spin, "crashed", {"cpu_seconds": 1}),
                                          (_sleep, "timeout", {"timeout": 1.0}), (_crash, "crashed", {})])
def test_limits(fn, reason, kw):
    with pytest.raises(SandboxError) as e:
        run_limited(fn, **kw)
    assert e.value.reason == reason


def test_vnx_errors_keep_their_type():
    with pytest.raises(VNXFormatError):
        run_limited(_fmt)


def test_gate_sandboxed_verify(lab):
    t = lab.login("alice", "s-a")
    arc = SecureArchive(lab.cp, lab.archive, key=lab.key, sandbox=True)
    assert arc.verify(lab.req(t, "s-a"))
    bad = lab.root / "bad.vnx"
    bad.write_bytes(b"\x00" * 64)
    with pytest.raises(SecurityFailure) as e:
        SecureArchive(lab.cp, bad, key=lab.key, sandbox=True).verify(lab.req(t, "s-a"))
    assert e.value.code == "malformed"
