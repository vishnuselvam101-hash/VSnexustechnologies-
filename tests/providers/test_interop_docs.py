"""Phase 7 documents (directive §17, §18, §21; spec §8.1): they exist, say that nothing is physically validated, the
DDSA mapping is a table with layouts "to be aligned", and no code produces Sector Zero / Sector One bytes."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from vnxdna.providers import STATUS

ROOT = Path(__file__).resolve().parents[2]
DOCS = {name: (ROOT / "docs" / name) for name in ("INTEROPERABILITY.md", "LAB_INTERFACE.md", "DDSA_MAPPING.md")}


@pytest.mark.parametrize("name", sorted(DOCS))
def test_document_exists_and_disclaims_physical_validation(name):
    text = DOCS[name].read_text()
    assert "No DNA has been synthesised, stored or sequenced by" in text or "produces **no Sector Zero" in text
    for bad in ("physically validated by VNX", "was synthesised by VNX", "world's", "guaranteed"):
        assert bad not in text


@pytest.mark.parametrize("name", ["INTEROPERABILITY.md", "LAB_INTERFACE.md"])
def test_nothing_is_physically_validated_is_stated(name):
    assert "Nothing" in DOCS[name].read_text() and "physically validated" in DOCS[name].read_text()


def test_ddsa_mapping_is_a_table_with_layouts_to_be_aligned():
    text = DOCS["DDSA_MAPPING.md"].read_text()
    assert "TO BE ALIGNED WITH THE PUBLISHED SPECIFICATION" in text
    rows = [line for line in text.splitlines() if line.startswith("| ") and not line.startswith("| DDSA element")]
    assert len(rows) >= 6 and all(line.rstrip().endswith("| to be aligned |") for line in rows)


def test_no_sector_zero_or_one_bytes_are_produced():
    """Only the schema reserves the DDSA roles; no source file writes a sector file or sector strands."""
    for p in (ROOT / "src" / "vnxdna").rglob("*.py"):
        text = p.read_text()
        assert not re.search(r"sector[-_ ]?(zero|one)", text, re.I), p


def test_status_table_matches_the_interoperability_document():
    text = DOCS["INTEROPERABILITY.md"].read_text()
    for row in STATUS[:3]:
        assert row["interface_implemented"] == "yes" and row["provider_integration_tested"].startswith("software only")
    assert "interface implemented" in text.lower() and "software only" in text
