"""Hybrid architecture: the CPU budget (``vnxdna.core.concurrency``) respects the affinity mask and a cgroup v2 quota,
and the performance profiles never ask for more workers than it allows."""
from __future__ import annotations

import os

import pytest

from vnxdna.core import concurrency as cc


@pytest.mark.parametrize("text, want", [("max 100000", None), ("200000 100000", 2), ("150000 100000", 2),
                                        ("50000 100000", 1), ("0 100000", None), ("garbage", None), ("", None),
                                        ("12 x", None)])
def test_cgroup_quota_parsing(tmp_path, text, want):
    p = tmp_path / "cpu.max"
    p.write_text(text)
    assert cc._cgroup_quota(p) == want


def test_missing_cgroup_file_means_no_quota(tmp_path):
    assert cc._cgroup_quota(tmp_path / "absent") is None


def test_available_cpus_is_affinity_capped_by_quota(monkeypatch):
    monkeypatch.setattr(os, "sched_getaffinity", lambda pid: {0, 1, 2, 3, 4, 5})
    monkeypatch.setattr(cc, "_cgroup_quota", lambda path=None: None)
    assert cc.available_cpus() == 6
    monkeypatch.setattr(cc, "_cgroup_quota", lambda path=None: 2)
    assert cc.available_cpus() == 2
    monkeypatch.setattr(cc, "_cgroup_quota", lambda path=None: 64)
    assert cc.available_cpus() == 6


def test_available_cpus_without_affinity_api(monkeypatch):
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    monkeypatch.setattr(cc, "_cgroup_quota", lambda path=None: None)
    assert cc.available_cpus() == 1


def test_profiles_stay_within_the_budget():
    from vnxdna.pipeline.performance import PERFORMANCE_PROFILES
    n = cc.available_cpus()
    assert all(1 <= p["workers"] <= n for p in PERFORMANCE_PROFILES.values())
    assert PERFORMANCE_PROFILES["safe"]["workers"] == 1
