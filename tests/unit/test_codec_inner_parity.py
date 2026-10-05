"""The reedsolo-free inner RS parity matrix (vnxdna.codec.inner_parity) equals the V1-V5 one (ecc.inner_rs, reedsolo)."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.codec.inner_parity import generator_poly, parity_matrix
from vnxdna.ecc.inner_rs import InnerReedSolomon, _parity_matrix


@pytest.mark.parametrize("nsym", range(2, 65, 2))
def test_parity_matrix_equals_reedsolo_for_every_inner_parity_size(nsym):
    for k in sorted({1, 2, 9, 40, 57, 100, 255 - nsym}):
        assert np.array_equal(parity_matrix(k, nsym), _parity_matrix(k, nsym)), (k, nsym)


def test_parity_of_random_messages_equals_the_v1_encoder():
    rng = np.random.default_rng(6060)
    from vnxdna.codec.codecs import InnerRS
    for nsym, k in ((16, 41), (8, 60), (32, 100)):
        msgs = rng.integers(0, 256, (50, k), dtype=np.uint8)
        assert np.array_equal(InnerRS(nsym).parity(msgs), InnerReedSolomon(nsym).encode_batch(msgs))


def test_generator_is_monic_with_the_expected_degree_and_bad_sizes_are_refused():
    for nsym in (0, 2, 16):
        g = generator_poly(nsym)
        assert g.size == nsym + 1 and g[0] == 1
    for k, nsym in ((0, 4), (250, 6), (10, -2)):
        with pytest.raises(ValueError):
            parity_matrix(k, nsym)
