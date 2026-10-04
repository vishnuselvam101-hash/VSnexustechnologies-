"""benchmarks/competitors: records are well-formed and comparability is computed, never asserted by hand."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("classify", ROOT / "benchmarks/competitors/classify.py")
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)


@pytest.fixture(scope="module")
def data():
    return cl.load()


def test_records_load_and_have_sources(data):
    assert len(data["records"]) >= 10
    for r in data["records"]:
        src = r["source"]
        assert src.get("doi") or src.get("urls"), r["id"]
        assert src.get("locator"), r["id"]


def test_physical_is_never_comparable(data):
    for r in data["records"]:
        assert cl.classify(r, "physical")[0] == cl.NOT


def test_wet_lab_recovery_is_not_comparable(data):
    for r in data["records"]:
        if r["medium"] == "wet_lab":
            assert cl.classify(r, "error_tolerance")[0] == cl.NOT


def test_secondary_values_are_context_only(data):
    for r in data["records"]:
        if r["verification"] != "primary_fulltext":
            assert all(cl.classify(r, m)[0] == cl.NOT for m in cl.METRICS)


def test_nothing_is_direct_without_a_protocol_run(data):
    rows = cl.matrix(data)
    assert all(v["label"] != cl.DIRECT for r in rows for v in r["metrics"].values())


def test_protocol_run_enables_direct_only_for_the_protocol_record(data):
    runs = {"gimpel2026_codec_benchmark": {"scenarios": ["fixed-53-45-2"], "hardware_class": None}}
    rows = {r["id"]: r for r in cl.matrix(data, runs)}
    assert rows["gimpel2026_codec_benchmark"]["metrics"]["error_tolerance"]["label"] == cl.DIRECT
    assert rows["gimpel2026_codec_benchmark"]["metrics"]["physical"]["label"] == cl.NOT
    others = [r for k, r in rows.items() if k != "gimpel2026_codec_benchmark"]
    assert all(v["label"] != cl.DIRECT for r in others for v in r["metrics"].values())


def test_no_superiority_language_in_records():
    text = (ROOT / "benchmarks/competitors/records.json").read_text().lower()
    for word in ("beats", "outperform", "superior", "world's", "fastest", "best-in-class"):
        assert word not in text


def test_cli_writes_json(tmp_path):
    out = tmp_path / "m.json"
    assert cl.main(["--json", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert "SIMULATED" in doc["statement"] and doc["rows"]


def test_bad_record_rejected(tmp_path, data):
    bad = dict(data)
    bad["records"] = [dict(data["records"][0], medium="lab")]
    p = tmp_path / "r.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        cl.load(p)
