"""Round-trip, determinism, integrity and independence properties of the codec (V6 Phase 8; directive section 15).

Every decoder test also asserts the no-false-SUCCESS property: a decode reports SUCCESS only together with the exact original
container bytes; anything else is FAILURE/PARTIAL or a typed VNX error and publishes nothing.
SYNTHETIC SOFTWARE TEST data; the channel is SIMULATED. No DNA was synthesised or sequenced.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from vnxdna.archive import operations as ar
from vnxdna.core.errors import VNXError
from vnxdna.pipeline.decode import decode_reads
from vnxdna.pipeline.encode import DNAOptions, encode_container
from vnxdna.recovery.options import DecodeOptions
from vnxdna.simulation import channel as ch
from vnxdna.simulation.loss import LossConfig, loss_mask

PASS = "vnx-test-passphrase-NOT-A-SECRET"
SLOW = settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def make(tmp: Path, data: bytes, opts: ar.ArchiveOptions | None = None, dna: DNAOptions | None = None, name="f.bin"):
    (tmp / "ds").mkdir(exist_ok=True)
    (tmp / "ds" / name).write_bytes(data)
    c = tmp / "a.vnx"
    c.unlink(missing_ok=True)
    ar.build_archive([tmp / "ds"], c, opts or ar.ArchiveOptions())
    s = tmp / "s.fasta"
    encode_container(c, s, dna or DNAOptions(), overwrite=True)
    return c, s


def attempt(reads: Path, out: Path, **kw):
    out.unlink(missing_ok=True)
    try:
        return decode_reads(reads, out, DecodeOptions(), overwrite=True, **kw)
    except VNXError as e:
        return e


def assert_exact_or_nothing(res, out: Path, container: Path, *, must_succeed: bool):
    """No false SUCCESS: SUCCESS implies the exact original container; failure publishes nothing."""
    if isinstance(res, VNXError) or res.status != "SUCCESS":
        assert not must_succeed, res
        assert not out.exists()
    else:
        assert sha(out) == sha(container)


@given(data=st.binary(min_size=1, max_size=6000), profile=st.sampled_from(["v4-balanced", "v4-dense", "v4-indel", "v4-archival"]))
@SLOW
def test_clean_round_trip_for_any_input_and_profile(tmp_path_factory, data, profile):
    t = tmp_path_factory.mktemp("rt")
    c, s = make(t, data, dna=DNAOptions(profile=profile))
    res = attempt(s, t / "o.vnx")
    assert_exact_or_nothing(res, t / "o.vnx", c, must_succeed=True)


def test_empty_file_round_trips(tmp_path):
    c, s = make(tmp_path, b"")
    res = attempt(s, tmp_path / "o.vnx")
    assert_exact_or_nothing(res, tmp_path / "o.vnx", c, must_succeed=True)


@pytest.mark.parametrize("size", [1, 4095, 4096, 4097, 8192])      # chunk boundaries of the 4 KiB minimum chunk size
def test_chunk_boundaries_round_trip(tmp_path, size):
    c, s = make(tmp_path, (bytes(range(256)) * (size // 256 + 1))[:size], ar.ArchiveOptions(chunk_size=4096))
    res = attempt(s, tmp_path / "o.vnx")
    assert_exact_or_nothing(res, tmp_path / "o.vnx", c, must_succeed=True)


def test_encoding_and_decoding_are_deterministic_across_workers(tmp_path):
    data = np.random.default_rng(1).integers(0, 256, 5000, dtype=np.uint8).tobytes()
    outs = []
    for w in (1, 2):
        d = tmp_path / f"w{w}"
        d.mkdir()
        c, s = make(d, data, dna=DNAOptions(workers=w))
        o = d / "o.vnx"
        res = decode_reads(s, o, DecodeOptions(workers=w), overwrite=True)
        assert res.status == "SUCCESS"
        outs.append((sha(c), sha(s), sha(o)))
    assert outs[0] == outs[1] and outs[0][0] == outs[0][2]


@pytest.mark.parametrize("key", ["passphrase", "none"])
@pytest.mark.parametrize("compression", ["zstd", "none"])
def test_encryption_and_compression_round_trip_and_wrong_key_fails_closed(tmp_path, key, compression):
    data = (b"compressible text " * 400)[:5000]
    opts = ar.ArchiveOptions(compression=compression, passphrase=PASS if key == "passphrase" else None, chunk_size=4096)
    c, s = make(tmp_path, data, opts)
    kw = {"passphrase": PASS} if key == "passphrase" else {}
    res = attempt(s, tmp_path / "o.vnx", **kw)
    assert_exact_or_nothing(res, tmp_path / "o.vnx", c, must_succeed=True)
    ar.extract(tmp_path / "o.vnx", tmp_path / "x", overwrite=True, **kw)
    assert (tmp_path / "x" / "ds" / "f.bin").read_bytes() == data
    if key == "passphrase":
        bad = attempt(s, tmp_path / "bad.vnx", passphrase="wrong")
        assert isinstance(bad, VNXError) and bad.code == "WRONG_KEY" and not (tmp_path / "bad.vnx").exists()


@given(seed=st.integers(0, 10_000), dropout=st.sampled_from([0.0, 0.02, 0.05]))
@SLOW
def test_channel_within_capability_recovers_and_beyond_never_lies(tmp_path_factory, seed, dropout):
    """Dropout within the documented 20 % row budget and mild substitutions at coverage 3 recover; 85 % loss never gives
    wrong bytes (SIMULATED channel)."""
    t = tmp_path_factory.mktemp("ch")
    data = np.random.default_rng(seed).integers(0, 256, 3000, dtype=np.uint8).tobytes()
    c, s = make(t, data)
    seqs = [ln for ln in s.read_text().split() if not ln.startswith(">")]
    keep = loss_mask(len(seqs), LossConfig(dropout=dropout, seed=seed))
    (t / "kept.fasta").write_text("".join(f">r{i}\n{q}\n" for i, q in enumerate(q for q, k in zip(seqs, keep.tolist()) if k)))
    ch.simulate_file(t / "kept.fasta", t / "reads.fasta", ch.ChannelConfig(coverage=4.0, substitution_rate=0.002, seed=seed),
                     fmt="fasta", overwrite=True)
    res = attempt(t / "reads.fasta", t / "o.vnx")
    assert_exact_or_nothing(res, t / "o.vnx", c, must_succeed=True)
    heavy = loss_mask(len(seqs), LossConfig(dropout=0.85, seed=seed))
    (t / "heavy.fasta").write_text("".join(f">r{i}\n{q}\n" for i, q in enumerate(q for q, k in zip(seqs, heavy.tolist()) if k)))
    res = attempt(t / "heavy.fasta", t / "h.vnx")
    assert_exact_or_nothing(res, t / "h.vnx", c, must_succeed=False)


@given(pos=st.integers(0, 10_000), bit=st.integers(0, 7))
@SLOW
def test_any_single_bit_flip_in_a_container_is_detected_by_full_verification(tmp_path_factory, pos, bit):
    t = tmp_path_factory.mktemp("int")
    c, _ = make(t, b"integrity " * 600, ar.ArchiveOptions(chunk_size=4096))
    raw = bytearray(c.read_bytes())
    raw[pos % len(raw)] ^= 1 << bit
    bad = t / "bad.vnx"
    bad.write_bytes(bytes(raw))
    with pytest.raises(VNXError):
        ar.verify_container(bad)


def test_chunk_independence_and_random_access(tmp_path):
    data = np.random.default_rng(3).integers(0, 256, 3 * 4096, dtype=np.uint8).tobytes()
    c, _ = make(tmp_path, data, ar.ArchiveOptions(chunk_size=4096, compression="none"))
    ok = ar.verify_container(c, chunk=1)
    assert ok["status"] == "VERIFIED"
    raw = bytearray(c.read_bytes())
    raw[16 + 4096 * 2 + 5] ^= 1                              # damage chunk 2 only (stored uncompressed, body offset 16)
    bad = tmp_path / "bad.vnx"
    bad.write_bytes(bytes(raw))
    assert ar.verify_container(bad, chunk=0)["status"] == "VERIFIED"      # chunks 0 and 1 are unaffected
    assert ar.verify_container(bad, chunk=1)["status"] == "VERIFIED"
    with pytest.raises(VNXError):
        ar.verify_container(bad, chunk=2)
    with pytest.raises(VNXError):
        ar.verify_container(bad)


def test_older_goldens_are_read_and_the_generating_versions_are_answered():
    import json
    from vnxdna.archive import container as ct
    root = Path(__file__).resolve().parents[1] / "fixtures"
    for d in ("v4_0", "v5_0", "v6_0"):
        for p in sorted((root / d).glob("*.vnx")):
            c = ct.open_container(p, passphrase=PASS if "encrypted" in p.name else None)
            assert c.manifest["format_version"] == [4, 0]
            assert c.manifest["encoder"]["name"] == "vnxdna" and c.manifest["encoder"]["version"]
    assert json.loads((root / "v6_0" / "manifest.json").read_text())["cases"]
