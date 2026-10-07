"""V8.9 failure taxonomy (experiments/v8/matrix/taxonomy.py): every lost strand and every non-EXACT decode maps to one of
the 12 categories, unknown stages raise, unobserved funnels are not counted as losses, and classified == total."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments/v8/matrix"))
import taxonomy as TX      # noqa: E402


def _strand(i, g, lost=None, reason=""):
    return {"kind": "data", "strand": i, "group": g, "lost_at": lost, "reason": reason}


@pytest.mark.parametrize("lost_at, reason, pool, cat", [
    ("observed", "", True, "read_loss"), ("observed", "", False, "insufficient_coverage"),
    ("two_reads", "", False, "insufficient_coverage"), ("stored", "", False, "insufficient_coverage"),
    ("clustered", "", False, "clustering"), ("oriented", "", False, "clustering"), ("candidate", "", False, "alignment"),
    ("valid_frame", "another address", False, "consensus"), ("rs_recoverable", "shifted by 1", False, "indel_placement"),
    ("rs_recoverable", "payload errors", False, "substitution_correction")])
def test_strand_categories(lost_at, reason, pool, cat):
    assert TX.strand_category(lost_at, reason, pool) == cat and cat in TX.CATEGORIES


def test_unknown_stage_is_a_blocker():
    with pytest.raises(TX.UnclassifiedFailure):
        TX.strand_category("teleported", "", False)
    with pytest.raises(TX.UnclassifiedFailure):
        TX.decode_category({"outcome": "EXPLICIT_FAILURE", "terminal_stage": "other:weird"}, TX.Counter(), {}, 10)


def test_decode_level_classification_and_accounting():
    strands = [_strand(i, i % 2) for i in range(10)] + [_strand(10 + i, 0, "observed") for i in range(3)] + \
              [_strand(20, 1, "rs_recoverable", "shifted by 2")]
    doc = {"decode": {"outcome": "EXPLICIT_FAILURE", "terminal_stage": "outer_ecc", "error_code": None}, "strands": strands,
           "reads": 100}
    t = TX.classify(doc, M=1, pool_lost={10})
    assert t["lost_strands"] == 4 and sum(t["lost_by_category"].values()) == 4
    assert t["lost_by_category"] == {"read_loss": 1, "insufficient_coverage": 2, "indel_placement": 1}
    assert t["primary"] == "rs_parity_budget" and t["failing_rows"] == 1 and t["deficit"] == 2
    ok = dict(doc, decode={"outcome": "EXACT", "terminal_stage": None})
    assert TX.classify(ok, 1, set())["primary"] is None
    for stage, code, cat in (("superblock", "NO_SUPERBLOCK", "archive_reconstruction"),
                             ("archive", "CONTAINER_HASH_MISMATCH", "integrity_verification"),
                             ("read_parsing", "BAD_INPUT", "configuration_input_error")):
        d = dict(doc, decode={"outcome": "EXPLICIT_FAILURE", "terminal_stage": stage, "error_code": code})
        assert TX.classify(d, 1, set())["primary"] == cat
    fs = dict(doc, decode={"outcome": "FALSE_SUCCESS", "terminal_stage": None})
    assert TX.classify(fs, 1, set())["primary"] == "integrity_verification"
    assert TX.classify(dict(doc, reads=0), 1, set())["primary"] == "read_generation"


def test_unobserved_funnel_is_not_counted_as_loss():
    doc = {"decode": {"outcome": "EXACT", "terminal_stage": None}, "strands": [_strand(0, 0, "stored")], "reads": 5}
    t = TX.classify(doc, 1, set(), cluster_ran=False)
    assert t["lost_strands"] is None and t["primary"] is None and "not observed" in t["note"]
