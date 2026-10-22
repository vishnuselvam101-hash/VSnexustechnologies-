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


def _outcome(fn, rows):
    """What a backend does with an input: the result, or the exception type and message."""
    try:
        return ("ok", tuple(int(v) for v in fn(rows)))
    except Exception as error:                        # noqa: BLE001 - the type is the thing under test
        return (type(error).__name__, str(error))


#: the inputs both backends must treat identically
AGREEMENT_CASES = [
    ("list of lists", [[1, 2, 3], [4, 5, 6]]),
    ("list of tuples", [(1, 2, 3), (4, 5, 6)]),
    ("tuple of lists", ((1, 2, 3), (4, 5, 6))),
    ("tuple of tuples", ((1, 2), (3, 4))),
    ("numpy uint8 array", np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)),
    ("numpy uint8 scalars in a list", [[np.uint8(1), np.uint8(2)], [np.uint8(3), np.uint8(4)]]),
    ("numpy int64 scalars in a list", [[np.int64(1), np.int64(2)]]),
    ("bool in a list", [[1, 2, True]]),
    ("bool in a tuple", [(1, 2, False)]),
    ("value above 255", [[1, 2, 256]]),
    ("negative value", [[1, 2, -1]]),
    ("non-integer value", [[1.5, 2.5]]),
    ("string value", [["a", "b"]]),
    ("ragged rows", [[1, 2], [3]]),
    ("a row that is not a list", [1, 2, 3]),
]


@pytest.mark.parametrize("name,rows", AGREEMENT_CASES, ids=[name for name, _ in AGREEMENT_CASES])
def test_both_backends_agree(name, rows):
    """The reference and the native kernel accept the same inputs and refuse the same ones the same way.

    They share one validator (:mod:`vnxdna.core.crc_rows`), so they cannot drift apart again: this is the test that
    would catch a drift.
    """
    reference = _outcome(reference_crc32_rows, rows)
    native = _outcome(ni.crc32_rows_native, rows)
    assert reference == native, f"{name}: reference {reference} vs native {native}"


def test_both_backends_refuse_a_bool_with_the_same_error():
    """A boolean is not a byte value: both backends refuse it with the same error.

    This is the one intentional behaviour change against the previous release, which accepted ``True`` as the byte
    1 (and the reference of that release rejected it while the native kernel accepted it).
    """
    for rows in ([[1, 2, True]], [(1, 2, False)]):
        with pytest.raises(ValueError, match="boolean values"):
            reference_crc32_rows(rows)
        with pytest.raises(ValueError, match="boolean values"):
            ni.crc32_rows_native(rows)


def test_both_backends_accept_numpy_uint8_scalars():
    """A ``np.uint8`` scalar is a byte: both backends accept it (the previous release accepted it too)."""
    rows = [[np.uint8(0), np.uint8(128), np.uint8(255)]]
    np.testing.assert_array_equal(reference_crc32_rows(rows), ni.crc32_rows_native(rows))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])