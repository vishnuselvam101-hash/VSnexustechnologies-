"""V7 item A "never wrong" tests (directive §9, protocol §6, V7_ARCHITECTURE §8 "Adversarial"): adversarial pools decoded
with read clustering on must end EXACT or in an explicit failure — never FALSE SUCCESS, never a crash. Classified with
``vnxdna.benchmark.outcome``. SYNTHETIC test data; SIMULATED channel."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from vnxdna.archive import container as ct
from vnxdna.benchmark.outcome import CRASH, EXACT, EXPLICIT_FAILURE, FALSE_SUCCESS, PARTIAL, classify_outcome, decode_claim
from vnxdna.simulation.channel import ChannelConfig, simulate_file
from vnxdna.v4 import decoder as de
from vnxdna.v4.errors import VNXError

from .cluster_support import make_strands

ALLOWED = (EXACT, EXPLICIT_FAILURE, PARTIAL)
COMMON = dict(coverage_model="negative-binomial", coverage_dispersion=4.0, reverse_complement_rate=0.5, n_rate=0.001)


def _channel(seed: int, cov: float = 10.0) -> ChannelConfig:
    return ChannelConfig(substitution_rate=0.01, insertion_rate=0.005, deletion_rate=0.015, coverage=cov, seed=seed,
                         **COMMON)


def _tag(container: Path) -> int:
    return int.from_bytes(ct.open_container(container).archive_id[:2], "big")


def _decode(reads: Path, out: Path, truth_sha: str, **kw) -> tuple[dict, object, object]:
    if out.exists():
        out.unlink()
    claim, res, err = decode_claim(lambda: de.decode_reads(reads, out, de.DecodeOptions(read_clustering="fallback", **kw)),
                                   out)
    oc = classify_outcome(claim, {"container": truth_sha})
    assert oc["outcome"] not in (FALSE_SUCCESS, CRASH), oc
    return oc, res, err


def _mix(paths: list[Path], out: Path, seed: int) -> Path:
    """Interleave FASTQ records of several files in a seeded random order."""
    recs = []
    for p in paths:
        lines = p.read_text().splitlines()
        recs += ["\n".join(lines[i:i + 4]) + "\n" for i in range(0, len(lines), 4)]
    order = np.random.default_rng(seed).permutation(len(recs))
    out.write_text("".join(recs[i] for i in order.tolist()))
    return out


@pytest.fixture(scope="module")
def two_archives(tmp_path_factory):
    d = tmp_path_factory.mktemp("nw_two")
    out = {}
    for name, seed in (("a", 11), ("b", 12)):
        w = d / name
        c, _ = make_strands(w, 1200, seed=seed, archive_id=bytes([seed, 0x5A]) + bytes([seed]) * 14)
        simulate_file(w / "s.fasta", w / "r.fastq", _channel(100 + seed))
        out[name] = {"dir": w, "container": c, "sha": hashlib.sha256(c).hexdigest(), "tag": _tag(w / "a.vnx")}
    assert out["a"]["tag"] != out["b"]["tag"]
    out["mixed"] = _mix([out["a"]["dir"] / "r.fastq", out["b"]["dir"] / "r.fastq"], d / "mixed.fastq", 5)
    return out


def test_two_archives_mixed_with_a_chosen_tag(two_archives, tmp_path):
    a = two_archives["a"]
    oc, res, err = _decode(two_archives["mixed"], tmp_path / "o.vnx", a["sha"], archive_tag=a["tag"])
    assert oc["outcome"] in ALLOWED
    if res is not None and res.status != "SUCCESS":
        assert res.report["clustering"]["fill"].get("data_conflicts", 0) == 0


def test_two_archives_mixed_without_a_tag_is_refused_or_exact(two_archives, tmp_path):
    out = tmp_path / "o.vnx"
    claim, res, err = decode_claim(lambda: de.decode_reads(two_archives["mixed"], out,
                                                          de.DecodeOptions(read_clustering="fallback")), out)
    published = claim.published.get("container")
    assert published in (None, two_archives["a"]["sha"], two_archives["b"]["sha"])
    if published is None:
        assert err is not None or res.status != "SUCCESS"


def test_two_archives_sharing_a_tag(tmp_path):
    """Equal archive tags (first two ID bytes): the archives cannot be told apart by their frames' tag."""
    paths, shas = [], []
    for name, seed in (("a", 21), ("b", 22)):
        w = tmp_path / name
        aid = bytes([0xAB, 0xCD]) + bytes([seed]) * 14
        c, _ = make_strands(w, 1200, seed=seed, archive_id=aid)
        simulate_file(w / "s.fasta", w / "r.fastq", _channel(200 + seed))
        paths.append(w / "r.fastq")
        shas.append(hashlib.sha256(c).hexdigest())
    mixed = _mix(paths, tmp_path / "mixed.fastq", 6)
    for truth in shas:
        oc, res, err = _decode(mixed, tmp_path / "o.vnx", truth)
        assert oc["outcome"] in ALLOWED
    out = tmp_path / "p.vnx"
    claim, _, _ = decode_claim(lambda: de.decode_reads(mixed, out, de.DecodeOptions(read_clustering="fallback")), out)
    assert claim.published.get("container") in (None, *shas)


@pytest.mark.parametrize("cov", [6.0, 12.0])
def test_zero_filled_input_near_duplicate_strands(cov, tmp_path):
    """Cell L: an uncompressed zero-filled file gives strands that differ only in header, CRC and inner parity."""
    c, _ = make_strands(tmp_path, 0, data=bytes(1500))
    simulate_file(tmp_path / "s.fasta", tmp_path / "r.fastq", _channel(31, cov))
    oc, res, err = _decode(tmp_path / "r.fastq", tmp_path / "o.vnx", hashlib.sha256(c).hexdigest(), stage_counters=True)
    assert oc["outcome"] in ALLOWED
    rep = res.report if res is not None else err.details
    assert rep["clustering"]["status"] in ("ran", "not_run")


def test_near_duplicate_strands_never_merge_into_a_wrong_frame(tmp_path):
    """A pool made only of reads of near-identical strands (zero payloads), all mixed: every cluster frame the stage
    returns must be one of the true frames."""
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.recovery.cluster import ClusterConfig
    from vnxdna.recovery.cluster.stage import cluster_frames
    from .cluster_support import frame_key, noisy, truth_frames
    lay = PROFILES["v4-balanced"][0]
    _, strands = make_strands(tmp_path, 0, data=bytes(2500))
    rng = np.random.default_rng(32)
    reads = [noisy(rng, s, 0.01, 0.005, 0.015) for s in strands for _ in range(6)]
    reads = [reads[i] for i in rng.permutation(len(reads)).tolist()]
    frames, _ = cluster_frames(reads, lay, ClusterConfig())
    truth = truth_frames(lay, strands)
    assert all(frame_key(f) in truth for f in frames)


def test_random_reads_only_end_in_failure_with_no_frame(tmp_path):
    rng = np.random.default_rng(33)
    p = tmp_path / "rand.fastq"
    p.write_text("".join(f"@r{i}\n{''.join('ACGT'[x] for x in rng.integers(0, 4, 313))}\n+\n{'I' * 313}\n"
                         for i in range(400)))
    with pytest.raises(VNXError) as e:
        de.decode_reads(p, tmp_path / "o.vnx", de.DecodeOptions(read_clustering="fallback", profile="v4-balanced"))
    assert not (tmp_path / "o.vnx").exists()
    cl = e.value.details.get("clustering")
    if cl is not None:
        assert cl["frames_verified"] == 0
