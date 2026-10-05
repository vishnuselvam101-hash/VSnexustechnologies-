"""Security-audit regressions (V6 Phase 1, item 22): event/report file handling, CLI outcome events, bounded stripe
recovery, encryption-downgrade refusal, forged superblocks and scrypt parameter caps.

Strand headers (``vnx4|tag|kind|group|symbol``) are used only by the tests to choose which strands to drop or forge.
"""
from __future__ import annotations

import json
import os
import struct
import tracemalloc
import zlib

import numpy as np
import pytest
from typer.testing import CliRunner

from vnxdna.v4 import archive as ar
from vnxdna.v4 import container as ct
from vnxdna.v4 import crypto
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.cli import app
from vnxdna.v4.codecs import CauchyRSCodec
from vnxdna.v4.constraints import iter_fasta
from vnxdna.v4.errors import VNXConfigurationError, VNXFormatError, VNXKeyError
from vnxdna.v4.frame import KIND_SUPER, build_strands
from vnxdna.v6 import outer as ou
from vnxdna.v6.decode import StripeRecovery
from vnxdna.v6.observe import JsonlObserver


# ---------------------------------------------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    """A plain archive and an encrypted (key file) archive, each with error-free strands used directly as reads."""
    d = tmp_path_factory.mktemp("sec")
    (d / "in").mkdir()
    datagen.generate(d / "in" / "a.bin", 12_000, "random", 2201)
    datagen.generate(d / "in" / "b.bin", 9_000, "random", 2202)
    ar.build_archive([d / "in"], d / "plain.vnx", ar.ArchiveOptions(chunk_size=4096))
    en.encode_container(d / "plain.vnx", d / "plain.fasta", en.DNAOptions())
    crypto.generate_key_file(d / "k.key")
    crypto.generate_key_file(d / "other.key")
    key = crypto.load_key_file(d / "k.key")
    ar.build_archive([d / "in"], d / "enc.vnx", ar.ArchiveOptions(chunk_size=4096, key=key))
    en.encode_container(d / "enc.vnx", d / "enc.fasta", en.DNAOptions())
    return d


def _cli(*args, env=None):
    return CliRunner().invoke(app, [str(a) for a in args], env=env)


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


# ---------------------------------------------------------------------------------------------------------------- V6-1
def test_events_refuses_symlink(arc, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n")
    link = tmp_path / "ev.jsonl"
    link.symlink_to(victim)
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--events", link)
    assert r.exit_code == 7, r.output
    assert victim.read_text() == "keep me\n"
    assert not (tmp_path / "o.vnx").exists()
    with pytest.raises(VNXConfigurationError):
        JsonlObserver(link)


@pytest.mark.parametrize("which", ["reads", "key", "output", "report", "hardlink-to-reads"])
def test_events_refuses_an_input_or_output_file(arc, tmp_path, which):
    reads = tmp_path / "r.fasta"
    reads.write_bytes((arc / "enc.fasta").read_bytes())
    key = tmp_path / "k.key"
    key.write_bytes((arc / "k.key").read_bytes())
    out = tmp_path / "o.vnx"
    rep = tmp_path / "rep.json"
    if which == "output":
        out.write_bytes(b"old output")
    if which == "report":
        rep.write_text("{}\n")
    if which == "hardlink-to-reads":
        os.link(reads, tmp_path / "alias.fasta")
    target = {"reads": reads, "key": key, "output": out, "report": rep,
              "hardlink-to-reads": tmp_path / "alias.fasta"}[which]
    before = target.read_bytes()
    r = _cli("decode", reads, "-o", out, "--force", "--key-file", key, "--report", rep, "--events", target)
    assert r.exit_code == 7, r.output
    assert target.read_bytes() == before


def test_events_refuses_non_regular_file(arc, tmp_path):
    fifo = tmp_path / "ev.fifo"
    os.mkfifo(fifo)
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--events", fifo)
    assert r.exit_code == 7, r.output


def test_events_new_file_mode_0600_and_append(arc, tmp_path):
    ev = tmp_path / "ev.jsonl"
    assert _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--events", ev).exit_code == 0
    n = len(_lines(ev))
    assert (ev.stat().st_mode & 0o777) == 0o600
    assert _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o2.vnx", "--events", ev).exit_code == 0
    assert len(_lines(ev)) == 2 * n                       # an existing regular events file is appended to


# ---------------------------------------------------------------------------------------------------------------- V6-2
def test_cli_events_record_extract_failure(arc, tmp_path):
    ev = tmp_path / "ev.jsonl"
    r = _cli("decode", arc / "enc.fasta", "-o", tmp_path / "o.vnx", "--extract", tmp_path / "x",
             "--key-file", arc / "other.key", "--events", ev, "--task-id", "job9")
    assert r.exit_code == 4, r.output
    lines = _lines(ev)
    names = [e["event"] for e in lines]
    # V6-SEC-02: the decoder itself now checks the key before SUCCESS, so the wrong key fails the decode (nothing is
    # published) instead of the extraction after a SUCCESS decode
    assert "decode_end" not in names and not (tmp_path / "o.vnx").exists()
    err = [e for e in lines if e["event"] == "error"]
    assert err and err[-1]["error_class"] == "VNXKeyError"
    end = lines[-1]
    assert end["event"] == "command_end" and end["exit_code"] == 4 and end["status"] == "FAILED"
    assert end["error_class"] == "VNXKeyError" and all(e["task_id"] == "job9" for e in lines)
    # an extraction that fails after a SUCCESS decode is still recorded (an existing file without --force: exit 8)
    ev2 = tmp_path / "ev2.jsonl"
    (tmp_path / "x2" / "in").mkdir(parents=True)
    (tmp_path / "x2" / "in" / "a.bin").write_bytes(b"existing")
    r = _cli("decode", arc / "enc.fasta", "-o", tmp_path / "o2.vnx", "--extract", tmp_path / "x2",
             "--key-file", arc / "k.key", "--events", ev2, "--task-id", "job9")
    assert r.exit_code == 8, r.output
    lines = _lines(ev2)
    assert next(e for e in lines if e["event"] == "decode_end")["status"] == "SUCCESS"
    err = [e for e in lines if e["event"] == "error"]
    assert err and err[-1]["error_class"] == "VNXOutputError"
    end = lines[-1]
    assert end["event"] == "command_end" and end["exit_code"] == 8 and end["status"] == "FAILED"
    assert (tmp_path / "x2" / "in" / "a.bin").read_bytes() == b"existing"


def test_cli_events_command_end_on_success(arc, tmp_path):
    ev = tmp_path / "ev.jsonl"
    r = _cli("decode", arc / "enc.fasta", "-o", tmp_path / "o.vnx", "--extract", tmp_path / "x",
             "--key-file", arc / "k.key", "--events", ev)
    assert r.exit_code == 0, r.output
    lines = _lines(ev)
    assert [e["event"] for e in lines][-2:] == ["decode_end", "command_end"]
    assert not any(e["event"] == "error" for e in lines)
    end = lines[-1]
    assert end["exit_code"] == 0 and end["status"] == "SUCCESS" and end["extract"]["files"] == 2


def test_cli_events_error_not_duplicated(arc, tmp_path):
    ev = tmp_path / "ev.jsonl"
    empty = tmp_path / "empty.fastq"
    empty.write_text("")
    r = _cli("decode", empty, "-o", tmp_path / "o.vnx", "--events", ev)
    assert r.exit_code != 0
    lines = _lines(ev)
    assert sum(e["event"] == "error" for e in lines) == 1
    assert lines[-1]["event"] == "command_end" and lines[-1]["exit_code"] == r.exit_code


# ---------------------------------------------------------------------------------------------------------------- V6-3 / V6-4
def _geo(D, Mc, G=40, K=4, M=2, P=50):
    return ou.Geometry(K, M, D, Mc, P, G * K * P - 7).validate()


def test_no_column_parity_finish_does_not_assemble_the_stripe(tmp_path):
    geo = ou.Geometry(4, 2, 65535, 0, 200, 3000 * 4 * 200).validate()      # one stripe of 3000 data rows
    rec = StripeRecovery(geo)
    fd = os.open(tmp_path / "c.bin", os.O_RDWR | os.O_CREAT, 0o600)
    calls = []
    try:
        done = set(range(geo.G))
        failed: dict = {}
        rec.row_failed(7, {0: np.zeros(200, np.uint8)}, "too few symbols")
        done.add(7)
        tracemalloc.start()
        written = rec.finish(fd, lambda todo, d: calls.append(set(todo)), done, failed,
                             admit=lambda s: calls.append(("admit", s)) or True)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    finally:
        os.close(fd)
    assert written == 0 and set(failed) == {7} and "no column parity" in failed[7]
    assert peak < 4 << 20, peak                       # the (D + Mc) × (K + M) × P array was 157 MB here
    assert calls == []                                 # neither the other rows nor the stripe budget were touched
    r = rec.report()
    assert r["rows_failed_row_wise"] == 1 and r["data_rows_unrecovered"] == 1 and r["rows_recovered_by_columns"] == 0
    assert r["stripes_attempted"] == 0


def test_no_column_parity_selective_run_is_not_widened(tmp_path):
    geo = _geo(D=40, Mc=0)
    rec = StripeRecovery(geo)
    fd = os.open(tmp_path / "c.bin", os.O_RDWR | os.O_CREAT, 0o600)
    calls = []
    try:
        rec.row_failed(3, {}, "lost")
        rec.finish(fd, lambda todo, d: calls.append(todo), {3}, {})
    finally:
        os.close(fd)
    assert calls == []


def test_finish_checkpoint_per_stripe(tmp_path):
    geo = _geo(D=4, Mc=2)
    rec = StripeRecovery(geo)
    for g in (1, 5, 9):                                # three different stripes
        rec.row_failed(g, {}, "lost")
    ticks = []

    def checkpoint():
        ticks.append(1)
        if len(ticks) == 2:
            raise RuntimeError("budget")
    fd = os.open(tmp_path / "c.bin", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with pytest.raises(RuntimeError, match="budget"):
            rec.finish(fd, lambda todo, d: d.update(todo), set(range(geo.total_groups)), {}, checkpoint=checkpoint)
    finally:
        os.close(fd)
    assert len(ticks) == 2


def test_failed_row_is_not_reprocessed_by_a_later_finish(tmp_path):
    geo = _geo(D=4, Mc=1)
    rec = StripeRecovery(geo)
    fd = os.open(tmp_path / "c.bin", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        done = set(range(geo.total_groups))
        failed: dict = {}
        rec.row_failed(0, {}, "lost")
        rec.row_failed(1, {}, "lost")                  # two rows lost in a stripe with one column-parity row
        rec.finish(fd, lambda todo, d: None, done, failed)
        assert set(failed) == {0, 1}
        rec.finish(fd, lambda todo, d: None, done, failed)   # selective decode calls finish again
        rec.finish(fd, lambda todo, d: None, done, failed)
    finally:
        os.close(fd)
    r = rec.report()
    assert rec.pending == {}
    assert r["rows_failed_row_wise"] == 2 and r["data_rows_unrecovered"] == 2 and r["rows_recovered_by_columns"] == 0
    assert r["stripes_attempted"] == 1
    assert not set(rec.recovered) & set(failed)


def test_unrecovered_parity_rows_counted_once(tmp_path):
    geo = _geo(D=4, Mc=2)
    rec = StripeRecovery(geo)
    par = geo.stripe_rows(0)[1]
    fd = os.open(tmp_path / "c.bin", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        failed: dict = {}
        for g in (0, 1, 2, *par):
            rec.row_failed(g, {}, "lost")
        rec.finish(fd, lambda todo, d: None, set(range(geo.total_groups)), failed)
        rec.finish(fd, lambda todo, d: None, set(range(geo.total_groups)), failed)
    finally:
        os.close(fd)
    r = rec.report()
    assert r["rows_failed_row_wise"] == 5 and r["data_rows_unrecovered"] == 3 and r["stripes_attempted"] == 1


def test_decoder_interleaved_without_column_parity_reports_failed_group(arc, tmp_path):
    """Superblock v2 with Mc = 0 (automatic depth: one stripe of every group): a lost group is reported, not assembled."""
    s = tmp_path / "s.fasta"
    en.encode_container(arc / "plain.vnx", s, en.DNAOptions(strand_order="interleaved"))
    reads = tmp_path / "r.fasta"
    with open(reads, "w") as f:
        for i, (head, seq) in enumerate(iter_fasta(s)):
            _, _, kind, g, _ = head.split("|")
            if not (int(kind) != KIND_SUPER and int(g) == 1):
                f.write(f">r{i}\n{seq}\n")
    res = de.decode_reads(reads, tmp_path / "o.vnx", de.DecodeOptions())
    assert res.status in ("PARTIAL", "FAILURE")
    assert res.report["failed_groups"] == [1]
    v6 = res.report["outer_v6"]
    assert v6["geometry"]["Mc"] == 0 and v6["rows_failed_row_wise"] == 1 and v6["data_rows_unrecovered"] == 1
    assert not (tmp_path / "o.vnx").exists()


def test_decoder_passes_checkpoint_to_finish(arc, tmp_path, monkeypatch):
    seen = {}
    orig = StripeRecovery.finish

    def spy(self, *a, **kw):
        seen.update(kw)
        return orig(self, *a, **kw)
    monkeypatch.setattr(StripeRecovery, "finish", spy)
    s = tmp_path / "s.fasta"
    en.encode_container(arc / "plain.vnx", s, en.DNAOptions(stripe_depth=4, column_parity=2))
    reads = tmp_path / "r.fasta"
    with open(reads, "w") as f:
        for i, (head, seq) in enumerate(iter_fasta(s)):
            _, _, kind, g, _ = head.split("|")
            if not (int(kind) != KIND_SUPER and int(g) == 2):
                f.write(f">r{i}\n{seq}\n")
    res = de.decode_reads(reads, tmp_path / "o.vnx", de.DecodeOptions())
    assert res.status == "SUCCESS"
    assert callable(seen.get("checkpoint")) and callable(seen.get("admit"))
    assert (tmp_path / "o.vnx").read_bytes() == (arc / "plain.vnx").read_bytes()


# ---------------------------------------------------------------------------------------------------------------- P-1
def test_api_refuses_key_for_unencrypted_archive(arc, tmp_path):
    key = crypto.load_key_file(arc / "k.key")
    with pytest.raises(VNXKeyError, match="not encrypted"):
        ar.extract(arc / "plain.vnx", tmp_path / "x", key=key)
    assert not (tmp_path / "x").exists() or not any((tmp_path / "x").rglob("*.bin"))
    with pytest.raises(VNXKeyError):
        ar.extract(arc / "plain.vnx", tmp_path / "y", passphrase="pw")
    with pytest.raises(VNXKeyError):
        ar.verify_container(arc / "plain.vnx", key=key)
    with pytest.raises(VNXKeyError):
        ar.list_container(arc / "plain.vnx", key=key)
    with pytest.raises(VNXKeyError):
        ct.open_container(arc / "plain.vnx", key=key)
    res = ar.extract(arc / "plain.vnx", tmp_path / "z", key=key, allow_unencrypted=True)
    assert res["files"] == 2
    assert ar.verify_container(arc / "plain.vnx", key=key, allow_unencrypted=True)["status"] == "VERIFIED"
    assert ar.extract(arc / "plain.vnx", tmp_path / "w")["files"] == 2      # no key: unchanged
    assert ar.extract(arc / "enc.vnx", tmp_path / "e", key=key)["files"] == 2


def test_cli_refuses_key_for_unencrypted_archive(arc, tmp_path):
    k = arc / "k.key"
    assert _cli("extract", arc / "plain.vnx", tmp_path / "x", "--key-file", k).exit_code == 4
    assert _cli("verify", arc / "plain.vnx", "--key-file", k).exit_code == 4
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--extract", tmp_path / "d", "--key-file", k)
    assert r.exit_code == 4, r.output
    assert not (tmp_path / "d").exists() or not any((tmp_path / "d").rglob("*.bin"))
    r = _cli("decode", arc / "plain.fasta", "--select", "in/a.bin", "--extract", tmp_path / "s", "--key-file", k)
    assert r.exit_code == 4, r.output
    env = {"VNX_PW": "secret"}
    assert _cli("extract", arc / "plain.vnx", tmp_path / "p", "--passphrase-env", "VNX_PW", env=env).exit_code == 4
    # explicit opt-in
    assert _cli("extract", arc / "plain.vnx", tmp_path / "x2", "--key-file", k, "--allow-unencrypted").exit_code == 0
    assert _cli("verify", arc / "plain.vnx", "--key-file", k, "--allow-unencrypted").exit_code == 0
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o2.vnx", "--extract", tmp_path / "d2", "--key-file", k,
             "--allow-unencrypted")
    assert r.exit_code == 0, r.output
    assert (tmp_path / "d2" / "in" / "a.bin").read_bytes() == (arc / "in" / "a.bin").read_bytes()
    r = _cli("decode", arc / "plain.fasta", "--select", "in/a.bin", "--extract", tmp_path / "s2", "--key-file", k,
             "--allow-unencrypted")
    assert r.exit_code == 0, r.output


def test_partial_decode_with_key_refuses_unencrypted(arc, tmp_path):
    key = crypto.load_key_file(arc / "k.key")
    reads = tmp_path / "r.fasta"
    with open(reads, "w") as f:                       # group 0 lost entirely (file data; the index survives)
        for i, (head, seq) in enumerate(iter_fasta(arc / "plain.fasta")):
            _, _, kind, g, _ = head.split("|")
            if not (int(kind) != KIND_SUPER and int(g) == 0):
                f.write(f">r{i}\n{seq}\n")
    res = de.decode_reads(reads, None, de.DecodeOptions(), partial_dir=tmp_path / "p", key=key)
    assert res.report.get("files_recovered") == []
    assert "not encrypted" in res.report.get("partial_note", "")
    ok = de.decode_reads(reads, None, de.DecodeOptions(), partial_dir=tmp_path / "p2", key=key, allow_unencrypted=True)
    assert ok.report["files_recovered"]


# ---------------------------------------------------------------------------------------------------------------- P-3
def _forged_superblock_reads(lay, tag: int, patch: dict[int, int]) -> str:
    aid = tag.to_bytes(2, "big") + bytes(14)
    raw = bytearray(en.Superblock("cauchy-rs", 64, 16, lay, "dense", 0, aid, 10_000, bytes(32), 9000, 4).pack()[:-4])
    for i, v in patch.items():
        raw[i] = v
    raw = bytes(raw) + struct.pack(">I", zlib.crc32(bytes(raw)))
    P = lay.payload_bytes
    ks, ms = en.Superblock.symbols(P)
    data = np.zeros((ks, P), dtype=np.uint8)
    data.reshape(-1)[: len(raw)] = np.frombuffer(raw, dtype=np.uint8)
    coded = CauchyRSCodec(ks, ms).encode(data)
    strands, _ = build_strands(lay, en.DNAOptions().constraints, tag, KIND_SUPER, np.zeros(ks + ms, dtype=np.int64),
                               np.arange(ks + ms, dtype=np.int64), coded)
    acgt = np.frombuffer(b"ACGT", dtype=np.uint8)
    return "".join(f">forged{i}\n{acgt[row].tobytes().decode()}\n" for i, row in enumerate(strands))


def test_superblock_k_zero_is_a_format_error():
    lay = en.DNAOptions().resolve()[0]
    raw = bytearray(en.Superblock("cauchy-rs", 64, 16, lay, "dense", 0, bytes(16), 10_000, bytes(32), 9000, 4).pack()[:-4])
    raw[8] = raw[9] = 0
    raw = bytes(raw) + struct.pack(">I", zlib.crc32(bytes(raw)))
    with pytest.raises(VNXFormatError):
        en.Superblock.unpack(raw)


@pytest.mark.parametrize("patch", [{8: 0, 9: 0}, {14: 3}], ids=["K=0", "invalid-layout"])
def test_forged_superblock_candidate_does_not_abort_decode(arc, tmp_path, patch):
    lay = en.DNAOptions().resolve()[0]
    reads = tmp_path / "r.fasta"
    reads.write_text((arc / "plain.fasta").read_text() + _forged_superblock_reads(lay, 0xBEEF, patch) * 2)
    res = de.decode_reads(reads, tmp_path / "o.vnx", de.DecodeOptions())
    assert res.status == "SUCCESS"
    assert (tmp_path / "o.vnx").read_bytes() == (arc / "plain.vnx").read_bytes()
    assert "beef" in res.report["archive_tags_seen"]


# ---------------------------------------------------------------------------------------------------------------- P-4
def test_report_refuses_symlink_and_existing_without_force(arc, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n")
    link = tmp_path / "rep.json"
    link.symlink_to(victim)
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--report", link)
    assert r.exit_code in (7, 8), r.output
    assert victim.read_text() == "keep me\n" and link.is_symlink()
    existing = tmp_path / "old.json"
    existing.write_text("old\n")
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o2.vnx", "--report", existing)
    assert r.exit_code == 8, r.output
    assert existing.read_text() == "old\n"
    assert not (tmp_path / "o2.vnx").exists()               # refused before decoding, nothing published
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o3.vnx", "--report", existing, "--force")
    assert r.exit_code == 0, r.output
    assert json.loads(existing.read_text())["status"] == "SUCCESS"
    assert (existing.stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("which", ["reads", "key", "output"])
def test_report_refuses_an_input_file(arc, tmp_path, which):
    reads = tmp_path / "r.fasta"
    reads.write_bytes((arc / "enc.fasta").read_bytes())
    key = tmp_path / "k.key"
    key.write_bytes((arc / "k.key").read_bytes())
    out = tmp_path / "o.vnx"
    out.write_bytes(b"old")
    target = {"reads": reads, "key": key, "output": out}[which]
    before = target.read_bytes()
    r = _cli("decode", reads, "-o", out, "--force", "--key-file", key, "--report", target)
    assert r.exit_code == 7, r.output
    assert target.read_bytes() == before


def test_report_new_file_mode_0600(arc, tmp_path):
    rep = tmp_path / "sub" / "rep.json"
    r = _cli("decode", arc / "plain.fasta", "-o", tmp_path / "o.vnx", "--report", rep)
    assert r.exit_code == 0, r.output
    assert (rep.stat().st_mode & 0o777) == 0o600 and json.loads(rep.read_text())["status"] == "SUCCESS"


# ---------------------------------------------------------------------------------------------------------------- P-2
@pytest.mark.parametrize("params", [{"n": 1 << 20, "r": 32, "p": 16}, {"n": 1 << 20, "r": 16, "p": 1},
                                    {"n": 1 << 19, "r": 8, "p": 16}, {"n": 1 << 20, "r": 8, "p": 8}])
def test_scrypt_parameters_above_caps_rejected_before_derivation(arc, tmp_path, params, monkeypatch):
    pw_arc = tmp_path / "pw.vnx"
    ar.build_archive([arc / "in" / "a.bin"], pw_arc, ar.ArchiveOptions(chunk_size=4096, passphrase="pw",
                                                                         scrypt={"n": 1024, "r": 8, "p": 1}))
    c = ct.open_container(pw_arc, passphrase="pw")
    m = json.loads(json.dumps(c.manifest))
    m["encryption"]["scrypt"] = dict(params)
    with pytest.raises(VNXFormatError, match="scrypt"):
        ct.validate_manifest(m)

    def boom(*a, **k):
        raise AssertionError("scrypt must not run")
    monkeypatch.setattr(crypto, "Scrypt", boom)
    with pytest.raises((VNXFormatError, VNXConfigurationError)):
        crypto.scrypt_master("pw", bytes(16), params)


@pytest.mark.parametrize("params", [dict(crypto.SCRYPT_DEFAULT), {"n": 1 << 20, "r": 8, "p": 1},
                                    {"n": 1 << 18, "r": 8, "p": 16}, {"n": 2, "r": 1, "p": 1}])
def test_scrypt_parameters_within_caps_accepted(arc, params):
    c = ct.open_container(arc / "plain.vnx")
    m = json.loads(json.dumps(c.manifest))
    m["encryption"] = {"algorithm": "AES-256-GCM", "kdf": "scrypt-hkdf-sha256", "salt": "00" * crypto.SALT_BYTES,
                       "key_check": "00" * 16, "scrypt": dict(params)}
    m["required_features"] = sorted(set(m["required_features"]) | {"aes-256-gcm", "kdf-hkdf-sha256", "kdf-scrypt"})
    ct.validate_manifest(m)
