"""``vnx locate --dna-profile`` strand mapping (audit §5.5, job #62).

V4/V5 options (superblock version 1) keep the sequential mapping: group g covers container bytes [g·K·P, (g+1)·K·P), and
its strands follow the superblock strands in order. V6 outer-code options (superblock version 2: stripes with
column-parity groups, interleaved strand order, superblock strands spread through the pool, adaptive plan) give a
different strand order; locate must report the strand-file records that the encoder actually writes for those options.
Every check here compares locate's records with the labels of a strand file encoded with the same options (ground
truth). SYNTHETIC SOFTWARE TEST data.
"""
from __future__ import annotations

import json
import random

import pytest
from typer.testing import CliRunner

from vnxdna import sdk
from vnxdna.commands import app
from vnxdna.core.errors import VNXConfigurationError
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.dnaenc.superblock import Superblock
from vnxdna.pipeline.locate import dna_location
from vnxdna.v4 import datagen
from vnxdna.v4.frame import PROFILES

V6_OPTIONS = [
    pytest.param({"stripe_depth": 4, "column_parity": 2}, id="striped-sequential"),
    pytest.param({"stripe_depth": 4, "column_parity": 2, "strand_order": "interleaved"}, id="striped-interleaved"),
    pytest.param({"strand_order": "interleaved", "outer_plan": "adaptive"}, id="adaptive-interleaved"),
    pytest.param({"column_parity": 1}, id="one-stripe-column-parity"),
    pytest.param({"stripe_depth": 3, "column_parity": 1, "strand_order": "interleaved", "data_symbols": 8,
                  "parity_symbols": 4}, id="short-rows-interleaved"),
    pytest.param({"stripe_depth": 5, "data_symbols": 16, "parity_symbols": 4}, id="stripes-without-column-parity"),
]


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    d = tmp_path_factory.mktemp("loc")
    (d / "ds").mkdir()
    datagen.generate(d / "ds" / "a.bin", 50_000, "random", 81)
    datagen.generate(d / "ds" / "b.bin", 9_000, "random", 82)
    sdk.archive([d / "ds"], d / "a.vnx")
    return d


def _labels(path) -> list[tuple[int, int, int]]:
    """(kind, group, symbol) of every record of a FASTA strand file, in file order."""
    out = []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            f = line[1:].split("|")
            out.append((int(f[2]), int(f[3]), int(f[4])))
    return out


def _expand(runs) -> list[int]:
    return [i for first, count in runs for i in range(first, first + count)]


def _encoded(arc, tmp_path, opts: DNAOptions):
    rep = sdk.encode(arc / "a.vnx", tmp_path / "s.fasta", dna=opts).body
    return rep, _labels(tmp_path / "s.fasta")


def _check_against_strand_file(loc: dict, ranges, labels, K: int, P: int) -> None:
    """locate's records must be exactly the records of the strand file that carry the located groups/bytes."""
    groups = set(loc["groups"])
    expect_groups = {g for off, n in ranges for g in range(off // (K * P), (off + n - 1) // (K * P) + 1)}
    assert groups == expect_groups
    # all strands (data + row parity) of the located data groups
    assert sorted(_expand(loc["strand_records"])) == [i for i, (k, g, _) in enumerate(labels) if k == 0 and g in groups]
    # the systematic strands that hold the located bytes
    held = {(off_g, t) for off, n in ranges for b in range(off, off + n, 1) for off_g, t in [divmod(b, K * P)]}
    held = {(g, r // P) for g, r in held}
    got = [labels[i] for i in _expand(loc["data_strand_records"])]
    assert all(k == 0 for k, _, _ in got) and {(g, t) for _, g, t in got} == held and len(got) == len(held)
    # column-parity rows of the stripes involved and the superblock strands
    cp = set(loc["column_parity_groups"])
    assert sorted(_expand(loc["column_parity_records"])) == [i for i, (k, g, _) in enumerate(labels) if k == 0 and g in cp]
    assert sorted(_expand(loc["superblock_records"])) == [i for i, (k, _, _) in enumerate(labels) if k == 1]


@pytest.mark.parametrize("v6", V6_OPTIONS)
def test_v6_locate_reports_the_strands_actually_carrying_the_file(arc, tmp_path, v6):
    opts = sdk.DNAOptions(**v6)
    rep, labels = _encoded(arc, tmp_path, opts)
    geo = rep["outer_v6"]
    for name in ("ds/a.bin", "ds/b.bin"):
        body = sdk.locate(arc / "a.vnx", name, dna=opts).body
        loc = body["dna"]
        assert loc["superblock_version"] == 2 and loc["strand_order"] == geo["order"]
        assert (loc["data_symbols"], loc["parity_symbols"]) == (geo["K"], geo["M"])
        assert (loc["stripe_depth"], loc["column_parity"]) == (geo["D"], geo["Mc"])
        assert loc["groups_total"] == geo["groups"] and loc["strands_total"] == len(labels)
        _check_against_strand_file(loc, body["container_ranges"], labels, geo["K"], geo["P"])


@pytest.mark.parametrize("v6", V6_OPTIONS)
def test_v6_dna_location_of_arbitrary_byte_ranges(arc, tmp_path, v6):
    """Whole container (covers the short last row) and random ranges, checked against the encoded strand file."""
    opts = sdk.DNAOptions(**v6)
    rep, labels = _encoded(arc, tmp_path, opts)
    geo, size = rep["outer_v6"], rep["container_bytes"]
    rng = random.Random(62)
    cases = [[[0, size]], [[size - 1, 1]], [[0, 1]]]
    for _ in range(6):
        cases.append(sorted([o, rng.randint(1, min(4000, size - o))] for o in rng.sample(range(size), 3)))
    for ranges in cases:
        loc = dna_location(ranges, None, size, opts)
        _check_against_strand_file(loc, ranges, labels, geo["K"], geo["P"])


def test_redundancy_profile_with_v6_outer_code_on_the_cli(arc, tmp_path):
    r = CliRunner().invoke(app, ["locate", str(arc / "a.vnx"), "ds/b.bin", "--dna-profile", "maximum-recovery"])
    assert r.exit_code == 0, r.output
    body = json.loads(r.stdout)["result"]
    _, labels = _encoded(arc, tmp_path, sdk.DNAOptions(profile="v4-archival", stripe_depth=8, column_parity=2,
                                                       strand_order="interleaved"))
    lay, _, _ = PROFILES["v4-archival"]
    assert body["dna"]["profile"] == "maximum-recovery" and body["dna"]["strand_order"] == "interleaved"
    _check_against_strand_file(body["dna"], body["container_ranges"], labels, 32, lay.payload_bytes)
    r = CliRunner().invoke(app, ["locate", str(arc / "a.vnx"), "ds/b.bin", "--dna-profile", "v4-balanced",
                                 "--column-parity", "2"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["result"]["dna"]["column_parity"] == 2


def test_invalid_v6_options_are_still_configuration_errors(arc):
    with pytest.raises(VNXConfigurationError) as e:
        sdk.locate(arc / "a.vnx", "ds/b.bin", dna=sdk.DNAOptions(stripe_depth=200, column_parity=100))
    assert e.value.code == "CONFIGURATION_ERROR"
    with pytest.raises(VNXConfigurationError):
        sdk.locate(arc / "a.vnx", "ds/b.bin", dna=sdk.DNAOptions(strand_order="diagonal"))


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


def test_v4_output_is_unchanged(arc):
    """V4/V5 options: same keys and values as before V6 support (superblock version 1, one record run per group)."""
    loc = sdk.locate(arc / "a.vnx", "ds/b.bin", dna_profile="v4-balanced").body["dna"]
    assert set(loc) == {"profile", "data_symbols", "parity_symbols", "groups", "groups_total", "strand_records",
                        "superblock_records", "superblock_version", "note"}
    ks, ms = Superblock.symbols(PROFILES["v4-balanced"][0].payload_bytes)
    assert loc["superblock_version"] == 1 and loc["superblock_records"] == [0, ks + ms]
    assert all(len(r) == 2 for r in loc["strand_records"]) and len(loc["strand_records"]) == len(loc["groups"])


def test_strand_profile_still_works(arc):
    loc = sdk.locate(arc / "a.vnx", "ds/b.bin", dna_profile="v4-balanced").body["dna"]
    assert loc["groups"] and loc["profile"] == "v4-balanced"
