"""V6 (job #80): opt-in retry band for reads whose net drift exceeds the alignment band.

``DecodeOptions.retry_band`` (0 = off, the default): reads with band < |len − T| ≤ retry_band, which the V4 band leaves
unaligned, are aligned by a second ``TemplateAligner`` of band ``retry_band`` (the same DP and native kernel; the V5
alignment contract holds for every band ≤ 64). Reads inside the band never reach it, so their projection, and the whole
default decode, are unchanged bit for bit. Every recovered frame is still RS- and CRC-verified and the container SHA-256
decides SUCCESS. SYNTHETIC test data; SIMULATED channel.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import PROFILES, Layout
from vnxdna.native import align as na
from vnxdna.sync.smart.path import align_with_path
from vnxdna.sync.template import SyncCosts, TemplateAligner
from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.errors import VNXError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from v5.native_support import FIELDS, assert_identical, strand  # noqa: E402

LAY = PROFILES["v4-balanced"][0]
GOLDEN = HERE / "retry_band_golden.json"


def drifted_reads(layout: Layout, rng: np.random.Generator, n: int, max_drift: int = 24, subs: float = 0.01) -> list:
    """Reads with a net drift drawn uniformly in [−max_drift, +max_drift] (random deletions or insertions), plus
    substitutions, a few N calls and reverse complements: inside the band, in the retry window and beyond it."""
    out = []
    for _ in range(n):
        s = strand(layout, rng)
        drift = int(rng.integers(-max_drift, max_drift + 1))
        extra = int(rng.integers(0, 3))                     # cancelling ins/del pairs on top of the net drift
        for _ in range(abs(drift) + extra):
            if drift < 0 and s.size:
                s = np.delete(s, int(rng.integers(0, s.size)))
            else:
                s = np.insert(s, int(rng.integers(0, s.size + 1)), np.uint8(rng.integers(0, 4)))
        for _ in range(extra):
            if drift < 0:
                s = np.insert(s, int(rng.integers(0, s.size + 1)), np.uint8(rng.integers(0, 4)))
            elif s.size:
                s = np.delete(s, int(rng.integers(0, s.size)))
        flip = rng.random(s.size) < subs
        s = s.copy()
        s[flip] = (s[flip] + rng.integers(1, 4, int(flip.sum()))) % 4
        if rng.random() < 0.02 and s.size:
            s[int(rng.integers(0, s.size))] = 4
        if rng.random() < 0.1:
            s = (3 - s[::-1]).astype(np.uint8)
            s[s > 3] = 4
        out.append(s.astype(np.uint8))
    return out


def _sha(p) -> str:
    h = hashlib.sha256()
    for f in FIELDS:
        a = np.ascontiguousarray(getattr(p, f))
        h.update(f.encode() + str(a.dtype).encode() + str(a.shape).encode() + a.tobytes())
    return h.hexdigest()


def _native_or_skip():
    if not na.available():
        pytest.skip("native aligner not available")


# ----------------------------------------------------------------------------------------------- the aligner
def test_retry_band_must_exceed_band():
    with pytest.raises(ValueError):
        TemplateAligner(LAY, 6, retry_band=6)
    assert TemplateAligner(LAY, 6).retry is None and TemplateAligner(LAY, 6).retry_band == 0


@pytest.mark.parametrize("backend", ["reference", "native"])
def test_reads_inside_the_band_are_unchanged(backend):
    """Every read with |drift| ≤ band gets exactly the projection of the V4 aligner without the retry band."""
    if backend == "native":
        _native_or_skip()
    rng = np.random.default_rng(80_001)
    reads = [r for r in drifted_reads(LAY, rng, 600, max_drift=8) if abs(r.size - LAY.strand_nt) <= 6]
    base = TemplateAligner(LAY, 6, backend=backend).project(reads)
    retry = TemplateAligner(LAY, 6, backend=backend, retry_band=16).project(reads)
    assert_identical(base, retry, "in-band")


@pytest.mark.parametrize("backend", ["reference", "native"])
def test_reads_in_the_retry_window_get_the_wide_band_projection_and_the_rest_stay_unaligned(backend):
    if backend == "native":
        _native_or_skip()
    rng = np.random.default_rng(80_002)
    reads = drifted_reads(LAY, rng, 800, max_drift=24)
    drift = np.abs(np.array([r.size for r in reads]) - LAY.strand_nt)
    p = TemplateAligner(LAY, 6, backend=backend, retry_band=16).project(reads)
    narrow = TemplateAligner(LAY, 6, backend=backend).project(reads)
    wide = TemplateAligner(LAY, 16, backend=backend).project(reads)
    for name, sel, want in (("in-band", drift <= 6, narrow), ("window", (drift > 6) & (drift <= 16), wide),
                            ("beyond", drift > 16, narrow)):
        assert sel.any(), name
        for f in FIELDS:
            assert np.array_equal(getattr(p, f)[sel], getattr(want, f)[sel]), (name, f)
    assert p.ok[(drift > 6) & (drift <= 16)].all()
    assert not p.ok[drift > 16].any() and (p.cost[drift > 16] == 1 << 28).all()


def test_native_and_reference_agree_with_a_retry_band():
    _native_or_skip()
    rng = np.random.default_rng(80_003)
    for layout in (LAY, Layout(10, 16, 24, 3), Layout(40, 16, 32, 2)):
        for band, retry, costs in ((6, 16, None), (3, 12, SyncCosts(guard_segments=1)), (6, 64, None), (1, 2, None)):
            reads = drifted_reads(layout, rng, 300, max_drift=retry + 2)
            quals = [rng.integers(2, 41, r.size).astype(np.uint8) for r in reads]
            ref = TemplateAligner(layout, band, costs, backend="reference", retry_band=retry).project(reads, quals, 10)
            nat = TemplateAligner(layout, band, costs, backend="native", retry_band=retry).project(reads, quals, 10)
            assert_identical(ref, nat, f"{layout} band={band} retry={retry}")


@pytest.mark.parametrize("backend", ["reference", "native"])
def test_align_with_path_matches_project_with_a_retry_band(backend):
    if backend == "native":
        _native_or_skip()
    rng = np.random.default_rng(80_004)
    reads = drifted_reads(LAY, rng, 400, max_drift=20)
    al = TemplateAligner(LAY, 6, backend=backend, retry_band=16)
    proj, rpos = align_with_path(al, reads)
    assert_identical(al.project(reads), proj, "path vs project")
    _, ref_rpos = align_with_path(TemplateAligner(LAY, 6, backend="reference", retry_band=16), reads, backend="reference")
    assert np.array_equal(rpos, ref_rpos)
    drift = np.abs(np.array([r.size for r in reads]) - LAY.strand_nt)
    win = (drift > 6) & (drift <= 16)
    assert (rpos[win] >= 0).any(axis=1).all()             # window reads now have a traceback path
    assert (rpos[drift > 16] == -1).all()


def _golden_cases():
    rng = np.random.default_rng(80_005)
    cases = {}
    for name, layout in (("default-313nt", LAY), ("short-178nt", Layout(10, 16, 24, 3))):
        for retry in (12, 16, 24):
            reads = drifted_reads(layout, np.random.default_rng(rng.integers(1 << 32)), 1024, max_drift=retry + 4)
            cases[f"{name}|band6|retry{retry}"] = (layout, retry, reads)
    return cases


def test_golden_retry_band_fingerprints():
    """New golden cases (added for job #80; the Phase 1 golden fingerprints are untouched): projection SHA-256 of the
    retry-band aligner on 1,024 drifted reads per case, for the reference and (if available) the native backend."""
    golden = json.loads(GOLDEN.read_text())["cases"]
    cases = _golden_cases()
    assert set(golden) == set(cases)
    for key, (layout, retry, reads) in cases.items():
        reads_sha = hashlib.sha256(b"".join(r.tobytes() + b"|" for r in reads)).hexdigest()
        assert reads_sha == golden[key]["reads_sha256"], f"{key}: read generation drifted"
        backends = ["reference"] + (["native"] if na.available() else [])
        for b in backends:
            p = TemplateAligner(layout, 6, backend=b, retry_band=retry).project(reads)
            assert _sha(p) == golden[key]["projection_sha256"], f"{key} {b}"


# ----------------------------------------------------------------------------------------------- options
def test_option_defaults_off_and_is_validated():
    assert de.DecodeOptions().retry_band == 0
    de.DecodeOptions(retry_band=16).validate()
    for bad in (6, 3, 65, -1, True, 16.0):
        with pytest.raises(VNXConfigurationError):
            de.DecodeOptions(retry_band=bad).validate()
    with pytest.raises(VNXConfigurationError):
        de.DecodeOptions(band=20, retry_band=16).validate()


def test_config_file_and_cli_option_reach_the_decoder(tmp_path):
    from vnxdna.sdk.config import decode_options_for
    assert decode_options_for(None, retry_band=16).retry_band == 16
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"decode": {"retry_band": 12}}))
    assert decode_options_for(cfg).retry_band == 12
    assert decode_options_for(None).retry_band == 0


# ----------------------------------------------------------------------------------------------- end to end
def _drift_reads_file(strands: Path, out: Path, seed: int, drift_share: float, drift: int, coverage: int = 3) -> None:
    """Every strand `coverage` times; a share of the reads loses a contiguous run of `drift` bases (one burst
    deletion at a random position: a net drift of −drift that erases about one segment)."""
    from vnxdna.v4.constraints import iter_fasta
    rng = np.random.default_rng(seed)
    lines = []
    k = 0
    for _, seq in iter_fasta(strands):
        for _ in range(coverage):
            s = bytearray(seq.encode() if isinstance(seq, str) else seq)
            if rng.random() < drift_share:
                p = int(rng.integers(0, len(s) - drift))
                del s[p:p + drift]
            lines.append(f"@r{k}\n{s.decode()}\n+\n{'I' * len(s)}\n")
            k += 1
    order = rng.permutation(len(lines))
    out.write_text("".join(lines[i] for i in order))


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    d = tmp_path_factory.mktemp("rb")
    datagen.generate(d / "in.bin", 20_000, "random", 8001)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    _drift_reads_file(d / "s.fasta", d / "inband.fastq", 8002, 0.5, 3)
    _drift_reads_file(d / "s.fasta", d / "drifted.fastq", 8003, 0.85, 9)
    return {"dir": d, "container": (d / "a.vnx").read_bytes()}


def _decode(path, out, **kw):
    try:
        res = de.decode_reads(path, out, de.DecodeOptions(**kw), overwrite=True)
    except VNXError as error:
        return f"ERROR:{error.code}", None, {}
    return res.status, (out.read_bytes() if out.exists() else None), res.report


def test_retry_band_changes_nothing_when_every_read_is_inside_the_band(pool, tmp_path):
    s0, c0, r0 = _decode(pool["dir"] / "inband.fastq", tmp_path / "a.vnx")
    s1, c1, r1 = _decode(pool["dir"] / "inband.fastq", tmp_path / "b.vnx", retry_band=16)
    assert s0 == s1 == "SUCCESS" and c0 == c1 == pool["container"]
    reads1 = dict(r1["reads"])
    assert reads1.pop("retry_band_reads") == 0
    assert reads1 == r0["reads"]
    assert "retry_band_reads" not in r0["reads"]


def test_retry_band_aligns_drifted_reads_deterministically_and_never_returns_wrong_data(pool, tmp_path):
    path = pool["dir"] / "drifted.fastq"
    s0, c0, r0 = _decode(path, tmp_path / "d0.vnx")
    s1, c1, r1 = _decode(path, tmp_path / "d1.vnx", retry_band=16, workers=1)
    s2, c2, r2 = _decode(path, tmp_path / "d2.vnx", retry_band=16, workers=2)
    assert (s1, c1) == (s2, c2) and r1["reads"] == r2["reads"]
    assert r1["reads"]["retry_band_reads"] > 0 and r1["reads"]["unaligned"] < r0["reads"].get("unaligned", 1 << 60)
    assert s1 == "SUCCESS" and c1 == pool["container"]            # 85 % of reads drift by 9: only the retry band decodes
    assert s0 != "SUCCESS" and c0 is None                         # without it the same pool is refused (detected)


def test_smart_and_soft_recovery_accept_retry_band_reads(pool, tmp_path):
    status, container, rep = _decode(pool["dir"] / "drifted.fastq", tmp_path / "s.vnx", retry_band=16,
                                     indel_recovery="smart", soft_decoding="auto")
    assert status == "SUCCESS" and container == pool["container"]
    assert rep["reads"]["retry_band_reads"] > 0
