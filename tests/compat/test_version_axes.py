"""Version axes (spec §4, V6 Phase 2.4): the development version, the writer-provenance block ``extensions.vnx`` written
by default (decision 2), its opt-out, and readers that ignore it. SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import json
import re

import pytest

from vnxdna import __version__
from vnxdna.v4 import archive as ar
from vnxdna.v4 import container as ct
from vnxdna.v4 import datagen
from vnxdna.v4 import decoder as de
from vnxdna.v4 import encoder as en


def test_development_version_is_pep440_and_not_a_release():
    assert __version__ == "6.0.0.dev0"
    assert re.fullmatch(r"\d+\.\d+\.\d+(\.dev\d+)?", __version__)


@pytest.mark.parametrize("encrypted", [False, True])
def test_manifest_carries_extensions_vnx_by_default(tmp_path, encrypted):
    datagen.generate(tmp_path / "in.bin", 5000, "mixed", 31)
    opts = ar.ArchiveOptions(passphrase="pw-TEST-ONLY", scrypt={"n": 1024, "r": 8, "p": 1}) if encrypted else ar.ArchiveOptions()
    ar.build_archive([tmp_path / "in.bin"], tmp_path / "a.vnx", opts)
    c = ct.open_container(tmp_path / "a.vnx", passphrase="pw-TEST-ONLY" if encrypted else None)
    vnx = c.manifest["extensions"]["vnx"]
    assert vnx == {"spec": "6.0", "software": __version__,
                   "archive_id_derivation": "random" if encrypted else "options-v1"}
    assert vnx["software"] == c.manifest["encoder"]["version"]


def test_opt_out_gives_the_5x_manifest_layout_and_the_same_archive_id(tmp_path):
    datagen.generate(tmp_path / "in.bin", 5000, "mixed", 32)
    ar.build_archive([tmp_path / "in.bin"], tmp_path / "a.vnx", ar.ArchiveOptions())
    ar.build_archive([tmp_path / "in.bin"], tmp_path / "b.vnx", ar.ArchiveOptions(writer_provenance=False))
    a, b = ct.open_container(tmp_path / "a.vnx"), ct.open_container(tmp_path / "b.vnx")
    assert "vnx" not in b.manifest["extensions"] and a.archive_id == b.archive_id      # never part of the ID derivation


def test_readers_ignore_extensions_vnx_and_strands_follow_container_bytes(tmp_path):
    datagen.generate(tmp_path / "in.bin", 8000, "random", 33)
    ar.build_archive([tmp_path / "in.bin"], tmp_path / "a.vnx", ar.ArchiveOptions())
    en.encode_container(tmp_path / "a.vnx", tmp_path / "s.fasta", en.DNAOptions())
    res = de.decode_reads(tmp_path / "s.fasta", tmp_path / "o.vnx", de.DecodeOptions())
    assert res.status == "SUCCESS" and (tmp_path / "o.vnx").read_bytes() == (tmp_path / "a.vnx").read_bytes()
    ar.extract(tmp_path / "o.vnx", tmp_path / "x")
    assert (tmp_path / "x" / "in.bin").read_bytes() == (tmp_path / "in.bin").read_bytes()
    raw = json.loads(ct.open_container(tmp_path / "a.vnx").manifest_bytes)
    assert set(raw["extensions"]) == {"vnx"}
