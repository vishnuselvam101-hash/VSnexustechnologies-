"""Phase 1 regression (EXPERIMENTAL, SIMULATED): ``consensus_template="full"`` on the frozen nanopore corpus reproduces
the pre-registered A-CONS frozen-corpus record (``experiments/v7/a-cons/results/frozen.jsonl``, commit aac15a1).

The reference path stays pinned by ``test_nanopore_corpus.py``; this file pins the opt-in Phase 1 path. Each case is
built exactly as ``experiments/v7/a-cons/run.py`` builds the ``full`` arm (only ``consensus_template`` differs from the
corpus case), and every recorded funnel, frame and outcome field must be equal. The corpus inputs are not touched.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import nanofunnel as nf  # noqa: E402

RECORD = ROOT / "experiments" / "v7" / "a-cons" / "results" / "frozen.jsonl"
FIELDS = ("outcome", "data_total", "two_reads", "clustered", "candidate", "consensus_ok", "data_frames",
          "superblock_frames", "false_frames", "rows_decodable")
CASES = nf.load_corpus()
RECORDED = {r["case"]: r for r in map(json.loads, RECORD.read_text().splitlines()) if r["arm"] == "full"}


def _check(case: dict, tmp_path: Path) -> None:
    c = copy.deepcopy(case)
    c["decoder"]["cluster_config"]["consensus_template"] = "full"
    d = nf.run_case(c, tmp_path, oracle=False)
    fd = d["funnel_data"]
    got = {"outcome": d["decode"]["outcome"], "data_total": fd["total"], "two_reads": fd["two_reads"],
           "clustered": fd["clustered"], "candidate": fd["candidate"], "consensus_ok": fd["valid_frame"],
           "data_frames": d["frames"]["data_recovered"], "superblock_frames": d["frames"]["superblock_recovered"],
           "false_frames": d["frames"]["false_frames"], "rows_decodable": d["rows"]["decodable_from_cluster_frames"]}
    assert got["outcome"] != "FALSE_SUCCESS", "FALSE SUCCESS: stop-and-report bug"
    assert got["false_frames"] == 0
    assert got == {k: RECORDED[case["id"]][k] for k in FIELDS}


def test_record_covers_the_whole_corpus():
    assert sorted(RECORDED) == sorted(c["id"] for c in CASES)
    assert all(RECORDED[k]["false_success"] is False for k in RECORDED)


@pytest.mark.parametrize("case", [c for c in CASES if c["tier"] == "fast"], ids=lambda c: c["id"])
def test_fast_case_reproduces_the_phase1_record(case, tmp_path):
    _check(case, tmp_path)


@pytest.mark.slow
@pytest.mark.parametrize("case", [c for c in CASES if c["tier"] == "slow"], ids=lambda c: c["id"])
def test_slow_case_reproduces_the_phase1_record(case, tmp_path):
    _check(case, tmp_path)
