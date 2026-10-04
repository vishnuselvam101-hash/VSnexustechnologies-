"""Structured decode events: complete, ordered, valid JSON lines, and never able to change a decode."""
from __future__ import annotations

import hashlib
import json

import pytest
from typer.testing import CliRunner

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.cli import app
from vnxdna.v6.observe import Events, JsonlObserver

VOLATILE = ("stage_seconds", "seconds", "peak_rss_bytes", "output")


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("obs")
    datagen.generate(d / "in.bin", 30_000, "random", 9101)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
    ch.simulate_file(d / "s.fasta", d / "r.fastq", ch.ChannelConfig(substitution_rate=0.003, insertion_rate=0.001,
                                                                      deletion_rate=0.001, coverage=4, seed=9102))
    return d


def _events(arc, workers=1, **kw):
    got = []
    res = de.decode_reads(arc / "r.fastq", arc / f"o{workers}.vnx", de.DecodeOptions(workers=workers, batch_reads=256, **kw),
                          overwrite=True, observer=got.append, task_id="t-1")
    return res, got


@pytest.mark.parametrize("workers", [1, 2])
def test_event_sequence_and_fields(arc, workers):
    res, ev = _events(arc, workers)
    assert res.status == "SUCCESS"
    names = [e["event"] for e in ev]
    assert names[0] == "decode_start" and names[-1] == "decode_end"
    for n in ("pass1_progress", "pass1_end", "pass2_end", "verify"):
        assert n in names
    assert names.index("pass1_end") < names.index("pass2_end") < names.index("verify")
    for e in ev:
        assert e["task_id"] == "t-1" and {"ts", "stage", "elapsed", "rss_bytes"} <= set(e)
    end = ev[-1]
    assert end["status"] == "SUCCESS" and end["archive_id"] == res.report["superblock"]["archive_id"]
    p1 = next(e for e in ev if e["event"] == "pass1_end")
    assert p1["reads_processed"] == res.report["reads"]["reads"]
    assert 0 < p1["worker_utilisation"] <= 1.5
    assert next(e for e in ev if e["event"] == "verify")["sha256_match"] is True
    if workers > 1:
        assert any(e.get("queue_depth", 0) > 0 for e in ev if e["event"] == "pass1_progress")


def test_observer_does_not_change_the_report(arc):
    a = de.decode_reads(arc / "r.fastq", arc / "x.vnx", de.DecodeOptions(batch_reads=256), overwrite=True)
    b, _ = _events(arc)
    strip = lambda r: {k: v for k, v in r.items() if k not in VOLATILE}   # noqa: E731
    assert strip(a.report) == strip(b.report)


def test_failing_observer_is_detached(arc):
    def bad(_):
        raise RuntimeError("sink down")
    res = de.decode_reads(arc / "r.fastq", arc / "y.vnx", de.DecodeOptions(), overwrite=True, observer=bad)
    assert res.status == "SUCCESS"


def test_error_event(arc, tmp_path):
    p = tmp_path / "empty.fastq"
    p.write_text("")
    got = []
    with pytest.raises(Exception):
        de.decode_reads(p, None, de.DecodeOptions(), observer=got.append)
    assert got and got[-1]["event"] == "error" and got[-1]["error_class"]


def test_jsonl_observer(tmp_path):
    path = tmp_path / "e.jsonl"
    with JsonlObserver(path) as obs:
        Events(obs, "k").emit("decode_start", "input", reads_bytes=3)
    rec = json.loads(path.read_text().splitlines()[0])
    assert rec["task_id"] == "k" and rec["reads_bytes"] == 3
    assert (path.stat().st_mode & 0o777) == 0o600


def test_cli_events_and_budget(arc, tmp_path):
    ev = tmp_path / "ev.jsonl"
    out = tmp_path / "o.vnx"
    r = CliRunner().invoke(app, ["decode", str(arc / "r.fastq"), "-o", str(out), "--events", str(ev), "--task-id", "job7",
                                 "--max-recovery-reads", "1000", "--max-wall-seconds", "3600"])
    assert r.exit_code == 0, r.output
    lines = [json.loads(x) for x in ev.read_text().splitlines()]
    assert lines[-1]["event"] == "decode_end" and all(x["task_id"] == "job7" for x in lines)
    assert hashlib.sha256(out.read_bytes()).digest() == hashlib.sha256((arc / "a.vnx").read_bytes()).digest()
    doc = json.loads(r.output[r.output.index("{"):])
    assert doc["recovery_plan"]["budget"]["max_reads_examined"] == 1000
    late = CliRunner().invoke(app, ["decode", str(arc / "r.fastq"), "-o", str(tmp_path / "late.vnx"),
                                    "--max-wall-seconds", "0.000001"])
    assert late.exit_code == 5 and not (tmp_path / "late.vnx").exists()
