"""Security-audit regressions (V6 Phase 1, item 22): event/report file handling, CLI outcome events, bounded stripe
recovery, encryption-downgrade refusal, forged superblocks and scrypt parameter caps.

Strand headers (``vnx4|tag|kind|group|symbol``) are used only by the tests to choose which strands to drop or forge.
"""
from __future__ import annotations

import json
import os
import tracemalloc

import numpy as np
import pytest
from typer.testing import CliRunner

from vnxdna.v4 import archive as ar
from vnxdna.v4 import crypto
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.cli import app
from vnxdna.v4.constraints import iter_fasta
from vnxdna.v4.errors import VNXConfigurationError
from vnxdna.v4.frame import KIND_SUPER
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
    assert "decode_end" in names and next(e for e in lines if e["event"] == "decode_end")["status"] == "SUCCESS"
    err = [e for e in lines if e["event"] == "error"]
    assert err and err[-1]["error_class"] == "VNXKeyError"
    end = lines[-1]
    assert end["event"] == "command_end" and end["exit_code"] == 4 and end["status"] == "FAILED"
    assert end["error_class"] == "VNXKeyError" and all(e["task_id"] == "job9" for e in lines)


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
