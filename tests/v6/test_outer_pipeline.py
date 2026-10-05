"""V6 outer code through the real pipeline: encode → controlled strand loss → decode → SHA-256-verified container.

Strand headers (``vnx4|tag|kind|group|symbol``) are used only by the tests to choose which strands to drop; the
decoder never sees them (reads are written with neutral names).
"""
from __future__ import annotations

import hashlib
import json
import struct
import zlib

import numpy as np
import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import config as cf
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.constraints import ConstraintConfig, iter_fasta
from vnxdna.v4.errors import VNXConfigurationError, VNXFormatError, VNXIntegrityError, VNXUnsupportedVersionError
from vnxdna.v4.frame import KIND_DATA, PROFILES, build_strands
from vnxdna.v6 import outer as ou
from vnxdna.v6.loss import LossConfig, apply_loss

SIZE = 90_000


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("v6")
    datagen.generate(d / "in.bin", SIZE, "random", 6101)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    return {"dir": d, "vnx": d / "a.vnx", "sha": hashlib.sha256((d / "a.vnx").read_bytes()).hexdigest()}


def _encode(arc, name, **kw):
    out = arc["dir"] / f"{name}.fasta"
    if not out.exists():
        en.encode_container(arc["vnx"], out, en.DNAOptions(**kw))
    recs = []
    for head, seq in iter_fasta(out):
        _, _, kind, g, s = head.split("|")
        recs.append(((int(kind), int(g), int(s)), seq))
    return out, recs


def _decode(arc, recs, name="r", **kw):
    path = arc["dir"] / f"{name}.fasta"
    with open(path, "w") as f:
        for i, (_, seq) in enumerate(recs):
            f.write(f">read{i}\n{seq}\n")
    out = arc["dir"] / f"{name}.out.vnx"
    try:
        res = de.decode_reads(path, out, de.DecodeOptions(**kw), overwrite=True)
    except VNXIntegrityError:
        return "INTEGRITY", None, False
    ok = res.status == "SUCCESS" and hashlib.sha256(out.read_bytes()).hexdigest() == arc["sha"]
    if res.status == "SUCCESS":
        assert ok, "false SUCCESS"
    return res.status, res.report, ok


def _drop(recs, pred):
    return [r for r in recs if not pred(r[0])]


# ---------------------------------------------------------------------------------------------------------------- compatibility
def test_default_encoding_identical_to_v5_release(tmp_path):
    """Golden SHA-256s produced by the released VNX-DNA 5.0.0 tree (6aef3f4) for the same input.

    Restated in V6 Phase 2.4 (founder-approved, V6_ARCHITECTURE §8 decision 1). 5.0.0 pinned the whole-container
    SHA-256 of the default archive and the strand SHA-256s of encoding that archive. From 6.0.0.dev0 the manifest names
    the producing software (``encoder.version``, ``extensions.vnx``), so the container differs in those informational
    fields only. Kept in full: (1) every non-manifest section of the default archive is pinned by digest and the
    manifest is pinned after normalising the informational fields; (2) encoding the 5.0.0 container bytes gives the
    5.0.0 strand SHA-256s: the 5.0.0 container is the stored fixture where one exists (tests/compat/test_byte_identity),
    and here it is the archive built with the 5.0.0 manifest fields (``writer_provenance=False``, version 5.0.0), whose
    whole SHA-256 must equal the 5.0.0 value.
    """
    import json

    from compat.test_byte_identity import container_sections, normalised_manifest
    datagen.generate(tmp_path / "g.bin", 50_000, "mixed", 6001)
    ar.build_archive([tmp_path / "g.bin"], tmp_path / "g.vnx", ar.ArchiveOptions())
    sec = container_sections((tmp_path / "g.vnx").read_bytes())
    assert {k: hashlib.sha256(sec[k]).hexdigest()[:16] for k in ("header", "body", "chunk_table", "file_table", "refs")} == \
        {"header": "fd0a1ad70dad8a7d", "body": "26f532edbde4b522", "chunk_table": "904e39642102ef9a",
         "file_table": "5cd96844a52e8299", "refs": "df3f619804a92fdb"}
    assert hashlib.sha256(json.dumps(normalised_manifest(sec["manifest"]), sort_keys=True).encode()).hexdigest() == \
        "8b8629001acd5763b0c69bf3cfa213dd2e5c078573b1ffa00f21c4c79d04eaaa"
    v5 = tmp_path / "g5.vnx"
    _build_with_5_0_0_manifest_fields([tmp_path / "g.bin"], v5)
    assert hashlib.sha256(v5.read_bytes()).hexdigest() == "6f2f30b416eacb541b6a879a7ae8ada8624eb27857ef98555a55dded65aced83"
    golden = {"v4-balanced": "214868de03a7c2b9b49cfa974a4a97bec22d182ea9f2ee61022eab00aaeda3ac",
              "v4-archival": "e031a2ea560b0893968ad741cfce227a0dedc20a5e22dfbe0a5c5dd118abcb80"}
    for prof, sha in golden.items():
        en.encode_container(v5, tmp_path / f"{prof}.fasta", en.DNAOptions(profile=prof))
        assert hashlib.sha256((tmp_path / f"{prof}.fasta").read_bytes()).hexdigest() == sha
        assert not en.DNAOptions(profile=prof).v6


def _build_with_5_0_0_manifest_fields(inputs, out):
    """The default archive exactly as 5.0.0 wrote it: encoder.version 5.0.0 and no writer-provenance block."""
    from vnxdna.v4 import container as ct
    old = ct.__version__
    ct.__version__ = "5.0.0"
    try:
        ar.build_archive(inputs, out, ar.ArchiveOptions(writer_provenance=False))     # 6.x: extensions.vnx is opt-out
    finally:
        ct.__version__ = old


def test_superblock_v1_bytes_unchanged_and_v2_roundtrip():
    lay = PROFILES["v4-balanced"][0]
    sb = en.Superblock("cauchy-rs", 64, 16, lay, "dense", 7, bytes(range(16)), 123_456, b"\x11" * 32, 1000, 49)
    raw = sb.pack()
    body = (b"VNX4SB" + bytes([1, 1]) + struct.pack(">HHHBBBB", 64, 16, 40, 16, 6, 3, 0) + struct.pack(">I", 7)
            + bytes(range(16)) + struct.pack(">Q", 123_456) + b"\x11" * 32 + struct.pack(">QI", 1000, 49) + b"\0\0")
    assert raw == body + struct.pack(">I", zlib.crc32(body))
    assert en.Superblock.unpack(raw) == sb and sb.total_groups == 49
    v2 = en.Superblock("cauchy-rs", 64, 16, lay, "dense", 0, bytes(range(16)), 123_456, b"\x11" * 32, 1000, 49,
                       2, 16, 2, "interleaved")
    back = en.Superblock.unpack(v2.pack())
    assert back == v2 and back.total_groups == 49 + 4 * 2
    assert back.geometry() == ou.Geometry(64, 16, 16, 2, 40, 123_456, "interleaved")


@pytest.mark.parametrize("patch", [(7, 2), (17, 1), (21, 9), (8, 255)])
def test_superblock_v2_invalid_fields_rejected(patch):
    lay = PROFILES["v4-balanced"][0]
    v2 = en.Superblock("cauchy-rs", 64, 16, lay, "dense", 0, bytes(16), 123_456, b"\x11" * 32, 1000, 49, 2, 16, 2, "sequential")
    raw = bytearray(v2.pack()[:-4])
    raw[patch[0]] = patch[1]                 # outer code id / distribution / strand order / K high byte (K + M > 256)
    raw = bytes(raw) + struct.pack(">I", zlib.crc32(bytes(raw)))
    with pytest.raises(VNXFormatError):
        en.Superblock.unpack(raw)


def test_unknown_superblock_version_still_refused():
    lay = PROFILES["v4-balanced"][0]
    raw = bytearray(en.Superblock("cauchy-rs", 64, 16, lay, "dense", 0, bytes(16), 100, b"\0" * 32, 16, 1).pack()[:-4])
    raw[6] = 3
    with pytest.raises(VNXUnsupportedVersionError):
        en.Superblock.unpack(bytes(raw) + struct.pack(">I", zlib.crc32(bytes(raw))))


def test_v6_sequential_row_only_data_strands_equal_v4(arc):
    _, v4 = _encode(arc, "v5")
    _, v6 = _encode(arc, "seq-rows", stripe_depth=8)
    assert [r for r in v4 if r[0][0] == KIND_DATA] == [r for r in v6 if r[0][0] == KIND_DATA]
    assert [r for r in v4 if r[0][0] != KIND_DATA] != [r for r in v6 if r[0][0] != KIND_DATA]   # superblock v2


@pytest.mark.parametrize("kw", [dict(), dict(stripe_depth=4, column_parity=2), dict(strand_order="interleaved"),
                                dict(stripe_depth=5, column_parity=1, strand_order="interleaved"), dict(outer_plan="adaptive")])
def test_lossless_roundtrip(arc, kw):
    name = "x-" + "-".join(f"{k}{v}" for k, v in kw.items())
    _, recs = _encode(arc, name, **kw)
    status, rep, ok = _decode(arc, recs, name)
    assert status == "SUCCESS" and ok
    if kw:
        assert rep["outer_v6"]["symbols_missing"] == 0 and rep["outer_v6"]["rows_failed_row_wise"] == 0
    else:
        assert "outer_v6" not in rep


def test_encoder_parallel_equals_serial(arc):
    a = arc["dir"] / "par1.fasta"
    b = arc["dir"] / "par3.fasta"
    kw = dict(stripe_depth=4, column_parity=2, strand_order="interleaved", groups_per_task=6)
    en.encode_container(arc["vnx"], a, en.DNAOptions(workers=1, **kw))
    en.encode_container(arc["vnx"], b, en.DNAOptions(workers=3, **kw))
    assert a.read_bytes() == b.read_bytes()


def test_interleaved_order_spreads_groups_and_superblock(arc):
    _, recs = _encode(arc, "inter", stripe_depth=5, column_parity=1, strand_order="interleaved")
    keys = [k for k, _ in recs]
    sb_pos = [i for i, k in enumerate(keys) if k[0] != KIND_DATA]
    assert sb_pos[0] > 0 and sb_pos[-1] < len(keys) - 1 and min(np.diff(sb_pos)) > 10
    data = [k for k in keys if k[0] == KIND_DATA]
    # consecutive data strands of a full stripe belong to different groups
    assert all(a[1] != b[1] for a, b in zip(data[:50], data[1:51]))


# ---------------------------------------------------------------------------------------------------------------- loss handling
def test_whole_rows_recovered_by_columns(arc):
    _, recs = _encode(arc, "cols", stripe_depth=4, column_parity=2)
    lost_rows = {1, 2}                                  # two whole data groups of stripe 0
    status, rep, ok = _decode(arc, _drop(recs, lambda k: k[0] == KIND_DATA and k[1] in lost_rows), "cols-a")
    assert status == "SUCCESS" and ok
    v6 = rep["outer_v6"]
    assert v6["rows_lost_entirely"] == 2 and v6["data_rows_recovered_by_columns"] == 2 and v6["data_rows_unrecovered"] == 0
    # the same loss without column parity is not decodable (and is never reported as SUCCESS)
    _, rows_only = _encode(arc, "v5")
    status, rep, ok = _decode(arc, _drop(rows_only, lambda k: k[0] == KIND_DATA and k[1] in lost_rows), "cols-b")
    assert status != "SUCCESS" and not ok


def test_more_lost_rows_than_column_parity_fails_closed(arc, tmp_path):
    _, recs = _encode(arc, "cols", stripe_depth=4, column_parity=2)
    status, rep, ok = _decode(arc, _drop(recs, lambda k: k[0] == KIND_DATA and k[1] in {0, 1, 2}), "cols-c")
    assert status in ("PARTIAL", "FAILURE") and not ok
    assert rep["outer_v6"]["data_rows_unrecovered"] >= 1 and rep["groups_failed"] >= 1


def test_column_parity_rows_lost_are_harmless(arc):
    _, recs = _encode(arc, "cols", stripe_depth=4, column_parity=2)
    geo = ou.Geometry(64, 16, 4, 2, 40, (arc["vnx"]).stat().st_size)
    par = set(range(geo.G, geo.total_groups))
    status, rep, ok = _decode(arc, _drop(recs, lambda k: k[0] == KIND_DATA and k[1] in par), "cols-d")
    assert status == "SUCCESS" and ok and rep["outer_v6"]["rows_lost_entirely"] == len(par)


def test_mixed_partial_rows_need_iteration(arc):
    """Every row of stripe 0 loses more than M symbols in a pattern only row/column iteration resolves."""
    _, recs = _encode(arc, "cols", stripe_depth=4, column_parity=2)
    rng = np.random.default_rng(12)
    drop = set()
    for g in range(4):                                  # 4 data rows: each loses 20 > M = 16 symbols
        for s in rng.choice(80, 20, replace=False).tolist():
            drop.add((g, s))
    status, rep, ok = _decode(arc, _drop(recs, lambda k: k[0] == KIND_DATA and (k[1], k[2]) in drop), "iter")
    pred_known = np.ones((6, 80), dtype=bool)
    for g, s in drop:
        pred_known[g, s] = False
    pred = ou.structural_decode(pred_known[None], 64, 16, 4, 2)[0][:4].all()
    assert pred and status == "SUCCESS" and ok and rep["outer_v6"]["data_rows_recovered_by_columns"] == 4


@pytest.mark.parametrize("seed", range(4))
def test_iid_loss_matches_structural_prediction(arc, seed):
    _, recs = _encode(arc, "cols", stripe_depth=4, column_parity=2)
    geo = ou.Geometry(64, 16, 4, 2, 40, arc["vnx"].stat().st_size).validate()
    rng = np.random.default_rng(100 + seed)
    keep = rng.random(len(recs)) >= 0.24
    kept = [r for r, k in zip(recs, keep.tolist()) if k or r[0][0] != KIND_DATA]   # superblock kept
    have = {(k[1], k[2]) for k, _ in kept if k[0] == KIND_DATA}
    decodable = True
    for s in range(geo.stripes):
        data, par = geo.stripe_rows(s)
        known = ou.stripe_padding(geo, s)
        for r, g in enumerate(data + par):
            ri = r if g < geo.G else geo.D + (g - geo.G - s * geo.Mc)
            for t in range(geo.symbols_of(g)):
                if (g, t) in have:
                    known[ri, geo.position(g, t)] = True
        decodable &= bool(ou.structural_decode(known[None], geo.K, geo.M, geo.D, geo.Mc)[0][: geo.D].all())
    status, _, ok = _decode(arc, kept, f"iid{seed}")
    assert (status == "SUCCESS") == decodable
    assert ok == decodable


def test_burst_interleaved_survives_where_sequential_fails(arc, tmp_path):
    seq, _ = _encode(arc, "v5")
    inter, _ = _encode(arc, "inter-rows", strand_order="interleaved")
    for path, expect in ((seq, False), (inter, True)):
        out = tmp_path / f"{path.stem}.lost.fasta"
        info = apply_loss(str(path), str(out), LossConfig(burst_count=1, burst_length=300, seed=5))
        assert info["lost"] == 300
        recs = [((0, 0, 0), s) for _, s in iter_fasta(out)]
        status, _, ok = _decode(arc, recs, f"burst-{path.stem}")
        assert ok == expect and (status == "SUCCESS") == expect


def test_random_access_recovers_selected_file_through_columns(arc, tmp_path):
    d = tmp_path / "ds"
    d.mkdir()
    rng = np.random.default_rng(2)
    for i in range(3):
        (d / f"f{i}.bin").write_bytes(rng.integers(0, 256, 30_000, dtype=np.uint8).tobytes())
    ar.build_archive([d], tmp_path / "m.vnx", ar.ArchiveOptions(chunk_size=8192))
    en.encode_container(tmp_path / "m.vnx", tmp_path / "m.fasta", en.DNAOptions(stripe_depth=4, column_parity=1))
    recs = [(tuple(int(x) for x in h.split("|")[2:]), s) for h, s in iter_fasta(tmp_path / "m.fasta")]
    reads = tmp_path / "m.reads.fasta"
    with open(reads, "w") as f:
        for i, (k, s) in enumerate(recs):
            if not (k[0] == KIND_DATA and k[1] == 1):            # group 1 (inside ds/f0.bin) lost entirely
                f.write(f">r{i}\n{s}\n")
    res = de.decode_reads(reads, None, de.DecodeOptions(), select=["ds/f0.bin"], select_dir=tmp_path / "sel")
    assert res.status == "SUCCESS"
    assert (tmp_path / "sel" / "ds" / "f0.bin").read_bytes() == (d / "f0.bin").read_bytes()
    assert res.report["outer_v6"]["data_rows_recovered_by_columns"] == 1


def test_wrong_verified_symbol_never_yields_success(arc):
    """A forged frame with a valid CRC but a wrong payload for a missing address must not produce a false SUCCESS."""
    _, recs = _encode(arc, "cols", stripe_depth=4, column_parity=2)
    lay = PROFILES["v4-balanced"][0]
    sb_tag = int(next(iter_fasta(arc["dir"] / "cols.fasta"))[0].split("|")[1], 16)
    kept = _drop(recs, lambda k: k[0] == KIND_DATA and k[1] in {1, 2})
    forged, _ = build_strands(lay, ConstraintConfig(), sb_tag, KIND_DATA, np.array([1]), np.array([5]),
                              np.full((1, 40), 0xA5, dtype=np.uint8))
    lut = np.frombuffer(b"ACGT", dtype=np.uint8)
    kept.append(((KIND_DATA, 1, 5), lut[forged[0]].tobytes().decode()))
    status, _, ok = _decode(arc, kept, "forged")
    assert status != "SUCCESS" and not ok


def test_smart_and_soft_decoding_work_on_v6_archives(arc):
    _, recs = _encode(arc, "cols", stripe_depth=4, column_parity=2)
    rng = np.random.default_rng(31)
    noisy = []
    for k, s in recs:
        b = bytearray(s.encode())
        if rng.random() < 0.3:
            i = int(rng.integers(20, len(b) - 20))
            del b[i]                                        # one deletion
        noisy.append((k, b.decode()))
    kept = _drop(noisy, lambda k: k[0] == KIND_DATA and k[1] == 3)
    status, rep, ok = _decode(arc, kept, "smart", indel_recovery="smart", soft_decoding="auto")
    assert status == "SUCCESS" and ok and rep["outer_v6"]["data_rows_recovered_by_columns"] == 1


# ---------------------------------------------------------------------------------------------------------------- options, config, CLI
@pytest.mark.parametrize("kw", [dict(outer_code="lt-fountain", experimental=True, stripe_depth=4),
                                dict(strand_order="zigzag"), dict(outer_plan="best"),
                                dict(outer_plan="adaptive", data_symbols=32), dict(redundancy_budget=0.3),
                                dict(outer_plan="adaptive", redundancy_budget=5.0), dict(stripe_depth=250, column_parity=7)])
def test_invalid_v6_options(arc, kw, tmp_path):
    with pytest.raises(VNXConfigurationError):
        en.encode_container(arc["vnx"], tmp_path / "x.fasta", en.DNAOptions(**kw))


def test_config_accepts_v6_keys():
    cfg = cf.validate_config({"dna": {"stripe_depth": 8, "column_parity": 2, "strand_order": "interleaved"}})
    opt = cf.dna_options(cfg)
    assert opt.v6 and opt.stripe_depth == 8 and opt.column_parity == 2
    with pytest.raises(VNXConfigurationError):
        cf.validate_config({"dna": {"column_parity_groups": 2}})


def test_adaptive_budget_respected(arc, tmp_path):
    rep = en.encode_container(arc["vnx"], tmp_path / "ad.fasta", en.DNAOptions(outer_plan="adaptive", redundancy_budget=0.3))
    assert rep["outer_v6"]["overhead"] <= 0.3 and rep["outer_v6"]["plan"]["mode"] == "adaptive"
    assert rep["outer_v6"]["order"] == "interleaved"


def test_cli_encode_v6(arc, tmp_path):
    from typer.testing import CliRunner
    from vnxdna.v4.cli import app
    out = tmp_path / "c.fasta"
    r = CliRunner().invoke(app, ["encode", str(arc["vnx"]), str(out), "--stripe-depth", "4", "--column-parity", "1",
                                 "--strand-order", "interleaved"])
    assert r.exit_code == 0, r.output
    rep = json.loads(r.output)
    assert rep["outer_v6"]["D"] == 4 and rep["outer_v6"]["Mc"] == 1
    r = CliRunner().invoke(app, ["decode", str(out), "-o", str(tmp_path / "c.vnx")])
    assert r.exit_code == 0, r.output
    assert hashlib.sha256((tmp_path / "c.vnx").read_bytes()).hexdigest() == arc["sha"]
