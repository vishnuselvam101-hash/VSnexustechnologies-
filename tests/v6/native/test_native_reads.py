"""Native streaming read parser (vnxdna.v6.native_reads) == reference (vnxdna.v4.reads): directed cases.

Every case runs the reference and the native parser (block-aligned reads and several arbitrary chunkings)
and requires the same batches and the same exception. Each malformed-input class also pins the
reference's exception class so the case provably exercises that class.
"""
from __future__ import annotations

import ctypes
import gzip
import logging
import subprocess
from pathlib import Path

import numpy as np
import pytest

from vnxdna.v4 import reads as ref
from vnxdna.v4.errors import VNXFormatError, VNXResourceError
from vnxdna.v6 import native_reads as nr

from .native_reads_support import assert_same, assert_same_outcome, limits, outcome, run_both, write

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def _native(tmp_path_factory):
    from .native_reads_support import ensure_native

    ensure_native(tmp_path_factory)


def chunkings(data: bytes):
    yield None
    if len(data) < 20_000:
        yield [1]
        yield [2, 3, 5]
    else:
        yield [2, 3, 5, 4093]
    yield [max(1, len(data) // 2), 1]
    yield [7, 4096]


def check(path: Path, data: bytes, batch: int = 8192, max_reads=None):
    write(path, data)
    first = None
    for sizes in chunkings(data):
        r, n = run_both(path, batch, max_reads, sizes)
        assert_same_outcome(r, n)
        first = r
    return first


# ------------------------------------------------------------------------------------------- valid inputs
VALID = {
    "fastq-basic": b"@r1\nACGT\n+\nIIII\n@r2\nGGTTA\n+r2\n!!!!~\n",
    "fastq-no-final-newline": b"@r1\nACGT\n+\nIIII\n@r2\nGG\n+\n#!",
    "fastq-crlf": b"@r1\r\nACGT\r\n+\r\nIIII\r\n@r2\r\nAC\r\n+\r\nII\r\n",
    "fastq-multi-cr": b"@r1\r\r\nAC\r\r\r\n+\r\nII\r\r\n",
    "fastq-cr-inside": b"@r1\nA\rC\n+\nI\rI\n",
    "fastq-empty-record": b"@r1\n\n+\n\n@r2\nA\n+\nI\n",
    "fastq-empty-record-last": b"@r1\nA\n+\nI\n@r2\n\n+\n\n",
    "fastq-blank-lines-between": b"\n\n@r1\nA\n+\nI\n\r\n\n@r2\nC\n+\nJ\n\n",
    "fastq-odd-symbols": b"@r1\nAXN-n*\x00\n+\n\x00\x01~\x7f\xff \t\n",
    "fastq-lowercase": b"@r\nacgtnN\n+\nIIIIII\n",
    "fasta-single-line": b">a\nACGT\n>b\nGG\n",
    "fasta-multi-line": b">a desc\nACGT\nTTGA\nC\n>b\nGG\nCC\n",
    "fasta-empty-records": b">a\n>b\n>c\nA\n>d\n",
    "fasta-crlf": b">a\r\nAC\r\nGT\r\n>b\r\n\r\nA\r\n",
    "fasta-whitespace": b">a\n  AC GT \t\n\t\n \r\n>b\n\x0bA\x0c\n",
    "fasta-blank-lines": b"\n\n>a\n\nAC\n\n\nGT\n\n",
    "fasta-cr-led-header": b">a\nAC\n\r>notheader\nGT\n",
    "fasta-no-final-newline": b">a\nAC\n>b\nGT",
    "plain-basic": b"ACGT\nGGA\n",
    "plain-whitespace": b"  ACGT  \n\n\t\nGG A\r\n \r\nTT",
    "plain-odd-later": b"ACGT\n" * 300 + b"AXXA\n@@\n>>\n+\n",
}


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_inputs(tmp_path, name):
    r = check(tmp_path / name, VALID[name])
    assert r[1] is None, r[1]
    assert sum(b.count for b in r[0]) > 0


@pytest.mark.parametrize("batch", [-3, 0, 1, 2, 3, 4, 7, 8192, 10**12])
@pytest.mark.parametrize("name", ["fastq-basic", "fasta-empty-records", "plain-whitespace", "fasta-multi-line"])
def test_batch_sizes(tmp_path, name, batch):
    data = VALID[name] * 5 if name != "fasta-multi-line" else VALID[name] * 3
    r = check(tmp_path / "f", data, batch)
    assert r[1] is None


@pytest.mark.parametrize("max_reads", [0, 1, 3, 4, 5, 9, 10, 11, None])
@pytest.mark.parametrize("batch", [1, 3, 8192])
def test_max_reads_cap(tmp_path, batch, max_reads):
    data = b"".join(b"@r%d\nACGT\n+\nIIII\n" % i for i in range(10))
    check(tmp_path / "f", data, batch, max_reads)


def test_float_batch_delegates_to_reference(tmp_path):
    p = write(tmp_path / "f", VALID["fastq-basic"] * 3)
    assert_same_outcome(outcome(lambda: ref.iter_reads(p, 2.5)), outcome(lambda: nr.iter_reads_native(p, 2.5)))


# ------------------------------------------------------------------------------------------ malformed inputs
M = ref.MAX_READ_NT
MALFORMED = {
    # FASTQ structure
    "fq-truncated-after-header": (b"@r1\nACGT\n+\nIIII\n@r2\n", VNXFormatError),
    "fq-truncated-after-seq": (b"@r1\nACGT\n+\nIIII\n@r2\nAC\n", VNXFormatError),
    "fq-truncated-after-plus": (b"@r1\nACGT\n+\nIIII\n@r2\nAC\n+\n", VNXFormatError),
    "fq-truncated-crlf": (b"@r1\r\nAC\r\n+\r\n", VNXFormatError),
    "fq-header-only-eof": (b"@r1", VNXFormatError),
    "fq-missing-at": (b"@r1\nA\n+\nI\nr2\nA\n+\nI\n", VNXFormatError),
    "fq-header-space-line": (b"@r1\nA\n+\nI\n \n@r2\nA\n+\nI\n", VNXFormatError),
    "fq-cr-then-at": (b"@r1\nA\n+\nI\n\r@r2\nA\n+\nI\n", VNXFormatError),
    "fq-long-bad-header": (b"@r1\nA\n+\nI\n" + b"x" * 100 + b"\n", VNXFormatError),
    "fq-missing-plus": (b"@r1\nACGT\n-\nIIII\n", VNXFormatError),
    "fq-empty-plus": (b"@r1\nACGT\n\nIIII\n", VNXFormatError),
    "fq-cr-plus": (b"@r1\nACGT\n\r+\nIIII\n", VNXFormatError),
    "fq-qual-short": (b"@r1\nACGT\n+\nIII\n", VNXFormatError),
    "fq-qual-long": (b"@r1\nACGT\n+\nIIIII\n", VNXFormatError),
    "fq-qual-inner-cr-counts": (b"@r1\r\nACGT\r\n+\r\nII\rI\r\n", None),
    "fq-qual-crlf-mismatch": (b"@r1\r\nACGT\r\n+\r\nI\rI\r\n", VNXFormatError),
    "fq-final-empty-record-needs-blank": (b"@r1\nA\n+\nI\n@r2\n\n+\n", VNXFormatError),
    "fq-qual-missing-eof-blank": (b"@r1\nACGT\n+\n\n", VNXFormatError),
    "fq-long-header-in-error": (b"@" + b"h" * 80 + b"\nAC\n+\nI\n", VNXFormatError),
    "fq-binary-header-in-error": (b"@\x00\xff'\"\\\nAC\n+\nI\n", VNXFormatError),
    # FASTQ record cap: exactly at the cap is fine, one over is rejected (length check after format check)
    "fq-over-cap": (b"@r\n" + b"A" * (M + 1) + b"\n+\n" + b"I" * (M + 1) + b"\n", VNXResourceError),
    "fq-over-cap-and-qual-mismatch": (b"@r\n" + b"A" * (M + 1) + b"\n+\n" + b"I" * M + b"\n", VNXFormatError),
    "fq-at-cap": (b"@r\n" + b"A" * M + b"\n+\n" + b"I" * M + b"\n", None),
    "fq-at-cap-crlf": (b"@r\n" + b"A" * M + b"\r\n+\n" + b"I" * M + b"\r\r\n", None),
    # FASTA
    "fa-data-before-header": (b" \n>a\nAC\n", VNXFormatError),
    "fa-cr-data-before-header": (b"\r\n\t\r\n>a\nAC\n", VNXFormatError),
    "fa-over-cap-one-line": (b">a\n" + b"A" * (M + 1) + b"\n", VNXResourceError),
    "fa-over-cap-multi-line": (b">a\n" + (b"A" * 1000 + b"\n") * 100 + b"C\n", VNXResourceError),
    "fa-at-cap-multi-line": (b">a\n" + (b"A" * 1000 + b"\n") * 100 + b">b\nC\n", None),
    "fa-over-cap-by-whitespace": (b">a\n" + (b" " + b"A" * 999 + b"\n") * 100 + b" \n", VNXResourceError),
    "fa-cap-ignores-trailing-cr": (b">a\n" + (b"A" * 1000 + b"\r\n") * 100, None),
    # plain
    "plain-over-cap": (b"ACGT\n" + b"A" * (M + 1) + b"\n", VNXResourceError),
    "plain-at-cap-padded": (b"ACGT\n  " + b"A" * M + b" \t\r\n", None),
    # detection-level refusals (shared code path, still must match)
    "gzip": (gzip.compress(b"@r\nA\n+\nI\n"), VNXFormatError),
    "bam": (b"BAM\x01" + bytes(64), VNXFormatError),
    "garbage": (b"\x00\x01\x02hello", VNXFormatError),
    "whitespace-only": (b" \n\r\n\t", VNXFormatError),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_inputs(tmp_path, name):
    data, expected = MALFORMED[name]
    r = check(tmp_path / name, data, batch=1)
    if expected is None:
        assert r[1] is None, r[1]
    else:
        assert r[1] is not None and r[1][0] is expected, r[1]


def test_error_after_batches_yielded_in_order(tmp_path):
    good = b"".join(b"@r%d\nACGT\n+\nIIII\n" % i for i in range(7))
    r = check(tmp_path / "f", good + b"@bad\nAC\n+\nI\n" + good, batch=2)
    assert len(r[0]) == 3 and r[1][0] is VNXFormatError


# ------------------------------------------------------------------- long lines and block-boundary ordering
def _long_line_files():
    rec = b"@r\nAC\n+\nII\n"
    big = 2 * M + 4096 + 1
    yield "header-over-limit", rec * 3 + b"@" + b"h" * big + b"\nA\n+\nI\n"
    yield "plus-over-limit", rec * 3 + b"@x\nA\n+" + b"p" * big + b"\nI\n"
    yield "seq-over-limit", rec * 3 + b"@x\n" + b"A" * big + b"\n+\nI\n"
    yield "header-at-limit", rec * 3 + b"@" + b"h" * (big - 2) + b"\nA\n+\nI\n"
    yield "fasta-header-over-limit", b">a\nAC\n>" + b"h" * big + b"\nAC\n"
    yield "plain-over-limit", b"ACGT\n" * 3 + b"A" * big + b"\n"
    yield "final-line-over-limit", rec * 2 + b"@" + b"h" * big


@pytest.mark.parametrize("block", [1000, 4096, 65536, 8 << 20])
@pytest.mark.parametrize("name", [name for name, _ in _long_line_files()])
def test_long_lines_match_reference_at_block_boundaries(tmp_path, name, block):
    data = dict(_long_line_files())[name]
    with limits(block=block):
        r = check(tmp_path / name, data, batch=1)
    assert r[1] is None or r[1][0] in (VNXResourceError, VNXFormatError)


@pytest.mark.parametrize("block", [1, 2, 3, 64, 1000, 5000])
def test_small_limits_line_check_ordering(tmp_path, block):
    """Limit 4116 (MAX_READ_NT=10): lines of a few KiB cross many tiny blocks; ordering vs batches is exact."""
    rng = np.random.default_rng(block)
    parts = []
    for i in range(30):
        h = b"h" * int(rng.choice([1, 4000, 4200, 9000]))
        parts.append(b"@%d%s\nACGT\n+\nIIII\n" % (i, h))
    with limits(block=block, max_nt=10):
        r = check(tmp_path / "f", b"".join(parts), batch=1)
    assert r[1] is not None


def test_every_two_chunk_split(tmp_path):
    data = b"@r1\r\nACGT\r\n+\r\nIIII\r\n\n@r2\nAC\n+\nII\n@r3\n\n+\n\n"
    p = write(tmp_path / "f", data)
    r = outcome(lambda: ref.iter_reads(p, 1))
    for cut in range(1, len(data) + 1):
        assert_same_outcome(r, outcome(lambda: nr.iter_reads_native(p, 1, read_sizes=[cut, len(data)])))


def test_one_byte_chunks_with_tiny_blocks(tmp_path):
    data = b">a\n" + b"ACGT \n" * 2000 + b">b\n" + b"C" * 5000 + b"\n"
    for block in (1, 3, 1000):
        with limits(block=block, max_nt=20_000):
            p = write(tmp_path / "f", data)
            assert_same_outcome(outcome(lambda: ref.iter_reads(p, 1)),
                                outcome(lambda: nr.iter_reads_native(p, 1, read_sizes=[1])))


# --------------------------------------------------------------------------------- real files and defaults
def _fixture_files():
    root = REPO / "tests" / "fixtures"
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in (".fasta", ".fastq", ".fa", ".fq", ".gz"))


def test_committed_fixtures(tmp_path):
    files = _fixture_files()
    assert files
    for path in files:
        assert_same(path, 4096)
        if path.suffix == ".gz":
            plain = write(tmp_path / path.stem, gzip.decompress(path.read_bytes()))
            r = assert_same(plain, 4096)
            assert r[1] is None and r[0]


def test_multi_block_file_with_default_limits(tmp_path):
    """~20 MiB FASTQ: three real 8 MiB blocks, records split across block boundaries."""
    rng = np.random.default_rng(7)
    seq = rng.integers(0, 4, 150 * 4096).astype(np.uint8)
    body = np.frombuffer(b"ACGT", np.uint8)[seq].reshape(4096, 150)
    q = rng.integers(33, 75, (4096, 150)).astype(np.uint8)
    chunk = b"".join(b"@read%d\n%s\n+\n%s\n" % (i, body[i].tobytes(), q[i].tobytes()) for i in range(4096))
    data = chunk * (20 * 2**20 // len(chunk) + 1)
    p = write(tmp_path / "big.fastq", data)
    r = assert_same(p, 8192)
    assert r[1] is None and sum(b.count for b in r[0]) == data.count(b"\n+\n")


def test_batches_own_their_memory(tmp_path):
    p = write(tmp_path / "f", VALID["fastq-basic"] * 100)
    batches = list(nr.iter_reads_native(p, 7))
    snapshot = [b.codes.copy() for b in batches]
    for b in batches:
        assert b.codes.flags.owndata and b.quals.flags.owndata and b.lengths.flags.owndata
        assert b.codes.base is None
        b.codes[:] = 9     # consumers may modify; nothing else changes
    assert all((b.codes == 9).all() for b in batches) and snapshot[0].max() <= 4


def test_deterministic(tmp_path):
    rng = np.random.default_rng(3)
    from .native_reads_support import gen_fastq, random_sizes

    data = gen_fastq(rng, 500, odd=0.05, crlf=0.3, blank=0.1)
    p = write(tmp_path / "f", data)
    runs = [outcome(lambda s=s: nr.iter_reads_native(p, 33, read_sizes=s))
            for s in (None, None, random_sizes(rng, len(data)), [1])]
    for other in runs[1:]:
        assert_same_outcome(runs[0], other)


# ------------------------------------------------------------------------------ backend selection / fallback
def test_backend_selection_and_fallback(tmp_path, monkeypatch, caplog):
    p = write(tmp_path / "f", VALID["fastq-basic"])
    want = outcome(lambda: ref.iter_reads(p))
    for name in ("auto", "native", "reference"):
        monkeypatch.setenv("VNXDNA_READS_BACKEND", name)
        assert_same_outcome(want, outcome(lambda: nr.iter_reads(p)))
    assert nr.resolve_backend("reference") == "reference"
    assert nr.resolve_backend("native") == "native"
    monkeypatch.setenv("VNXDNA_READS_BACKEND", "bogus")
    with pytest.raises(ValueError):
        list(nr.iter_reads(p))
    st = nr.status()
    assert st["error"] and st["native_available"]

    # library missing: auto falls back (logged once), native raises, reference unaffected
    monkeypatch.setenv("VNXDNA_READS_LIB", str(tmp_path / "missing.so"))
    monkeypatch.setattr(nr, "_INPLACE", tmp_path / "also-missing.so")
    nr._reset_for_tests()
    try:
        monkeypatch.setenv("VNXDNA_READS_BACKEND", "auto")
        with caplog.at_level(logging.WARNING, logger="vnxdna.v6.native_reads"):
            assert_same_outcome(want, outcome(lambda: nr.iter_reads(p)))
            assert_same_outcome(want, outcome(lambda: nr.iter_reads(p)))
        assert sum("unavailable" in r.message for r in caplog.records) == 1
        assert nr.resolve_backend() == "reference" and not nr.available()
        monkeypatch.setenv("VNXDNA_READS_BACKEND", "native")
        with pytest.raises(nr.NativeReadsError, match="missing.so"):
            list(nr.iter_reads(p))
        with pytest.raises(nr.NativeReadsError):
            list(nr.iter_reads_native(p))
        monkeypatch.setenv("VNXDNA_READS_BACKEND", "reference")
        assert_same_outcome(want, outcome(lambda: nr.iter_reads(p)))
    finally:
        monkeypatch.undo()
        nr._reset_for_tests()
    assert nr.available()


def test_abi_mismatch_is_rejected(tmp_path, monkeypatch):
    src = tmp_path / "stub.c"
    src.write_text("int vnx_reads_abi_version(void) { return 999; }\n"
                   "long vnx_reads_state_size(void) { return 0; }\nint vnx_reads_init(void) { return 0; }\n"
                   "int vnx_reads_new_batch(void) { return 0; }\nint vnx_reads_feed(void) { return 0; }\n")
    lib = tmp_path / "stub.so"
    try:
        subprocess.run(["cc", "-shared", "-fPIC", str(src), "-o", str(lib)], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        pytest.skip(f"no C compiler for the ABI stub: {error}")
    monkeypatch.setenv("VNXDNA_READS_LIB", str(lib))
    monkeypatch.setattr(nr, "_INPLACE", tmp_path / "missing.so")
    nr._reset_for_tests()
    try:
        assert not nr.available()
        assert "ABI 999 != 1" in nr.status()["load_error"]
        assert nr.resolve_backend("auto") == "reference"
    finally:
        monkeypatch.undo()
        nr._reset_for_tests()


def test_build_command(tmp_path):
    out = nr.build(tmp_path / "libvnx_reads.so")
    lib = ctypes.CDLL(str(out))
    assert lib.vnx_reads_abi_version() == nr.ABI_VERSION
    assert "-march" not in " ".join(nr.CFLAGS) and "-Werror" in nr.CFLAGS


# ------------------------------------------------------------------------------------- raw C ABI hardening
def _state(fmt=1, max_nt=100, limit=4296, block=1 << 23):
    lib = nr._load()
    st = np.zeros(int(lib.vnx_reads_state_size()), np.uint8)
    assert lib.vnx_reads_init(st.ctypes.data, st.size, fmt, max_nt, limit, block) == 0
    return lib, st


def test_c_abi_rejects_bad_arguments():
    lib = nr._load()
    st = np.zeros(int(lib.vnx_reads_state_size()), np.uint8)
    assert lib.vnx_reads_init(None, 0, 1, 1, 1, 1) == -1
    assert lib.vnx_reads_init(st.ctypes.data, st.size - 1, 1, 1, 1, 1) == -1
    for fmt, mx, lim, blk in ((0, 1, 1, 1), (4, 1, 1, 1), (1, -1, 1, 1), (1, 1, -1, 1), (1, 1, 1, 0)):
        assert lib.vnx_reads_init(st.ctypes.data, st.size, fmt, mx, lim, blk) == -1
    info = np.zeros(4, np.int64)
    assert lib.vnx_reads_feed(st.ctypes.data, None, 0, 0, None, None, 0, None, None, 0, info.ctypes.data, None) == -1
    assert lib.vnx_reads_new_batch(st.ctypes.data) == -1
    lib, st = _state()
    data = np.frombuffer(b"@r\nAC\n+\nII\n", np.uint8)
    codes = np.zeros(200, np.uint8)
    quals = np.zeros(200, np.uint8)
    lengths = np.zeros(4, np.int64)
    inv = np.zeros(4, np.uint8)
    feed = lib.vnx_reads_feed
    p = lambda a: a.ctypes.data  # noqa: E731
    assert feed(p(st), None, 5, 0, p(codes), p(quals), 200, p(lengths), p(inv), 4, p(info), None) == -1
    assert feed(p(st), p(data), -1, 0, p(codes), p(quals), 200, p(lengths), p(inv), 4, p(info), None) == -1
    assert feed(p(st), p(data), data.size, 0, p(codes), None, 200, p(lengths), p(inv), 4, p(info), None) == -1
    assert feed(p(st), p(data), data.size, 0, p(codes), p(quals), 200, None, p(inv), 4, p(info), None) == -1
    assert feed(p(st), p(data), data.size, 0, p(codes), p(quals), -5, p(lengths), p(inv), 4, p(info), None) == -1
    # capacity below MAX_READ_NT for an open record -> NEED_SPACE, never a write past cap
    lib, st = _state(max_nt=100)
    small = np.zeros(8, np.uint8)
    rc = feed(p(st), p(data), data.size, 1, p(small), p(small), 8, p(lengths), p(inv), 4, p(info), None)
    assert rc == nr.NEED_SPACE and int(info[0]) == 3
    # zero record capacity -> BATCH_FULL without consuming
    rc = feed(p(st), p(data), data.size, 1, p(codes), p(quals), 200, p(lengths), p(inv), 0, p(info), None)
    assert rc == nr.BATCH_FULL and int(info[0]) == 0
    rc = feed(p(st), p(data) + 3, data.size - 3, 1, p(codes), p(quals), 200, p(lengths), p(inv), 4, p(info), None)
    assert rc == nr.DONE and int(info[1]) == 1 and lengths[0] == 2
    # after DONE: DONE again; after an error: the same error again
    assert feed(p(st), None, 0, 1, p(codes), p(quals), 200, p(lengths), p(inv), 4, p(info), None) == nr.DONE
    lib, st = _state()
    bad = np.frombuffer(b"x\n", np.uint8)
    for _ in range(2):
        assert feed(p(st), p(bad), bad.size, 1, p(codes), p(quals), 200, p(lengths), p(inv), 4, p(info), None) == -11


def test_c_abi_position_overflow_is_rejected():
    lib, st = _state()
    info = np.zeros(4, np.int64)
    one = np.frombuffer(b"@", np.uint8)
    buf = np.zeros(200, np.uint8)
    lens = np.zeros(4, np.int64)
    p = lambda a: a.ctypes.data  # noqa: E731
    assert lib.vnx_reads_feed(p(st), p(one), 1, 0, p(buf), p(buf), 200, p(lens), p(buf), 4, p(info), None) == 0
    # a length that would overflow the absolute position (pointer is never dereferenced: rejected first)
    assert lib.vnx_reads_feed(p(st), p(one), 2**63 - 1, 0, p(buf), p(buf), 200, p(lens), p(buf), 4, p(info), None) == -1


@pytest.mark.parametrize("fmt", ["fastq", "fasta", "plain"])
def test_every_byte_value_in_sequence_and_quality(tmp_path, fmt):
    """All 256 byte values (except the line feed) as sequence and quality symbols, one per read and all in one read."""
    values = [bytes([v]) for v in range(256) if v != 10]
    if fmt == "fastq":
        recs = [b"@b%d\nAC%sGT\n+\n!%sI%s!\n" % (i, v, v, v) for i, v in enumerate(values)]
        recs.append(b"@all\n" + b"".join(values) + b"\n+\n" + b"".join(values) + b"\n")
    elif fmt == "fasta":
        recs = [b">b%d\nA%sC\n" % (i, v) for i, v in enumerate(values)] + [b">all\n" + b"".join(values) + b"\n"]
    else:
        recs = [b"ACGT\n" * 260] + [b"A%sC\n" % v for v in values] + [b"".join(values) + b"\n"]
    r = check(tmp_path / fmt, b"".join(recs), batch=64)
    assert r[1] is None and sum(b.count for b in r[0]) > 200
