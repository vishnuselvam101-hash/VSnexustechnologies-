"""Tests for J4 and J5 hardening tasks related to align_usable functions."""

import numpy as np
import pytest
from vnxdna.native import align as na


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