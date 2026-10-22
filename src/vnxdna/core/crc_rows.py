"""The one validator and converter for the CRC-32 row input (shared by the reference and the native kernel).

The reference (:func:`vnxdna.core.crc.crc32_rows`) and the native kernel
(:func:`vnxdna.native.integrity.crc32_rows_native`) must accept the same inputs and refuse the same ones with the
same error type: a divergence between them is exactly the defect the two backends are checked against (O-4). Both
backends import this module, so they cannot drift apart again.

The rules for a Python table of rows (a ``list`` or a ``tuple`` of rows):

* a row is a ``list``, a ``tuple`` or an array, and the table is rectangular: anything else is not a 2-D table of
  bytes and is refused;
* a ``bool`` is refused: a boolean is not a byte value (``True`` is not the byte 1), and one that reaches a frame is
  a bug, not data. This is the one intentional behaviour change against the previous release, which accepted
  ``True`` as the byte 1;
* an integer (a Python ``int`` or any NumPy integer scalar, ``np.uint8`` included) outside 0..255 is refused: the
  kernel would wrap it silently;
* a real number outside 0..255 is refused for the same reason; a real number inside 0..255 keeps the conversion the
  previous release had (the byte is its truncated value).

A NumPy array given directly is not checked value by value: both backends convert it the same way, and its dtype
already says what its bytes are.
"""
from __future__ import annotations

import numbers

import numpy as np

BOOL_MESSAGE = "crc32_rows input contains boolean values"
RANGE_MESSAGE = "crc32_rows input contains values outside the range 0..255"
SHAPE_MESSAGE = "crc32_rows expects a 2-D array"


def _check_value(value) -> None:
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(BOOL_MESSAGE)
    if isinstance(value, numbers.Integral):
        if not 0 <= int(value) <= 255:
            raise ValueError(RANGE_MESSAGE)
    elif isinstance(value, numbers.Real):
        if not 0 <= float(value) <= 255:
            raise ValueError(RANGE_MESSAGE)


def validate_byte_rows(rows) -> None:
    """Refuse a table of rows that is not a table of byte values; a no-op for a NumPy array.

    Raises :class:`ValueError` (the same type and message from every backend) for a boolean, a value outside
    0..255, or a table that is not 2-D and rectangular.
    """
    if not isinstance(rows, (list, tuple)):
        return
    width: int | None = None
    for row in rows:
        if not isinstance(row, (list, tuple, np.ndarray)):
            raise ValueError(SHAPE_MESSAGE)
        size = len(row)
        if width is None:
            width = size
        elif size != width:
            raise ValueError(SHAPE_MESSAGE)
        for value in row:
            _check_value(value)


def byte_rows_array(rows) -> np.ndarray:
    """The one conversion both backends use: a table of bytes, or an array, as a uint8 array.

    The table form is validated first (:func:`validate_byte_rows`); the array form is converted as it always was.
    A value the uint8 cast cannot hold is refused with the same :class:`ValueError` from both backends.
    """
    validate_byte_rows(rows)
    try:
        return np.asarray(rows, dtype=np.uint8)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(RANGE_MESSAGE) from error
