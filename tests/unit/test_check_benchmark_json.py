"""tools/check_benchmark_json.py: shape check of `vnx benchmark` output used by the CI job `benchmark-smoke`."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_benchmark_json", ROOT / "tools" / "check_benchmark_json.py")
assert _spec is not None and _spec.loader is not None
cbj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cbj)

GOOD = {"vnx_benchmark": 1, "environment": {"python": "3.12"}, "performance_profile": "balanced",
        "results": [{"case": "stages", "archive_mb_s": 70.1, "input_size": 4194304},
                    {"case": "end_to_end", "status": "SUCCESS", "error": None, "input_sha256": "ab", "output_sha256": "ab",
                     "channel_seconds": None}]}


def _write(tmp_path: Path, doc) -> str:
    p = tmp_path / "bench.json"
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return str(p)


def test_valid_document_passes(tmp_path):
    assert cbj.main([_write(tmp_path, GOOD)]) == 0


def test_invalid_json_and_nan_are_rejected(tmp_path):
    assert cbj.main([_write(tmp_path, "{not json")]) == 1
    assert cbj.main([_write(tmp_path, json.dumps(GOOD).replace("70.1", "NaN"))]) == 1


def test_missing_cases_and_keys_are_reported():
    assert cbj.problems({**GOOD, "results": []}) == ["results is not a non-empty list"]
    assert "missing key 'environment'" in cbj.problems({k: v for k, v in GOOD.items() if k != "environment"})
    assert "no 'end_to_end' case" in cbj.problems({**GOOD, "results": GOOD["results"][:1]})


def test_failed_or_wrong_hash_end_to_end_is_rejected():
    doc = copy.deepcopy(GOOD)
    doc["results"][1]["status"] = "FAILURE"
    assert any("status 'FAILURE'" in p for p in cbj.problems(doc))
    doc = copy.deepcopy(GOOD)
    doc["results"][1]["output_sha256"] = "cd"
    assert any("hash differs" in p for p in cbj.problems(doc))


def test_negative_or_infinite_numbers_are_rejected():
    doc = copy.deepcopy(GOOD)
    doc["results"][0]["archive_mb_s"] = -1.0
    assert any("archive_mb_s" in p for p in cbj.problems(doc))
    doc["results"][0]["archive_mb_s"] = float("inf")
    assert any("archive_mb_s" in p for p in cbj.problems(doc))
