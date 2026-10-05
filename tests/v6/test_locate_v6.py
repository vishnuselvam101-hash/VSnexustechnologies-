"""``vnx locate --dna-profile`` (audit §5.5): the strand mapping it prints is the V4 geometry with sequential order. For
an archive encoded with the V6 outer code (superblock version 2: column-parity groups, interleaved order, adaptive plan)
those ranges are wrong, so it must refuse with CONFIGURATION_ERROR (exit 7) instead of printing them. Custom K/M give
correct V4 ranges. SYNTHETIC SOFTWARE TEST data.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from vnxdna import sdk
from vnxdna.commands import app
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.v4 import datagen
from vnxdna.v4.frame import PROFILES


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("loc")
    (d / "ds").mkdir()
    datagen.generate(d / "ds" / "a.bin", 50_000, "random", 81)
    datagen.generate(d / "ds" / "b.bin", 9_000, "random", 82)
    sdk.archive([d / "ds"], d / "a.vnx")
    return d


@pytest.mark.parametrize("v6", [{"stripe_depth": 4, "column_parity": 2}, {"strand_order": "interleaved", "outer_plan": "adaptive"},
                                {"column_parity": 1}])
def test_v6_outer_options_are_refused(arc, v6):
    with pytest.raises(VNXConfigurationError) as e:
        sdk.locate(arc / "a.vnx", "ds/b.bin", dna=sdk.DNAOptions(**v6))
    assert e.value.code == "CONFIGURATION_ERROR" and "superblock" in str(e.value)


def test_redundancy_profile_with_v6_outer_code_is_refused_on_the_cli(arc):
    r = CliRunner().invoke(app, ["locate", str(arc / "a.vnx"), "ds/b.bin", "--dna-profile", "maximum-recovery"])
    assert r.exit_code == 7, r.output
    err = json.loads(r.stderr)
    assert err["code"] == "CONFIGURATION_ERROR"
    r = CliRunner().invoke(app, ["locate", str(arc / "a.vnx"), "ds/b.bin", "--dna-profile", "v4-balanced",
                                 "--column-parity", "2"])
    assert r.exit_code == 7 and json.loads(r.stderr)["code"] == "CONFIGURATION_ERROR"


def test_unknown_profile_is_a_configuration_error_not_an_internal_one(arc):
    r = CliRunner().invoke(app, ["locate", str(arc / "a.vnx"), "ds/b.bin", "--dna-profile", "no-such-profile"])
    assert r.exit_code == 7 and json.loads(r.stderr)["code"] == "CONFIGURATION_ERROR"


def test_custom_k_and_m_give_the_ranges_of_that_geometry(arc, tmp_path):
    """The records printed must be the ones the encoder writes for the same options."""
    opts = sdk.DNAOptions(profile="v4-balanced", data_symbols=32, parity_symbols=8)
    loc = sdk.locate(arc / "a.vnx", "ds/b.bin", dna=opts).body["dna"]
    sdk.encode(arc / "a.vnx", tmp_path / "s.fasta", dna=opts)
    labels = [line[1:] for line in (tmp_path / "s.fasta").read_text().splitlines() if line.startswith(">")]
    for first, count in loc["strand_records"]:
        groups = {int(labels[i].split("|")[3]) for i in range(first, first + count)}
        kinds = {int(labels[i].split("|")[2]) for i in range(first, first + count)}
        assert len(groups) == 1 and groups <= set(loc["groups"]) and kinds == {0}
    lay, _, _ = PROFILES["v4-balanced"]
    assert loc["groups"] == sorted({g for off, n in sdk.locate(arc / "a.vnx", "ds/b.bin").body["container_ranges"]
                                    for g in range(off // (32 * lay.payload_bytes), (off + n - 1) // (32 * lay.payload_bytes) + 1)})


def test_strand_profile_still_works(arc):
    loc = sdk.locate(arc / "a.vnx", "ds/b.bin", dna_profile="v4-balanced").body["dna"]
    assert loc["groups"] and loc["profile"] == "v4-balanced"
