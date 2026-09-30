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
