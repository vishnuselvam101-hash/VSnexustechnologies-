"""V7 A-DIAG harness (experiments/v7/a-diag/adiag.py): ground-truth funnel and first failing stage on small pools.

SYNTHETIC strands; SIMULATED channel (the unfitted V6 models). No DNA was synthesised or sequenced.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ADIAG = Path(__file__).resolve().parents[2] / "experiments" / "v7" / "a-diag"
sys.path.insert(0, str(ADIAG))
import adiag  # noqa: E402

p4 = adiag.p4


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    d = tmp_path_factory.mktemp("adiag")
    info = p4.prepare(d, {"size": 6000, "profile": "v4-balanced"}, 6201)
    models = {}
    for base, cov in (("nanopore-like", 10.0), ("illumina-like", 5.0), ("deletion-heavy", 10.0)):
        spec = {"base": base, "name": f"{base}-t{int(cov)}", "channel": {"coverage": cov}}
        models[base] = p4.materialise_model(spec, d / "models")
    return d, info, models


def _job(setup, base, seed=82000):
    d, info, models = setup
    return {"cell": f"{base}/test", "model": models[base], "seed": seed, "strands": str(d / "strands.fasta"),
            "container_sha256": info["container_sha256"], "profile": "v4-balanced", "tmp": str(d), "command": "test"}


def _check_funnel(t):
    f = t["diagnosis"]["funnel"]
    for part in ("data_strands", "superblock_strands"):
        seq = [f[part]["total"]] + [f[part][f"after_{s}"] for s in adiag.STRAND_STAGES]
        assert all(a >= b for a, b in zip(seq, seq[1:])), (part, seq)
        assert f[part]["recovered"] == seq[-1]
    counts = t["diagnosis"]["strand_stage_counts"]
    assert sum(counts["data"].values()) == f["data_strands"]["total"]
    assert sum(counts["superblock"].values()) == f["superblock_strands"]["total"] == 12
    assert set(counts["data"]) | set(counts["superblock"]) <= set(adiag.STRAND_STAGES) | {"recovered"}
    r = f["reads"]
    assert r["reads"] >= r["oriented_correctly"] >= r["oriented_and_aligned"]
    assert r["verified_wrong_address"] == 0


def test_clean_control_is_exact_and_fully_recovered(setup):
    t = adiag.run_trial(_job(setup, "illumina-like"))
    assert t["decode"]["outcome"]["outcome"] == "EXACT"
    assert t["diagnosis"]["first_failing_stage"] is None
    assert t["diagnosis"]["replication_ok"] is True and t["diagnosis"]["pass2_reached"]
    assert t["determinism_w4"]["identical"]
    f = t["diagnosis"]["funnel"]
    # strands without a read (negative-binomial coverage) are outer-code erasures; every row keeps k strands
    assert f["data_rows"]["after_consensus"] == f["data_rows"]["total"]
    assert set(t["diagnosis"]["strand_stage_counts"]["data"]) <= {"recovered", "coverage"}
    _check_funnel(t)


def test_nanopore_like_fails_explicitly_with_a_first_stage(setup):
    t = adiag.run_trial(_job(setup, "nanopore-like"))
    assert t["decode"]["outcome"]["outcome"] == "EXPLICIT_FAILURE"
    assert t["decode"]["error_code"] == "NO_SUPERBLOCK"
    assert t["decode"]["stage_counters"]["terminal_stage"] == "superblock"
    assert t["diagnosis"]["first_failing_stage"] in adiag.STRAND_STAGES + ("superblock",)
    assert t["diagnosis"]["units"][0]["unit"] == "superblock"
    assert t["determinism_w4"]["identical"]
    _check_funnel(t)


def test_pass2_failure_is_attributed_and_replication_matches_the_decoder(setup):
    t = adiag.run_trial(_job(setup, "deletion-heavy"))
    oc = t["decode"]["outcome"]["outcome"]
    assert oc in ("EXACT", "EXPLICIT_FAILURE", "PARTIAL")
    if t["diagnosis"]["pass2_reached"]:
        assert t["diagnosis"]["replication_ok"] is True
    if oc != "EXACT":
        assert t["diagnosis"]["first_failing_stage"] in adiag.DECODE_STAGES
    _check_funnel(t)


def test_truth_refuses_a_read_file_that_is_not_the_simulators(setup, tmp_path):
    d, info, models = setup
    model = p4.chn.load_model(models["illumina-like"])
    reads = tmp_path / "r.fastq"
    p4.chn.simulate_with_sidecar(model, d / "strands.fasta", reads, 82001, workers=1)
    src, rc, rl = adiag.read_truth(model, d / "strands.fasta", 82001, reads)
    assert src.size == len(rl) == rc.size
    lines = reads.read_text().split("\n")
    lines[1] = ("A" if lines[1][0] != "A" else "C") + lines[1][1:]           # one base of the first read
    reads.write_text("\n".join(lines))
    with pytest.raises(SystemExit):
        adiag.read_truth(model, d / "strands.fasta", 82001, reads)


def test_seeds_outside_the_exploration_range_are_refused(tmp_path):
    import json
    cfg = json.loads((ADIAG / "config.json").read_text())
    cfg["base_seed"] = 82100                                             # confirmatory range: needs a PREREG
    cfg["cells"] = cfg["cells"][:1]
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg))
    with pytest.raises(SystemExit):
        adiag.main(["run", "--config", str(p), "--jobs", "1", "--seeds", "1", "--out", str(tmp_path / "t.jsonl")])
