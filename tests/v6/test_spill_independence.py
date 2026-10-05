"""Spill-bucket independence (audit §5.6, an UNVERIFIED claim until now; V6 Phase 2.8): the decode outcome must not
depend on the number of spill buckets B, which follows the open-file limit (RLIMIT_NOFILE) for large pools.

The bucket count is derived exactly as in production (``recovery.spill._bucket_count``) from different
``RLIMIT_NOFILE`` values; only the reads-per-bucket target is lowered so that the limit binds for a small read file.

Finding (V6 Phase 2.8, plan conflict documented in the phase notes): the status, the published container, the
superblock and the decoded/failed groups are identical, but symbol-level consensus internals are not (snap counters,
consensus attempts, received symbols per row, smart-consensus groups): pass 2 snaps a pending read to a missing address
of another group only if that group is in the same bucket (group mod B). Removing the dependence at bounded memory
would mean snapping every read to its own group only, which loses recoveries at B = 1 (most pools), so it is not done.
The test therefore pins the outcome; an outcome difference stays possible in principle for pools above ~200,000 reads.
SIMULATED channel; SYNTHETIC test data.
"""
from __future__ import annotations

import pytest

from vnxdna.recovery import spill as sp
from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

OUTCOME = ("status", "superblock", "groups_decoded", "groups_failed", "failed_groups", "container_sha256", "encrypted",
           "archive_tags_seen")


@pytest.fixture(scope="module")
def reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("spill")
    datagen.generate(d / "in.bin", 40_000, "random", 4401)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    out = {"dir": d}
    for name, v6 in (("sb1", {}), ("sb2", {"stripe_depth": 4, "column_parity": 1})):
        en.encode_container(d / "a.vnx", d / f"{name}.fasta", en.DNAOptions(**v6))
        ch.simulate_file(d / f"{name}.fasta", d / f"{name}.fastq",
                         ch.ChannelConfig(substitution_rate=0.004, insertion_rate=0.003, deletion_rate=0.003,
                                          dropout_rate=0.02, coverage=3, seed=4402))
        out[name] = d / f"{name}.fastq"
    return out


def _decode(path, out, monkeypatch, nofile, opts):
    import resource
    seen = []
    real = sp._bucket_count
    if nofile is not None:
        monkeypatch.setattr(sp, "READS_PER_BUCKET", 1)
        monkeypatch.setattr(resource, "getrlimit", lambda what: (nofile, resource.RLIM_INFINITY))

    def spy(est):
        b = real(est)
        seen.append(b)
        return b
    from vnxdna.pipeline import decode as pd
    monkeypatch.setattr(pd, "_bucket_count", spy)
    res = de.decode_reads(path, out, de.DecodeOptions(**opts), overwrite=True)
    monkeypatch.undo()
    rep = res.report
    outcome = {k: rep.get(k) for k in OUTCOME}
    outcome["outer_v6_unrecovered"] = (rep.get("outer_v6") or {}).get("data_rows_unrecovered")
    return res.status, out.read_bytes() if out.exists() else None, outcome, seen[0]


@pytest.mark.parametrize("layout", ["sb1", "sb2"])
@pytest.mark.parametrize("opts", [{}, {"indel_recovery": "smart"}], ids=["v4", "smart-deferred"])
def test_decode_outcome_does_not_depend_on_the_spill_bucket_count(reads, tmp_path, monkeypatch, layout, opts):
    runs = {nofile: _decode(reads[layout], tmp_path / f"o{nofile}.vnx", monkeypatch, nofile, opts)
            for nofile in (None, 640, 700)}
    buckets = {k: v[3] for k, v in runs.items()}
    assert buckets[None] == 1 and buckets[640] == 256 and buckets[700] == 286, buckets   # three different bucket counts
    status, container, report, _ = runs[None]
    assert status == "SUCCESS" and container == (reads["dir"] / "a.vnx").read_bytes()
    for k in (640, 700):
        assert runs[k][0] == status and runs[k][1] == container
        assert runs[k][2] == report
