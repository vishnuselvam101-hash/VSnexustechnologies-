"""V11 WP3: a decode stopped in pass 1 (recovery budget, or a cancel raised from the progress callback) leaves no spill
file open and no spill directory behind, even while the caller still holds the exception (its traceback keeps the
decode frame, and with it the Spill/UnplacedStore objects, alive). An open handle on a removed spill file keeps its
disk blocks allocated until the process exits."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v6.recovery import RecoveryBudget, VNXBudgetExceeded


@pytest.fixture(scope="module")
def reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("spillcancel")
    datagen.generate(d / "in.bin", 30_000, "random", 11301)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    ch.simulate_file(d / "s.fasta", d / "r.fastq",
                     ch.ChannelConfig(substitution_rate=0.004, insertion_rate=0.004, deletion_rate=0.004, coverage=3,
                                      seed=11302))
    return d / "r.fastq"


class _Cancel(Exception):
    pass


def _open_under(root: Path) -> list[str]:
    out = []
    for fd in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if target.startswith(str(root)):
            out.append(target)
    return out


def _cancel_progress(info):
    raise _Cancel("cancelled by the caller")


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("clustering", ["off", "fallback"])
@pytest.mark.parametrize("stop", ["budget", "cancel"])
def test_pass1_stop_closes_and_removes_every_spill_file(reads, tmp_path, workers, clustering, stop):
    wd = tmp_path / "wd"
    wd.mkdir()
    opts = dict(workers=workers, indel_recovery="smart", read_clustering=clustering, batch_reads=64)
    if stop == "budget":
        opts["recovery_budget"] = RecoveryBudget(max_wall_seconds=1e-9)
    exc = VNXBudgetExceeded if stop == "budget" else _Cancel
    with pytest.raises(exc) as info:
        de.decode_reads(reads, tmp_path / "out.vnx", de.DecodeOptions(**opts), workdir=wd,
                        progress=_cancel_progress if stop == "cancel" else None)
    assert info.value.__traceback__ is not None             # the caller still holds the decode frame
    assert _open_under(wd) == []
    assert list(wd.iterdir()) == []
    assert not (tmp_path / "out.vnx").exists()


def _mapped_under(root: Path) -> list[str]:
    with open("/proc/self/maps") as f:
        return [line.split(None, 5)[-1].strip() for line in f if str(root) in line]


@pytest.mark.skipif(not Path("/proc/self/maps").is_file(), reason="needs /proc")
@pytest.mark.parametrize("workers", [1, 2])
def test_stop_in_deferred_recovery_releases_the_spill(reads, tmp_path, monkeypatch, workers):
    """A stop inside the deferred recovery stage (here: the first verified-frame append raises) must not keep the
    unaddressed-read memory map or any spill handle of the removed directory."""
    from vnxdna.recovery import spill as sp

    def boom(self, fields, payload):
        raise _Cancel("cancelled in deferred recovery")

    monkeypatch.setattr(sp.Spill, "append_acc", boom)
    wd = tmp_path / "wd"
    wd.mkdir()
    with pytest.raises(_Cancel) as info:
        de.decode_reads(reads, tmp_path / "out.vnx", de.DecodeOptions(workers=workers, indel_recovery="smart",
                                                                     batch_reads=64), workdir=wd)
    assert info.value.__traceback__ is not None
    assert _open_under(wd) == [] and _mapped_under(wd) == []
    assert list(wd.iterdir()) == []


@pytest.mark.skipif(not Path("/proc/self/maps").is_file(), reason="needs /proc")
def test_stop_in_read_clustering_releases_the_unplaced_read_map(tmp_path, monkeypatch):
    """A stop raised inside the read-clustering stage (a budget checkpoint, here a cancel) must not leave the removed
    unplaced-read store mapped through the frames that held views of it."""
    from vnxdna.recovery.cluster import stage as cs
    from vnxdna.simulation.channel import ChannelConfig, simulate_file
    from tests.v7.cluster_support import make_strands

    make_strands(tmp_path, 1200)
    simulate_file(tmp_path / "s.fasta", tmp_path / "r.fastq",
                  ChannelConfig(substitution_rate=0.01, insertion_rate=0.005, deletion_rate=0.015, coverage=10.0, seed=1,
                                coverage_model="negative-binomial", coverage_dispersion=4.0, reverse_complement_rate=0.5,
                                n_rate=0.001))
    seen = {}

    def stopped(reads, lay, cfg, *args, raw=None, **kw):
        seen["raw"] = raw is not None and len(raw) > 0
        raise _Cancel("cancelled in read clustering")

    monkeypatch.setattr(cs, "cluster_frames", stopped)
    wd = tmp_path / "wd"
    wd.mkdir()
    with pytest.raises(_Cancel) as info:
        de.decode_reads(tmp_path / "r.fastq", tmp_path / "out.vnx", de.DecodeOptions(read_clustering="fallback"),
                        workdir=wd)
    assert seen["raw"] and info.value.__traceback__ is not None
    assert _open_under(wd) == [] and _mapped_under(wd) == []
    assert list(wd.iterdir()) == []
