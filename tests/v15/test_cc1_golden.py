"""V15 CC-1 (decode speed): every speed change must leave decoding bit-for-bit as in 14.0.0.

For a set of SIMULATED pools (clean, indel, reverse-complement, duplicates with strand bias, a harsh mixed channel, low
coverage, a zero-heavy payload that takes the strand retry rung, a shuffled pool), a full decode at 1 and 3 threads
must give exactly the 14.0.0 result:
- the outcome (status, or error type + code) and the SHA-256 of the output bytes;
- the SHA-256 of the decode report without its timing / RSS / thread-count fields;
- the SHA-256 of the streaming engine's per-read decision trace (every read's decision and address, every engine run).
The golden digests were recorded with the 14.0.0 tree (tag v14.0.0, 7369e09) by running this file as a script
(``PYTHONPATH=<v14.0.0 tree>/src python tests/v15/test_cc1_golden.py``). Results must also agree across thread counts.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from pathlib import Path

import pytest

from vnxdna.native import engine as ne
from vnxdna.simulation.channel import ChannelConfig, simulate_file
from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en

KIB = 1 << 10
INDEL = {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.002, "coverage": 5}
#: name -> (payload, size, archive options, channel parameters, seed)
CASES = {
    "clean-cov3": ("random", 256 * KIB, {}, {"coverage": 3}, 15100),
    "indel-cov5": ("random", 256 * KIB, {}, INDEL, 15500),
    "rc50-indel": ("random", 192 * KIB, {}, {**INDEL, "reverse_complement_rate": 0.5}, 15700),
    "dup-strandbias": ("random", 192 * KIB, {}, {**INDEL, "coverage": 5, "coverage_model": "poisson",
                                                  "duplication_rate": 0.3, "reverse_complement_rate": 0.85}, 15060),
    "mixed-harsh": ("random", 192 * KIB, {}, {"substitution_rate": 0.015, "insertion_rate": 0.004,
                                               "deletion_rate": 0.004, "coverage": 6, "coverage_model": "poisson",
                                               "homopolymer_indel_multiplier": 1.5, "n_rate": 0.001,
                                               "burst_rate": 0.002, "burst_max_len": 4}, 15001),
    "lowcov-indel": ("random", 128 * KIB, {}, {**INDEL, "coverage": 1.5, "coverage_model": "poisson"}, 15130),
    "zero-heavy-indel": ("zero-heavy", 256 * KIB, {"compression": "none", "dedup": False}, INDEL, 15080),
    "shuffled-indel": ("random", 192 * KIB, {}, {**INDEL, "shuffle_window": 1000000}, 15200),
}
THREADS = (1, 3)
VOLATILE = {"seconds", "stage_seconds", "peak_rss_bytes", "threads", "elapsed", "pass1_worker_cpu", "library_origin"}

#: recorded with 14.0.0 (7369e09): name -> (outcome, output sha256, normalised report sha256, engine trace sha256)
GOLDEN: dict[str, tuple] = {
    'clean-cov3': ('SUCCESS', '96db1888a82abf1519a8cb977213cfa2f2c1c633dc7b2f01e4907e401d3bd121', '8af416ad03a460705918f56778d8daf5d29c3cfda0a2ab46f6b5937829d39469', '498fbd930fdc37bb8721b3b20a1c9a68c7a67bc68a7332a3432deecbd6359dae'),
    'dup-strandbias': ('SUCCESS', '25f3ad3f2f85a6a2d6e16df03b4c0247d0dd77fe83be3ca5ef0d0810b15402be', '7dc5028a3f96457ffefae251cc95c3d8bc591ca3a8cd719a3583b780ef6d5d97', '2490014b41fb7b2c906f8d576428896ea666d81b0ee44419e4b98974d2ad5899'),
    'indel-cov5': ('SUCCESS', '6997939abef7bb4931abc7b0fc85d429c2c375a9f66722109dc013446e0ceef3', '04b12882f0d5f7c3f8bc8511fe6c52bbde8a3056cc693ab683fea296999aa9e2', 'aea60bb2d2aad25ded50a85abbfb42941408fb4e496aff8d0a812b1a28ae9e5a'),
    'lowcov-indel': ('FAILURE', None, 'a21f7272a19f3121359a6478c6c5c5177b803ee028c4a46526bf7767e2651907', '846689aeb224837d509593abe4ed47465ed64092a4d55cf661cb967d0b23a25b'),
    'mixed-harsh': ('SUCCESS', '4b42c143ab1ddaf2ab4be0b9c891e44ee1fed299e69ce02b68a352eb26d7a761', '66c4897e25be4da1a4fc628e826fd2c85d0d7d41f99ca27b03b43ebef5c66ce8', 'f816e3b91868821a7ff5f7205a500dbd01b7a34cc13f56a76eda4ddf27188437'),
    'rc50-indel': ('SUCCESS', '2cf333d6336aff8c1b7191df563e3433fdfb973f620d46aca9482f46059d9498', '1f93af3dd756d2a0ed3000ad2002425cc96e7751d4090ddd8e71a16799566950', '1ea33320ed26b415d1aae6a20ffd177c566f5f4a40aaefb3a847c0394b7a1850'),
    'shuffled-indel': ('SUCCESS', 'dd639fcb19a64c2680cf794551cff80712db9f1a2053b6db84acd6003de6da2b', 'f01d88d408cb96532d74adcc1d1395b8b55047dc2133d3adb56dc430cab505a6', 'aea705960109edd0641b7513b7406f9d4c0d3b5ba43e834f100fc82354b999d5'),
    'zero-heavy-indel': ('SUCCESS', '13787aa36281c48f260de8c4a286527416e83450a1a03a6218a5b22de6af0270', 'bbcacdfa94e4eb79f8bc8c06512b078d269c30fd3b25a786e20dd0ee7c0dc6d1', 'c0419b48fd7470f95c38e9ffc957a38526cbb33ea3496d651b7a6f7036b7c5e3'),
}


def normalize(o):
    if isinstance(o, dict):
        return {k: normalize(v) for k, v in o.items()
                if k not in VOLATILE and not k.startswith("ns_") and not k.endswith("_seconds")}
    if isinstance(o, list):
        return [normalize(v) for v in o]
    return o


def _zero_heavy(n: int, seed: int) -> bytes:
    rng = random.Random(seed)
    out = bytearray(n)
    for start in range(0, n, 1 << 16):
        if rng.random() < 0.5:
            continue
        end = min(n, start + (1 << 16))
        for _ in range(max(1, (end - start) // 50)):
            out[rng.randrange(start, end)] = rng.randrange(1, 256)
    return bytes(out)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build(d: Path, name: str) -> Path:
    payload, size, opts, channel, seed = CASES[name]
    d.mkdir(parents=True, exist_ok=True)
    if payload == "zero-heavy":
        (d / "in.bin").write_bytes(_zero_heavy(size, seed))
    else:
        datagen.generate(d / "in.bin", size, payload, seed)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions(**opts))
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    simulate_file(d / "s.fasta", d / "r.fastq", ChannelConfig(seed=seed + 7, **channel))
    return d


def digest(d: Path, threads: int) -> tuple:
    out = d / f"o{threads}.vnx"
    trace = d / f"trace{threads}.txt"
    trace.unlink(missing_ok=True)
    os.environ["VNX_ENG_TRACE"] = str(trace)
    try:
        res = de.decode_reads(d / "r.fastq", out, de.DecodeOptions(workers=threads))
        outcome, report = res.status, normalize(res.report)
    except Exception as error:  # noqa: BLE001 - an explicit failure is an outcome
        outcome, report = f"ERROR:{type(error).__name__}:{getattr(error, 'code', '')}", None
    finally:
        del os.environ["VNX_ENG_TRACE"]
    sha = _sha(out) if out.is_file() else None
    if outcome == "SUCCESS":
        assert sha == _sha(d / "a.vnx"), "SUCCESS with wrong bytes"
    rep = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
    tr = _sha(trace) if trace.is_file() else None
    out.unlink(missing_ok=True)
    return outcome, sha, rep, tr


pytestmark = pytest.mark.skipif(not ne.available(), reason="engine library not built (python -m vnxdna.native.engine build)")


@pytest.mark.parametrize("name", sorted(CASES))
def test_decode_identical_to_v14(tmp_path, name):
    d = build(tmp_path / name, name)
    got = {t: digest(d, t) for t in THREADS}
    assert got[THREADS[0]] == got[THREADS[1]], f"{name}: result depends on the thread count"
    assert got[THREADS[0]] == GOLDEN[name], f"{name}: differs from 14.0.0"


if __name__ == "__main__":         # record the golden digests (run with the 14.0.0 tree on PYTHONPATH)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(CASES):
            d = build(Path(tmp) / name, name)
            got = {t: digest(d, t) for t in THREADS}
            assert got[THREADS[0]] == got[THREADS[1]], name
            print(f"    {name!r}: {got[THREADS[0]]!r},", flush=True)
    sys.exit(0)
