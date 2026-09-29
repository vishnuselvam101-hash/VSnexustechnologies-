"""Duplicate strands: every copy is validated; valid copies win; conflicts are resolved deterministically."""
from collections import Counter

import numpy as np

from conftest import FAST
from vnxdna.container.builder import build_container
from vnxdna.container.reader import ContainerReader, load_manifest
from vnxdna.dna.mapping import to_codes, to_string
from vnxdna.dna.strand import KIND_DATA, build_strands
from vnxdna.storage.decoder import ReadsSource, resolve_candidates, scan_reads
from vnxdna.storage.encoder import constraints_of, encode_container, geometry_of

DATA = bytes(range(256)) * 20


def _pool():
    c = build_container(DATA, FAST)
    e = encode_container(c.manifest, c.manifest_bytes, c.stored_chunks)
    return c, e


def _corrupt(seq, n=20, seed=0):
    rng = np.random.default_rng(seed)
    codes = to_codes(seq).copy()
    pos = rng.choice(len(codes), n, replace=False)
    codes[pos] = (codes[pos] + 1) % 4
    return to_string(codes)


def _restore(c, reads):
    scan = scan_reads(reads, geometry_of(c.manifest))
    loaded = load_manifest(c.manifest_bytes, None)
    stats = Counter()
    reader = ContainerReader(loaded, ReadsSource(scan, c.manifest))
    return reader.read_all()[0], scan, reader


def test_resolve_candidates_rules():
    assert resolve_candidates(Counter()) == (None, "missing")
    assert resolve_candidates(Counter({b"a": 1})) == (b"a", "unique")
    assert resolve_candidates(Counter({b"a": 3})) == (b"a", "duplicates_consistent")
    assert resolve_candidates(Counter({b"a": 2, b"b": 1})) == (b"a", "conflict_majority")
    assert resolve_candidates(Counter({b"a": 1, b"b": 1})) == (None, "conflict_tie")


def test_corrupt_copy_before_or_after_a_valid_copy_never_shadows_it():
    c, e = _pool()
    first, second = [], []
    for s in e.sequences:
        first += [_corrupt(s), s]      # corrupt then valid
        second += [s, _corrupt(s)]     # valid then corrupt
    for reads in (first, second, list(reversed(first))):
        data, scan, _ = _restore(c, reads)
        assert data == DATA
        assert scan.stats["reads_rejected"] == len(e.sequences)


def test_multiple_valid_copies_and_all_copies_corrupt():
    c, e = _pool()
    reads = [s for s in e.sequences for _ in range(3)]
    assert _restore(c, reads)[0] == DATA
    # every copy of four strands is corrupt: they become erasures and the outer code restores them
    doomed = set(range(4))
    reads = [(_corrupt(s, seed=k) if i in doomed else s) for i, s in enumerate(e.sequences) for k in range(2)]
    data, _, reader = _restore(c, reads)
    assert data == DATA


def _forge(c, stripe, shard, payload):
    """A frame that passes CRC and inner RS but carries different payload (a 'conflicting valid-looking' copy)."""
    g = geometry_of(c.manifest)
    codes, _ = build_strands(g, constraints_of(c.manifest), c.manifest.archive_tag, np.array([KIND_DATA]), np.array([stripe]),
                             np.array([shard]), np.frombuffer(payload, dtype=np.uint8).reshape(1, -1))
    return to_string(codes[0])


def test_conflicting_valid_looking_copies_tie_becomes_erasure_and_majority_wins():
    c, e = _pool()
    forged = _forge(c, 0, 0, bytes(range(24)))
    # tie: one genuine + one forged copy -> erasure -> outer code restores the genuine data
    data, _, reader = _restore(c, e.sequences + [forged])
    assert data == DATA
    stats = Counter()
    ReadsSource(scan_reads(e.sequences + [forged], geometry_of(c.manifest)), c.manifest)(0, stats)
    assert stats["shards_conflict_tie"] == 1
    # the forged payload in the majority must be caught by the SHA-256 chain, never returned
    import pytest
    from vnxdna.errors import IntegrityError
    with pytest.raises(IntegrityError):
        _restore(c, e.sequences + [forged, forged])
