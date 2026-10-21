"""V11-RT-09 (LOW): the C++ outer-list entry points take k, m and P from ``parity`` and ``sym`` and read ``known`` (n),
``sym`` (n x P), ``mul`` (256 x 256) and ``inv`` (256) without a length. The Python wrappers passed any array: a short
``known``, ``sym``, ``mul`` or ``inv`` was read past its end (a heap over-read under ASan). Every buffer size is now
checked before the call. SYNTHETIC SOFTWARE TEST data."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from vnxdna.codec import gf256
from vnxdna.native import outerlist as nol
from vnxdna.recovery import outer_list as ol

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "arch"))
from test_outer_list import _ransac_case, _row_case  # noqa: E402

pytestmark = pytest.mark.skipif(not nol.KERNEL.available(), reason="native outerlist library not built")


def _row_args():
    _, parity, known, sym, cpos, cval, cok = _row_case(3, f=2, n_cands=4)
    return [parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, cpos, cval, cok, 1, 1, 50]


def _ransac_args():
    parity, known, sym, hpos, hval, _ = _ransac_case(1)
    return [parity, gf256.MUL, ol.INV, known.astype(np.uint8), sym, hpos, hval, 4, 100]


def test_well_formed_calls_still_work():
    assert nol.decode_row(*_row_args())[1] >= 0
    assert nol.ransac(*_ransac_args())[0] >= 0


@pytest.mark.parametrize("index, cut", [(1, lambda a: a.reshape(-1)[:1000]), (2, lambda a: a[:10]),
                                        (3, lambda a: a[:-1]), (4, lambda a: a[:-1]), (0, lambda a: a.reshape(-1))])
@pytest.mark.parametrize("fn, make", [("decode_row", _row_args), ("ransac", _ransac_args)])
def test_short_buffers_are_refused_before_the_call(fn, make, index, cut):
    args = make()
    args[index] = cut(np.asarray(args[index]))
    with pytest.raises(ValueError):
        getattr(nol, fn)(*args)


@pytest.mark.parametrize("fn, make, ref", [("decode_row", _row_args, ol.decode_row_reference),
                                           ("ransac", _ransac_args, ol.ransac_reference)])
@pytest.mark.parametrize("extra_known", [0, 1])
def test_no_missing_data_symbol_is_refused_like_the_reference(fn, make, ref, extra_known):
    """f = k - known must be >= 1 (OL_EARG in the kernel): both paths raise ValueError, never a crash."""
    args = make()
    known = np.ones_like(np.asarray(args[3]))                       # every position known: f = k - n = -m < 1
    if extra_known:
        m, k = np.asarray(args[0]).shape
        known[:] = 0
        known[:k] = 1                                                # exactly k known: f = 0
    args[3] = known
    with pytest.raises(ValueError, match="k - known"):
        getattr(nol, fn)(*args)
    with pytest.raises(ValueError, match="k - known"):
        ref(*args)


def test_candidate_payload_rows_must_match_positions():
    args = _row_args()
    args[6] = args[6][:-1]                      # cval: one row fewer than cpos
    with pytest.raises(ValueError):
        nol.decode_row(*args)
    args = _ransac_args()
    args[6] = args[6][:-1]                      # hval: one row fewer than hpos
    with pytest.raises(ValueError):
        nol.ransac(*args)
