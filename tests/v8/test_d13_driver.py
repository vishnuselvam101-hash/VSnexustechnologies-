"""V8.1 D13 driver (experiments/v8/d13): access guard and ledger, cache round trip and hash determinism, the pinned
subsample, and segment splitting with held-out discard. Synthetic data only (no public data needed)."""
from __future__ import annotations

import gzip
import json
import random
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("edlib")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments/v8/d13"))
import access as A                     # noqa: E402
import pipeline as PL                  # noqa: E402

nl = PL.nl


def test_guard_grants_fit_dev_refuses_heldout_and_run13(tmp_path):
    led = tmp_path / "ledger.jsonl"
    g = A.V8Guard("test", ledger=led)
    g.authorize("run15", A.FIT, "t")
    g.authorize("run20", A.DEV, "t")
    for run, split, why in (("run13", A.FIT, "run 13"), ("run13", A.DEV, "run 13"), ("run15", A.HELDOUT, "PREREG"),
                            ("run13", A.HELDOUT, "PREREG"), ("run99", A.FIT, "unknown run"), ("run15", "TEST", "unknown split")):
        with pytest.raises(A.AccessRefused, match=why):
            g.authorize(run, split, "t")
    rows = [json.loads(x) for x in led.read_text().splitlines()]
    assert [r["granted"] for r in rows] == [True, True] + [False] * 6
    assert all(r["dataset"] == "d13-lopez-nanopore" and r["script"] == "test" for r in rows)
    assert "refused_because" in rows[2] and "refused_because" not in rows[0]


def test_heldout_needs_a_real_prereg_commit(tmp_path):
    assert not A.prereg_ok(None) and not A.prereg_ok("0" * 40)
    head = A._git(A.REPO, "rev-parse", "HEAD").stdout.strip()
    # HEAD exists and is an ancestor of itself but holds no experiments/v8/**/PREREG-HELDOUT*.md yet
    has = any(Path(n).name.startswith("PREREG-HELDOUT") for n in A._git(A.REPO, "ls-tree", "-r", "--name-only", head,
                                                                           "experiments/v8").stdout.split())
    assert A.prereg_ok(head) == has
    g = A.V8Guard("test", prereg_sha="0" * 40, ledger=tmp_path / "l.jsonl")
    with pytest.raises(A.AccessRefused):
        g.authorize("run15", A.HELDOUT, "t")


def test_cache_round_trip_is_grouped_and_deterministic(tmp_path):
    by_ref = {7: [(b"ACGT", b"IIII")], 2: [(b"AAC", b"!!#"), (b"GG", b"55")]}
    a, b = tmp_path / "a.tsv.gz", tmp_path / "b.tsv.gz"
    assert PL.write_cache(a, by_ref) == PL.write_cache(b, dict(reversed(list(by_ref.items()))))
    assert list(PL.read_cache(a)) == [(2, [b"AAC", b"GG"], [b"!!#", b"55"]), (7, [b"ACGT"], [b"IIII"])]


def _fastq(p: Path, recs):
    with gzip.open(p, "wb") as fh:
        for rid, seq in recs:
            fh.write(b"@%s\n%s\n+\n%s\n" % (rid, seq, b"5" * len(seq)))


def test_subsample_is_the_v7_rule(tmp_path):
    p = tmp_path / "r.fastq.gz"
    recs = [(b"read%d" % i, b"ACGT" * 10) for i in range(30)]
    _fastq(p, recs)
    assert PL.subsample_ids(p, 100) is None
    got = PL.subsample_ids(p, 10)
    import hashlib
    want = sorted((r for r, _ in recs), key=lambda r: hashlib.sha256(b"VNX-D3-SUBSAMPLE/" + r).digest())[:10]
    assert got == set(want)


def _noisy(rnd, s: bytes, p=0.03) -> bytes:
    out = bytearray()
    for b in s:
        u = rnd.random()
        if u < p / 3:
            continue
        out.append(b if u >= p else rnd.choice(b"ACGT"))
    return bytes(out)


def test_segments_go_to_their_reference_split_and_heldout_is_discarded():
    rnd = random.Random(3)
    refs = [bytes(rnd.choice(b"ACGT") for _ in range(150)) for _ in range(40)]
    buckets = np.array([nl.bucket(r) for r in refs], dtype=np.int8)
    PL._init(refs, buckets)
    batch, truth = [], []
    for i in range(30):
        picks = rnd.sample(range(40), 4)
        truth.append(picks)
        read = b"".join(_noisy(rnd, refs[k]) + bytes(rnd.choice(b"ACGT") for _ in range(20)) for k in picks)
        batch.append((b"r%d" % i, read, b"5" * len(read)))
    segs, c, reads = PL._batch(batch)
    assert c["reads"] == 30
    expect = {s: sum(1 for picks in truth for k in picks if nl.bucket_split(int(buckets[k])) == s)
              for s in (A.FIT, A.DEV, nl.HELDOUT)}
    found = {s: len(segs[s]) for s in PL.SPLITS}
    assert found[A.FIT] >= 0.9 * expect[A.FIT] and found[A.DEV] >= 0.9 * expect[A.DEV]
    assert c["segments_heldout_reference_discarded"] >= 0.9 * expect[nl.HELDOUT]
    for s in PL.SPLITS:
        assert all(nl.bucket_split(int(buckets[ref])) == s for ref, _seq, _q in segs[s])        # no cross-split segment
    assert sum(r["n"] for r in reads.values()) + c["reads_heldout_id_bucket_readlevel_skipped"] == 30
