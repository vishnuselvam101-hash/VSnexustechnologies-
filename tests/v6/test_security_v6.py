"""Security-audit regressions (V6 Phase 1, item 22): event/report file handling, CLI outcome events, bounded stripe
recovery, encryption-downgrade refusal, forged superblocks and scrypt parameter caps.

Strand headers (``vnx4|tag|kind|group|symbol``) are used only by the tests to choose which strands to drop or forge.
"""
from __future__ import annotations

import json
import os

import pytest
from typer.testing import CliRunner

from vnxdna.v4 import archive as ar
from vnxdna.v4 import crypto
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en
from vnxdna.v4.cli import app
from vnxdna.v4.errors import VNXConfigurationError
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
