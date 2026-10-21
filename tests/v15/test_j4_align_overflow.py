"""Tests for J4 and J5 hardening tasks related to align_usable functions.

J4 (O-5): the flat-buffer domain check must refuse a read whose ``start + length`` overflows an int64, and it must
stay exact and vectorised: a valid read is never refused.

J5 (O-6): the native domain check must refuse a geometry whose arrays do not have the sizes the kernel indexes them
with, so the caller takes the documented ``None`` path and runs the reference aligner.
"""
import numpy as np
import pytest


def test_align_usable_flat_overflow():
    """Test that align_usable_flat handles overflow in starts + lengths correctly (J4)."""
    # Create a simple case where we can directly test the _flat_in_domain function
    from vnxdna.native.align import _load, _flat_in_domain

    lib = _load()
    if lib is None:
        pytest.skip("Native align not available")

    # Test the overflow case: starts=2**62, lengths=2**62 should return False (J4)
    # This will test the internal _flat_in_domain function directly
    codes = np.zeros(1000, dtype=np.uint8)
    starts = np.array([2**62], dtype=np.int64)  # Large start value
    lengths = np.array([2**62], dtype=np.int64)  # Large length value that when added should overflow
    qflat = None

    # This should return False due to overflow causing the sum to be negative,
    # which will make the check fail and return None from align_usable_flat
    result = _flat_in_domain(codes, starts, lengths, qflat)
    assert result is False  # Should be rejected due to overflow

    # Now test that normal case works
    starts_normal = np.array([0], dtype=np.int64)
    lengths_normal = np.array([100], dtype=np.int64)
    result_normal = _flat_in_domain(codes, starts_normal, lengths_normal, qflat)
    assert result_normal is True  # Should be accepted


def test_flat_in_domain_refuses_a_read_past_the_end():
    """A read whose end is past the buffer is out of the domain."""
    from vnxdna.native.align import _flat_in_domain

    codes = np.zeros(1000, dtype=np.uint8)
    assert _flat_in_domain(codes, np.array([900], dtype=np.int64), np.array([200], dtype=np.int64), None) is False
    assert _flat_in_domain(codes, np.array([1000], dtype=np.int64), np.array([1], dtype=np.int64), None) is False
    assert _flat_in_domain(codes, np.array([-1], dtype=np.int64), np.array([1], dtype=np.int64), None) is False
    assert _flat_in_domain(codes, np.array([0], dtype=np.int64), np.array([-1], dtype=np.int64), None) is False


def test_flat_in_domain_is_exact_not_conservative():
    """A valid read is accepted even when the largest start and the largest length are at different indices.

    The conservative bound ``max(starts) + max(lengths)`` would refuse this input; the check must not.
    """
    from vnxdna.native.align import _flat_in_domain

    codes = np.zeros(1000, dtype=np.uint8)
    starts = np.array([900, 0], dtype=np.int64)
    lengths = np.array([1, 900], dtype=np.int64)
    # every read fits: [900, 901) and [0, 900)
    assert _flat_in_domain(codes, starts, lengths, None) is True


def test_flat_in_domain_refuses_a_wrapped_sum():
    """A sum that wraps an int64 to a small number is refused, not accepted."""
    from vnxdna.native.align import _flat_in_domain

    codes = np.zeros(1000, dtype=np.uint8)
    # 2**62 + 2**62 wraps to 0 in int64: a naive bound would see 0 and accept the read
    starts = np.array([2**62], dtype=np.int64)
    lengths = np.array([2**62], dtype=np.int64)
    assert _flat_in_domain(codes, starts, lengths, None) is False


def test_config_in_domain_refuses_a_mismatched_geometry():
    """A geometry whose arrays do not match ``T`` and ``frame_nt`` is refused (O-6)."""
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.native import align as na
    from vnxdna.sync.template import TemplateAligner

    lay = PROFILES["v4-balanced"][0]
    aligner = TemplateAligner(lay, backend="reference")
    assert na.config_in_domain(aligner, 0) is True          # the real geometry is in the domain

    # a template array shorter than T: the kernel would read past its end
    aligner.tpl = aligner.tpl[:-5].copy()
    aligner._native_geometry = None                        # the cache would hide the change
    assert na.config_in_domain(aligner, 0) is False

    # a frame map shorter than frame_nt: the kernel would read past its end
    aligner = TemplateAligner(lay, backend="reference")
    g = dict(na.geometry(aligner))
    g["frame_pos"] = g["frame_pos"][:-3].copy()
    aligner._native_geometry = g
    assert na.config_in_domain(aligner, 0) is False

    # a segment id outside the segment table: the kernel would mark outside hit[]
    aligner = TemplateAligner(lay, backend="reference")
    g = dict(na.geometry(aligner))
    g["seg_of"] = g["seg_of"].copy()
    g["seg_of"][0] = 999
    aligner._native_geometry = g
    assert na.config_in_domain(aligner, 0) is False

    # a segment map shorter than T: the kernel would read past its end
    aligner = TemplateAligner(lay, backend="reference")
    g = dict(na.geometry(aligner))
    g["seg_of"] = g["seg_of"][:-1].copy()
    aligner._native_geometry = g
    assert na.config_in_domain(aligner, 0) is False


def test_align_usable_returns_none_for_a_mismatched_geometry():
    """The documented fallback: a refused geometry makes :func:`align_usable` return ``None``."""
    from vnxdna.dnaenc.layout import PROFILES
    from vnxdna.native import align as na
    from vnxdna.sync.template import TemplateAligner

    lay = PROFILES["v4-balanced"][0]
    aligner = TemplateAligner(lay, backend="native")
    read = np.zeros(lay.strand_nt, dtype=np.uint8)
    aligner.tpl = aligner.tpl[:-1].copy()
    assert na.align_usable(aligner, [read], None, 0) is None
