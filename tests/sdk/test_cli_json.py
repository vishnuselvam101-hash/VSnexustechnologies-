"""Every JSON the ``vnx`` CLI prints validates against the shipped schemas (V6 Phase 2.3/2.6): stdout results
(``vnx.result/1``, ``vnx version`` = ``vnx.version/1``), stderr errors (``vnx.error/1``), ``--report`` files
(``vnx.decode-report/1``) and ``--events`` lines (``vnx.event/1``). The 5.x top-level fields stay next to the envelope.

SYNTHETIC SOFTWARE TEST data; channel results are SIMULATED.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from typer.testing import CliRunner

from vnxdna.commands import app
from vnxdna.core import schema


def cli(*args, code=0):
    r = CliRunner().invoke(app, [str(a) for a in args])
    assert r.exit_code == code, (r.exit_code, r.stdout[-3000:], r.stderr[-3000:] if r.stderr_bytes else "")
    return r


def out_json(r) -> dict:
    doc = json.loads(r.stdout)
    schema.validate(doc, doc["schema"])
    return doc


def err_json(r) -> dict:
    doc = json.loads(r.stderr)
    schema.validate(doc, "vnx.error/1")
    return doc


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    d = tmp_path_factory.mktemp("clijson")
    (d / "ds").mkdir()
    cli("generate", d / "ds" / "a.bin", "--size", "30KB", "--seed", "9")
    cli("generate", d / "ds" / "b.txt", "--size", "8KB", "--pattern", "text", "--seed", "10")
    cli("archive", d / "ds", d / "a.vnx")
    cli("encode", d / "a.vnx", d / "s.fasta")
    cli("channel", "simulate", d / "s.fasta", d / "r.fastq", "--coverage", "3", "--seed", "11",
        "--substitution-rate", "0.002")
    return d


def test_container_commands(ws, tmp_path):
    for args in (("verify", ws / "a.vnx"), ("list", ws / "a.vnx"), ("inspect", ws / "a.vnx"), ("inspect", ws / "r.fastq"),
                 ("locate", ws / "a.vnx", "ds/b.txt", "--dna-profile", "v4-balanced"), ("profiles",), ("native",),
                 ("validate", ws / "s.fasta"), ("extract", ws / "a.vnx", tmp_path / "x"),
                 ("generate", tmp_path / "g.bin", "--size", "2KB"), ("keygen", tmp_path / "k.key"),
                 ("archive", ws / "ds", tmp_path / "b.vnx"), ("conformance",)):
        doc = out_json(cli(*args))
        assert doc["schema"] == "vnx.result/1", args
    assert out_json(cli("verify", ws / "a.vnx"))["status"] == "VERIFIED"         # 5.x value kept
    assert out_json(cli("archive", ws / "ds", tmp_path / "c.vnx"))["files"] == 2  # 5.x top-level field kept


def test_version_is_vnx_version_1():
    doc = out_json(cli("version"))
    assert doc["schema"] == "vnx.version/1" and doc["vnx4_format"] == [4, 0]


def test_encode_and_decode_with_report_and_events(ws, tmp_path):
    enc = out_json(cli("encode", ws / "a.vnx", tmp_path / "s.fasta"))
    assert enc["kind"] == "encode" and enc["strands"] == enc["result"]["strands"]
    rep, ev = tmp_path / "rep.json", tmp_path / "ev.jsonl"
    dec = out_json(cli("decode", ws / "r.fastq", "-o", tmp_path / "o.vnx", "--report", rep, "--events", ev, "--task-id", "t9"))
    assert dec["status"] == "SUCCESS" and dec["container_sha256"] == hashlib.sha256((ws / "a.vnx").read_bytes()).hexdigest()
    report = json.loads(rep.read_text())
    schema.validate(report, "vnx.decode-report/1")
    assert report["schema"] == "vnx.decode-report/1" and report["software"]["version"] and report["spec"] == "6.0"
    assert report["provenance"]["backends"]["rs"]["active"] in ("native", "reference")
    assert report["inputs"][0]["sha256"] == hashlib.sha256((ws / "r.fastq").read_bytes()).hexdigest()
    lines = [json.loads(x) for x in ev.read_text().splitlines()]
    for e in lines:
        schema.validate(e, "vnx.event/1")
    assert [e["seq"] for e in lines] == list(range(len(lines))) and len({e["run_id"] for e in lines}) == 1
    names = [e["event"] for e in lines]
    assert names[0] == "run_start" and names[1] == "decode_start" and names[-2:] == ["decode_end", "command_end"]
    assert "superblock" in names and lines[names.index("superblock")]["formats"]["superblock_version"] == 1
    assert lines[-1]["report_sha256"] == hashlib.sha256(rep.read_bytes()).hexdigest()


def test_errors_are_vnx_error_1(ws, tmp_path):
    e = err_json(cli("verify", tmp_path / "missing.vnx", code=3))
    assert e["code"] == "FORMAT_ERROR" and e["error_class"] == "VNXFormatError"     # error_class kept in 6.x
    e = err_json(cli("decode", ws / "r.fastq", "-o", tmp_path / "o.vnx", "--performance", "bogus", code=7))
    assert e["code"] == "CONFIGURATION_ERROR" and "bogus" in e["message"]


def test_encode_archive_options_pass_through_and_refusal(ws, tmp_path):
    doc = out_json(cli("encode", ws / "ds", tmp_path / "s.fasta", "--chunk-size", "4096", "--compression", "none",
                       "--keep-archive", tmp_path / "k.vnx"))
    m = out_json(cli("inspect", tmp_path / "k.vnx"))["manifest"]
    assert m["chunking"]["chunk_size"] == 4096 and doc["archive"]["files"] == 2
    e = err_json(cli("encode", ws / "a.vnx", tmp_path / "t.fasta", "--chunk-size", "4096", code=7))
    assert e["code"] == "CONFIGURATION_ERROR" and not (tmp_path / "t.fasta").exists()


def test_remaining_json_commands(ws, tmp_path):
    """channel simulate, benchmark, experimental encode, experiment run and reproduce: every JSON they print validates."""
    doc = out_json(cli("channel", "simulate", ws / "s.fasta", tmp_path / "r.fastq", "--seed", "3", "--coverage", "2"))
    assert doc["kind"] == "simulate" and doc["result"]["evidence_class"] == "SIMULATED"
    doc = out_json(cli("benchmark", "--profile", "safe", "--size", "8KB"))
    assert doc["kind"] == "benchmark"
    doc = out_json(cli("experimental", "encode", ws / "a.vnx", tmp_path / "lt.fasta", "-K", "16", "-M", "8"))
    assert doc["stability"] == "EXPERIMENTAL" and doc["result"]["stability"] == "EXPERIMENTAL"
    exp = tmp_path / "exp"
    exp.mkdir()
    (exp / "config.json").write_text(json.dumps({
        "id": "cli-json", "type": "sweep", "purpose": "SYNTHETIC SOFTWARE TEST of the CLI JSON",
        "input": {"size": 4096, "pattern": "random", "seed": 5}, "dna": {"profile": "v4-balanced"},
        "channel": {"coverage_model": "fixed", "seed": 6},
        "sweep": {"grid": {"substitution_rate": [0.0], "coverage": [2]}}, "trials": 1, "workers": 1}))
    doc = out_json(cli("experiment", "run", exp / "config.json"))
    assert doc["status"] == "DONE" and doc["kind"] == "experiment"
    doc = out_json(cli("experiment", "reproduce", exp))
    assert doc["reproduced"] is True


def test_every_command_is_covered():
    """A new command must get a JSON-validation case here (sweep, codec-compare and benchmark --human print tables)."""
    covered = {"version", "native", "keygen", "archive", "inspect", "list", "verify", "locate", "extract", "encode", "decode",
               "validate", "simulate", "benchmark", "sweep", "run", "reproduce", "generate", "profiles", "conformance",
               "codec-compare"}
    names = set()
    for group in [app] + [g.typer_instance for g in app.registered_groups]:
        for c in group.registered_commands:
            names.add(c.name or c.callback.__name__.replace("_cmd", "").replace("_", "-"))
    names = {n.split("-", 1)[1] if n.startswith(("channel-", "experiment-", "experimental-")) else n for n in names}
    assert names <= covered, names - covered
