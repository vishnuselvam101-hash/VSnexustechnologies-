import os

import pytest

from vnxdna.v5 import native_alignment as na


@pytest.fixture(scope="session")
def native_ready(tmp_path_factory):
    if na.available():
        return True
    try:
        lib = na.build(tmp_path_factory.mktemp("native") / "libvnx_align.so")
    except na.NativeAlignmentError as error:
        pytest.skip(f"native aligner unavailable and cannot be built here: {error}")
    os.environ["VNXDNA_NATIVE_LIB"] = str(lib)
    na._reset_for_tests()
    assert na.available(), na.status()
    return True


