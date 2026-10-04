"""Shared helpers for the native read-parser tests: file generators and a strict native == reference check.

The reference (vnxdna.v4.reads.iter_reads) is the specification. ``outcome`` runs a parser to completion and
returns every batch plus the exception that ended it (class, message, stage); ``assert_same`` compares the
two outcomes field by field: batch count, array dtypes, shapes, contiguity and values, quals presence, and
the exact exception.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import numpy as np

from vnxdna.v4 import reads as ref
from vnxdna.v6 import native_reads as nr

SEQ_COMMON = b"ACGTACGTACGTACGTNacgtn"
SEQ_ODD = b" \t\r\x0b\x0cXx-.*\x00\xff>@+"


def outcome(factory):
    batches, err = [], None
    try:
        for b in factory():
            batches.append(b)
    except Exception as error:  # noqa: BLE001 - any exception class must match the reference exactly
        err = (type(error), str(error), getattr(error, "stage", None))
    return batches, err


def _same_array(a: np.ndarray, b: np.ndarray, what: str) -> None:
    assert isinstance(a, np.ndarray) and isinstance(b, np.ndarray), what
    assert a.dtype == b.dtype, (what, a.dtype, b.dtype)
    assert a.shape == b.shape, (what, a.shape, b.shape)
    assert b.flags.c_contiguous and b.flags.owndata and b.flags.writeable, what
    assert np.array_equal(a, b), what


def assert_same_outcome(r, n) -> None:
    rb, rerr = r
    nb, nerr = n
    assert rerr == nerr, (rerr, nerr)
    assert len(rb) == len(nb), (len(rb), len(nb), rerr)
    for i, (x, y) in enumerate(zip(rb, nb)):
        assert type(x) is type(y) is ref.Reads
        _same_array(x.codes, y.codes, f"batch {i} codes")
        _same_array(x.lengths, y.lengths, f"batch {i} lengths")
        _same_array(x.invalid, y.invalid, f"batch {i} invalid")
        assert (x.quals is None) == (y.quals is None), f"batch {i} quals presence"
        if x.quals is not None:
            _same_array(x.quals, y.quals, f"batch {i} quals")


def run_both(path, batch=8192, max_reads=None, read_sizes=None):
    r = outcome(lambda: ref.iter_reads(path, batch, max_reads=max_reads))
    n = outcome(lambda: nr.iter_reads_native(path, batch, max_reads=max_reads, read_sizes=read_sizes))
    return r, n


def assert_same(path, batch=8192, max_reads=None, read_sizes=None):
    r, n = run_both(path, batch, max_reads, read_sizes)
    assert_same_outcome(r, n)
    return r


@contextlib.contextmanager
def limits(block: int | None = None, max_nt: int | None = None):
    """Temporarily change the reference's block size / record cap (both parsers read them at call time)."""
    old = ref.BLOCK, ref.MAX_READ_NT
    try:
        if block is not None:
            ref.BLOCK = block
        if max_nt is not None:
            ref.MAX_READ_NT = max_nt
        yield
    finally:
        ref.BLOCK, ref.MAX_READ_NT = old


# ----------------------------------------------------------------------------------------------- generators
def _seq(rng: np.random.Generator, n: int, odd: float) -> bytes:
    pool = np.frombuffer(SEQ_COMMON, np.uint8)
    out = pool[rng.integers(0, pool.size, n)]
    if odd > 0 and n:
        mask = rng.random(n) < odd
        bad = np.frombuffer(SEQ_ODD, np.uint8)
        out = out.copy()
        out[mask] = bad[rng.integers(0, bad.size, int(mask.sum()))]
    return out.tobytes()


def _qual(rng: np.random.Generator, n: int, odd: float) -> bytes:
    q = rng.integers(33, 127, n).astype(np.uint8)
    if odd > 0 and n:
        mask = rng.random(n) < odd
        q[mask] = rng.integers(0, 256, int(mask.sum())).astype(np.uint8)
        q[q == 10] = 73
    return q.tobytes()


def _eol(rng, crlf: float) -> bytes:
    r = rng.random()
    if r < crlf:
        return b"\r\n" if rng.random() < 0.9 else b"\r\r\n"
    return b"\n"


def gen_fastq(rng: np.random.Generator, n_reads: int, max_len: int = 200, odd: float = 0.0, crlf: float = 0.0,
              blank: float = 0.0, long_line: float = 0.0) -> bytes:
    parts = []
    for i in range(n_reads):
        if rng.random() < blank:
            parts.append(_eol(rng, crlf) * int(rng.integers(1, 3)))
        n = int(rng.integers(0, max_len + 1))
        if rng.random() < long_line:
            n = int(rng.integers(4000, 9000))
        name = b"r%d" % i + (b" x" * int(rng.integers(0, 4)))
        plus = b"+" if rng.random() < 0.7 else b"+" + name
        parts += [b"@", name, _eol(rng, crlf), _seq(rng, n, odd), _eol(rng, crlf), plus, _eol(rng, crlf),
                  _qual(rng, n, odd), _eol(rng, crlf)]
    data = b"".join(parts)
    if data and rng.random() < 0.3:
        data = data.rstrip(b"\n")
    return data


def gen_fasta(rng: np.random.Generator, n_reads: int, max_len: int = 200, odd: float = 0.0, crlf: float = 0.0,
              blank: float = 0.0, long_line: float = 0.0) -> bytes:
    parts = []
    for i in range(n_reads):
        parts += [b">r%d" % i, b" desc" * int(rng.integers(0, 3)), _eol(rng, crlf)]
        n = int(rng.integers(0, max_len + 1))
        if rng.random() < long_line:
            n = int(rng.integers(4000, 9000))
        seq = _seq(rng, n, odd)
        width = int(rng.integers(1, 120)) if rng.random() < 0.6 else max(1, n)
        for k in range(0, len(seq), width):
            if rng.random() < blank:
                parts.append(_eol(rng, crlf))
            pad_l = b" " * int(rng.integers(0, 2)) if odd else b""
            pad_r = (b" \t"[: int(rng.integers(0, 3))]) if odd else b""
            parts += [pad_l, seq[k:k + width], pad_r, _eol(rng, crlf)]
    data = b"".join(parts)
    if data and rng.random() < 0.3:
        data = data.rstrip(b"\n")
    return data


def gen_plain(rng: np.random.Generator, n_reads: int, max_len: int = 200, odd: float = 0.0, crlf: float = 0.0,
              blank: float = 0.0, long_line: float = 0.0) -> bytes:
    parts = []
    for _ in range(n_reads):
        if rng.random() < blank:
            parts.append(b" \t"[: int(rng.integers(0, 3))] + _eol(rng, crlf))
        n = int(rng.integers(1, max(1, max_len) + 1))
        if rng.random() < long_line:
            n = int(rng.integers(4000, 9000))
        parts += [b" " * int(rng.integers(0, 2)), _seq(rng, n, odd), _eol(rng, crlf)]
    data = b"".join(parts)
    if not data.lstrip()[:1].isalpha():
        data = b"ACGT\n" + data
    return data


GENERATORS = {"fastq": gen_fastq, "fasta": gen_fasta, "plain": gen_plain}


def mutate(rng: np.random.Generator, data: bytes, n_edits: int) -> bytes:
    """Random byte substitutions, insertions, deletions and truncation (structure-aware alphabet)."""
    buf = bytearray(data)
    alphabet = b"\n\r@+>ACGTN \tI!~\x00\xff"
    for _ in range(n_edits):
        if not buf:
            break
        op = rng.integers(0, 4)
        pos = int(rng.integers(0, len(buf)))
        ch = alphabet[int(rng.integers(0, len(alphabet)))] if rng.random() < 0.8 else int(rng.integers(0, 256))
        if op == 0:
            buf[pos] = ch
        elif op == 1:
            buf.insert(pos, ch)
        elif op == 2:
            del buf[pos]
        else:
            del buf[pos:]
    return bytes(buf)


def random_sizes(rng: np.random.Generator, total: int) -> list[int]:
    """Chunk sizes from 1 byte to a few KiB, including many tiny chunks."""
    out, acc = [], 0
    while acc <= total:
        k = int(rng.choice([1, 2, 3, 7, int(rng.integers(1, 64)), int(rng.integers(64, 5000))]))
        out.append(k)
        acc += k
    return out


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def fuzz_case(seed: int) -> tuple[bytes, dict]:
    """One seeded fuzz case: a generated (often mutated) file plus parser limits and iteration options."""
    rng = np.random.default_rng(seed)
    kind = ("fastq", "fasta", "plain")[seed % 3]
    data = GENERATORS[kind](rng, int(rng.integers(0, 40)), int(rng.integers(0, 300)),
                            odd=float(rng.choice([0.0, 0.02, 0.2])), crlf=float(rng.choice([0.0, 0.5, 1.0])),
                            blank=float(rng.choice([0.0, 0.2])), long_line=float(rng.choice([0.0, 0.0, 0.05])))
    if kind == "plain" and rng.random() < 0.6:
        data = b"ACGTN acgt\n" * 100 + data          # keep detection on 'plain' despite odd symbols later
    if kind == "fasta" and rng.random() < 0.15:
        data = [b"\n", b"\r\n", b" \n", b"\t\r\n", b"\r"][int(rng.integers(0, 5))] + data
    if rng.random() < 0.5:
        data = mutate(rng, data, int(rng.integers(1, 6)))
    opts = {"block": int(rng.choice([1, 7, 64, 1000, 4200, 8 << 20])), "max_nt": int(rng.choice([0, 30, 200, 100_000])),
            "batch": int(rng.choice([-1, 0, 1, 2, 3, 5, 8192])), "max_reads": [None, None, 5, 0][int(rng.integers(0, 4))],
            "sizes": random_sizes(rng, len(data))}
    return data, opts


def check_case(path: Path, data: bytes, opts: dict) -> None:
    write(path, data)
    with limits(opts["block"], opts["max_nt"]):
        r = outcome(lambda: ref.iter_reads(path, opts["batch"], max_reads=opts["max_reads"]))
        for sizes in (None, opts["sizes"]):
            n = outcome(lambda: nr.iter_reads_native(path, opts["batch"], max_reads=opts["max_reads"], read_sizes=sizes))
            assert_same_outcome(r, n)


def ensure_native(tmp_path_factory) -> None:
    """Load the native library, building it into a temporary directory if needed (skip if impossible)."""
    import os

    import pytest

    if nr.available():
        return
    try:
        lib = nr.build(tmp_path_factory.mktemp("native_reads") / "libvnx_reads.so")
    except nr.NativeReadsError as error:
        pytest.skip(f"native reads parser unavailable and cannot be built here: {error}")
    os.environ["VNXDNA_READS_LIB"] = str(lib)
    nr._reset_for_tests()
    assert nr.available(), nr.status()
