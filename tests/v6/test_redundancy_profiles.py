"""V6 redundancy profiles: presets over existing options; balanced is byte-identical to the V5 default encoding."""
from __future__ import annotations

import hashlib

import pytest
from typer.testing import CliRunner

from vnxdna.v4 import archive as ar
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en
from vnxdna.v4.cli import app
from vnxdna.v6 import profiles as pr
from vnxdna.v6.errors import V6ConfigurationError


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("prof")
    datagen.generate(d / "in.bin", 30_000, "random", 8101)
    ar.build_archive([d / "in.bin"], d / "a.vnx", ar.ArchiveOptions())
    return d


def test_unknown_profile_rejected():
    with pytest.raises(V6ConfigurationError):
        pr.dna_options("fastest")


def test_balanced_is_the_v5_default(arc):
    en.encode_container(arc / "a.vnx", arc / "default.fasta", en.DNAOptions())
    en.encode_container(arc / "a.vnx", arc / "balanced.fasta", pr.dna_options("balanced"))
    assert (arc / "default.fasta").read_bytes() == (arc / "balanced.fasta").read_bytes()


@pytest.mark.parametrize("name", sorted(pr.REDUNDANCY_PROFILES))
def test_every_profile_round_trips(arc, name):
    out = arc / f"rt-{name}.fasta"
    en.encode_container(arc / "a.vnx", out, pr.dna_options(name))
    res = de.decode_reads(out, arc / f"{name}.vnx", de.DecodeOptions(), overwrite=True)
    assert res.status == "SUCCESS"
    assert (arc / f"{name}.vnx").read_bytes() == (arc / "a.vnx").read_bytes()


def test_density_order_is_as_described():
    d = {n: pr.describe(n) for n in pr.REDUNDANCY_PROFILES}
    assert d["maximum-density"]["nominal_nt_per_byte"] < d["balanced"]["nominal_nt_per_byte"] \
        < d["maximum-recovery"]["nominal_nt_per_byte"]
    assert all(v["classification"] == "THEORETICAL" for v in d.values())


def test_overrides_win():
    o = pr.dna_options("maximum-recovery", column_parity=1, workers=2)
    assert o.column_parity == 1 and o.workers == 2 and o.profile == "v4-archival"


def test_cli_redundancy_profile(arc):
    r = CliRunner().invoke(app, ["encode", str(arc / "a.vnx"), str(arc / "cli.fasta"), "--redundancy-profile",
                                 "maximum-recovery", "--force"])
    assert r.exit_code == 0, r.output
    ref = arc / "ref.fasta"
    en.encode_container(arc / "a.vnx", ref, pr.dna_options("maximum-recovery"))
    assert hashlib.sha256((arc / "cli.fasta").read_bytes()).digest() == hashlib.sha256(ref.read_bytes()).digest()
    bad = CliRunner().invoke(app, ["encode", str(arc / "a.vnx"), str(arc / "x.fasta"), "--redundancy-profile", "nope"])
    assert bad.exit_code == 7                      # configuration error exit code
    assert "unknown redundancy profile" in bad.output


def test_high_dropout_is_the_benchmarked_lab_layout(arc):
    """high-dropout encodes exactly like the explicit options of the b0-dropout lab profile hd-l256-i4 (SIMULATED study:
    benchmarks/competitors/lab/results/b0-dropout); it is opt-in and the default stays balanced."""
    import json
    from pathlib import Path

    from vnxdna.dnaenc.layout import Layout
    cfg = json.loads((Path(__file__).resolve().parents[2] / "benchmarks/competitors/lab/adapters/vnx/profiles/hd-l256-i4.json")
                     .read_text())["dna"]
    explicit = en.DNAOptions(layout=Layout(**cfg["layout"]), data_symbols=cfg["data_symbols"],
                             parity_symbols=cfg["parity_symbols"])
    en.encode_container(arc / "a.vnx", arc / "hd-explicit.fasta", explicit)
    en.encode_container(arc / "a.vnx", arc / "hd-named.fasta", pr.dna_options("high-dropout"))
    assert (arc / "hd-explicit.fasta").read_bytes() == (arc / "hd-named.fasta").read_bytes()
    assert en.DNAOptions().profile == "v4-balanced"
    d = pr.describe("high-dropout")
    assert d["strand_nt"] == 256 and d["row_code"] == [160, 48] and d["sync_markers"] is False


def test_high_dropout_rate_and_dropout_tolerance(tmp_path):
    """About 1.0 bit/nt on a 19,456-byte random input without compression (within 2 %), and the archive decodes
    exactly from strands with 10 % of them dropped, with layout auto-detection (no profile passed to the decoder)."""
    import random
    datagen.generate(tmp_path / "in.bin", 19_456, "random", 8102)
    ar.build_archive([tmp_path / "in.bin"], tmp_path / "a.vnx", ar.ArchiveOptions(compression="none"))
    en.encode_container(tmp_path / "a.vnx", tmp_path / "s.fasta", pr.dna_options("high-dropout"))
    lines = (tmp_path / "s.fasta").read_text().split("\n")
    recs = [(lines[i], lines[i + 1]) for i in range(0, len(lines) - 1, 2) if lines[i].startswith(">")]
    nt = sum(len(s) for _, s in recs)
    assert abs(19_456 * 8 / nt - 1.0) <= 0.02
    rng = random.Random(8103)
    kept = [r for r in recs if rng.random() >= 0.10]
    assert len(kept) < len(recs)
    (tmp_path / "d.fasta").write_text("".join(f"{h}\n{s}\n" for h, s in kept))
    res = de.decode_reads(tmp_path / "d.fasta", tmp_path / "out.vnx", de.DecodeOptions(), overwrite=True)
    assert res.status == "SUCCESS"
    assert (tmp_path / "out.vnx").read_bytes() == (tmp_path / "a.vnx").read_bytes()


def test_v7_lowcov_survives_strand_loss_beyond_balanced(tmp_path):
    """v7-lowcov (opt-in, experiments/v7/a-par, SIMULATED) keeps the v4-balanced strand layout with row code 64 + 48:
    the archive decodes exactly, with layout auto-detection, from strands with 30 % of them dropped, while v4-balanced
    (64 + 16) refuses the same loss explicitly instead of returning wrong data."""
    import random

    from vnxdna.dnaenc.layout import PROFILES
    assert PROFILES["v7-lowcov"][0] == PROFILES["v4-balanced"][0]
    assert PROFILES["v7-lowcov"][1:] == (64, 48)
    datagen.generate(tmp_path / "in.bin", 12_000, "random", 8104)
    ar.build_archive([tmp_path / "in.bin"], tmp_path / "a.vnx", ar.ArchiveOptions(compression="none"))
    for profile, ok in (("v7-lowcov", True), ("v4-balanced", False)):
        en.encode_container(tmp_path / "a.vnx", tmp_path / f"{profile}.fasta", en.DNAOptions(profile=profile))
        lines = (tmp_path / f"{profile}.fasta").read_text().split("\n")
        recs = [(lines[i], lines[i + 1]) for i in range(0, len(lines) - 1, 2) if lines[i].startswith(">")]
        rng = random.Random(8105)
        kept = [r for r in recs if rng.random() >= 0.30]
        (tmp_path / "d.fasta").write_text("".join(f"{h}\n{s}\n" for h, s in kept))
        out = tmp_path / f"{profile}.vnx"
        res = de.decode_reads(tmp_path / "d.fasta", out, de.DecodeOptions(), overwrite=True)
        if ok:
            assert res.status == "SUCCESS"
            assert out.read_bytes() == (tmp_path / "a.vnx").read_bytes()
        else:
            assert res.status != "SUCCESS"
