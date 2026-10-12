"""Streaming, bounded memory, generator, measurement harness and large-file corruption logic (at test scale)."""
import sys

import pytest

from v2_support import FAST, mixed_bytes, write
from vnxdna.errors import ConfigurationError, InsufficientRedundancyError
from vnxdna.v2 import api
from vnxdna.v2.archive import store_file
from vnxdna.v2.encoder import encode_file
from vnxdna.v2.scale import damage_vxs, generate_file, parse_size, run_measured


def test_parse_size_units():
    assert parse_size("10GB") == 10 * 10**9 and parse_size("64MiB") == 64 * 2**20 and parse_size("1.5MB") == 1_500_000
    assert parse_size("123") == 123
    with pytest.raises(ConfigurationError):
        parse_size("ten gigabytes")


@pytest.mark.parametrize("pattern", ["random", "compressible", "mixed", "structured"])
def test_generator_is_a_pure_function_of_size_pattern_and_seed(tmp_path, pattern):
    a = generate_file(tmp_path / "a.bin", 9_000_000, pattern, 7)
    again = generate_file(tmp_path / "again.bin", 9_000_000, pattern, 7)
    b = generate_file(tmp_path / "b.bin", 5_000_000, pattern, 7)
    other = generate_file(tmp_path / "other.bin", 9_000_000, pattern, 8)
    assert a["size"] == 9_000_000 and b["size"] == 5_000_000
    assert a["sha256"] == again["sha256"] != other["sha256"]
    full = 4 << 20  # full leading blocks are shared between sizes
    assert (tmp_path / "a.bin").read_bytes()[:full] == (tmp_path / "b.bin").read_bytes()[:full]


def test_generator_patterns_differ_in_compressibility(tmp_path):
    import zlib
    ratios = {}
    for pattern in ("random", "compressible", "structured"):
        generate_file(tmp_path / f"{pattern}.bin", 1_000_000, pattern, 1)
        data = (tmp_path / f"{pattern}.bin").read_bytes()
        ratios[pattern] = len(zlib.compress(data, 6)) / len(data)
    assert ratios["random"] > 0.99 and ratios["compressible"] < 0.5 and ratios["structured"] < 0.9


def test_run_measured_reports_process_tree_resources(tmp_path):
    r = run_measured([sys.executable, "-c", "import time; b = bytearray(80 * 2**20); time.sleep(0.6); print('{\"ok\": 1}')"],
                     watch_dir=tmp_path)
    assert r["returncode"] == 0 and r["report"] == {"ok": 1}
    assert r["peak_rss_tree_bytes"] > 60 * 2**20 and r["wall_s"] >= 0.5 and r["cpu_user_s"] >= 0


def test_large_file_corruption_logic_within_and_beyond_the_guarantee(tmp_path):
    data = mixed_bytes(120_000, seed=61)
    write(tmp_path / "in.bin", data)
    store_file(tmp_path / "in.bin", tmp_path / "a.vxdna", options=FAST)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.vxs")
    truth = damage_vxs(tmp_path / "s.vxs", tmp_path / "s.vxs.vxidx", tmp_path / "within.vxs", groups_within=5, groups_beyond=0,
                       seed=1, substitutions_per_group=3)
    assert all(g["within_guarantee"] for g in truth["damaged_groups"])
    r = api.recover(tmp_path / "within.vxs", tmp_path / "o.bin")
    assert (tmp_path / "o.bin").read_bytes() == data and r["recovery"]["max_erasures_in_a_group"] == FAST.parity_shards
    truth = damage_vxs(tmp_path / "s.vxs", tmp_path / "s.vxs.vxidx", tmp_path / "beyond.vxs", groups_within=2, groups_beyond=1, seed=2)
    beyond = [g for g in truth["damaged_groups"] if not g["within_guarantee"]]
    with pytest.raises(InsufficientRedundancyError) as info:
        api.recover(tmp_path / "beyond.vxs", tmp_path / "x.bin")
    assert info.value.details["chunk"] == beyond[0]["chunk"]
    assert not (tmp_path / "x.bin").exists()
    v = api.verify(tmp_path / "beyond.vxs")
    assert [d["chunk"] for d in v["damaged_chunks"]] == [beyond[0]["chunk"]]


@pytest.mark.slow
def test_peak_memory_does_not_grow_with_file_size(tmp_path):
    """Store → encode (VXS) → recover of 8 MB and 64 MB with one worker: peak RSS must not scale with the input."""
    peaks = {}
    for size in (8_000_000, 64_000_000):
        generate_file(tmp_path / f"{size}.bin", size, "random", 3)
        stage_peaks = []
        for args in (["store", f"{size}.bin", "-o", f"{size}.vxdna", "--workers", "1"],
                     ["encode", f"{size}.vxdna", "-o", f"{size}.vxs", "--workers", "1"],
                     ["recover", f"{size}.vxs", "-o", f"{size}.out", "--workers", "1", "--temp-dir", str(tmp_path)]):
            r = run_measured([sys.executable, "-m", "vnxdna", *[str(tmp_path / a) if a.startswith(str(size)) else a for a in args]],
                             watch_dir=tmp_path)
            assert r["returncode"] == 0, r["stderr_tail"]
            stage_peaks.append(r["peak_rss_tree_bytes"])
        assert (tmp_path / f"{size}.out").read_bytes() == (tmp_path / f"{size}.bin").read_bytes()
        peaks[size] = stage_peaks
    for small, large in zip(peaks[8_000_000], peaks[64_000_000]):
        assert large < small * 1.25 + 48 * 2**20, peaks


_DNA_STAGE = r"""
import json, resource, sys
from pathlib import Path
from vnxdna import sdk
from vnxdna.archive.operations import ArchiveOptions
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.recovery.options import DecodeOptions
stage, a, b, wd = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
with open("/proc/self/statm") as f:
    base = int(f.read().split()[1]) * 4096
if stage == "encode":
    r = sdk.encode([a], b, dna=DNAOptions(profile="v4-balanced", workers=1), archive_options=ArchiveOptions(workers=1),
                   allow_unencrypted=True)
else:
    import tempfile
    from vnxdna.recovery import spill
    tempfile.tempdir = wd
    spill.READS_PER_BUCKET = int(sys.argv[5])        # small buckets: the pass-2 bound is reached at test scale
    r = sdk.decode(a, b, options=DecodeOptions(workers=1), allow_unencrypted=True)
print(json.dumps({"status": str(r.status), "growth": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 - base}))
"""


@pytest.mark.slow
def test_dna_pipeline_peak_memory_does_not_grow_with_input_size(tmp_path):
    """V11 WP3 memory regression guard for the DNA pipeline (sdk.encode -> sdk.decode of the strands as reads, one
    worker, each stage in a fresh process). With spill buckets of 20 000 reads (production: 200 000) both sizes are past
    the one-bucket bound, so the peak-RSS growth over the post-import baseline must not grow from 1 MiB to 4 MiB
    (v10.0.0 and V11 measure about 13 MiB encode and 50-59 MiB decode at 1, 4 and 8 MiB; experiments/v11/mem); the
    decode leaves no spill files behind."""
    import json
    import os
    import random
    import subprocess

    import vnxdna
    env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(vnxdna.__file__)))
    wd = tmp_path / "spill"
    wd.mkdir()
    growth: dict = {}
    for size in (1 << 20, 4 << 20):
        src = tmp_path / f"{size}.bin"
        src.write_bytes(random.Random(size).randbytes(size))
        for stage, a, b in (("encode", src, tmp_path / f"{size}.fasta"), ("decode", tmp_path / f"{size}.fasta", tmp_path / f"{size}.vnx")):
            p = subprocess.run([sys.executable, "-c", _DNA_STAGE, stage, str(a), str(b), str(wd), "20000"],
                               capture_output=True, text=True, env=env, timeout=600, check=False)
            assert p.returncode == 0, p.stderr[-800:]
            r = json.loads(p.stdout.strip().splitlines()[-1])
            assert r["status"].endswith("SUCCESS"), r
            growth[(stage, size)] = r["growth"]
    for stage in ("encode", "decode"):
        assert growth[(stage, 4 << 20)] < growth[(stage, 1 << 20)] * 1.25 + 24 * 2**20, growth
    assert list(wd.iterdir()) == []


def test_pass2_memory_is_bounded_by_two_spill_blocks(tmp_path, monkeypatch):
    """Pass 2 (records → chunks) holds about two spill blocks (the one being read and the carried tail of the previous).

    Before the fix, gathering a chunk's records kept the raw block, a filtered copy, the previous joined array, the
    ``np.concatenate`` result and its big-endian ``astype`` copy alive together, about five blocks; so the recover
    peak stepped up once the input needed more than one spill block (the 8 MB / 64 MB CI peak test).
    """
    import random
    import tracemalloc

    from vnxdna.v2 import decoder

    write(tmp_path / "in.bin", random.Random(5).randbytes(600_000))
    store_file(tmp_path / "in.bin", tmp_path / "a.vxdna", options=FAST)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.vxs")
    monkeypatch.setattr(decoder, "SPILL_BLOCK_RECORDS", 8192)
    block_bytes = 8192 * decoder.record_dtype(FAST.payload_bytes).itemsize
    real, peak = decoder.assemble_chunks, {}

    def measured(*args, **kwargs):
        tracemalloc.reset_peak()
        start = tracemalloc.get_traced_memory()[0]
        yield from real(*args, **kwargs)
        peak["pass2"] = tracemalloc.get_traced_memory()[1] - start

    monkeypatch.setattr(decoder, "assemble_chunks", measured)
    tracemalloc.start()
    try:
        report = api.recover(tmp_path / "s.vxs", tmp_path / "o.bin", workers=1)
    finally:
        tracemalloc.stop()
    assert (tmp_path / "o.bin").read_bytes() == (tmp_path / "in.bin").read_bytes()
    assert report["recovery"]["pass2_mode"] == "sequential"
    assert report["validated_records_spilled"] > 4 * 8192  # several spill blocks
    assert peak["pass2"] < 3 * block_bytes, (peak, block_bytes)  # was about 5.2 blocks
