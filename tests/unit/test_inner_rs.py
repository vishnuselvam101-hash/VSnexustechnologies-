"""Inner (per-strand) Reed–Solomon code and CRC-protected frames."""
import numpy as np
import pytest
import reedsolo

from vnxdna.ecc import gf256
from vnxdna.ecc.inner_rs import InnerReedSolomon
from vnxdna.errors import ConfigurationError


@pytest.mark.parametrize("nsym", [2, 8, 16])
def test_vectorized_parity_is_bit_identical_to_reedsolo(nsym):
    rs = InnerReedSolomon(nsym)
    codec = reedsolo.RSCodec(nsym, nsize=255, fcr=0, prim=gf256.PRIMITIVE_POLY, generator=gf256.GENERATOR)
    msgs = np.random.default_rng(nsym).integers(0, 256, (20, 50), dtype=np.uint8)
    parity = rs.encode_batch(msgs)
    for m, p in zip(msgs, parity):
        assert bytes(codec.encode(m.tobytes()))[50:] == p.tobytes()


@pytest.mark.parametrize("nsym", [4, 8])
def test_corrects_up_to_half_parity_errors_and_full_parity_erasures(nsym):
    rs = InnerReedSolomon(nsym)
    rng = np.random.default_rng(1)
    for trial in range(30):
        msg = rng.integers(0, 256, (1, 40), dtype=np.uint8)
        word = np.concatenate([msg, rs.encode_batch(msg)], axis=1)[0]
        bad = word.copy()
        pos = rng.choice(word.size, nsym // 2, replace=False)
        bad[pos] ^= rng.integers(1, 256, pos.size).astype(np.uint8)
        fixed, count = rs.correct_codeword(bad.tobytes())
        assert fixed == word.tobytes() and count == nsym // 2
        erased = word.copy()
        epos = rng.choice(word.size, nsym, replace=False)
        erased[epos] = 0
        assert rs.correct_codeword(erased.tobytes(), epos.tolist())[0] == word.tobytes()


def test_too_many_erasures_is_refused():
    rs = InnerReedSolomon(4)
    assert rs.correct_codeword(bytes(20), [0, 1, 2, 3, 4]) is None


@pytest.mark.parametrize("bad", [-2, 3, 66, True, 2.0])
def test_invalid_parity_configuration(bad):
    with pytest.raises(ConfigurationError):
        InnerReedSolomon(bad)


def test_zero_parity_never_corrects():
    assert InnerReedSolomon(0).correct_codeword(b"abc") is None
