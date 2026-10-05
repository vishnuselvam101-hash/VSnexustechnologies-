"""V6 Phase 3 acceptance (SIMULATED channel; no biological claims): the 14 Phase 1 models load as ``/0`` and as the
shipped ``/1`` conversions, and the staged simulator gives byte-identical reads to the current simulator (the Phase 1
composer ``experiments/v6/channel/channel.py`` = ``vnxdna.simulation.loss`` + ``vnxdna.simulation.channel``) for identical
seeds. Old ``ChannelConfig`` JSON is still accepted by ``sdk.simulate`` / ``vnx channel simulate`` with byte-identical
output to ``vnxdna.simulation.channel.simulate_file``."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from vnxdna import sdk
from vnxdna.core import schema
from vnxdna.simulation import channel as v4ch
from vnxdna.simulation import engine, registry
from vnxdna.simulation import model as cm

ROOT = Path(__file__).resolve().parents[2]
CHAN_DIR = ROOT / "experiments" / "v6" / "channel"
V0_DIR = CHAN_DIR / "models"
sys.path.insert(0, str(CHAN_DIR))
import channel as chn  # noqa: E402

NAMES = sorted(p.stem for p in V0_DIR.glob("*.json"))


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _random_strands(path: Path, n: int, length: int, seed: int) -> Path:
    rng = np.random.default_rng(seed)
    with open(path, "w") as f:
        for i, row in enumerate(rng.integers(0, 4, (n, length))):
            f.write(f">s{i}\n{''.join('ACGT'[b] for b in row)}\n")
    return path


@pytest.fixture(scope="module")
def strand_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("strands")
    return {"random-1100x100": _random_strands(d / "a.fasta", 1100, 100, 31),     # two simulator batches
            "random-300x80": _random_strands(d / "b.fasta", 300, 80, 32),
            "v6-stripes-seq": ROOT / "tests" / "fixtures" / "v6_0" / "stripes-seq.strands.fasta"}   # an encoder output


def test_fourteen_models_shipped():
    assert len(NAMES) == 14 and registry.model_names() == NAMES
    assert [n for n, _ in registry.available()] == NAMES


@pytest.mark.parametrize("name", NAMES)
def test_shipped_v1_is_the_conversion_of_the_v0_file(name):
    v0_path = V0_DIR / f"{name}.json"
    m0, seed = cm.read_file(v0_path)
    assert seed is None and m0.read_as == cm.SCHEMA_V0
    m1 = registry.load_model(name)
    assert m1.read_as == cm.SCHEMA_V1 and m1.doc == m0.doc and m1.sha256 == m0.sha256
    assert m1.doc["provenance"]["converted_from"] == {"file": v0_path.name, "sha256": sha(v0_path), "schema": cm.SCHEMA_V0}
    assert m1.doc["data_source"] == "SIMULATED" and m1.doc["evidence_class"] == "SIMULATED"
    schema.validate(m1.doc, "vnx.channel-model/1")
    # and back: /1 → /0 restores the Phase 1 document exactly
    assert m1.to_v0() == json.loads(v0_path.read_text())
    # the shipped file is the canonical dump
    assert (registry.MODEL_DIR / f"{name}.json").read_text() == m1.dumps()


def _reference(name: str, strands: Path, out: Path, seed: int) -> str:
    chn.compose(chn.load_model(name), strands, out, seed)
    return sha(out)


CASES = [(n, "random-1100x100", s) for n in NAMES for s in (0, 77)] + \
        [(n, f, 5) for n in NAMES for f in ("random-300x80", "v6-stripes-seq")]


@pytest.mark.parametrize("name,strands,seed", CASES)
def test_byte_identical_reads_v0_and_v1(name, strands, seed, strand_files, tmp_path):
    src = strand_files[strands]
    ref = _reference(name, src, tmp_path / "ref.fastq", seed)
    m0, _ = cm.read_file(V0_DIR / f"{name}.json")
    m1 = registry.load_model(f"{name}@1.0.0")
    r0 = engine.simulate_file(src, tmp_path / "v0.fastq", m0, seed)
    r1 = engine.simulate_file(src, tmp_path / "v1.fastq", m1, seed)
    assert sha(tmp_path / "v0.fastq") == ref == sha(tmp_path / "v1.fastq"), (name, strands, seed)
    assert r1["metadata"]["output"]["sha256"] == ref
    # the V4 counters agree with the reference composer's
    rep = chn.compose(chn.load_model(name), src, tmp_path / "ref2.fastq", seed)
    for k in engine.V4_STATS:
        assert r0[k] == r1[k] == rep["channel"][k], (name, k)
    assert r1["storage_lost"] == rep["loss"]["lost"]


@pytest.mark.parametrize("name", ["mixed-harsh", "burst-loss", "nanopore-like"])
def test_byte_identical_across_workers(name, strand_files, tmp_path):
    src = strand_files["random-1100x100"]
    ref = _reference(name, src, tmp_path / "ref.fastq", 9)
    m = registry.load_model(name)
    engine.simulate_file(src, tmp_path / "w3.fastq", m, 9, workers=3)
    assert sha(tmp_path / "w3.fastq") == ref


CONFIGS = [
    {},
    {"substitution_rate": 0.01, "insertion_rate": 0.002, "deletion_rate": 0.003, "dropout_rate": 0.05, "coverage": 4,
     "seed": 7},
    {"coverage": 6, "coverage_model": "negative-binomial", "coverage_dispersion": 1.5, "gc_bias_strength": 0.4,
     "duplication_rate": 0.1, "reverse_complement_rate": 0.5, "seed": 99},
    {"coverage": 3, "coverage_model": "fixed", "gc_bias_strength": 0.2, "burst_rate": 0.05, "burst_max_len": 6,
     "n_rate": 0.01, "quality_informative": 0.7, "homopolymer_indel_multiplier": 3.0,
     "homopolymer_substitution_multiplier": 2.0, "homopolymer_min_run": 2, "deletion_rate": 0.002, "seed": 3},
    {"coverage": 5, "coverage_model": "poisson", "shuffle_window": 97, "substitution_rate": 0.002, "seed": 11},
]


@pytest.mark.parametrize("k", range(len(CONFIGS)))
def test_old_channel_config_json_is_byte_identical(k, strand_files, tmp_path):
    cfg_doc = CONFIGS[k]
    src = strand_files["random-1100x100"]
    path = tmp_path / "channel.json"
    path.write_text(json.dumps(cfg_doc))
    cfg = v4ch.ChannelConfig.load(path)
    v4ch.simulate_file(src, tmp_path / "v4.fastq", cfg)
    res = sdk.simulate(src, tmp_path / "sdk.fastq", config=path)
    assert sha(tmp_path / "sdk.fastq") == sha(tmp_path / "v4.fastq")
    body = res.body
    assert body["model"] == "channel-config" and body["model_schema"] == cm.CONFIG_SCHEMA_V0 and body["seed"] == cfg.seed
    assert body["config"] == cfg.to_dict() and body["evidence_class"] == "SIMULATED"
    # the object form and three workers give the same reads
    sdk.simulate(src, tmp_path / "obj.fastq", config=cfg, workers=3)
    assert sha(tmp_path / "obj.fastq") == sha(tmp_path / "v4.fastq")


def test_channel_config_overrides_match_v4_semantics(strand_files, tmp_path):
    """--seed / --coverage / rate overrides on a ChannelConfig: the same reads as setting the V4 fields."""
    src = strand_files["random-300x80"]
    sdk.simulate(src, tmp_path / "a.fastq", seed=21, coverage=2.5, overrides={"substitution_rate": 0.01,
                                                                                "dropout_rate": 0.1})
    cfg = v4ch.ChannelConfig(seed=21, coverage=2.5, coverage_model="poisson", substitution_rate=0.01, dropout_rate=0.1)
    v4ch.simulate_file(src, tmp_path / "b.fastq", cfg.validate())
    assert sha(tmp_path / "a.fastq") == sha(tmp_path / "b.fastq")
    # the V1 SDK defaults (no config at all) are the ChannelConfig defaults, seed 12345
    sdk.simulate(src, tmp_path / "c.fastq")
    v4ch.simulate_file(src, tmp_path / "d.fastq", v4ch.ChannelConfig().validate())
    assert sha(tmp_path / "c.fastq") == sha(tmp_path / "d.fastq")


def test_channel_config_with_schema_v1_and_v0_markers(strand_files, tmp_path):
    src = strand_files["random-300x80"]
    doc = {"substitution_rate": 0.01, "coverage": 2, "seed": 4}
    v4ch.simulate_file(src, tmp_path / "ref.fastq", v4ch.ChannelConfig.from_dict(doc))
    for sid in ("vnx.channel-config/0", "vnx.channel-config/1"):
        p = tmp_path / f"{sid[-1]}.json"
        p.write_text(json.dumps({"schema": sid, **doc}))
        res = sdk.simulate(src, tmp_path / f"{sid[-1]}.fastq", config=p)
        assert sha(tmp_path / f"{sid[-1]}.fastq") == sha(tmp_path / "ref.fastq") and res.body["model_schema"] == sid


def test_cli_named_model_matches_reference(strand_files, tmp_path):
    """``vnx channel simulate --model NAME[@VERSION] --seed N``: named models with loss and bursts (burst-loss)."""
    from typer.testing import CliRunner

    from vnxdna.commands import app
    src = strand_files["random-1100x100"]
    ref = _reference("burst-loss", src, tmp_path / "ref.fastq", 13)
    for ref_name in ("burst-loss", "burst-loss@1.0.0", str(V0_DIR / "burst-loss.json")):
        out = tmp_path / "cli.fastq"
        r = CliRunner().invoke(app, ["channel", "simulate", str(src), str(out), "--model", ref_name, "--seed", "13", "-f"])
        assert r.exit_code == 0, r.stdout
        doc = json.loads(r.stdout)
        assert sha(out) == ref
        assert doc["result"]["model"] == "burst-loss" and doc["result"]["model_version"] == "1.0.0"
        assert doc["result"]["storage_lost"] > 0 and doc["result"]["bursts"] > 0
        assert doc["result"]["metadata"]["output"]["sha256"] == ref
