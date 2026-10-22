"""CLI tests for the AP-8 cost planner command (V15 Task E part 2b).

``vnx plan FILE`` reports the encoder's own strand and base counts for a file and, with ``--prices``, the cost
per vendor from an operator-supplied price file. No DNA is synthesised, stored or sequenced: the counts are the
software's own encoder counts and the prices are the operator's numbers.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vnxdna.commands.cli import app
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.pipeline.plan import load_prices, plan_file

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "docs" / "examples"
PRICE_EXAMPLE = EXAMPLES / "vendor-prices.example.json"
PROFILE = "v4-balanced"


def _cli(*args):
    return CliRunner().invoke(app, [str(a) for a in args])


def _payload(tmp_path, size=4096, name="payload.bin"):
    """A file of ``size`` bytes: the planner only measures its size."""
    path = tmp_path / name
    path.write_bytes((bytes(range(256)) * (size // 256 + 1))[:size])
    return path


def test_plan_command_exits_zero_and_prints_the_plan_as_json(tmp_path):
    """The command exits 0 and prints the plan: its strand and base counts are the planner's own."""
    payload = _payload(tmp_path)
    result = _cli("plan", payload, "--profile", PROFILE)

    assert result.exit_code == 0, (result.stdout, result.stderr)
    doc = json.loads(result.stdout)
    expected = plan_file(payload, options=DNAOptions(profile=PROFILE))
    assert doc["schema"] == "vnx.result/1" and doc["kind"] == "plan"
    assert doc["strands"] == expected.strands
    assert doc["bases"] == expected.bases
    assert doc["result"]["strands"] == expected.strands
    assert doc["result"]["bases"] == expected.bases
    assert doc["profile"] == PROFILE
    assert doc["input_bytes"] == payload.stat().st_size
    assert doc["evidence"] == "THEORETICAL (encoder count)"
    assert doc["costs"] == []


def test_plan_command_default_profile_is_v4_balanced(tmp_path):
    """Without --profile the default layout profile is v4-balanced."""
    payload = _payload(tmp_path)
    result = _cli("plan", payload)

    assert result.exit_code == 0, (result.stdout, result.stderr)
    doc = json.loads(result.stdout)
    assert doc["profile"] == "v4-balanced"
    assert doc["strands"] == plan_file(payload, options=DNAOptions(profile="v4-balanced")).strands
    assert doc["bases"] == plan_file(payload, options=DNAOptions(profile="v4-balanced")).bases


def test_plan_command_with_prices_gives_one_cost_per_vendor(tmp_path):
    """With --prices the plan carries one cost entry per vendor of the price file."""
    payload = _payload(tmp_path)
    result = _cli("plan", payload, "--profile", PROFILE, "--prices", PRICE_EXAMPLE)

    assert result.exit_code == 0, (result.stdout, result.stderr)
    doc = json.loads(result.stdout)
    vendors = load_prices(PRICE_EXAMPLE)
    assert len(vendors) == 2
    assert len(doc["costs"]) == len(vendors)
    assert [c["vendor"] for c in doc["costs"]] == [v.vendor for v in vendors]
    for cost, vendor in zip(doc["costs"], vendors):
        assert cost["currency"] == vendor.currency
        assert isinstance(cost["total"], str)
        assert isinstance(cost["fits"], bool)


def test_plan_command_bad_prices_file_exits_non_zero(tmp_path):
    """A price file that is not vnx.vendor-prices/1 is refused with a clear message and a non-zero exit."""
    payload = _payload(tmp_path)
    bad = tmp_path / "bad-prices.json"
    bad.write_text(json.dumps({"format": "wrong.format/1", "vendors": []}), encoding="utf-8")

    result = _cli("plan", payload, "--prices", bad)

    assert result.exit_code != 0
    assert "vnx.vendor-prices/1" in result.output
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("text", [
    json.dumps({"format": "vnx.vendor-prices/1", "vendors": [{"currency": "USD", "per_base": "0.01"}]}),
    json.dumps({"format": "vnx.vendor-prices/1", "vendors": [{"vendor": "v", "currency": "USD", "per_base": 0.01}]}),
    json.dumps({"format": "vnx.vendor-prices/1", "vendors": [{"vendor": "v", "currency": "USD", "per_base": "-0.01"}]}),
    json.dumps({"format": "vnx.vendor-prices/1"}),
    "this is not JSON at all",
], ids=["missing-vendor", "price-not-a-string", "negative-price", "missing-vendors", "not-json"])
def test_plan_command_refuses_a_broken_price_file_with_a_typed_error(tmp_path, text):
    """A broken price file is refused with a typed error document (exit 7); no plan is printed."""
    payload = _payload(tmp_path)
    bad = tmp_path / "broken-prices.json"
    bad.write_text(text, encoding="utf-8")

    result = _cli("plan", payload, "--prices", bad)

    assert result.exit_code == 7
    error = json.loads(result.stderr)
    assert error["schema"] == "vnx.error/1"
    assert error["code"] == "CONFIGURATION_ERROR"
    assert error["message"]
    assert result.stdout.strip() == ""


def test_plan_command_unknown_profile_exits_non_zero(tmp_path):
    """An unknown layout profile is refused (exit 7) and nothing is printed on stdout."""
    payload = _payload(tmp_path)
    result = _cli("plan", payload, "--profile", "no-such-profile")

    assert result.exit_code != 0
    assert "no-such-profile" in result.output
    assert result.stdout.strip() == ""


def test_example_price_file_loads_and_the_plan_examples_parse():
    """The shipped examples are valid: the price file loads, the two plan examples parse."""
    vendors = load_prices(PRICE_EXAMPLE)
    assert len(vendors) == 2
    assert all(v.vendor for v in vendors)

    for name in ("plan.example.json", "bad-plan.example.json"):
        doc = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
        assert doc["profile"]
        assert doc["strands"] > 0
        assert doc["bases"] > 0
        assert doc["input_bytes"] > 0
        assert isinstance(doc["costs"], list)
