"""Nanopore regression corpus (DIAGNOSTIC / SIMULATED): every case reproduces from its seeds, never returns wrong data,
and its per-strand loss funnel, ORACLE address test and outcome equal the pinned ``expected/<id>.json``.

A decoder change that alters a case's outcome or funnel must update ``expected/`` deliberately
(``reproduce.py --update-expected``) and say why in its commit. A case that decodes EXACT must keep decoding EXACT.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nanofunnel as nf  # noqa: E402

CASES = nf.load_corpus()
FAST = [c for c in CASES if c["tier"] == "fast"]
SLOW = [c for c in CASES if c["tier"] == "slow"]


def _expected(cid: str) -> dict:
    return json.loads((HERE / "expected" / f"{cid}.json").read_text())


def _check(case: dict, tmp_path: Path) -> None:
    doc = nf.run_case(case, tmp_path, oracle=True)
    assert doc["decode"]["outcome"] != "FALSE_SUCCESS", "FALSE SUCCESS: stop-and-report bug"
    assert doc["decode"]["outcome"] in ("EXACT", "EXPLICIT_FAILURE", "PARTIAL")
    assert doc["frames"]["false_frames"] == 0
    assert doc["oracle"]["false_frames"] == 0
    for r in doc["strands"]:
        assert (r["lost_at"] is None) == (r["reason"] is None), r
        assert r["lost_at"] is None or r["lost_at"] in nf.STAGES
    got = json.loads(json.dumps(nf.expected_of(doc), sort_keys=True))
    exp = _expected(case["id"])
    if exp["outcome"] == "EXACT":
        assert got["outcome"] == "EXACT", "a corpus case that decoded EXACT no longer does"
    assert got == exp


@pytest.mark.parametrize("case", FAST, ids=[c["id"] for c in FAST])
def test_fast_case_reproduces(case, tmp_path):
    _check(case, tmp_path)


@pytest.mark.slow
@pytest.mark.parametrize("case", SLOW, ids=[c["id"] for c in SLOW])
def test_slow_case_reproduces(case, tmp_path):
    _check(case, tmp_path)


def test_corpus_is_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    for c in CASES:
        assert 82000 <= c["channel"]["seed"] <= 82099, "exploration seeds only (protocol section 7)"
        assert c["tier"] in ("fast", "slow")
        assert (HERE / "expected" / f"{c['id']}.json").is_file()
        assert (HERE / "diagnostics" / f"{c['id']}.json").is_file()


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_committed_artifact_matches_expected(case):
    """The committed failure artifact is the one the pinned expectation was taken from."""
    doc = json.loads((HERE / "diagnostics" / f"{case['id']}.json").read_text())
    exp = _expected(case["id"])
    assert doc["case"] == case
    assert doc["reads_sha256"] == exp["reads_sha256"]
    assert doc["funnel_data"] == exp["funnel_data"]
    assert doc["decode"]["outcome"] == exp["outcome"]
    lost = {r["strand"]: r for r in doc["lost_strands"]}
    assert len(lost) == doc["strands_total"] - doc["frames"]["data_recovered"] - doc["frames"]["superblock_recovered"]
    assert all(r["reason"] for r in lost.values())


def test_oracle_hook_is_not_in_the_decoder():
    """The ORACLE grouping lives only in this test package; the decoder package never references it."""
    src = Path(nf.__file__).resolve().parents[2] / "src" / "vnxdna"
    for p in src.rglob("*.py"):
        text = p.read_text()
        assert "oracle_frames" not in text and "nanofunnel" not in text, p
