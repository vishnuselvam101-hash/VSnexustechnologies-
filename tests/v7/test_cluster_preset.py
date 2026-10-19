"""V15 CC-3: the opt-in ``nanopore`` read-clustering preset (decoder preset F) from the SDK, config files and the CLI.

SIMULATED: reads from the software channel simulator; no DNA was synthesised, stored or sequenced.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vnxdna.commands.cli import app
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.recovery.cluster import CLUSTER_PRESETS, ClusterConfig, cluster_preset
from vnxdna.sdk.config import decode_options, validate_config
from vnxdna.simulation.channel import ChannelConfig, simulate_file

from .cluster_support import make_strands

ROOT = Path(__file__).resolve().parents[2]


def test_nanopore_preset_is_evaluated_preset_f():
    spec = importlib.util.spec_from_file_location("cands", ROOT / "experiments/v9/consensus/candidates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert CLUSTER_PRESETS["nanopore"] == mod.cluster_config("F")
    assert cluster_preset("nanopore") == ClusterConfig(**mod.cluster_config("F"))
    assert cluster_preset("default") == ClusterConfig()
    with pytest.raises(VNXConfigurationError):
        cluster_preset("F")


def test_preset_turns_clustering_on_and_rejects_off():
    assert decode_options({}).read_clustering == "off"                     # the default path is unchanged
    opt = decode_options({}, cluster_preset="nanopore")
    assert opt.read_clustering == "fallback" and opt.cluster_config.outer_list == "on"
    cfg = validate_config({"decode": {"cluster_preset": "nanopore"}})
    assert decode_options(cfg).cluster_config == cluster_preset("nanopore")
    with pytest.raises(VNXConfigurationError):
        decode_options({}, cluster_preset="nanopore", read_clustering="off")
    with pytest.raises(VNXConfigurationError):
        decode_options({}, cluster_preset="nope")


@pytest.fixture(scope="module")
def pool(tmp_path_factory):
    d = tmp_path_factory.mktemp("preset_pool")
    container, _ = make_strands(d, 1200)
    reads = {}
    for name, cfg in {
        # the indel-heavy pool of test_read_clustering: the default path leaves a row undecodable
        "helped": ChannelConfig(substitution_rate=0.01, insertion_rate=0.005, deletion_rate=0.015, coverage=10.0, seed=1,
                                coverage_model="negative-binomial", coverage_dispersion=4.0,
                                reverse_complement_rate=0.5, n_rate=0.001),
        "clean": ChannelConfig(substitution_rate=0.002, coverage=4.0, coverage_model="fixed",
                               reverse_complement_rate=0.5, seed=7704),
    }.items():
        simulate_file(d / "s.fasta", d / f"{name}.fastq", cfg)
        reads[name] = d / f"{name}.fastq"
    return {"dir": d, "sha": hashlib.sha256(container).hexdigest(), "reads": reads}


@pytest.mark.parametrize("name", ["helped", "clean"])
def test_cli_nanopore_preset_decodes_exactly(pool, tmp_path, name):
    out, rep = tmp_path / "o.vnx", tmp_path / "r.json"
    r = CliRunner().invoke(app, ["decode", str(pool["reads"][name]), "-o", str(out), "--cluster-preset", "nanopore",
                                 "--report", str(rep), "--allow-unencrypted"])
    assert r.exit_code == 0, r.output
    assert hashlib.sha256(out.read_bytes()).hexdigest() == pool["sha"]
    cl = json.loads(rep.read_text()).get("clustering") or {}
    assert cl["mode"] == "fallback" and cl["config"]["outer_list"] == "on" and cl["config"]["fill"] == "template"
    if name == "helped":
        assert cl["status"] == "ran" and "outer_list" in cl


def test_cli_without_preset_leaves_the_helped_pool_partial(pool, tmp_path):
    """The default path (no preset) does not recover the indel-heavy pool; only the opt-in preset changes that."""
    out, rep = tmp_path / "o.vnx", tmp_path / "r.json"
    r = CliRunner().invoke(app, ["decode", str(pool["reads"]["helped"]), "-o", str(out), "--report", str(rep),
                                 "--allow-unencrypted"])
    assert r.exit_code != 0 and json.loads(rep.read_text())["status"] != "SUCCESS"
    assert not out.exists() or hashlib.sha256(out.read_bytes()).hexdigest() != pool["sha"]
