"""V2 test fixtures. Helpers live in ``tests/v2_support.py`` (``tests/conftest.py`` belongs to the V1 suite)."""
import pytest

from v2_support import FAST


@pytest.fixture
def fast():
    return FAST
