"""V7 item D3 tooling (experiments/v7/nanodata/nanolib.py): concatemer splitting, repeat units, consensus, error tally.

SYNTHETIC: random oligos, synthetic concatemers and rolling-circle reads with i.i.d. errors; no public read and no DNA is
used. Acceptance thresholds of docs/V7_PROTOCOL_AMENDMENT_D3.md PR-4.3 are asserted here.
"""
from __future__ import annotations

import gzip
import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("edlib")

NANODATA = Path(__file__).resolve().parents[2] / "experiments" / "v7" / "nanodata"
sys.path.insert(0, str(NANODATA))
import nanolib as nl  # noqa: E402

BASES = b"ACGT"
PARAMS = nl.SplitParams()


def rand_seq(rng: np.random.Generator, n: int) -> bytes:
    return bytes(BASES[i] for i in rng.integers(0, 4, n))


def mutate(rng: np.random.Generator, s: bytes, sub: float, ins: float, dele: float) -> bytes:
    out = bytearray()
    for c in s:
        u = rng.random()
        if u < dele:
            continue
        if u < dele + sub:
            out.append(BASES[(BASES.index(c) + int(rng.integers(1, 4))) % 4])
        else:
            out.append(c)
        if rng.random() < ins:
            out.append(BASES[int(rng.integers(0, 4))])
    return bytes(out)


def make_refs(rng: np.random.Generator, n: int) -> list[bytes]:
    """D13-like oligos: one shared 20-nt primer pair, 110-nt random payload (150 nt)."""
    fwd, rev = rand_seq(rng, 20), rand_seq(rng, 20)
    return [fwd + rand_seq(rng, 110) + rev for _ in range(n)]


def concatemer(rng, refs, n_oligos, err):
    """A read of ``n_oligos`` oligos in random orientation with 0-12-nt linkers; returns (read, truth list)."""
    read = bytearray(rand_seq(rng, int(rng.integers(0, 30))))
    truth = []
    for _ in range(n_oligos):
        i = int(rng.integers(0, len(refs)))
        strand = 1 if rng.random() < 0.5 else -1
        body = refs[i] if strand == 1 else nl.revcomp(refs[i])
        if err:
            body = mutate(rng, body, err * 0.375, err * 0.25, err * 0.375)
        start = len(read)
        read += body
        truth.append((i, strand, start, len(read)))
        read += rand_seq(rng, int(rng.integers(0, 13)))
    return bytes(read), truth


def qual_for(seq: bytes) -> bytes:
    return b"5" * len(seq)


# ---------------------------------------------------------------------------------------------------------------- split rules
def test_split_rule_and_heldout_units_are_pinned():
    # the held-out units named in the pre-registration (PR-3.1, PR-3.2)
    assert nl.heldout_unit("D13", ["vitruvian", "apollo", "space_shuttle", "365-dishes"]) == "space_shuttle"
    assert nl.heldout_unit("CAS9", ["g24", "g02", "g13"]) == "g13"
    assert [nl.bucket_split(b) for b in range(10)] == ["FIT"] * 6 + ["DEV"] * 2 + ["HELDOUT"] * 2
    with pytest.raises(ValueError):
        nl.bucket_split(10)
    s = b"ACGTTGCAAGGCT"
    assert nl.bucket(s) == nl.bucket(nl.revcomp(s)) == nl.bucket(s.lower())
    assert nl.bucket(s) == int(hashlib.sha256(min(s, nl.revcomp(s))).hexdigest(), 16) % 10
    assert nl.id_bucket(b"read-1") == int(hashlib.sha256(b"read-1").hexdigest(), 16) % 10


def test_read_fastq_and_refs(tmp_path):
    p = tmp_path / "r.fastq.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(b"@r1 runid=x\nACGT\n+\nIIII\n@r2\nAC\n+\nII\n")
    assert list(nl.read_fastq(p)) == [(b"r1", b"ACGT", b"IIII"), (b"r2", b"AC", b"II")]
    bad = tmp_path / "bad.fastq"
    bad.write_bytes(b"@r1\nACGT\n+\nIII\n")
    with pytest.raises(ValueError, match="malformed"):
        list(nl.read_fastq(bad))
    refs = tmp_path / "seqs.txt"
    refs.write_bytes(b"5'-ACGT-3'\n\n5'-GGCA-3'\n")
    assert nl.parse_d13_refs(refs) == [b"ACGT", b"GGCA"]
    refs.write_bytes(b"5'-ACNT-3'\n")
    with pytest.raises(ValueError):
        nl.parse_d13_refs(refs)
    refs.write_bytes(b"ACGT\n")
    with pytest.raises(ValueError):
        nl.parse_d13_refs(refs)


# ---------------------------------------------------------------------------------------------------------------- splitter
@pytest.fixture(scope="module")
def pool():
    rng = np.random.default_rng(83001)
    refs = make_refs(rng, 400)
    return refs, nl.build_index(refs, PARAMS)


def test_index_drops_shared_primer_kmers(pool):
    refs, index = pool
    # primer k-mers occur in all 400 references, above max_occ: none is indexed
    p, c = nl.kmer_codes(refs[0][:20], PARAMS.k)
    assert not np.isin(c, index.codes).any()
    assert index.dropped_kmers >= 400 * 2 * (20 - PARAMS.k + 1)


def test_error_free_concatemer_is_split_exactly(pool):
    refs, index = pool
    rng = np.random.default_rng(83002)
    for _ in range(10):
        read, truth = concatemer(rng, refs, 8, 0.0)
        res = nl.split_read(read, qual_for(read), index, PARAMS)
        got = [(g.ref, g.strand, g.start, g.end, g.ed) for g in res.segments]
        assert got == [(i, s, a, b, 0) for i, s, a, b in truth]
        assert res.ambiguous == 0
        for g in res.segments:
            assert g.seq == refs[g.ref] and g.ops == "=" * 150


def _match(truth, seg):
    """True if ``seg`` overlaps the truth oligo it should be (same reference, same strand, > 50 % overlap)."""
    for i, s, a, b in truth:
        ov = min(b, seg.end) - max(a, seg.start)
        if ov > 0.5 * (b - a):
            return (i, s) == (seg.ref, seg.strand)
    return None  # segment over a linker only: counted as wrong below


def test_noisy_concatemers_recall_and_no_wrong_reference(pool):
    """PR-4.3: 8 % i.i.d. errors per base: recall >= 0.95 and 0 segments on a wrong reference."""
    refs, index = pool
    rng = np.random.default_rng(83003)
    found = total = wrong = 0
    for _ in range(60):
        read, truth = concatemer(rng, refs, 10, 0.08)
        res = nl.split_read(read, qual_for(read), index, PARAMS)
        total += len(truth)
        for seg in res.segments:
            m = _match(truth, seg)
            if m is True:
                found += 1
            else:
                wrong += 1
            assert len(seg.seq) == seg.end - seg.start
    assert wrong == 0
    assert found / total >= 0.95, (found, total)


def test_near_identical_references_are_ambiguous_never_assigned():
    rng = np.random.default_rng(83004)
    refs = make_refs(rng, 50)
    twin = bytearray(refs[7])
    for pos in (60, 61):  # two substitutions: the twin is 2 edits from reference 7 (< margin 5)
        twin[pos] = BASES[(BASES.index(twin[pos]) + 1) % 4]
    refs.append(bytes(twin))
    index = nl.build_index(refs, PARAMS)
    read = rand_seq(rng, 25) + refs[7] + rand_seq(rng, 9) + refs[3] + rand_seq(rng, 11)
    res = nl.split_read(read, qual_for(read), index, PARAMS)
    assert [g.ref for g in res.segments] == [3]
    assert res.ambiguous == 1


def test_degenerate_reads(pool):
    refs, index = pool
    for read in (b"", b"ACGT", b"N" * 400, rand_seq(np.random.default_rng(1), 15)):
        res = nl.split_read(read, qual_for(read), index, PARAMS)
        assert res.segments == [] and res.ambiguous == 0
    # an oligo interrupted by a run of N is still found if most of it is intact, and N never matches
    body = bytearray(refs[5])
    body[70:76] = b"NNNNNN"
    read = b"GATTACA" + bytes(body) + b"CCGT"
    res = nl.split_read(read, qual_for(read), index, PARAMS)
    assert [g.ref for g in res.segments] == [5] and res.segments[0].ed >= 6
    with pytest.raises(ValueError):
        nl.split_read(b"ACGT", b"II", index, PARAMS)
    with pytest.raises(ValueError):
        nl.split_read(b"ACGT", b"IIII", nl.RefIndex(refs[:3], k=12, max_occ=8), PARAMS)


def test_split_is_order_independent(pool):
    """Each read is split independently: splitting in another order gives the same segments."""
    refs, index = pool
    rng = np.random.default_rng(83005)
    reads = [concatemer(rng, refs, 5, 0.08)[0] for _ in range(12)]
    a = [nl.split_read(r, qual_for(r), index, PARAMS).segments for r in reads]
    b = [nl.split_read(r, qual_for(r), index, PARAMS).segments for r in reversed(reads)][::-1]
    assert a == b


# ---------------------------------------------------------------------------------------------------------------- CAS9 units
ADDR = b"TCGCAGAGGTGGCG" + b"CTTTACAAGG"
OTHER = b"TCGCAGAGGTGGCG" + b"CGATTGTTGG"


def test_rolling_circle_units_and_consensus():
    rng = np.random.default_rng(83006)
    unit = ADDR + rand_seq(rng, 141)  # 165 nt circular template
    copies = [mutate(rng, unit, 0.02, 0.015, 0.02) for _ in range(9)]
    read = rand_seq(rng, 40) + b"".join(copies) + rand_seq(rng, 30)
    motif = b"TCGCAGAGGTGGCG" + b"N" * 10
    hits = nl.find_addresses(read, motif, 3)
    assert len(hits) >= 8
    assert hits == sorted(hits)
    units = nl.units_between(hits, 132, 198)
    assert len(units) >= 7
    names = {"g13": ADDR, "g02": OTHER}
    classes = [nl.classify_address(read[a:b + 4], names, 3) for a, b, _ in hits]
    assert classes.count("g13") >= len(hits) - 1 and "g02" not in classes
    segs = [read[a:b] for a, b in units]
    cons = nl.star_consensus(segs)
    true_rot = unit  # units start at the address, as the template does
    assert nl._edlib().align(cons, true_rot, mode="NW")["editDistance"] <= 3
    # leave-one-out consensus never uses the unit it is compared with
    d = nl.pairwise_distances(segs)
    assert (d == d.T).all() and (np.diag(d) == 0).all()
    loo = nl.star_consensus(segs[1:], d[1:, 1:])
    assert loo == nl.star_consensus(segs[1:])


def test_classify_address_margin():
    names = {"a": ADDR, "b": OTHER}
    assert nl.classify_address(b"GG" + ADDR + b"TT", names, 3) == "a"
    assert nl.classify_address(b"A" * 30, names, 3) is None
    assert nl.classify_address(b"GG" + ADDR + b"TT", {"a": ADDR, "a2": ADDR}, 3) is None  # tie: not assigned


def test_star_consensus_edge_cases():
    assert nl.star_consensus([b"ACGT"]) == b"ACGT"
    assert nl.star_consensus([b"ACGT", b"ACGT", b"ACTT"]) == b"ACGT"
    assert nl.star_consensus([b"ACGGT", b"ACGGT", b"ACGT"]) == b"ACGGT"
    with pytest.raises(ValueError):
        nl.star_consensus([])
    with pytest.raises(ValueError):
        nl.star_consensus([b"A", b"C"], np.zeros((3, 3), dtype=np.int64))


# ---------------------------------------------------------------------------------------------------------------- tally
def test_error_tally_counts_known_alignment():
    ref = b"AAACGTTTGC"
    # read: sub at 3 (C->G), deletion of one T (ref 6), insertion of an extra A in the A-run (homopolymer extension)
    read = b"AAAAGGTTGC"
    ops = "===IX==D==="  # explicit: the aligner may prefer another equally cheap path
    assert nl.edlib_ops(nl._edlib().align(ref, read, mode="NW", task="path")["cigar"]).count("=") >= 7
    t = nl.ErrorTally()
    t.add(ref, read, ops, b"I" * len(read))
    s = t.summary()
    assert s["segments"] == 1 and s["reference_sites"] == 10
    c = s["counts"]
    assert c["substitutions"] + c["deleted_bases"] + c["inserted_bases"] == 3
    assert c["deleted_bases"] == 1 and c["inserted_bases"] == 1
    assert sum(s["deletion_run_lengths"]["counts"]) == 1
    assert sum(s["homopolymer"]["sites"]) == 10
    assert s["homopolymer"]["extension_insertion_events"][2] == 1  # the extra A extends the run of three A
    assert s["substitution_matrix"]["counts"][1][2] == 1  # C read as G
    assert s["segment_edit_count"]["mean"] == 3
    assert s["quality_calibration"]["read_bases"] == len(read)
    with pytest.raises(ValueError):
        t.add(ref, read, "=" * 9)


def test_error_tally_rates_and_merge_are_order_free():
    rng = np.random.default_rng(83007)
    refs = [rand_seq(rng, 150) for _ in range(300)]
    reads = [mutate(rng, r, 0.03, 0.02, 0.04) for r in refs]
    ed = nl._edlib()
    pairs = [(r, s, nl.edlib_ops(ed.align(r, s, mode="NW", task="path")["cigar"])) for r, s in zip(refs, reads)]
    whole = nl.ErrorTally()
    for r, s, o in pairs:
        whole.add(r, s, o)
    a, b = nl.ErrorTally(), nl.ErrorTally()
    for k, (r, s, o) in enumerate(reversed(pairs)):
        (a if k % 2 else b).add(r, s, o)
    a.merge(b)
    assert a.summary() == whole.summary()
    rates = whole.summary()["rates_per_reference_site"]
    # alignment absorbs some events, so measured rates sit near (not exactly at) the injected ones
    assert 0.02 < rates["substitution"] < 0.045
    assert 0.025 < rates["deletion"] < 0.055
    assert 0.01 < rates["inserted_bases"] < 0.03
