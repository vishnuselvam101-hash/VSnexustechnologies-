"""Test for V15 Task J3: hardening O-4 - crc32_rows input validation.

The native path wraps Python-list values > 255 (and < 0, and bools) while the reference path raises.
Make both paths raise ValueError for list input with any value outside 0..255 or a bool;
numpy uint8 input is unchanged.
"""
import numpy as np
import pytest
from vnxdna.core.crc import crc32_rows as reference_crc32_rows
from vnxdna.native import integrity as ni


def test_reference_implementation_validation():
    """Test that reference implementation raises ValueError for invalid inputs."""
    
    # Test with list containing values outside 0..255
    with pytest.raises(ValueError, match="values outside the range 0..255"):
        reference_crc32_rows([[1, 2, 256]])  # 256 > 255
    
    with pytest.raises(ValueError, match="values outside the range 0..255"):
        reference_crc32_rows([[1, 2, -1]])  # -1 < 0
    
    # Test with list containing bool values
    with pytest.raises(ValueError, match="boolean values"):
        reference_crc32_rows([[1, 2, True]])  # bool value
    
    with pytest.raises(ValueError, match="boolean values"):
        reference_crc32_rows([[1, 2, False]])  # bool value


def test_valid_inputs_work_identically():
    """Test that valid inputs still work identically in both backends."""
    
    # Valid numpy uint8 input
    rows = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)
    
    ref_result = reference_crc32_rows(rows)
    native_result = ni.crc32_rows(rows)
    
    np.testing.assert_array_equal(ref_result, native_result)
    
    # Test with list of valid values (should be converted to uint8)
    rows_list = [[1, 2, 3], [4, 5, 6]]
    ref_result_list = reference_crc32_rows(rows_list)
    native_result_list = ni.crc32_rows(rows_list)
    
    np.testing.assert_array_equal(ref_result_list, native_result_list)


def test_backend_consistency():
    """Test that both backends behave consistently for valid inputs."""
    
    # Test with various valid inputs
    test_cases = [
        [[10, 20, 30], [40, 50, 60]],
        [[0, 128, 255], [1, 129, 254]],
        [[255, 0, 127], [128, 64, 32]]
    ]
    
    for rows in test_cases:
        ref_result = reference_crc32_rows(rows)
        native_result = ni.crc32_rows(rows)
        np.testing.assert_array_equal(ref_result, native_result)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])