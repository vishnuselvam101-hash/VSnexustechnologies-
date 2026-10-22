"""V15 Task J2 (V14 hardening row O-3): the result-cache key race.

The cache key is built from the SHA-256 of the read file, and the decode then reads the same file again: a file
written in between stored the new result under the key of the old content, so a later decode of the old content was
served the new content's container. The fix re-hashes the read file after the decode and caches the result only when
the file did not change. All reads here are SIMULATED (the channel is simulated by vnxdna.v4.channel, fixed seeds).
"""
from __future__ import annotations

import shutil

import numpy as np

from vnxdna import sdk
from vnxdna.sdk import cache as sc
from vnxdna.v4 import channel as ch


def _simulated_reads(directory, name: str, payload: bytes, seed: int):
    """A SIMULATED read file of one archive (channel simulation, fixed seed)."""
    payload_path = directory / f"{name}.bin"
    payload_path.write_bytes(payload)
    archive, fasta, fastq = directory / f"{name}.vnx", directory / f"{name}.fasta", directory / f"{name}.fastq"
    sdk.archive([payload_path], archive)
    sdk.encode(archive, fasta)
    ch.simulate_file(fasta, fastq, ch.ChannelConfig(coverage=6, seed=seed))
    return fastq


def test_j2_cache_key_race_caches_nothing_under_the_old_key(tmp_path, monkeypatch):
    a = _simulated_reads(tmp_path, "a", np.random.default_rng(7).integers(0, 256, 6000, dtype=np.uint8).tobytes(), 3)
    b = _simulated_reads(tmp_path, "b", np.random.default_rng(8).integers(0, 256, 7000, dtype=np.uint8).tobytes(), 5)
    reads = tmp_path / "reads.fastq"
    shutil.copyfile(a, reads)
    cdir = tmp_path / "cache"

    from vnxdna.pipeline import decode as _de
    real_decode = _de.decode_reads
    swapped = []

    def swap_then_decode(reads_path, *args, **kwargs):
        if not swapped:                 # the file is written after the key was built and before the decode reads it
            swapped.append(True)
            shutil.copyfile(b, reads_path)
        return real_decode(reads_path, *args, **kwargs)

    monkeypatch.setattr(_de, "decode_reads", swap_then_decode)
    first = sdk.decode(reads, tmp_path / "o1.vnx", cache_dir=cdir)
    assert first.status == "SUCCESS"
    old_key = first.body["cache"]["key"]

    # the read file changed between the hash and the decode: nothing is cached under the old content's key
    assert first.body["cache"]["stored"] is False
    assert sc.ResultCache(cdir).lookup(old_key) is None
    assert sdk.cache_stats(cdir).body["entries"] == 0

    # a later lookup under the old key returns nothing
    assert sc.ResultCache(cdir).lookup(old_key) is None

    # the new content has its own key, and it works
    second = sdk.decode(reads, tmp_path / "o2.vnx", cache_dir=cdir)
    new_key = second.body["cache"]["key"]
    assert new_key != old_key
    assert second.body["cache"]["stored"] is True
    hit = sc.ResultCache(cdir).lookup(new_key)
    assert hit is not None
    assert hit[0].read_bytes() == (tmp_path / "b.vnx").read_bytes()
    third = sdk.decode(reads, tmp_path / "o3.vnx", cache_dir=cdir)
    assert third.body["cache"]["hit"] is True
    assert third.body["cache"]["key"] == new_key
    assert (tmp_path / "o3.vnx").read_bytes() == (tmp_path / "b.vnx").read_bytes()

    # the old content is decoded from its own bytes: the old key never served the new content's container
    shutil.copyfile(a, reads)
    fourth = sdk.decode(reads, tmp_path / "o4.vnx", cache_dir=cdir)
    assert fourth.body["cache"]["hit"] is False
    assert fourth.body["cache"]["key"] == old_key
    assert (tmp_path / "o4.vnx").read_bytes() == (tmp_path / "a.vnx").read_bytes()
