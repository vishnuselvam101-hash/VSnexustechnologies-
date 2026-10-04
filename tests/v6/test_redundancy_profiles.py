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
