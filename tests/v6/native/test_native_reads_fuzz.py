"""Randomized native == reference equivalence for the streaming read parser.

* seeded structured fuzz: generated FASTQ / FASTA / plain files (odd symbols, CRLF, blank lines, long lines,
  then random substitutions / insertions / deletions / truncation), random block size, record cap, batch size
  and max_reads; each case is parsed by the reference once and by the native parser with block-aligned reads
  and with random chunk sizes (1 byte .. 5 KiB) -> 2 comparisons per case;
* random-bytes fuzz: arbitrary bytes behind a FASTQ / FASTA / plain prefix;
* hypothesis: token-built files and chunk-size lists.
Every comparison requires identical batches and an identical exception; a crash fails the whole process.
"""
from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from vnxdna.v4 import reads as ref
from vnxdna.v6 import native_reads as nr

from .native_reads_support import assert_same_outcome, check_case, ensure_native, fuzz_case, limits, outcome, write

SHARDS = 8
PER_SHARD = 500          # 4000 seeded cases, 8000 native-vs-reference comparisons


@pytest.fixture(scope="module", autouse=True)
def _native(tmp_path_factory):
    ensure_native(tmp_path_factory)


@pytest.mark.parametrize("shard", range(SHARDS))
def test_seeded_structured_fuzz(tmp_path, shard):
    path = tmp_path / "case"
    for seed in range(shard * PER_SHARD, (shard + 1) * PER_SHARD):
        data, opts = fuzz_case(seed)
        try:
            check_case(path, data, opts)
        except AssertionError as error:
            raise AssertionError(f"fuzz seed {seed} ({opts['block']=}, {opts['max_nt']=}, {opts['batch']=}): {error}") from None


@pytest.mark.parametrize("prefix", [b"@", b">", b"ACGT\n" * 220, b"@r\nACGT\n+\nIIII\n"],
                         ids=["fastq-at", "fasta-gt", "plain", "fastq-record"])
def test_random_bytes(tmp_path, prefix):
    rng = np.random.default_rng(len(prefix))
    path = tmp_path / "rnd"
    for i in range(250):
        n = int(rng.integers(0, 3000))
        if rng.random() < 0.5:
            body = rng.integers(0, 256, n).astype(np.uint8).tobytes()
        else:  # line-structured noise
            body = np.frombuffer(b"\n\n\r@+>ACGTN I\t", np.uint8)[rng.integers(0, 14, n)].tobytes()
        opts = {"block": int(rng.choice([1, 13, 512, 8 << 20])), "max_nt": int(rng.choice([5, 100, 100_000])),
                "batch": int(rng.choice([1, 4, 8192])), "max_reads": None, "sizes": [int(rng.integers(1, 700))]}
        try:
            check_case(path, prefix + body, opts)
        except AssertionError as error:
            raise AssertionError(f"random-bytes case {i}: {error}") from None


TOKENS = [b"@", b">", b"+", b"\n", b"\r\n", b"\r", b"A", b"C", b"G", b"T", b"N", b"acgt", b"x", b" ", b"\t",
          b"II", b"!~", b"\x00", b"\xff", b"\n\n", b"@r\nACGT\n+\nIIII\n", b">h\nAC\nGT\n", b"@r\n\n+\n\n"]


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(lead=st.sampled_from([b"@", b">", b"A"]), body=st.lists(st.sampled_from(TOKENS), max_size=80),
       sizes=st.lists(st.integers(1, 40), min_size=1, max_size=6), batch=st.integers(-1, 6),
       block=st.sampled_from([1, 2, 5, 64, 8 << 20]), max_nt=st.sampled_from([0, 3, 8, 100_000]),
       max_reads=st.sampled_from([None, 0, 2]))
def test_hypothesis_token_files(tmp_path, lead, body, sizes, batch, block, max_nt, max_reads):
    path = write(tmp_path / "h", lead + b"".join(body))
    with limits(block, max_nt):
        r = outcome(lambda: ref.iter_reads(path, batch, max_reads=max_reads))
        for s in (None, sizes):
            assert_same_outcome(r, outcome(lambda: nr.iter_reads_native(path, batch, max_reads=max_reads, read_sizes=s)))
