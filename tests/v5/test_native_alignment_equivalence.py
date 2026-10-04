"""Differential tests: native aligner vs the V4 NumPy reference, every Projection field, bit for bit.

Contract: docs/V5_NATIVE_ALIGNMENT_CONTRACT.md. A single disagreement on any field of any read is a failure. Cases:
the Phase 1 golden fingerprints, the edit categories of the Phase 2 mission (substitutions, insertions and deletions
at the start/middle/end, adjacent and multiple edits, mixed indels, marker corruption, reverse complements, N
calls, out-of-alphabet bytes, every boundary length around the band), quality scores, all bands and cost
settings in the native domain, and seeded random fuzzing. All data is SIMULATED.
"""
from __future__ import annotations

import json
import zlib
import sys
from pathlib import Path

import numpy as np
import pytest

from vnxdna.v4 import channel as ch
from vnxdna.v4.sync import SyncCosts

from .native_support import LAYOUTS, assert_identical, project_both, strand

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.usefixtures("native_ready")


# ============================================================================ golden (Phase 1 fingerprints)
def _phase1():
    sys.path.insert(0, str(REPO / "benchmarks" / "v5"))
    import phase1_baseline as p1
    return p1


GOLDEN = json.loads((REPO / "benchmarks/v5/baseline-v4/align.json").read_text())["cases"]


@pytest.mark.parametrize("case", GOLDEN, ids=lambda c: f"{c['layout_name']}|{c['error_point']}")
def test_phase1_golden_fingerprints(case):
    """The native aligner reproduces every Phase 1 V4 projection SHA-256 (4,096 reads each)."""
    from vnxdna.v4.sync import TemplateAligner
    p1 = _phase1()
    reads, meta = p1.make_reads(p1.LAYOUTS[case["layout_name"]], p1.ERROR_POINTS[case["error_point"]])
    assert meta["reads_sha256"] == case["reads_sha256"], "read generation drifted from Phase 1"
    nat = TemplateAligner(p1.LAYOUTS[case["layout_name"]], case["band"], backend="native").project(reads)
    assert p1._projection_sha(nat) == case["projection_sha256"]


# ============================================================================ targeted edit categories
def _ins(s, pos, base=0):
    return np.insert(s, pos, np.uint8(base))


def _del(s, pos):
    return np.delete(s, pos)


def _sub(s, pos):
    s = s.copy()
    s[pos] = (s[pos] + 1) % 4
    return s


def _edit_suite(layout, rng) -> list[np.ndarray]:
    base = strand(layout, rng)
    T = base.size
    mid, end = T // 2, T - 1
    tpl, _ = layout.template()
    markers = np.flatnonzero(tpl >= 0)
    out = [base]
    for p in (0, 1, mid, end):                                   # substitutions
        out.append(_sub(base, p))
    for p in (0, 1, mid, end, T):                                # single insertions incl. before the first/after the last base
        out.append(_ins(base, p, int(rng.integers(0, 4))))
    for p in (0, 1, mid, end):                                   # single deletions
        out.append(_del(base, p))
    out.append(_ins(_ins(base, mid), mid))                       # adjacent insertions
    out.append(_del(_del(base, mid), mid))                       # adjacent deletions
    out.append(_ins(_ins(_ins(base, 3), mid), end))              # multiple insertions
    out.append(_del(_del(_del(base, end), mid), 3))              # multiple deletions
    out.append(_del(_ins(base, mid, 2), mid + 5))                # insertion + deletion, same segment
    out.append(_ins(_del(base, 10), T - 20))                     # deletion early, insertion late
    out.append(_del(_ins(base, 0), end))                         # shift across the whole strand
    if markers.size:
        m = int(markers[markers.size // 2])
        out.append(_sub(base, m))                                # marker substitution
        out.append(_del(base, m))                                # marker deletion
        out.append(_ins(base, m))                                # insertion inside a marker
        corrupt = base.copy()
        corrupt[markers] = (corrupt[markers] + 1) % 4            # every marker corrupted
        out.append(corrupt)
    rc = (3 - base)[::-1].copy()                                 # reverse complement (aligned as given)
    out.append(rc)
    withn = base.copy()
    withn[[2, mid, end]] = 4                                     # N calls
    out.append(withn)
    junk = base.copy()
    junk[[5, mid]] = [5, 255]                                    # bytes outside the alphabet
    out.append(junk)
    return out


@pytest.mark.parametrize("name", sorted(LAYOUTS))
@pytest.mark.parametrize("band", [0, 1, 3, 6, 12])
def test_edit_categories(name, band):
    layout = LAYOUTS[name]
    rng = np.random.default_rng(zlib.crc32(f"{name}|{band}".encode()))
    reads = [r.astype(np.uint8) for r in _edit_suite(layout, rng)]
    ref, nat = project_both(layout, reads, band=band)
    assert_identical(ref, nat, f"{name} band={band}")


@pytest.mark.parametrize("name", ["default-313nt", "tiny-8/1", "dense-no-markers"])
@pytest.mark.parametrize("band", [0, 2, 6, 64])
def test_boundary_lengths(name, band):
    """Lengths 0, 1, T−B−1 … T+B+1 (inside the band: aligned; outside: the unaligned record)."""
    layout = LAYOUTS[name]
    rng = np.random.default_rng(7)
    T = layout.strand_nt
    base = strand(layout, rng)
    reads = [np.zeros(0, np.uint8), base[:1].copy()]
    for L in sorted({max(0, T - band - 1), max(0, T - band), T - 1, T, T + 1, T + band, T + band + 1}):
        if L <= T:
            reads.append(base[:L].copy())
        else:
            reads.append(np.concatenate([base, rng.integers(0, 4, L - T).astype(np.uint8)]))
    ref, nat = project_both(layout, reads, band=band)
    assert_identical(ref, nat, f"{name} band={band}")


@pytest.mark.parametrize("costs", [SyncCosts(), SyncCosts(0, 0, 0, 0), SyncCosts(1, 1, 1, 0), SyncCosts(4, 6, 6, 1, 1),
                                   SyncCosts(9, 2, 7, 3, 2), SyncCosts(65536, 65536, 65536, 65536, 1024)],
                         ids=lambda c: f"mm{c.marker_mismatch}-i{c.insertion}-d{c.deletion}-x{c.marker_deletion_extra}-g{c.guard_segments}")
def test_cost_settings(costs):
    """Zero costs (maximal ties), asymmetric costs, guard segments, and the domain maximum (no int32 overflow)."""
    layout = LAYOUTS["default-313nt"]
    reads = _edit_suite(layout, np.random.default_rng(11))
    ref, nat = project_both(layout, reads, band=6, costs=costs)
    assert_identical(ref, nat, str(costs))


@pytest.mark.parametrize("min_quality", [0, 10, 13, 36, 99, 100])
def test_quality_scores(min_quality):
    layout = LAYOUTS["default-313nt"]
    rng = np.random.default_rng(min_quality)
    reads = _edit_suite(layout, rng)
    quals = []
    for k, r in enumerate(reads):
        if k % 5 == 4:
            quals.append(None)                                   # a read without qualities
        else:
            quals.append(rng.integers(0, 42, r.size).astype(np.uint8))
    ref, nat = project_both(layout, reads, quals, min_quality)
    assert_identical(ref, nat, f"minq={min_quality}")


def test_empty_and_all_unusable():
    layout = LAYOUTS["default-313nt"]
    ref, nat = project_both(layout, [])
    assert_identical(ref, nat, "empty")
    reads = [np.zeros(5, np.uint8), np.zeros(1000, np.uint8)]
    ref, nat = project_both(layout, reads)
    assert_identical(ref, nat, "unusable")


def test_channel_reads_all_layouts():
    """V4 channel output (substitutions, indels, N calls, bursts, reverse complements, qualities) on every layout."""
    for name, layout in LAYOUTS.items():
        rng = np.random.default_rng(3)
        strands = np.stack([strand(layout, rng) for _ in range(150)])
        cfg = ch.ChannelConfig(substitution_rate=0.01, insertion_rate=0.006, deletion_rate=0.006, n_rate=0.002, burst_rate=0.05,
                               burst_max_len=3, reverse_complement_rate=0.2, quality_informative=0.8, coverage=2, seed=99)
        res = ch.simulate_batch(strands, cfg, 0)
        offs = np.concatenate([[0], np.cumsum(res["lengths"])])
        reads = [res["codes"][offs[i]:offs[i + 1]] for i in range(res["lengths"].size)]
        quals = [res["quals"][offs[i]:offs[i + 1]] for i in range(res["lengths"].size)]
        for minq in (0, 20):
            ref, nat = project_both(layout, reads, quals, minq)
            assert_identical(ref, nat, f"{name} minq={minq}")


# ============================================================================ seeded fuzzing
def _fuzz_reads(layout, rng, n):
    T = layout.strand_nt
    reads = []
    for _ in range(n):
        s = strand(layout, rng)
        kind = rng.integers(0, 6)
        if kind == 0:                                            # random-length random bases near T
            reads.append(rng.integers(0, 4, max(0, T + int(rng.integers(-14, 15)))).astype(np.uint8))
            continue
        n_edits = int(rng.integers(0, 2 + 6 * (kind >= 3)))
        for _ in range(n_edits):
            op = rng.integers(0, 3)
            if op == 0 and s.size:
                s = _sub(s, int(rng.integers(0, s.size)))
            elif op == 1:
                s = _ins(s, int(rng.integers(0, s.size + 1)), int(rng.integers(0, 6)))
            elif s.size:
                s = _del(s, int(rng.integers(0, s.size)))
        if kind == 5:
            s = (3 - s[::-1]).astype(np.uint8)
        reads.append(s.astype(np.uint8))
    return reads


@pytest.mark.parametrize("seed", range(12))
def test_fuzz_random(seed):
    """Seeded fuzzing: 12 × 400 reads over random layouts, bands, costs, qualities and edit mixes."""
    rng = np.random.default_rng(1000 + seed)
    names = sorted(LAYOUTS)
    for round_ in range(4):
        layout = LAYOUTS[names[int(rng.integers(0, len(names)))]]
        band = int(rng.choice([0, 1, 2, 3, 4, 6, 8, 12, 20]))
        costs = SyncCosts(*(int(v) for v in rng.integers(0, 10, 4)), int(rng.integers(0, 3)))
        reads = _fuzz_reads(layout, rng, 100)
        quals = None
        if rng.random() < 0.5:
            quals = [rng.integers(0, 45, r.size).astype(np.uint8) if rng.random() < 0.9 else None for r in reads]
        minq = int(rng.integers(0, 40))
        ref, nat = project_both(layout, reads, quals, minq, band=band, costs=costs)
        assert_identical(ref, nat, f"seed={seed} round={round_} band={band} costs={costs}")


def test_batch_composition_independence():
    """A read's native result is the same alone, in any batch position and in a ragged final SIMD group."""
    from vnxdna.v4.sync import TemplateAligner
    layout = LAYOUTS["default-313nt"]
    reads = _fuzz_reads(layout, np.random.default_rng(5), 37)
    al = TemplateAligner(layout, 6, backend="native")
    full = al.project(reads)
    perm = np.random.default_rng(6).permutation(len(reads))
    shuffled = al.project([reads[i] for i in perm])
    for f in ("bases", "erased", "ok", "insertions", "deletions", "marker_mismatches", "cost"):
        assert np.array_equal(getattr(full, f)[perm], getattr(shuffled, f))
    for k in (0, 13, 36):
        one = al.project([reads[k]])
        for f in ("bases", "erased", "ok", "insertions", "deletions", "marker_mismatches", "cost"):
            assert np.array_equal(getattr(one, f)[0], getattr(full, f)[k])
