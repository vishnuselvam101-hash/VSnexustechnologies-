"""V14 Phase 6 workstream F: configuration, SDK, CLI and boundary hardening (docs/V14_HARDENING.md).

One regression test per corrected defect (H-1 .. H-9 in docs/V14_HARDENING.md):

- H-1 DecodeOptions.validate rejects wrong types and out-of-range values with VNXConfigurationError (a JSON string
  "false" for a boolean, batch_reads 0, a float worker count, an archive tag outside 16 bits, ...);
- H-2 ``vnx decode --archive-tag`` with a non-hex value is a CONFIGURATION_ERROR (exit 2), not INTERNAL_ERROR (70);
- H-3 the config file's decode.workers / decode.batch_reads are honoured instead of being silently replaced by the
  performance profile's values;
- H-4 a result-cache hit verifies the published copy (and a report that does not match its container is a miss);
- H-5 a volume journal whose extents are not contiguous from 0 (or whose records are malformed) is refused at open
  instead of reading back shifted bytes with SUCCESS;
- H-6 a stream manifest with an unknown strands_format or an offset beyond any file is a typed VNXFormatError;
- H-7 stream encode/decode leave no work directory behind when they fail before their main loop;
- H-8 the native cluster polish_deltas wrapper refuses a non-monotonic read_start (heap over-read in C otherwise);
- H-9 a misspelled kernel backend variable (VNXDNA_*_BACKEND) is a CONFIGURATION_ERROR before any work starts (it was
  an INTERNAL_ERROR, a BrokenProcessPool for the aligner, or for the engine a silent fallback to the V13 path).

Every read here is SIMULATED.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from vnxdna.core.errors import VNXConfigurationError, VNXFormatError, VNXIntegrityError
from vnxdna.recovery.options import DecodeOptions

SRC = str(Path(__file__).resolve().parents[2] / "src")


# ------------------------------------------------------------------------------------------------------------ H-1
@pytest.mark.parametrize("kw", [
    {"batch_reads": 0}, {"batch_reads": -5}, {"batch_reads": "10"}, {"batch_reads": 2.5}, {"batch_reads": True},
    {"max_pending_per_address": 0}, {"max_pending_per_address": -1}, {"max_pending_per_address": 1.5},
    {"max_reads": 0}, {"max_reads": -1}, {"max_reads": "9"},
    {"archive_tag": -1}, {"archive_tag": 1 << 16}, {"archive_tag": "ab"}, {"archive_tag": True},
    {"workers": True}, {"workers": 2.5}, {"workers": "2"},
    {"band": 6.5}, {"band": "6"}, {"min_quality": 3.7}, {"min_quality": "3"},
    {"reverse_complement": "no"}, {"reverse_complement": "false"}, {"reverse_complement": 0},
    {"consensus_threshold": "0.6"}, {"retry_band": 8.0},
])
def test_h1_decode_options_reject_wrong_types_and_ranges(kw):
    with pytest.raises(VNXConfigurationError):
        DecodeOptions(**kw).validate()


@pytest.mark.parametrize("kw", [
    {}, {"batch_reads": 1}, {"batch_reads": np.int64(4096)}, {"max_pending_per_address": 1}, {"max_reads": 1},
    {"archive_tag": 0}, {"archive_tag": 0xFFFF}, {"archive_tag": np.uint16(0x91F7)}, {"workers": np.int32(3)},
    {"reverse_complement": False}, {"consensus_threshold": 1}, {"min_quality": 0},
])
def test_h1_decode_options_accept_valid_values(kw):
    DecodeOptions(**kw).validate()


# ------------------------------------------------------------------------------------------------------------ H-2
def test_h2_cli_archive_tag_not_hex_is_a_configuration_error(tmp_path):
    reads = tmp_path / "r.fastq"
    reads.write_text("@r\nACGT\n+\nIIII\n")
    env = {**os.environ, "PYTHONPATH": SRC}
    p = subprocess.run([sys.executable, "-c", "import sys; from vnxdna.commands import main; sys.argv[0]='vnx'; main()",
                        "decode", str(reads), "-o", str(tmp_path / "o.vnx"), "--archive-tag", "zz"],
                       capture_output=True, text=True, env=env, timeout=120)
    err = json.loads(p.stdout or p.stderr)
    assert err["code"] == "CONFIGURATION_ERROR", err
    assert p.returncode == err["exit_code"] != 70


def test_h2_archive_tag_parse_range():
    from vnxdna.sdk.config import decode_options_for
    assert decode_options_for(archive_tag="91f7").archive_tag == 0x91F7
    for bad in ("zz", "-1", "10000", ""):
        if bad == "":
            assert decode_options_for(archive_tag=bad).archive_tag is None
            continue
        with pytest.raises(VNXConfigurationError):
            decode_options_for(archive_tag=bad).validate()


# ------------------------------------------------------------------------------------------------------------ H-3
def test_h3_config_file_decode_workers_and_batch_reads_are_honoured(tmp_path):
    from vnxdna.sdk.config import decode_options_for
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"decode": {"batch_reads": 1234, "workers": 3}}))
    o = decode_options_for(cfg)
    assert (o.batch_reads, o.workers) == (1234, 3)
    o = decode_options_for(cfg, "safe")                     # a profile supplies defaults, the file's values win
    assert (o.batch_reads, o.workers) == (1234, 3)
    o = decode_options_for(cfg, workers=2)                  # an explicit flag wins over the file
    assert (o.batch_reads, o.workers) == (1234, 2)
    o = decode_options_for(None, "safe")                    # without a file: the profile's values
    assert (o.batch_reads, o.workers) == (4096, 1)


# ------------------------------------------------------------------------------------------------------------ H-4
@pytest.fixture(scope="module")
def small_pool(tmp_path_factory):
    from vnxdna import sdk
    from vnxdna.v4 import channel as ch
    d = tmp_path_factory.mktemp("pool")
    (d / "a.bin").write_bytes(np.random.default_rng(7).integers(0, 256, 6000, dtype=np.uint8).tobytes())
    sdk.archive([d / "a.bin"], d / "a.vnx")
    sdk.encode(d / "a.vnx", d / "a.fasta")
    ch.simulate_file(d / "a.fasta", d / "a.fastq", ch.ChannelConfig(coverage=6, seed=3))
    return d


def test_h4_cache_hit_verifies_the_published_copy(small_pool, tmp_path, monkeypatch):
    from vnxdna import sdk
    from vnxdna.sdk import api
    cdir = tmp_path / "cache"
    first = sdk.decode(small_pool / "a.fastq", tmp_path / "o1.vnx", cache_dir=cdir)
    assert first.status == "SUCCESS" and first.body["cache"]["stored"]
    real = shutil.copyfile

    def torn(src, dst, *a, **k):                         # a copy that does not reproduce its source
        real(src, dst, *a, **k)
        with open(dst, "r+b") as f:
            f.truncate(max(0, os.path.getsize(dst) - 1))
        return dst

    monkeypatch.setattr(api.shutil, "copyfile", torn)
    with pytest.raises(VNXIntegrityError):
        sdk.decode(small_pool / "a.fastq", tmp_path / "o2.vnx", cache_dir=cdir)
    assert not (tmp_path / "o2.vnx").exists()            # nothing published
    monkeypatch.setattr(api.shutil, "copyfile", real)
    hit = sdk.decode(small_pool / "a.fastq", tmp_path / "o3.vnx", cache_dir=cdir)
    assert hit.status == "SUCCESS" and hit.body["cache"]["hit"]
    assert (tmp_path / "o3.vnx").read_bytes() == (tmp_path / "o1.vnx").read_bytes()


def test_h4_cache_entry_whose_report_names_another_container_is_a_miss(small_pool, tmp_path):
    from vnxdna.sdk.cache import ResultCache
    from vnxdna import sdk
    cdir = tmp_path / "cache"
    first = sdk.decode(small_pool / "a.fastq", tmp_path / "o1.vnx", cache_dir=cdir)
    key = first.body["cache"]["key"]
    entry = cdir / key[:2] / key
    rep = json.loads((entry / "report.json").read_text())
    rep["container_sha256"] = "0" * 64
    (entry / "report.json").write_text(json.dumps(rep))
    assert ResultCache(cdir).lookup(key) is None


# ------------------------------------------------------------------------------------------------------------ H-5
E = 4096


def _volume(tmp_path, n_extents=3):
    from vnxdna import sdk
    from vnxdna.volume.journal import Journal
    data = np.random.default_rng(11).integers(0, 256, n_extents * E, dtype=np.uint8).tobytes()
    base = tmp_path / "vol"
    sdk.volume_create(base, extent_bytes=E)
    sdk.volume_write(base, data)
    j = Journal(base / "journal.log")
    j.replay()
    return base, data, [{k: v for k, v in r.items() if k != "seq"} for r in j.records]


def _rewrite_journal(base, records):
    from vnxdna.volume.journal import Journal
    (base / "journal.log").unlink()
    j = Journal(base / "journal.log")
    open(j.path, "xb").close()
    for r in records:
        j.append(r)


def test_h5_volume_with_a_hole_in_its_extents_is_refused(tmp_path):
    from vnxdna import sdk
    base, data, recs = _volume(tmp_path)
    assert sdk.volume_read(base, 0, len(data)).body["data"] == data
    _rewrite_journal(base, [r for r in recs if r.get("extent") != 1])
    with pytest.raises(VNXFormatError):
        sdk.volume_read(base, 0, len(data))


def test_h5_volume_with_a_far_extent_is_refused(tmp_path):
    from vnxdna import sdk
    base, data, recs = _volume(tmp_path)
    commits = [r for r in recs if r.get("op") == "commit"]
    _rewrite_journal(base, recs + [{**commits[-1], "extent": 2_000_000}])
    with pytest.raises(VNXFormatError):
        sdk.volume_read(base, 0, 2_000_001 * E)


def test_h5_short_extent_before_the_last_is_refused(tmp_path):
    from vnxdna import sdk
    base, data, recs = _volume(tmp_path)
    _rewrite_journal(base, [{**r, "length": 100} if r.get("extent") == 0 else r for r in recs])
    with pytest.raises(VNXFormatError):
        sdk.volume_stat(base)


@pytest.mark.parametrize("field,value", [("extent", None), ("extent", "x"), ("extent", True), ("extent", -1),
                                         ("length", "x"), ("length", E + 1), ("version", 0), ("file", 5),
                                         ("file", "/etc/hostname"), ("file", "../e0-v1.fasta"),
                                         ("archive_tag", "zz"), ("sha256", "nothex")])
def test_h5_malformed_commit_record_is_a_format_error(tmp_path, field, value):
    from vnxdna import sdk
    base, data, recs = _volume(tmp_path, n_extents=1)
    out = []
    for r in recs:
        if r.get("op") == "commit":
            r = dict(r)
            if value is None:
                r.pop(field)
            else:
                r[field] = value
        out.append(r)
    _rewrite_journal(base, out)
    for call in (lambda: sdk.volume_stat(base), lambda: sdk.volume_read(base, 0, E), lambda: sdk.volume_scrub(base)):
        with pytest.raises(VNXFormatError):
            call()


# ------------------------------------------------------------------------------------------------------------ H-6
def _stream(tmp_path):
    from vnxdna.pipeline import stream as st
    data = np.random.default_rng(5).integers(0, 256, 9000, dtype=np.uint8).tobytes()
    sink = tmp_path / "s.fasta"
    st.encode_stream(data, sink, options=st.StreamOptions(extent_bytes=4096))
    return data, sink, Path(str(sink) + ".stream.json")


def _rewrite_manifest(man, mutate):
    from vnxdna.core.util import canonical_json
    doc = json.loads(man.read_text())
    mutate(doc)
    man.write_bytes(canonical_json(doc) + b"\n")


@pytest.mark.parametrize("mutate", [
    lambda d: d.__setitem__("strands_format", "x/y"),
    lambda d: d.__setitem__("strands_format", 7),
    lambda d: d["extents"][0].__setitem__("strands_offset", 1 << 63),
    lambda d: d["extents"][0].__setitem__("strands_bytes", 1 << 63),
], ids=["format-path", "format-int", "offset-2^63", "bytes-2^63"])
def test_h6_stream_manifest_bad_format_or_offset_is_a_format_error(tmp_path, mutate):
    from vnxdna.pipeline import stream as st
    data, sink, man = _stream(tmp_path)
    _rewrite_manifest(man, mutate)
    for by_range in (None, True):
        with pytest.raises(VNXFormatError):
            st.decode_stream(sink, man, tmp_path / "out.bin", by_range=by_range, overwrite=True)
    assert not (tmp_path / "out.bin").exists()


# ------------------------------------------------------------------------------------------------------------ H-7
def test_h7_stream_encode_of_a_missing_source_leaves_no_work_dir(tmp_path):
    from vnxdna.pipeline import stream as st
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(OSError):
        st.encode_stream(tmp_path / "missing.bin", tmp_path / "s.fasta", workdir=work)
    assert list(work.iterdir()) == []


def test_h7_stream_decode_interrupted_in_pool_setup_leaves_no_work_dir(tmp_path, monkeypatch):
    from vnxdna.pipeline import decode as dc
    from vnxdna.pipeline import stream as st
    data, sink, man = _stream(tmp_path)
    work = tmp_path / "work"
    work.mkdir()

    class Interrupted:
        def __init__(self, *a, **k):
            raise KeyboardInterrupt

    monkeypatch.setattr(dc, "PoolDecoder", Interrupted)
    monkeypatch.setattr(dc, "pool_decode_refusal", lambda opt: None)
    with pytest.raises(KeyboardInterrupt):
        st.decode_stream(sink, man, tmp_path / "out.bin", by_range=False, workdir=work)
    assert list(work.iterdir()) == []


# ------------------------------------------------------------------------------------------------------------ H-8
def test_h8_polish_deltas_refuses_non_monotonic_read_start():
    from vnxdna.native import cluster as c
    T = 4
    args = (np.zeros((2, T), np.int16), np.zeros(T, np.int32), np.zeros(8, np.uint8), np.zeros(1, np.int64),
            np.zeros(1, np.int64))
    with pytest.raises(ValueError):
        c.polish_deltas(*args, np.array([0, 5, 1]), np.zeros(1, np.int64), np.zeros(1, np.uint8), 1, 1, 10, nthreads=1)


def test_h8_polish_deltas_valid_read_start_still_runs():
    from vnxdna.native import cluster as c
    T = 4
    d = c.polish_deltas(np.zeros((2, T), np.int16), np.zeros(T, np.int32), np.zeros(8, np.uint8),
                        np.zeros(1, np.int64), np.full(1, 4, np.int64), np.array([0, 1, 1]), np.full(1, 2, np.int64),
                        np.zeros(1, np.uint8), 1, 1, 10, nthreads=1)
    assert d[0].shape == (2, T, 4)


# ------------------------------------------------------------------------------------------------------------ H-9
BACKEND_VARS = ["VNXDNA_ALIGN_BACKEND", "VNXDNA_RS_BACKEND", "VNXDNA_READS_BACKEND", "VNXDNA_CLUSTER_BACKEND",
                "VNXDNA_STRAND_BACKEND", "VNXDNA_ORIENT_BACKEND", "VNXDNA_INTEGRITY_BACKEND", "VNXDNA_OUTERLIST_BACKEND",
                "VNXDNA_ENGINE_BACKEND"]


@pytest.mark.parametrize("var", BACKEND_VARS)
def test_h9_misspelled_backend_variable_is_a_configuration_error(var, monkeypatch):
    monkeypatch.setenv(var, "natve")
    with pytest.raises(VNXConfigurationError, match=var):
        DecodeOptions().validate()
    from vnxdna.pipeline.encode import DNAOptions
    with pytest.raises(VNXConfigurationError, match=var):
        DNAOptions().resolve()


@pytest.mark.parametrize("var,value", [("VNXDNA_ALIGN_BACKEND", "AVX2"), ("VNXDNA_RS_BACKEND", " scalar "),
                                       ("VNXDNA_READS_BACKEND", "reference"), ("VNXDNA_ENGINE_BACKEND", ""),
                                       ("VNXDNA_ORIENT_BACKEND", "auto")])
def test_h9_valid_backend_values_are_accepted(var, value, monkeypatch):
    monkeypatch.setenv(var, value)
    DecodeOptions().validate()


def test_h9_cli_decode_with_a_misspelled_backend_exits_2(small_pool, tmp_path):
    env = {**os.environ, "PYTHONPATH": SRC, "VNXDNA_ALIGN_BACKEND": "bogus"}
    p = subprocess.run([sys.executable, "-c", "import sys; from vnxdna.commands import main; sys.argv[0]='vnx'; main()",
                        "decode", str(small_pool / "a.fastq"), "-o", str(tmp_path / "o.vnx")],
                       capture_output=True, text=True, env=env, timeout=300)
    err = json.loads(p.stdout or p.stderr)
    assert err["code"] == "CONFIGURATION_ERROR" and "VNXDNA_ALIGN_BACKEND" in err["message"], err
    assert not (tmp_path / "o.vnx").exists()
