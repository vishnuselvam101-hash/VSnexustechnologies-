"""V7 item A decode-level tests: ``DecodeOptions(read_clustering=...)`` / ``vnx decode --read-clustering``.

Pinned here (V7_ARCHITECTURE §7, §8; directive §20): the default stays "off"; with "off" the decode is the 6.0 path
byte for byte (output and report); the V4/V5/V6 golden fixtures decode bit-exactly with clustering on as well; with it
on, a SIMULATED pool that 6.0 cannot decode is recovered EXACTLY (protocol §6 classifier), the cluster frames only fill
what 6.0 left unresolved, the outcome and every counter are identical for 1 and 4 workers, budgets stop the stage and
are named in the report, and malformed or pathological reads are counted or refused with a typed error — never a
FALSE SUCCESS. SYNTHETIC test data; SIMULATED channel.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from vnxdna.benchmark.outcome import EXACT, EXPLICIT_FAILURE, FALSE_SUCCESS, classify_outcome, decode_claim
from vnxdna.commands.cli import app
from vnxdna.core.errors import VNXFormatError
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.simulation.channel import ChannelConfig, simulate_file
from vnxdna.v4 import decoder as de
from vnxdna.v4.errors import VNXError

from .cluster_support import make_strands

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "read_clustering_golden.json"
PASSPHRASE = "vnx-test-passphrase-NOT-A-SECRET"
VOLATILE = {"seconds", "stage_seconds", "peak_rss_bytes", "elapsed", "elapsed_seconds", "wall_seconds"}
GOLDEN_SETS = [(s, c) for s in ("v4_0", "v5_0") for c in ("balanced", "archival", "encrypted", "multifile")] + \
    [("v6_0", c) for c in sorted(json.loads((FIXTURES / "v6_0" / "manifest.json").read_text())["cases"])]


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _run(reads: Path, out: Path, **kw) -> tuple:
    """(status or error, output bytes or None, report without volatile keys)."""
    pw = kw.pop("passphrase", None)
    if out.exists():
        out.unlink()
    try:
        res = de.decode_reads(reads, out, de.DecodeOptions(**kw), overwrite=True, passphrase=pw)
        status, rep = res.status, res.report
    except VNXError as error:
        status, rep = f"ERROR:{error.code}", dict(error.details)
    data = out.read_bytes() if out.exists() else None
    return status, data, json.loads(json.dumps(_strip(rep), default=str))


def _outcome(reads: Path, out: Path, container: bytes, **kw) -> dict:
    import hashlib
    if out.exists():
        out.unlink()
    claim, _, _ = decode_claim(lambda: de.decode_reads(reads, out, de.DecodeOptions(**kw)), out)
    return classify_outcome(claim, {"container": hashlib.sha256(container).hexdigest()})


@pytest.fixture(scope="module")
def pools(tmp_path_factory):
    d = tmp_path_factory.mktemp("rc_pools")
    container, _ = make_strands(d, 1200)
    common = dict(coverage_model="negative-binomial", coverage_dispersion=4.0, reverse_complement_rate=0.5, n_rate=0.001)
    cfgs = {
        # SIMULATED indel-heavy pool: 6.0 leaves a row undecodable; clustering fills it (EXACT)
        "helped": ChannelConfig(substitution_rate=0.01, insertion_rate=0.005, deletion_rate=0.015, coverage=10.0, seed=1,
                                **common),
        "clean": ChannelConfig(substitution_rate=0.002, coverage=4.0, coverage_model="fixed",
                               reverse_complement_rate=0.5, seed=7704),
    }
    paths = {}
    for name, cfg in cfgs.items():
        simulate_file(d / "s.fasta", d / f"{name}.fastq", cfg)
        paths[name] = d / f"{name}.fastq"
    return {"dir": d, "container": container, "reads": paths}


@pytest.fixture(scope="module")
def golden_reads(tmp_path_factory):
    d = tmp_path_factory.mktemp("rc_golden")
    out = {}
    for s, c in GOLDEN_SETS:
        p = d / f"{s}-{c}.fastq"
        p.write_bytes(gzip.decompress((FIXTURES / s / f"{c}.reads.fastq.gz").read_bytes()))
        out[(s, c)] = p
    return out


def _encrypted(s: str, c: str) -> bool:
    return json.loads((FIXTURES / s / "manifest.json").read_text())["cases"][c]["encrypted"]


# ------------------------------------------------------------------------------------------------ compatibility
@pytest.mark.parametrize("pool", ["helped", "clean"])
def test_off_is_the_default_and_the_6_0_path(pool, pools, tmp_path):
    assert de.DecodeOptions().read_clustering == "off"
    default = _run(pools["reads"][pool], tmp_path / "a.vnx")
    off = _run(pools["reads"][pool], tmp_path / "b.vnx", read_clustering="off")
    assert default == off
    assert "clustering" not in off[2]


@pytest.mark.parametrize("s,c", GOLDEN_SETS, ids=[f"{s}-{c}" for s, c in GOLDEN_SETS])
def test_golden_fixtures_decode_bit_exactly_with_clustering_on(s, c, golden_reads, tmp_path):
    kw = {"passphrase": PASSPHRASE} if _encrypted(s, c) else {}
    off = _run(golden_reads[(s, c)], tmp_path / "off.vnx", **kw)
    on = _run(golden_reads[(s, c)], tmp_path / "on.vnx", read_clustering="fallback", **kw)
    assert off[0] == on[0] == "SUCCESS"
    assert on[1] == off[1] == (FIXTURES / s / f"{c}.vnx").read_bytes()
    block = on[2].pop("clustering")
    assert block["status"] == "not_run"                    # 6.0 decodes these pools: the stage never runs
    assert on[2] == off[2]                                  # every other report key and value unchanged


def test_clean_pool_identical_with_clustering_on(pools, tmp_path):
    off = _run(pools["reads"]["clean"], tmp_path / "off.vnx", stage_counters=True)
    on = _run(pools["reads"]["clean"], tmp_path / "on.vnx", stage_counters=True, read_clustering="fallback")
    assert off[0] == on[0] == "SUCCESS" and off[1] == on[1] == pools["container"]
    assert on[2]["clustering"]["status"] == "not_run"
    sc_on = on[2].pop("stage_counters")
    sc_off = off[2].pop("stage_counters")
    on[2].pop("clustering")
    assert on[2] == off[2]
    assert sc_off["stages"]["clustering"] == {"applicable": 0}
    assert sc_on["stages"]["clustering"]["applicable"] == 1 and sc_on["stages"]["clustering"]["stage_run"] == 0
    for st in sc_off["stages"]:
        if st != "clustering":
            assert sc_on["stages"][st] == sc_off["stages"][st]


# ------------------------------------------------------------------------------------------------ recovery
def test_clustering_turns_a_6_0_failure_into_exact(pools, tmp_path):
    p = pools["reads"]["helped"]
    off = _outcome(p, tmp_path / "off.vnx", pools["container"])
    on = _outcome(p, tmp_path / "on.vnx", pools["container"], read_clustering="fallback")
    assert off["outcome"] == EXPLICIT_FAILURE
    assert on["outcome"] == EXACT


def test_fill_only_counters_and_conservation(pools, tmp_path):
    st, data, rep = _run(pools["reads"]["helped"], tmp_path / "o.vnx", read_clustering="fallback", stage_counters=True)
    assert st == "SUCCESS" and data == pools["container"]
    cl = rep["clustering"]
    assert cl["status"] == "ran" and cl["trigger"] == "rows" and cl["budget_exceeded"] == []
    assert cl["fill"]["data_symbols"] > 0 and cl["fill"].get("data_conflicts", 0) == 0
    # cluster frames at addresses 6.0 resolved are never used: they are confirmations (or conflicts), not fills
    assert cl["fill"]["data_confirmations"] > 0
    c = cl["clustering"]
    assert c["clustered_reads"] + c["unassigned"] == c["reads"] == cl["store"]["unplaced_stored"]
    assert cl["store"]["unplaced_offered"] == cl["store"]["unplaced_stored"] + cl["store"]["unplaced_over_budget"]
    sc = rep["stage_counters"]["stages"]
    assert sc["clustering"]["applicable"] == 1 and sc["clustering"]["stage_run"] == 1
    assert sc["outer_ecc"]["symbols_from_cluster"] == cl["fill"]["data_symbols"]
    assert sc["clustering"]["unassigned"] == c["unassigned"]
    assert sc["consensus"]["cluster_frames_verified"] == cl["frames_verified"]
    # pass-1 counters are those of the 6.0 path: clustering never changes a pass-1 decision
    off = _run(pools["reads"]["helped"], tmp_path / "x.vnx", stage_counters=True)[2]["stage_counters"]["stages"]
    for stage in ("read_parsing", "orientation", "alignment", "indel_placement"):
        assert sc[stage] == off[stage]
    assert rep["reads"] == _run(pools["reads"]["helped"], tmp_path / "y.vnx")[2]["reads"]


def test_regression_golden(pools, tmp_path):
    """Pinned outcome and counters of the 'helped' pool (EXPERIMENTAL reference behaviour; regenerate only with a
    reviewed algorithm change)."""
    st, data, rep = _run(pools["reads"]["helped"], tmp_path / "o.vnx", read_clustering="fallback")
    import hashlib
    cl = rep["clustering"]
    got = {"status": st, "container_sha256": hashlib.sha256(data).hexdigest() if data else None,
           "store": cl["store"], "clustering": cl["clustering"], "consensus": cl["consensus"],
           "frames_verified": cl["frames_verified"], "fill": cl["fill"]}
    want = json.loads(GOLDEN.read_text())
    assert got == want


def test_worker_count_and_batch_size_do_not_change_anything(pools, tmp_path):
    p = pools["reads"]["helped"]
    one = _run(p, tmp_path / "w1.vnx", read_clustering="fallback", stage_counters=True, workers=1, batch_reads=128)
    four = _run(p, tmp_path / "w4.vnx", read_clustering="fallback", stage_counters=True, workers=4, batch_reads=128)
    big = _run(p, tmp_path / "wb.vnx", read_clustering="fallback", stage_counters=True, workers=1)
    assert one == four
    assert one[0] == big[0] and one[1] == big[1] and one[2]["clustering"] == big[2]["clustering"]


# ------------------------------------------------------------------------------------------------ budgets
def test_unplaced_read_budget_stops_the_stage_and_is_reported(pools, tmp_path):
    p = pools["reads"]["helped"]
    off = _run(p, tmp_path / "off.vnx")
    st, data, rep = _run(p, tmp_path / "on.vnx", read_clustering="fallback", stage_counters=True,
                         cluster_config=ClusterConfig(max_unplaced_reads=100))
    cl = rep["clustering"]
    assert cl["status"] == "budget_exceeded"
    assert [b["limit"] for b in cl["budget_exceeded"]] == ["max_unplaced_reads"]
    assert cl["store"]["unplaced_stored"] == 100 and cl["store"]["unplaced_over_budget"] > 0
    assert (st, data) == (off[0], off[1]) and data is None           # the 6.0 outcome; nothing published
    assert rep["stage_counters"]["stages"]["clustering"]["unplaced_over_budget"] > 0
    assert any(d["stage"] == "CLUSTER" and not d["run"] for d in rep["recovery_plan"]["decisions"])


def test_candidate_pair_budget_stops_the_stage_and_is_reported(pools, tmp_path):
    st, data, rep = _run(pools["reads"]["helped"], tmp_path / "on.vnx", read_clustering="fallback",
                         cluster_config=ClusterConfig(max_candidate_pairs=10))
    cl = rep["clustering"]
    assert cl["status"] == "budget_exceeded" and cl["budget_exceeded"][0]["limit"] == "max_candidate_pairs"
    assert cl["frames_verified"] == 0 and data is None and st == "FAILURE"


# ------------------------------------------------------------------------------------------------ negative
def test_malformed_read_file_is_refused_with_a_typed_error(tmp_path):
    p = tmp_path / "bad.fastq"
    p.write_bytes(b"@r1\nACGT\x00\xffNNACGT\n+\n!!!!\n" + bytes(range(256)) * 4)
    for mode in ("off", "fallback"):
        with pytest.raises(VNXError) as e:
            de.decode_reads(p, tmp_path / "o.vnx", de.DecodeOptions(read_clustering=mode))
        assert isinstance(e.value, VNXFormatError) or e.value.exit_code in (3, 5)
        assert not (tmp_path / "o.vnx").exists()


def test_pathological_lengths_are_counted_never_a_false_success(pools, tmp_path):
    rng = np.random.default_rng(41)
    text = pools["reads"]["helped"].read_text()
    extra = []
    for i, n in enumerate([1, 3, 100, 156, 392, 393, 2000, 5000]):
        seq = "".join("ACGT"[x] for x in rng.integers(0, 4, n))
        extra.append(f"@x{i}\n{seq}\n+\n{'I' * n}\n")
    extra.append(f"@n\n{'N' * 313}\n+\n{'I' * 313}\n")
    p = tmp_path / "patho.fastq"
    p.write_text(text + "".join(extra))
    st, data, rep = _run(p, tmp_path / "o.vnx", read_clustering="fallback")
    store = rep["clustering"]["store"]
    assert store["unplaced_too_short"] >= 3 and store["unplaced_too_long"] >= 3     # < 156 nt; > 392 nt
    oc = _outcome(p, tmp_path / "c.vnx", pools["container"], read_clustering="fallback")
    assert oc["outcome"] != FALSE_SUCCESS and oc["outcome"] in (EXACT, EXPLICIT_FAILURE)


# ------------------------------------------------------------------------------------------------ CLI
def test_cli_read_clustering(pools, tmp_path):
    p = pools["reads"]["clean"]
    r = CliRunner().invoke(app, ["decode", str(p), "-o", str(tmp_path / "o.vnx"), "--read-clustering", "fallback"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["status"] == "SUCCESS"
    assert (tmp_path / "o.vnx").read_bytes() == pools["container"]
    r = CliRunner().invoke(app, ["decode", str(p), "-o", str(tmp_path / "p.vnx"), "--read-clustering", "always"])
    assert r.exit_code == 7, r.output
    assert not (tmp_path / "p.vnx").exists()
    from vnxdna.sdk.config import decode_options_for
    assert decode_options_for(None, None).read_clustering == "off"
    assert decode_options_for(None, None, read_clustering="fallback").read_clustering == "fallback"


def test_random_access_with_clustering_on(pools, tmp_path):
    """--select reads the index and the selected file's groups; with clustering the same fill-only rule applies."""
    p = pools["reads"]["helped"]
    out = tmp_path / "sel"
    claim, res, err = decode_claim(lambda: de.decode_reads(p, None, de.DecodeOptions(read_clustering="fallback"),
                                                          select=["in.bin"], select_dir=out), None,
                                   select_dir=out, selective=True)
    import hashlib
    truth = {"in.bin": hashlib.sha256((pools["dir"] / "in.bin").read_bytes()).hexdigest()}
    oc = classify_outcome(claim, truth)
    assert oc["outcome"] == EXACT, (oc, err)
    assert res.report["clustering"]["status"] == "ran"
