import os

import numpy as np
import pytest

from vnxdna.v4 import archive as ar
from vnxdna.v4 import encoder as en


@pytest.fixture
def dataset(tmp_path):
    """A small deterministic directory tree: random, repetitive, duplicate, empty file, empty dir, nested."""
    root = tmp_path / "ds"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "empty_dir").mkdir()
    rng = np.random.default_rng(1)
    (root / "random.bin").write_bytes(rng.integers(0, 256, 150_000, dtype=np.uint8).tobytes())
    (root / "sub" / "text.txt").write_bytes(b"VNX-DNA V4 test line\n" * 4000)
    (root / "sub" / "deep" / "copy.txt").write_bytes(b"VNX-DNA V4 test line\n" * 4000)
    (root / "empty.dat").write_bytes(b"")
    return root


@pytest.fixture
def small_archive(tmp_path, dataset):
    out = tmp_path / "a.vnx"
    ar.build_archive([dataset], out, ar.ArchiveOptions(chunk_size=16384))
    return out


@pytest.fixture
def small_strands(tmp_path, small_archive):
    out = tmp_path / "a.fasta"
    rep = en.encode_container(small_archive, out, en.DNAOptions())
    return out, rep


def tree_equal(a, b) -> bool:
    for dirpath, dirs, files in os.walk(a):
        rel = os.path.relpath(dirpath, a)
        other = os.path.join(b, rel)
        if sorted(dirs) != sorted(os.listdir(other)) and set(dirs) - set(os.listdir(other)):
            return False
        for f in files:
            with open(os.path.join(dirpath, f), "rb") as x, open(os.path.join(other, f), "rb") as y:
                if x.read() != y.read():
                    return False
    return True
