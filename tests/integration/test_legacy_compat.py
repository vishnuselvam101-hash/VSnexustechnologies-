"""V0.1 archives remain decodable through the explicit compatibility layer, and only through it."""
import hashlib
import json
import os

import pytest

from vnxdna import api
from vnxdna.container.crypto import parse_key
from vnxdna.errors import KeyRequiredError, UnsupportedFormatError
from vnxdna.legacy import v0_1

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "v0_1")
META = json.load(open(os.path.join(FIX, "fixtures.json")))
KEY = parse_key(META["test_only_fernet_key"])


@pytest.mark.parametrize("fixture", META["fixtures"], ids=lambda f: f["name"])
def test_every_v0_1_fixture_decodes_to_its_recorded_sha256(fixture):
    path = os.path.join(FIX, fixture.get("archive") or fixture["name"])
    data, report = v0_1.decode_legacy(path, KEY if fixture["key"] else None)
    assert hashlib.sha256(data).hexdigest() == fixture["input_sha256"]
    assert open(os.path.join(FIX, fixture["input"]), "rb").read() == data


def test_legacy_inputs_are_never_read_by_the_format_4_decoder(tmp_path):
    for name in ("dataset_v2_rs_8_4", "rd1_archive_plain.vnxdna.json"):
        with pytest.raises(UnsupportedFormatError):
            api.restore(os.path.join(FIX, name), tmp_path / "out")


def test_encrypted_legacy_requires_key():
    with pytest.raises(KeyRequiredError):
        v0_1.decode_legacy(os.path.join(FIX, "dataset_v1_zstd_encrypted"))


def test_v0_1_generator_really_was_non_mds_and_the_new_one_is():
    """Regression evidence for the V0.1 defect: {4,5,7,11} erased from 8+4."""
    from vnxdna.ecc import gf256
    from vnxdna.ecc.cauchy import CauchyErasureCode
    old = [[int(r == c) for c in range(8)] for r in range(8)] + [[gf256.power(p + 1, c) for c in range(8)] for p in range(4)]
    survivors = [i for i in range(12) if i not in (4, 5, 7, 11)]
    with pytest.raises(ValueError):
        gf256.matrix_invert([old[i] for i in survivors])
    new = CauchyErasureCode(8, 4)
    gf256.matrix_invert([new.generator_row(i) for i in survivors])
