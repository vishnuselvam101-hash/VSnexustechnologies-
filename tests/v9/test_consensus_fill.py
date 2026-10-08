"""V9 consensus candidate E (``fill="template"``, EXPERIMENTAL, SIMULATED): opt-in, validated, and on the frozen nanopore
corpus it only adds frames after the V8 ladder fails — never a false frame or a false success, and deterministic."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.recovery.cluster import ClusterConfig

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "nanopore"))
import nanofunnel as nf  # noqa: E402

FAST = [c for c in nf.load_corpus() if c["tier"] == "fast"][:3]


def test_fill_is_off_by_default_and_validated():
    assert ClusterConfig().fill == "none"
    ClusterConfig(fill="template", fill_trials=1).validate()
    with pytest.raises(VNXConfigurationError):
        ClusterConfig(fill="poa").validate()
    with pytest.raises(VNXConfigurationError):
        ClusterConfig(fill="template", fill_trials=0).validate()


def _run(case: dict, tmp: Path, fill: str) -> dict:
    c = copy.deepcopy(case)
    c["decoder"]["cluster_config"].update(consensus_template="full", fill=fill)
    return nf.run_case(c, tmp, oracle=False)


@pytest.mark.parametrize("case", FAST, ids=lambda c: c["id"])
def test_fill_only_adds_verified_frames(case, tmp_path):
    base = _run(case, tmp_path / "b", "none")
    fill = _run(case, tmp_path / "f", "template")
    again = _run(case, tmp_path / "g", "template")
    assert fill["decode"]["outcome"] != "FALSE_SUCCESS"
    assert fill["frames"]["false_frames"] == 0
    assert fill["frames"]["data_recovered"] >= base["frames"]["data_recovered"]
    assert fill["frames"] == again["frames"] and fill["decode"]["outcome"] == again["decode"]["outcome"]
