"""V13 S1: ``decode_frames`` descrambles and CRC-checks the inner-decoded rows only; every field equals the V12 form."""
from __future__ import annotations

import numpy as np
import pytest

from vnxdna.codec.codecs import InnerRS
from vnxdna.dnaenc.frame4 import Parsed, decode_frames, decode_frames_reference, plain_rows
from vnxdna.dnaenc.layout import KIND_SUPER, PROFILES, Layout
from vnxdna.dnaenc.scrambler import VARIANTS, keystreams

FIELDS = ("ok", "kind", "tag", "group", "symbol", "payload", "errata", "rs_ok", "crc_ok")


def _same(a: Parsed, b: Parsed) -> None:
    for f in FIELDS:
        x, y = getattr(a, f), getattr(b, f)
        assert x.dtype == y.dtype and x.shape == y.shape, f
        assert np.array_equal(x, y), f


def _build(rng, lay: Layout, n: int) -> np.ndarray:
    """Valid frames of the layout: random header fields, payloads and scrambler variants."""
    plain = plain_rows(int(rng.integers(0, 1 << 16)), rng.integers(0, KIND_SUPER + 1, size=n).astype(np.uint8),
                       rng.integers(0, 1 << 20, size=n), rng.integers(0, 1 << 16, size=n),
                       rng.integers(0, 256, size=(n, lay.payload_bytes)).astype(np.uint8))
    v = rng.integers(0, VARIANTS, size=n)
    msg = np.empty((n, 1 + plain.shape[1]), dtype=np.uint8)
    msg[:, 0] = v
    msg[:, 1:] = plain ^ keystreams(plain.shape[1])[v]
    fr = np.concatenate([msg, InnerRS(lay.inner_parity).parity(msg)], axis=1)
    assert fr.shape == (n, lay.frame_bytes)
    return fr


def _frames(rng, lay: Layout, n: int) -> np.ndarray:
    """Valid frames with random corruptions: 0 .. r+3 byte errors per row, and 10 % random rows."""
    fr = _build(rng, lay, n)
    errs = rng.integers(0, lay.inner_parity + 4, size=n)
    for i in range(n):
        pos = rng.choice(lay.frame_bytes, size=int(errs[i]), replace=False)
        fr[i, pos] ^= rng.integers(1, 256, size=pos.size).astype(np.uint8)
    rnd = rng.random(n) < 0.1
    fr[rnd] = rng.integers(0, 256, size=(int(rnd.sum()), lay.frame_bytes)).astype(np.uint8)
    return fr


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_equals_the_reference(seed, profile):
    lay = PROFILES[profile][0]
    rng = np.random.default_rng(seed * 101 + len(profile))
    n = int(rng.integers(0, 400))
    fr = _frames(rng, lay, n)
    er = rng.random((n, lay.frame_bytes)) < rng.choice([0.0, 0.02, 0.1, 0.4])
    for erasures in (None, er):
        for retry in (True, False):
            _same(decode_frames(lay, fr, erasures, errors_only_retry=retry),
                  decode_frames_reference(lay, fr, erasures, errors_only_retry=retry))


def test_clean_rows_need_no_inner_decode(monkeypatch):
    lay = PROFILES["v4-balanced"][0]
    fr = _build(np.random.default_rng(7), lay, 20)
    from vnxdna.codec import codecs
    called = []
    orig = codecs.InnerRS.decode
    monkeypatch.setattr(codecs.InnerRS, "decode", lambda self, c, e=None: called.append(c.shape[0]) or orig(self, c, e))
    P = decode_frames(lay, fr)
    assert P.ok.all() and called == []
    fr[3, 5] ^= 1
    P = decode_frames(lay, fr)
    assert P.ok.all() and called == [1] and P.errata[3] == 1 and P.errata.sum() == 1
    assert decode_frames(lay, fr[:0]).ok.shape == (0,)
