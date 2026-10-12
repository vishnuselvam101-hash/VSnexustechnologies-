"""V12 parallel pass 2: with workers > 1 a full decode hands each spill bucket's duplicate resolution and consensus to
the pass-1 worker pool and takes the results in bucket order. Output, outcome and every stable report field must equal
the serial (workers = 1) decode, on successful and on failing inputs; a stop in pass 2 must leave no worker or spill
file behind. Channel inputs are SIMULATED."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from vnxdna.recovery import outer
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

VOLATILE = ("stage_seconds", "seconds", "peak_rss_bytes", "output")
CHANNELS = {
    "clean": {"coverage": 2},
    "indel": {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 4},
    "rc": {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 4,
           "reverse_complement_rate": 0.5},
    # too few reads for every group: the outcome is not SUCCESS, and must be the same for every worker count
    "starved": {"substitution_rate": 0.01, "insertion_rate": 0.004, "deletion_rate": 0.004, "coverage": 1,
                "dropout_rate": 0.25},
}


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("v12pass2")
    # 300 kB: several spill buckets are not needed for correctness, but more than one group per bucket is
    datagen.generate(d / "in.bin", 300_000, "random", 12001)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    for i, (name, cfg) in enumerate(CHANNELS.items()):
        ch.simulate_file(d / "s.fasta", d / f"{name}.fastq", ch.ChannelConfig(seed=12010 + i, **cfg))
    return {"dir": d, "sha": hashlib.sha256((d / "a.vnx").read_bytes()).hexdigest()}


def _decode(arc, reads: str, workers: int, **kw):
    out = arc["dir"] / f"o-{reads}-{workers}.vnx"
    out.unlink(missing_ok=True)
    try:
        res = de.decode_reads(arc["dir"] / f"{reads}.fastq", out, de.DecodeOptions(workers=workers, **kw), overwrite=True)
    except Exception as error:  # noqa: BLE001 - a typed refusal is an outcome; compared across worker counts
        return ("ERROR", type(error).__name__, getattr(error, "code", None)), None, None
    sha = hashlib.sha256(out.read_bytes()).hexdigest() if out.exists() else None
    if res.status == "SUCCESS":
        assert sha == arc["sha"], "SUCCESS must mean the exact container"
    return res.status, sha, {k: v for k, v in res.report.items() if k not in VOLATILE}


@pytest.mark.parametrize("reads", list(CHANNELS))
def test_parallel_pass2_equals_serial(arc, reads):
    s1, h1, r1 = _decode(arc, reads, 1)
    for w in (2, 4):
        sw, hw, rw = _decode(arc, reads, w)
        assert (sw, hw) == (s1, h1), f"workers={w}"
        assert rw == r1, f"workers={w}: report differs"
    if reads != "starved":
        assert s1 == "SUCCESS"


def test_starved_fixture_does_not_succeed(arc):
    status, _, _ = _decode(arc, "starved", 2)
    assert status != "SUCCESS", "fixture must exercise the failing pass-2 path"


def test_parallel_path_is_taken_only_for_plain_full_decodes(arc, monkeypatch):
    calls = []
    real = outer._bucket_results

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(outer, "_bucket_results", spy)
    _decode(arc, "indel", 2)
    assert calls, "workers=2 full decode must use the parallel pass 2"
    calls.clear()
    _decode(arc, "indel", 1)
    assert not calls, "workers=1 keeps the serial loop"
    _decode(arc, "indel", 2, read_clustering="fallback")
    assert not calls, "read clustering keeps the serial loop"


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_stop_in_parallel_pass2_releases_workers_and_spill(arc, monkeypatch, tmp_path):
    from vnxdna.recovery.planner import RecoveryPlanner, VNXBudgetExceeded

    real = RecoveryPlanner.checkpoint
    seen = []

    def checkpoint(self, stage):
        if stage == "pass 2":
            seen.append(stage)
            if len(seen) == 2:             # stop after the first bucket, with buckets still in flight
                raise VNXBudgetExceeded("test stop in pass 2")
        return real(self, stage)

    monkeypatch.setattr(RecoveryPlanner, "checkpoint", checkpoint)
    # the 300 kB fixture fits one spill bucket; three buckets keep consensus work in flight at the stop
    from vnxdna.pipeline import decode as pipeline_decode
    monkeypatch.setattr(pipeline_decode, "_bucket_count", lambda est_reads: 3)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(VNXBudgetExceeded):
        de.decode_reads(arc["dir"] / "indel.fastq", tmp_path / "o.vnx", de.DecodeOptions(workers=2), overwrite=True,
                        workdir=work)
    assert len(seen) == 2
    assert not any(work.iterdir()), "spill directory must be removed"
    assert not (tmp_path / "o.vnx").exists(), "nothing is published on a stop"
    import multiprocessing
    assert not multiprocessing.active_children(), "pool workers must be shut down"
