"""F7: the policy's known-good MAC must be compared in constant time.

``secure/control.py`` compared the MAC of the current policy with the known-good MAC with a plain ``==`` (and the
recovery path with a plain ``!=``). A plain comparison stops at the first byte that differs, which tells an observer
how much of the check value matched. The file already used ``hmac.compare_digest`` for the state and the principals;
the policy's MAC now goes through the same comparison.
"""
from __future__ import annotations

from pathlib import Path

from vnxdna.secure import control
from vnxdna.secure.control import ControlPlane
from vnxdna.secure.events import ManualClock


def _source() -> str:
    return Path(control.__file__).read_text(encoding="utf-8")


def test_the_policy_mac_is_not_compared_with_a_plain_equality():
    """Every comparison of the policy's MAC must go through the constant-time comparison."""
    source = _source()
    assert "mac_eq(" in source, "the policy MAC must be compared with the constant-time comparison of the module"
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if "_mac_policy(" in stripped and ("==" in stripped or "!=" in stripped):
            raise AssertionError(f"the policy MAC is compared with a plain equality on line {number}: {stripped}")


def test_a_known_good_mac_that_does_not_match_is_a_mismatch(tmp_path):
    clock = ManualClock()
    cp = ControlPlane.init(tmp_path / "h", integrity_roots=[], admin=("ops", "ops-pw"), clock=clock)
    assert cp.check_integrity()["policy_known_good"] is True
    # the known-good MAC does not match the policy any more
    cp.good_policy_mac = "0" * 64
    report = cp.check_integrity()
    assert report["policy_known_good"] is False
    assert report["status"] == "MODIFIED"


def test_the_recovery_refuses_a_known_good_copy_that_does_not_match(tmp_path):
    clock = ManualClock()
    cp = ControlPlane.init(tmp_path / "h", integrity_roots=[], admin=("ops", "ops-pw"), clock=clock)
    cp.good_policy_mac = "0" * 64
    result = cp.recover()
    assert result["result"] == "FAILED"
    assert any(step.get("step") == "policy" and step.get("result") == "FAILED" for step in result["steps"])


def test_a_known_good_mac_that_is_not_ascii_is_a_mismatch_not_an_exception(tmp_path):
    """The MAC is read back from the state file: a value that is not ASCII must not turn the comparison into an
    exception."""
    clock = ManualClock()
    cp = ControlPlane.init(tmp_path / "h", integrity_roots=[], admin=("ops", "ops-pw"), clock=clock)
    cp.good_policy_mac = "not-a-mac-é"
    report = cp.check_integrity()
    assert report["policy_known_good"] is False
    assert report["status"] == "MODIFIED"
