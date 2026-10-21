"""LR-3: the minimal, dependency-free VNX-DNA reference decoder.

Purpose
-------
This module is the *reference* decoder required by the V15 finalization plan
(LR-3): a second, independent implementation of the format-4 read path, short
enough to read and re-implement, that a future party can port to another
language without looking at the production code.  It exists so that the
normative specification (LR-1) can be checked against *running* code, and so
that a format change that silently breaks the archive is caught by two
independent implementations rather than one.

What it implements
------------------
The whole format-4 read path, in the order a reader must do it:

1. read parsing (FASTA / FASTQ / one sequence per line) -- sequence text only,
   headers are labels and are never trusted;
2. frame demapping (2bit / rotation3 / codebook8) with an erasure mask;
3. de-scrambling, frame validation (CRC-32) and the inner GF(256)
   Reed-Solomon errors-and-erasures correction;
4. consensus over duplicate copies of one address (strict majority; a tie is
   an erasure, never a guess);
5. metadata recovery (Cauchy 8+8) and manifest validation;
6. the outer Cauchy Reed-Solomon erasure code per stripe;
7. the container layer: chunk SHA-256, decompression, AES-256-GCM and the
   whole-object SHA-256.

Every check is recomputed from bytes in hand.  The decoder never returns bytes
whose SHA-256 does not match the recorded digest: on any failure it raises a
typed :class:`ReferenceDecodeError` subclass and returns nothing.

Dependencies
------------
**Only the Python standard library.**  The algorithmic core (field
arithmetic, the inner RS decoder, the outer Cauchy code, the mappings, the
frame parser, AES-256-GCM) is implemented here from the primitives named in
the format specification; nothing is imported from ``vnxdna`` and nothing
outside the standard library is imported at all.  A test asserts the latter
mechanically (``tests/v15/test_hardware_lane.py``).

Two limits follow from the standard-library-only rule and are reported as
typed refusals rather than silent approximations:

* **zstd** compressed archives need a zstd decoder, which the standard
  library only provides from Python 3.14 (``compression.zstd``).  The decoder
  uses it when the interpreter has it and otherwise raises
  :class:`ReferenceUnsupportedError`.  The golden corpus therefore uses
  ``none`` and ``zlib`` so that it is fully covered on every supported
  interpreter.
* **Encrypted archives** are supported: AES-256-GCM is implemented here in
  terms of AES-256 and GHASH, so no third-party package is needed.

Evidence
--------
MEASURED in the sense that it is executed and compared against the production
decoder on the committed golden corpus (``reference/golden/``; see
``tests/v15/test_hardware_lane.py``).  The comparison is byte-exact.  Nothing
here was run on physical DNA: every input is a simulated read set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = [
    "ReferenceDecodeError",
    "ReferenceFormatError",
    "ReferenceIntegrityError",
    "ReferenceUnsupportedError",
    "ReferenceRedundancyError",
    "ReferenceKeyError",
    "decode_reads",
    "decode_container",
    "restore_reads",
    "restore_container",
    "read_sequences",
    "parse_reads",
]


# =========================================================================== errors


class ReferenceDecodeError(Exception):
    """Base class: the reference decoder refused to return bytes."""


class ReferenceFormatError(ReferenceDecodeError):
    """The input is not a VNX-DNA format-4 archive (bad magic, bad version, bad structure)."""


class ReferenceIntegrityError(ReferenceDecodeError):
    """A digest, tag or checksum did not match: the data is corrupt or tampered with."""


class ReferenceUnsupportedError(ReferenceDecodeError):
    """The archive requires a feature this decoder does not implement."""


class ReferenceRedundancyError(ReferenceDecodeError):
    """Too many strands were lost for the outer code to recover the data."""


class ReferenceKeyError(ReferenceDecodeError):
    """The archive is encrypted and no (or the wrong) key was supplied."""


# =========================================================================== GF(2^8)

PRIMITIVE_POLY = 0x11D
GENERATOR = 2
MAX_CODEWORD = 255


def _build_field() -> tuple[list[int], list[int]]:
    """EXP/LOG tables for GF(2^8) with primitive polynomial 0x11D, generator 2."""
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= PRIMITIVE_POLY
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


EXP, LOG = _build_field()


def gf_mul(a: int, b: int) -> int:
    """Field product (the software expression ``EXP[LOG[a] + LOG[b]]``)."""
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def gf_inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("0 has no inverse in GF(256)")
    return EXP[255 - LOG[a]]


def gf_div(a: int, b: int) -> int:
    return gf_mul(a, gf_inv(b))


def gf_poly_eval(poly: Sequence[int], x: int) -> int:
    """Evaluate a low-degree-first polynomial at ``x`` (Horner)."""
    acc = 0
    for coefficient in reversed(poly):
        acc = gf_mul(acc, x) ^ coefficient
    return acc


# =========================================================================== inner RS


def rs_generator_poly(nsym: int) -> list[int]:
    """Coefficients of g(x) = prod_{j<nsym} (x + alpha^j), highest degree first."""
    g = [1]
    for j in range(nsym):
        root = EXP[j]
        out = [0] * (len(g) + 1)
        for i, c in enumerate(g):
            out[i] ^= c
            out[i + 1] ^= gf_mul(c, root)
        g = out
    return g


def rs_parity(message: bytes, nsym: int) -> bytes:
    """Systematic RS parity: the remainder of message*x^nsym divided by g(x)."""
    if nsym == 0:
        return b""
    g = rs_generator_poly(nsym)
    buffer = bytearray(message) + bytearray(nsym)
    for i in range(len(message)):
        factor = buffer[i]
        if factor:
            for j in range(1, nsym + 1):
                buffer[i + j] ^= gf_mul(g[j], factor)
    return bytes(buffer[len(message):])


def rs_decode(codeword: bytes, nsym: int, erasures: Iterable[int] = ()) -> tuple[bytes, int] | None:
    """Errors-and-erasures decode of one GF(256) Reed-Solomon codeword.

    Returns ``(corrected codeword, errata count)`` or ``None`` when the
    decoder refuses.  The acceptance tests are the standard ones: the locator
    degree, the bound ``2(L-f)+f <= nsym``, the number of distinct Chien
    roots, the erasure/root consistency, a non-vanishing derivative, and zero
    syndromes of the corrected codeword.  A codeword whose syndromes are all
    zero is returned unchanged.

    Positions are codeword indices, 0 = first byte = highest-degree
    coefficient.
    """
    n = len(codeword)
    if nsym == 0:
        return (bytes(codeword), 0) if n else None
    if n == 0 or n > MAX_CODEWORD or nsym >= n:
        return None
    erase = sorted({int(i) for i in erasures if 0 <= int(i) < n})
    if len(erase) > nsym:
        return None
    cw = list(codeword)
    width = nsym + 1

    # 1. syndromes S_i = c(alpha^i) by Horner
    syndromes = []
    for i in range(nsym):
        root = EXP[i]
        acc = 0
        for byte in cw:
            acc = gf_mul(root, acc) ^ byte
        syndromes.append(acc)
    if not any(syndromes):
        return bytes(cw), 0

    # 2. erasure locator Gamma(x) = prod (1 + X_k x), low degree first
    x_of = [EXP[(n - 1 - j) % 255] for j in range(n)]
    gamma = [0] * width
    gamma[0] = 1
    for j in erase:
        shifted = [0] * width
        for k in range(width - 1, 0, -1):
            shifted[k] = gf_mul(x_of[j], gamma[k - 1])
        gamma = [a ^ b for a, b in zip(gamma, shifted)]

    # 3. Berlekamp-Massey initialised with Gamma (errors-and-erasures form)
    f = len(erase)
    lam = list(gamma)
    b = list(gamma)
    big_l = f
    for r in range(f + 1, nsym + 1):
        delta = 0
        for k in range(min(width, r)):
            delta ^= gf_mul(lam[k], syndromes[r - 1 - k])
        xb = [0] * width
        for k in range(1, width):
            xb[k] = b[k - 1]
        if delta == 0:
            b = xb
            continue
        t = [lam[k] ^ gf_mul(delta, xb[k]) for k in range(width)]
        if 2 * big_l <= r - 1 + f:
            scale = gf_inv(delta)
            b = [gf_mul(scale, lam[k]) for k in range(width)]
            big_l = r + f - big_l
        else:
            b = xb
        lam = t

    # 4. acceptance tests
    degree = max((k for k in range(width) if lam[k]), default=0)
    if degree != big_l or big_l > n or 2 * (big_l - f) + f > nsym:
        return None

    # 5. Chien search: position j is a root iff Lambda(X_j^-1) == 0
    roots = []
    for j in range(n):
        if gf_poly_eval(lam, gf_inv(x_of[j])) == 0:
            roots.append(j)
    if len(roots) != big_l:
        return None
    if not set(erase).issubset(set(roots)):
        return None

    # 6. Omega = (S * Lambda) mod x^nsym
    omega = [0] * nsym
    for k in range(width):
        if not lam[k]:
            continue
        span = nsym - k
        if span <= 0:
            break
        for t_index in range(span):
            omega[k + t_index] ^= gf_mul(lam[k], syndromes[t_index])

    # 7. formal derivative (characteristic 2: the odd-index coefficients)
    deriv = [0] * width
    for k in range(0, width - 1, 2):
        deriv[k] = lam[k + 1]

    # 8. Forney
    out = list(cw)
    for j in roots:
        x_inv = gf_inv(x_of[j])
        om = gf_poly_eval(omega, x_inv)
        de = gf_poly_eval(deriv, x_inv)
        if de == 0:
            return None
        out[j] ^= gf_mul(x_of[j], gf_div(om, de))

    # 9. the corrected codeword must have zero syndromes
    for i in range(nsym):
        root = EXP[i]
        acc = 0
        for byte in out:
            acc = gf_mul(root, acc) ^ byte
        if acc:
            return None
    return bytes(out), len(roots)


# =========================================================================== outer code


def gf_matrix_invert(matrix: list[list[int]]) -> list[list[int]]:
    """Gauss-Jordan inverse of a square matrix over GF(256)."""
    n = len(matrix)
    work = [list(row) + [int(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = next((r for r in range(col, n) if work[r][col]), None)
        if pivot is None:
            raise ReferenceRedundancyError("singular matrix: the stripe is not recoverable")
        work[col], work[pivot] = work[pivot], work[col]
        scale = gf_inv(work[col][col])
        work[col] = [gf_mul(v, scale) for v in work[col]]
        for r in range(n):
            factor = work[r][col]
            if r != col and factor:
                pivot_row = work[col]
                work[r] = [v ^ gf_mul(factor, p) for v, p in zip(work[r], pivot_row)]
    return [row[n:] for row in work]


class CauchyCode:
    """Systematic Cauchy Reed-Solomon erasure code over GF(256) (the outer code).

    Generator matrix ``G = [I_K ; C]`` with ``C[i][j] = 1 / (x_i + y_j)``,
    ``x_i = K + i``, ``y_j = j``.  Every square submatrix of a Cauchy matrix
    is nonsingular, so any K of the K+M shards reconstruct the data: any M
    erasures are recoverable.
    """

    def __init__(self, data_shards: int, parity_shards: int):
        if data_shards < 1 or parity_shards < 0 or data_shards + parity_shards > 256:
            raise ReferenceFormatError("invalid shard counts")
        self.k = data_shards
        self.m = parity_shards
        self.total = data_shards + parity_shards
        self.matrix = [[gf_inv((data_shards + i) ^ j) for j in range(data_shards)] for i in range(parity_shards)]

    def encode(self, data: list[bytes]) -> list[bytes]:
        """The systematic shards plus the M parity shards."""
        if len(data) != self.k:
            raise ReferenceFormatError("wrong number of data shards")
        length = len(data[0]) if data else 0
        parity = []
        for row in self.matrix:
            out = bytearray(length)
            for coefficient, shard in zip(row, data):
                if coefficient:
                    for pos, value in enumerate(shard):
                        out[pos] ^= gf_mul(coefficient, value)
            parity.append(bytes(out))
        return list(data) + parity

    def decode(self, shards: list[bytes | None]) -> list[bytes]:
        """Reconstruct the K data shards; ``None`` marks an erased shard."""
        present = [shard is not None for shard in shards]
        if sum(present) < self.k:
            raise ReferenceRedundancyError(
                f"stripe has {sum(present)} valid shards but {self.k} are needed")
        if all(present[: self.k]):
            return [bytes(shard) for shard in shards[: self.k]]  # type: ignore[arg-type]
        length = len(next(shard for shard in shards if shard is not None))
        out: list[bytes | None] = [bytes(shard) if shard is not None else None for shard in shards]
        missing = [j for j in range(self.k) if not present[j]]
        rows = [i for i in range(self.m) if present[self.k + i]][: len(missing)]
        if len(rows) < len(missing):
            raise ReferenceRedundancyError(
                f"stripe has {len(missing)} missing data shards but only {len(rows)} usable parity shards")
        sub = [[self.matrix[i][j] for j in missing] for i in rows]
        inverse = gf_matrix_invert(sub)
        syndrome = []
        for i in rows:
            # the parity shard minus the contribution of the known data shards
            parity_shard = shards[self.k + i]
            assert parity_shard is not None
            acc = bytearray(parity_shard)
            for j in range(self.k):
                if present[j] and j not in missing:
                    coefficient = self.matrix[i][j]
                    shard = shards[j]
                    if coefficient and shard is not None:
                        for pos, value in enumerate(shard):
                            acc[pos] ^= gf_mul(coefficient, value)
            syndrome.append(bytes(acc))
        for position, column in enumerate(missing):
            recovered = bytearray(length)
            for r, _ in enumerate(rows):
                coefficient = inverse[position][r]
                if coefficient:
                    for pos, value in enumerate(syndrome[r]):
                        recovered[pos] ^= gf_mul(coefficient, value)
            out[column] = bytes(recovered)
        return [bytes(shard) for shard in out[: self.k]]  # type: ignore[arg-type]


# =========================================================================== DNA mappings

BASES = "ACGT"
INVALID = 4
_ASCII_TO_CODE: dict[int, int] = {}
for _code, _base in enumerate(BASES):
    _ASCII_TO_CODE[ord(_base)] = _code
    _ASCII_TO_CODE[ord(_base.lower())] = _code
_ASCII_TO_CODE[ord("N")] = INVALID
_ASCII_TO_CODE[ord("n")] = INVALID
_COMPLEMENT = (3, 2, 1, 0, INVALID)
NT_PER_BYTE = {"2bit": 4, "rotation3": 6, "codebook8": 8}


def to_codes(sequence: str) -> list[int]:
    """Nucleotide string to codes (A=0, C=1, G=2, T=3, N=4)."""
    out = []
    for char in sequence:
        code = _ASCII_TO_CODE.get(ord(char))
        if code is None:
            raise ReferenceFormatError(f"sequence contains an invalid symbol: {char!r}")
        out.append(code)
    return out


def reverse_complement_codes(codes: Sequence[int]) -> list[int]:
    return [_COMPLEMENT[c] for c in reversed(codes)]


def _codebook8_words() -> tuple[str, ...]:
    """The 256 format-defined 8-mers: 'A' + 6 bases + 'T', exactly 4 G/C, runs <= 3."""
    from itertools import product

    words: list[str] = []
    for middle in product(BASES, repeat=6):
        word = "A" + "".join(middle) + "T"
        if sum(1 for b in word if b in "GC") != 4:
            continue
        run = best = 1
        for a, b in zip(word, word[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        if best <= 3:
            words.append(word)
        if len(words) == 256:
            return tuple(words)
    raise ReferenceFormatError("codebook8 construction failed")  # pragma: no cover - fixed definition


_CODEBOOK8 = _codebook8_words()
_CODEBOOK8_LOOKUP: dict[str, int] = {word: value for value, word in enumerate(_CODEBOOK8)}


def mapping_decode(name: str, codes: Sequence[int]) -> tuple[list[int], list[bool]]:
    """Demap one strand. Returns (bytes, erasure mask).

    A byte is flagged as an erasure when its nucleotides cannot be a valid
    encoding: an unreadable symbol, an unknown codebook word, a repeated base
    in the rotation code, or a value above 255 in the rotation code.
    """
    if name == "2bit":
        if len(codes) % 4:
            raise ReferenceFormatError("2bit sequences must be a multiple of 4 nt")
        out: list[int] = []
        erased: list[bool] = []
        for i in range(0, len(codes), 4):
            group = codes[i:i + 4]
            if any(c == INVALID for c in group):
                out.append(0)
                erased.append(True)
                continue
            out.append((group[0] << 6) | (group[1] << 4) | (group[2] << 2) | group[3])
            erased.append(False)
        return out, erased
    if name == "rotation3":
        if len(codes) % 6:
            raise ReferenceFormatError("rotation3 sequences must be a multiple of 6 nt")
        out = []
        erased = []
        previous = 0  # the base before the strand start is defined as 'A'
        digits: list[int] = []
        for code in codes:
            if code == INVALID:
                previous = 4
                digits.append(-1)
                continue
            allowed = [b for b in range(4) if b != previous]
            digits.append(allowed.index(code) if code in allowed else -1)
            previous = code
        for i in range(0, len(digits), 6):
            group = digits[i:i + 6]
            if any(d < 0 for d in group):
                out.append(0)
                erased.append(True)
                continue
            value = 0
            for d in group:
                value = value * 3 + d
            if value > 255:
                out.append(0)
                erased.append(True)
                continue
            out.append(value)
            erased.append(False)
        return out, erased
    if name == "codebook8":
        if len(codes) % 8:
            raise ReferenceFormatError("codebook8 sequences must be a multiple of 8 nt")
        out = []
        erased = []
        for i in range(0, len(codes), 8):
            group = codes[i:i + 8]
            if any(c == INVALID for c in group):
                out.append(0)
                erased.append(True)
                continue
            word = "".join(BASES[c] for c in group)
            value = _CODEBOOK8_LOOKUP.get(word)
            if value is None:
                out.append(0)
                erased.append(True)
                continue
            out.append(value)
            erased.append(False)
        return out, erased
    raise ReferenceUnsupportedError(f"unknown DNA mapping {name!r}")


# =========================================================================== frames

FRAME_FORMAT = 4
KIND_DATA = 0
KIND_META = 1
HEADER_BYTES = 9
CRC_BYTES = 4
TAG_BYTES = 3
MAX_STRIPES = 1 << 24
META_DATA_SHARDS = 8
META_PARITY_SHARDS = 8
META_MAGIC = b"VNXM"
MAX_READ_NT = 100_000


@dataclass(frozen=True)
class Geometry:
    """The frame geometry an archive declares."""

    mapping: str
    payload_bytes: int
    inner_parity_bytes: int

    @property
    def systematic_bytes(self) -> int:
        return HEADER_BYTES + self.payload_bytes + CRC_BYTES

    @property
    def frame_bytes(self) -> int:
        return self.systematic_bytes + self.inner_parity_bytes

    @property
    def strand_nt(self) -> int:
        return self.frame_bytes * NT_PER_BYTE[self.mapping]

    def to_dict(self) -> dict[str, Any]:
        return {"mapping": self.mapping, "payload_bytes": self.payload_bytes,
                "inner_parity_bytes": self.inner_parity_bytes, "frame_bytes": self.frame_bytes,
                "strand_nt": self.strand_nt}


def _keystream(variant: int, length: int) -> bytes:
    """SHAKE128("VNX-DNA/4 scrambler" || variant) -- the frame keystream."""
    return hashlib.shake_128(b"VNX-DNA/4 scrambler" + bytes([variant])).digest(length)


def _parse_plain(geometry: Geometry, system: bytes) -> tuple[int, int, int, int, bytes] | None:
    """Validate and unscramble one frame's systematic part.

    Returns ``(kind, tag, stripe, shard, payload)`` or ``None`` when the CRC or
    the frame-format field does not check out.
    """
    p = geometry.payload_bytes
    variant = system[0]
    plain = bytes(a ^ b for a, b in zip(system[1:], _keystream(variant, 12 + p)))
    body = plain[: 8 + p]
    crc = plain[8 + p:]
    if zlib.crc32(body) != int.from_bytes(crc, "big"):
        return None
    if body[0] >> 4 != FRAME_FORMAT or (body[0] & 0x0F) not in (KIND_DATA, KIND_META):
        return None
    return (body[0] & 0x0F, int.from_bytes(body[1:4], "big"), int.from_bytes(body[4:7], "big"), body[7], body[8:])


def parse_frame(geometry: Geometry, frame: bytes, erasures: list[bool]) -> tuple[tuple, int] | None:
    """Validate one demapped frame; use the inner code when the CRC fails."""
    system = geometry.systematic_bytes
    if not any(erasures):
        parsed = _parse_plain(geometry, frame[:system])
        if parsed is not None:
            return parsed, 0
    flagged = [i for i, e in enumerate(erasures) if e]
    attempts: list[list[int]] = []
    if len(flagged) <= geometry.inner_parity_bytes:
        attempts.append(flagged)
    if flagged:
        # the flags can themselves be wrong (e.g. a rotation-code artefact): also try errors-only decoding
        attempts.append([])
    for erase in attempts or [[]]:
        result = rs_decode(frame, geometry.inner_parity_bytes, erase)
        if result is None:
            continue
        corrected, count = result
        parsed = _parse_plain(geometry, corrected[:system])
        if parsed is not None:
            return parsed, max(count, 1)
    return None


# =========================================================================== reads


def parse_reads(text: str) -> list[str]:
    """Parse a read file: FASTA (single or multi-line), FASTQ, or one sequence per line."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []
    if lines[0].startswith("@"):
        if len(lines) % 4:
            raise ReferenceFormatError("FASTQ record count is not a multiple of 4 lines")
        sequences = []
        for i in range(0, len(lines), 4):
            if not lines[i].startswith("@") or not lines[i + 2].startswith("+"):
                raise ReferenceFormatError(f"malformed FASTQ record at line {i + 1}")
            sequences.append(lines[i + 1][: MAX_READ_NT + 1].upper())
        return sequences
    sequences = []
    if lines[0].startswith(">"):
        current: list[str] | None = None
        for line in lines:
            if line.startswith(">"):
                if current is not None:
                    sequences.append("".join(current)[: MAX_READ_NT + 1].upper())
                current = []
                continue
            if line.startswith(";"):
                continue
            current.append(line)  # type: ignore[union-attr]
        if current is not None:
            sequences.append("".join(current)[: MAX_READ_NT + 1].upper())
    else:
        for line in lines:
            sequences.append(line[: MAX_READ_NT + 1].upper())
    return sequences


def read_sequences(path: str | os.PathLike) -> list[str]:
    """Read a read file and return its sequences (headers are ignored)."""
    return parse_reads(Path(path).read_text(encoding="ascii", errors="replace"))


# =========================================================================== manifest

FORMAT_MAGIC = "VNX-DNA"
FORMAT_VERSION = 4
MAX_MANIFEST_BYTES = 64 * 1024 * 1024

KNOWN_FEATURES = frozenset({
    "chunked-v1", "outer-cauchy-rs-v1", "inner-rs-v1", "frame4-crc32-v1", "scrambler-shake128-v1",
    "mapping-2bit", "mapping-rotation3", "mapping-codebook8", "aes-256-gcm-hkdf-v1",
})


def canonical_bytes(value: Any) -> bytes:
    """The canonical manifest form: sorted keys, tight separators, ASCII, no floats."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


@dataclass
class Manifest:
    """The subset of the format-4 manifest the reference decoder needs."""

    raw: dict[str, Any]
    archive_id: str
    chunk_size: int
    compression: str
    compression_level: int
    encrypted: bool
    stored_size: int
    stored_sha256: str
    chunks: list[dict[str, Any]]
    content: dict[str, Any] | None
    sealed_content: str | None
    data_shards: int
    parity_shards: int
    geometry: Geometry
    seal: dict[str, Any]
    encryption: dict[str, Any]

    @property
    def archive_tag(self) -> int:
        return int(self.archive_id[:6], 16)

    @property
    def stripe_bytes(self) -> int:
        return self.data_shards * self.geometry.payload_bytes


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReferenceFormatError(message)


def validate_manifest(raw: Any) -> Manifest:
    """Validate the manifest's structure and the fields the decoder relies on."""
    _require(isinstance(raw, dict), "manifest must be a JSON object")
    _require(raw.get("format") == FORMAT_MAGIC, f"not a VNX-DNA manifest (format={raw.get('format')!r})")
    if raw.get("format_version") != FORMAT_VERSION:
        raise ReferenceUnsupportedError(
            f"unsupported format_version {raw.get('format_version')!r}; this decoder reads {FORMAT_VERSION}")
    features = raw.get("required_features", [])
    _require(isinstance(features, list), "required_features must be a list")
    unknown = [f for f in features if isinstance(f, str) and f not in KNOWN_FEATURES]
    if unknown:
        raise ReferenceUnsupportedError(f"archive requires unsupported feature(s): {unknown}")
    for key in ("archive_id", "chunk_size", "compression", "encryption", "stored_size", "stored_sha256",
                "chunks", "erasure_code", "strand", "seal", "content", "sealed_content", "dna_manifest"):
        _require(key in raw, f"manifest is missing {key!r}")
    strand = raw["strand"]
    _require(strand.get("frame_format") == FRAME_FORMAT, f"unsupported frame_format {strand.get('frame_format')!r}")
    mapping = strand.get("mapping")
    _require(mapping in NT_PER_BYTE, f"unsupported DNA mapping {mapping!r}")
    payload = strand.get("payload_bytes")
    parity = strand.get("inner_parity_bytes")
    _require(isinstance(payload, int) and not isinstance(payload, bool), "invalid payload_bytes")
    _require(isinstance(parity, int) and not isinstance(parity, bool), "invalid inner_parity_bytes")
    _require(1 <= payload <= 242, "invalid payload_bytes")
    _require(0 <= parity <= 64 and parity % 2 == 0, "invalid inner_parity_bytes")
    geometry = Geometry(mapping, payload, parity)
    _require(strand.get("frame_bytes") == geometry.frame_bytes, "manifest strand geometry is inconsistent")
    _require(strand.get("strand_nt") == geometry.strand_nt, "manifest strand geometry is inconsistent")
    _require(strand.get("inner_ecc") == "reed-solomon-gf256", f"unsupported inner ECC {strand.get('inner_ecc')!r}")
    _require(strand.get("crc") == "crc32" and strand.get("scrambler") == "shake128",
              "unsupported frame integrity mechanism")
    code = raw["erasure_code"]
    _require(code.get("algorithm") == "cauchy-rs-gf256", f"unsupported outer code {code.get('algorithm')!r}")
    data_shards = code.get("data_shards")
    parity_shards = code.get("parity_shards")
    _require(isinstance(data_shards, int) and not isinstance(data_shards, bool), "invalid erasure code parameters")
    _require(isinstance(parity_shards, int) and not isinstance(parity_shards, bool), "invalid erasure code parameters")
    _require(1 <= data_shards and 0 <= parity_shards and data_shards + parity_shards <= 256,
             "invalid erasure code parameters")
    chunks = raw["chunks"]
    _require(isinstance(chunks, list) and chunks, "manifest must have at least one chunk")
    encryption = raw["encryption"]
    encrypted = encryption.get("algorithm") != "none"
    if encrypted:
        _require(encryption.get("algorithm") == "AES-256-GCM", f"unsupported encryption {encryption.get('algorithm')!r}")
        _require(encryption.get("kdf") == "HKDF-SHA256", "unsupported key derivation function")
    content = raw.get("content")
    sealed = raw.get("sealed_content")
    if encrypted:
        _require(content is None and sealed, "encrypted archive must carry sealed content only")
    else:
        _require(content is not None and sealed is None, "unencrypted archive must carry clear content only")
    compression = raw["compression"]
    _require(compression.get("algorithm") in ("none", "zlib", "zstd"), "unsupported compression algorithm")
    level = compression.get("level")
    _require(isinstance(level, int) and not isinstance(level, bool), "invalid compression level")
    limits = {"none": (0, 0), "zlib": (0, 9), "zstd": (1, 22)}[compression["algorithm"]]
    _require(limits[0] <= level <= limits[1], "compression level out of range for algorithm")
    return Manifest(raw=raw, archive_id=raw["archive_id"], chunk_size=raw["chunk_size"],
                    compression=compression["algorithm"], compression_level=level,
                    encrypted=encrypted, stored_size=raw["stored_size"], stored_sha256=raw["stored_sha256"],
                    chunks=chunks, content=content, sealed_content=sealed, data_shards=data_shards,
                    parity_shards=parity_shards, geometry=geometry, seal=raw["seal"], encryption=encryption)


def check_manifest_semantics(manifest: Manifest) -> None:
    """The cross-field consistency checks the production manifest schema enforces."""
    stripe_bytes = manifest.stripe_bytes
    next_stripe = 0
    total = 0
    for index, record in enumerate(manifest.chunks):
        _require(record.get("index") == index, f"chunk {index} has index {record.get('index')}")
        _require(record.get("first_stripe") == next_stripe, f"chunk {index} first_stripe is not the next stripe")
        stored_size = record.get("stored_size")
        _require(isinstance(stored_size, int) and stored_size >= 0, f"chunk {index} has an invalid stored_size")
        expected_stripes = -(-stored_size // stripe_bytes) if stripe_bytes else 0
        _require(record.get("stripe_count") == expected_stripes, f"chunk {index} stripe_count does not match stored_size")
        next_stripe += expected_stripes
        total += stored_size
    _require(next_stripe == manifest.raw["erasure_code"].get("stripe_count"),
             "stripe_count does not equal the sum of chunk stripes")
    _require(total == manifest.stored_size, "stored_size does not equal the sum of chunk stored sizes")
    if manifest.content is not None:
        content = manifest.content
        chunks = content.get("chunks")
        _require(isinstance(chunks, list) and len(chunks) == len(manifest.chunks),
                 "content chunk count differs from chunk records")
        offset = 0
        size = content.get("size")
        _require(isinstance(size, int) and size >= 0, "invalid content size")
        for index, chunk in enumerate(chunks):
            expected = min(manifest.chunk_size, size - offset) if size else 0
            _require(chunk.get("index") == index and chunk.get("offset") == offset and chunk.get("size") == expected,
                     f"content chunk {index} offset/size mismatch")
            offset += chunk.get("size", 0)
        _require(offset == size, "content chunks do not cover the content size")


def check_manifest_digest(raw: dict[str, Any]) -> None:
    """The manifest's own SHA-256 seal, over the canonical bytes without ``seal``."""
    payload = canonical_bytes({k: v for k, v in raw.items() if k != "seal"})
    if hashlib.sha256(payload).hexdigest() != raw["seal"]["manifest_sha256"]:
        raise ReferenceIntegrityError("manifest digest mismatch: the manifest is corrupted or was modified")


# =========================================================================== crypto


def hkdf_sha256(master: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 (RFC 5869) -- extract, then expand."""
    prk = hmac.new(salt, master, hashlib.sha256).digest()
    out = b""
    block = b""
    counter = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def derive_keys(master: bytes, salt: bytes) -> tuple[bytes, bytes, bytes]:
    """The three archive subkeys: AEAD key, manifest MAC key, key check."""
    return (hkdf_sha256(master, salt, b"VNX-DNA/4 aead key"),
            hkdf_sha256(master, salt, b"VNX-DNA/4 manifest mac key"),
            hkdf_sha256(master, salt, b"VNX-DNA/4 key check", 8))


def _aead_nonce(domain: int, index: int) -> bytes:
    return domain.to_bytes(4, "big") + index.to_bytes(8, "big")


def _aead_ad(archive_id: bytes, domain: int, index: int, count: int) -> bytes:
    return (b"VNX-DNA/4 aead" + archive_id + domain.to_bytes(4, "big")
            + index.to_bytes(8, "big") + count.to_bytes(8, "big"))


# ---- AES-256 and GCM, implemented here so that no third-party package is needed


def _aes_sbox() -> bytes:
    """The AES S-box, derived from the field inverse and the affine transform.

    The AES field is GF(2^8) modulo 0x11B with generator 3; the S-box is
    ``s(a) = affine(a^-1)`` with ``affine(b) = b ^ rotl(b,1) ^ ... ^ rotl(b,4) ^ 0x63``.
    """
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        # multiply by the generator 3: x ^ xtime(x)
        doubled = (x << 1) & 0xFF
        if x & 0x80:
            doubled ^= 0x1B
        x = x ^ doubled
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    sbox = bytearray(256)
    for i in range(256):
        inverse = 0 if i == 0 else exp[255 - log[i]]
        rotated = inverse
        out = inverse
        for _ in range(4):
            rotated = ((rotated << 1) | (rotated >> 7)) & 0xFF
            out ^= rotated
        sbox[i] = out ^ 0x63
    return bytes(sbox)


_SBOX = _aes_sbox()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36, 0x6C, 0xD8]


def _xtime(value: int) -> int:
    value <<= 1
    return (value ^ 0x1B) & 0xFF if value & 0x100 else value


def _aes256_expand_key(key: bytes) -> list[list[int]]:
    """The AES-256 key schedule: 15 round keys of 16 bytes."""
    if len(key) != 32:
        raise ReferenceFormatError("AES-256 needs a 32-byte key")
    words = [list(key[4 * i:4 * i + 4]) for i in range(8)]
    for i in range(8, 60):
        temp = list(words[i - 1])
        if i % 8 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // 8 - 1]
        elif i % 8 == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([words[i - 8][j] ^ temp[j] for j in range(4)])
    return [sum(words[4 * r:4 * r + 4], []) for r in range(15)]


def _aes256_encrypt_block(round_keys: list[list[int]], block: bytes) -> bytes:
    """Encrypt one 16-byte block (the state is column-major: state[r][c] = block[r + 4c])."""
    state = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
    for r in range(4):
        for c in range(4):
            state[r][c] ^= round_keys[0][4 * c + r]
    for rnd in range(1, 15):
        for r in range(4):
            for c in range(4):
                state[r][c] = _SBOX[state[r][c]]
        for r in range(1, 4):
            state[r] = state[r][r:] + state[r][:r]
        if rnd != 14:
            for c in range(4):
                column = [state[r][c] for r in range(4)]
                total = column[0] ^ column[1] ^ column[2] ^ column[3]
                for r in range(4):
                    state[r][c] = column[r] ^ total ^ _xtime(column[r] ^ column[(r + 1) % 4])
        for r in range(4):
            for c in range(4):
                state[r][c] ^= round_keys[rnd][4 * c + r]
    return bytes(state[r][c] for c in range(4) for r in range(4))


def _ghash_mul(x: int, y: int) -> int:
    """The GHASH field product in GF(2^128) (bit-reflected convention)."""
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        v = (v >> 1) ^ (0xE1 << 120) if v & 1 else v >> 1
    return z


def _ghash(h: int, data: bytes) -> int:
    y = 0
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16].ljust(16, b"\x00")
        y = _ghash_mul(y ^ int.from_bytes(block, "big"), h)
    return y


def aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    """AES-256-GCM decryption with a 96-bit nonce (NIST SP 800-38D)."""
    if len(key) != 32:
        raise ReferenceFormatError("AES-256-GCM needs a 32-byte key")
    if len(nonce) != 12:
        raise ReferenceFormatError("AES-256-GCM needs a 96-bit nonce")
    if len(ciphertext) < 16:
        raise ReferenceIntegrityError("ciphertext is shorter than the authentication tag")
    round_keys = _aes256_expand_key(key)
    h = int.from_bytes(_aes256_encrypt_block(round_keys, b"\x00" * 16), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    tag_mask = int.from_bytes(_aes256_encrypt_block(round_keys, j0), "big")
    counter = int.from_bytes(j0, "big")
    plaintext = bytearray()
    body = ciphertext[:-16]
    for offset in range(0, len(body), 16):
        counter = (counter & ~0xFFFFFFFF) | ((counter + 1) & 0xFFFFFFFF)
        stream = _aes256_encrypt_block(round_keys, counter.to_bytes(16, "big"))
        block = body[offset:offset + 16]
        plaintext.extend(a ^ b for a, b in zip(block, stream))
    padded_aad = aad + b"\x00" * (-len(aad) % 16)
    padded_body = body + b"\x00" * (-len(body) % 16)
    length_block = (len(aad) * 8).to_bytes(8, "big") + (len(body) * 8).to_bytes(8, "big")
    s = _ghash(h, padded_aad + padded_body + length_block)
    expected = tag_mask ^ s
    if not hmac.compare_digest(expected.to_bytes(16, "big"), ciphertext[-16:]):
        raise ReferenceIntegrityError("AES-GCM authentication failed")
    return bytes(plaintext)


# =========================================================================== compression


def _decompress(data: bytes, algorithm: str, expected_size: int) -> bytes:
    """Decompress, refusing to produce more than the recorded plaintext size."""
    if algorithm == "none":
        out = data
    elif algorithm == "zlib":
        try:
            engine = zlib.decompressobj()
            out = engine.decompress(data, expected_size + 1)
            if engine.unconsumed_tail or not engine.eof:
                raise ReferenceIntegrityError("zlib stream is truncated or longer than the recorded size")
        except zlib.error as error:
            raise ReferenceIntegrityError(f"zlib stream is corrupt: {error}") from error
    elif algorithm == "zstd":
        try:
            from compression.zstd import ZstdFile  # type: ignore[import-not-found]
        except ImportError as error:
            raise ReferenceUnsupportedError(
                "zstd archives need a zstd decoder; the standard library provides one from Python 3.14 "
                "('compression.zstd'). Use the production decoder for zstd archives on older interpreters.") from error
        try:
            with ZstdFile(io.BytesIO(data)) as handle:
                out = handle.read(expected_size + 1)
        except Exception as error:  # noqa: BLE001 - any decompression failure is the same refusal
            raise ReferenceIntegrityError(f"zstd stream is corrupt: {error}") from error
    else:
        raise ReferenceUnsupportedError(f"unknown compression algorithm {algorithm!r}")
    if len(out) != expected_size:
        raise ReferenceIntegrityError(f"decompressed size {len(out)} does not match recorded size {expected_size}")
    return out


# =========================================================================== scanning


@dataclass
class Scan:
    """The result of scanning a read set."""

    geometry: Geometry
    candidates: dict[tuple[int, int, int, int], Counter] = field(default_factory=dict)
    stats: Counter = field(default_factory=Counter)


def _resolve(counter: Counter) -> bytes | None:
    """The strict-majority payload, or None when the copies disagree in a tie.

    A tie is an erasure, never a guess: the outer code supplies the shard.
    """
    if not counter:
        return None
    ranked = counter.most_common(2)
    if len(ranked) == 1:
        return ranked[0][0]
    if ranked[0][1] > ranked[1][1]:
        return ranked[0][0]
    return None


def scan_reads(sequences: Sequence[str], geometry: Geometry) -> Scan:
    """Validate every read and collect candidate payloads keyed by (tag, kind, stripe, shard)."""
    scan = Scan(geometry=geometry)
    stats = scan.stats
    stats["reads_total"] = len(sequences)
    length = geometry.strand_nt
    for sequence in sequences:
        if len(sequence) != length:
            stats["reads_length_mismatch"] += 1
            continue
        try:
            codes = to_codes(sequence)
        except ReferenceFormatError:
            stats["reads_invalid_symbols"] += 1
            continue
        frame, erasures = mapping_decode(geometry.mapping, codes)
        parsed = parse_frame(geometry, bytes(frame), erasures)
        if parsed is None:
            # try the reverse-complement orientation
            rc_frame, rc_erasures = mapping_decode(geometry.mapping, reverse_complement_codes(codes))
            parsed = parse_frame(geometry, bytes(rc_frame), rc_erasures)
            if parsed is None:
                stats["reads_rejected"] += 1
                continue
            stats["reads_reverse_complement"] += 1
        else:
            stats["reads_valid"] += 1
        if parsed[1]:
            stats["reads_inner_corrected"] += 1
            stats["inner_symbols_corrected"] += parsed[1]
        kind, tag, stripe, shard, payload = parsed[0]
        scan.candidates.setdefault((tag, kind, stripe, shard), Counter())[payload] += 1
    return scan


def discover_geometry(sequences: Sequence[str], sample: int = 400) -> Geometry:
    """Infer the mapping, payload size and inner parity from the DNA alone.

    For the most common read lengths, every mapping and every even inner
    parity size is tried.  A hypothesis scores only when clean reads pass the
    frame CRC and the frame-format check.
    """
    votes: Counter = Counter()
    by_length: Counter = Counter(len(s) for s in sequences)
    for length, _ in by_length.most_common(4):
        reads = [s for s in sequences if len(s) == length][:sample]
        for name in ("2bit", "rotation3", "codebook8"):
            nt_per_byte = NT_PER_BYTE[name]
            if length % nt_per_byte:
                continue
            frame_len = length // nt_per_byte
            clean: list[bytes] = []
            for read in reads:
                try:
                    codes = to_codes(read)
                except ReferenceFormatError:
                    continue
                frame, erasures = mapping_decode(name, codes)
                if not any(erasures):
                    clean.append(bytes(frame))
            for nsym in range(0, min(64, frame_len - 14) + 1, 2):
                payload = frame_len - 13 - nsym
                if payload < 1:
                    continue
                geometry = Geometry(name, payload, nsym)
                hits = 0
                for frame in clean[:64]:
                    if _parse_plain(geometry, frame[: geometry.systematic_bytes]):
                        hits += 1
                if hits:
                    votes[(name, payload, nsym)] += hits
        if votes:
            break
    if not votes:
        raise ReferenceFormatError("no VNX-DNA format-4 strands could be identified in the reads")
    (name, payload, nsym), _ = votes.most_common(1)[0]
    return Geometry(name, payload, nsym)


# =========================================================================== recovery


def _used_data_shards(stored_size: int, stripe_index_in_chunk: int, stripe_count: int,
                       data_shards: int, payload: int) -> int:
    """How many data shards of a chunk stripe carry stored bytes.

    The remaining data shards of the last stripe lie entirely in the zero
    padding and are not emitted (the shortened-code construction).
    """
    if stripe_index_in_chunk < stripe_count - 1:
        return data_shards
    tail = stored_size - (stripe_count - 1) * data_shards * payload
    return -(-tail // payload)


def _meta_stripe(scan: Scan, tag: int, code: CauchyCode, payload: int, stripe: int) -> bytes:
    """Decode one metadata stripe (its K data shards) into bytes."""
    shards: list[bytes | None] = []
    for shard in range(code.total):
        shards.append(_resolve(scan.candidates.get((tag, KIND_META, stripe, shard), Counter())))
    return b"".join(code.decode(shards))


def recover_manifest_from_dna(scan: Scan) -> bytes:
    """Reassemble the canonical manifest from the metadata strands (kind 1)."""
    meta_tags = sorted({key[0] for key in scan.candidates if key[1] == KIND_META})
    if not meta_tags:
        raise ReferenceRedundancyError("no metadata strands survived; the manifest cannot be recovered from DNA")
    if len(meta_tags) > 1:
        raise ReferenceFormatError(f"reads contain metadata for several archives (tags "
                                   f"{[f'{t:06x}' for t in meta_tags]}); separate the pools first")
    tag = meta_tags[0]
    code = CauchyCode(META_DATA_SHARDS, META_PARITY_SHARDS)
    p = scan.geometry.payload_bytes
    head = _meta_stripe(scan, tag, code, p, 0)
    if head[:4] != META_MAGIC:
        raise ReferenceFormatError("DNA manifest copy has an invalid header")
    length = int.from_bytes(head[4:8], "big")
    if length > MAX_MANIFEST_BYTES:
        raise ReferenceFormatError("DNA manifest copy declares an implausible length")
    total = -(-(8 + length) // (code.k * p))
    stream = head + b"".join(_meta_stripe(scan, tag, code, p, s) for s in range(1, total))
    return stream[8:8 + length]


def _rebuild_stored_chunks(scan: Scan, manifest: Manifest) -> list[bytes]:
    """Rebuild every stored chunk from the scanned reads through the outer code."""
    code = CauchyCode(manifest.data_shards, manifest.parity_shards)
    p = manifest.geometry.payload_bytes
    out: list[bytes] = []
    for record in manifest.chunks:
        if record["stripe_count"] == 0:
            out.append(b"")
            continue
        data = bytearray()
        for row in range(record["stripe_count"]):
            stripe = record["first_stripe"] + row
            used = _used_data_shards(record["stored_size"], row, record["stripe_count"], code.k, p)
            shards: list[bytes | None] = []
            for shard in range(code.total):
                if code.k > shard >= used:
                    shards.append(b"\x00" * p)   # shortened: an implicit zero shard
                    continue
                shards.append(_resolve(scan.candidates.get((manifest.archive_tag, KIND_DATA, stripe, shard), Counter())))
            data += b"".join(code.decode(shards))
        out.append(bytes(data[: record["stored_size"]]))
    return out


def _open_content(manifest: Manifest, key: bytes | None) -> tuple[dict[str, Any] | None, bytes | None]:
    """Authenticate the manifest and open the content description.

    Returns ``(content, aead_key)``.  ``content`` is None for an encrypted
    archive when no key was supplied: the container can still be recovered,
    but not the plaintext.
    """
    check_manifest_digest(manifest.raw)
    if not manifest.encrypted:
        return manifest.content, None
    if key is None:
        return None, None
    if len(key) != 32:
        raise ReferenceKeyError("the key must be 32 bytes")
    salt = bytes.fromhex(manifest.encryption["salt"] or "")
    aead_key, mac_key, check = derive_keys(key, salt)
    if not hmac.compare_digest(manifest.encryption.get("key_check") or "", check.hex()):
        raise ReferenceKeyError("wrong key: the key-check value does not match this archive")
    payload = canonical_bytes({k: v for k, v in manifest.raw.items() if k != "seal"})
    if not hmac.compare_digest(manifest.seal.get("manifest_hmac_sha256") or "",
                               hmac.new(mac_key, payload, hashlib.sha256).hexdigest()):
        raise ReferenceIntegrityError("manifest authentication failed: the manifest was tampered with")
    sealed = base64.b64decode(manifest.sealed_content or "", validate=True)
    plain = aes_gcm_decrypt(aead_key, _aead_nonce(1, 0), sealed,
                            _aead_ad(bytes.fromhex(manifest.archive_id), 1, 0, 1))
    return json.loads(plain.decode("utf-8")), aead_key


def _read_plaintext(manifest: Manifest, stored_chunks: list[bytes], content: dict[str, Any],
                    aead_key: bytes | None) -> bytes:
    """Decrypt, decompress and verify every chunk; return the original bytes."""
    parts = []
    archive_id = bytes.fromhex(manifest.archive_id)
    for index, record in enumerate(manifest.chunks):
        stored = stored_chunks[index]
        if len(stored) != record["stored_size"] or hashlib.sha256(stored).hexdigest() != record["stored_sha256"]:
            raise ReferenceIntegrityError(f"chunk {index}: stored bytes do not match the manifest SHA-256")
        if aead_key is not None:
            stored = aes_gcm_decrypt(aead_key, _aead_nonce(0, index), stored,
                                     _aead_ad(archive_id, 0, index, len(manifest.chunks)))
        info = content["chunks"][index]
        plain = _decompress(stored, manifest.compression, info["size"])
        if hashlib.sha256(plain).hexdigest() != info["sha256"]:
            raise ReferenceIntegrityError(f"chunk {index}: plaintext SHA-256 mismatch")
        parts.append(plain)
    data = b"".join(parts)
    if len(data) != content["size"] or hashlib.sha256(data).hexdigest() != content["sha256"]:
        raise ReferenceIntegrityError("whole-object SHA-256 mismatch after reconstruction")
    return data


# =========================================================================== public API


def decode_reads(sequences: Sequence[str], *, key: bytes | None = None,
                 geometry: Geometry | None = None) -> dict[str, Any]:
    """Decode a set of reads to the original bytes.

    Returns a report dict with the recovered bytes under ``"data"`` and the
    recomputed digests.  Raises a :class:`ReferenceDecodeError` subclass on any
    failure; it never returns bytes whose SHA-256 does not match.
    """
    if not sequences:
        raise ReferenceFormatError("the read set is empty")
    if geometry is None:
        geometry = discover_geometry(sequences)
    scan = scan_reads(sequences, geometry)
    manifest_bytes = recover_manifest_from_dna(scan)
    raw = json.loads(manifest_bytes.decode("utf-8"))
    if canonical_bytes(raw) != manifest_bytes:
        raise ReferenceFormatError("the DNA manifest copy is not in canonical form")
    manifest = validate_manifest(raw)
    check_manifest_semantics(manifest)
    if manifest.geometry != geometry:
        scan = scan_reads(sequences, manifest.geometry)
    stored_chunks = _rebuild_stored_chunks(scan, manifest)
    content, aead_key = _open_content(manifest, key)
    if content is None:
        return {"status": "CONTAINER_ONLY", "data": None, "manifest": manifest.raw,
                "geometry": manifest.geometry.to_dict(), "stats": dict(scan.stats),
                "note": "the archive is encrypted and no key was supplied: the container was recovered, not the plaintext"}
    data = _read_plaintext(manifest, stored_chunks, content, aead_key)
    return {"status": "SUCCESS", "data": data, "manifest": manifest.raw,
            "geometry": manifest.geometry.to_dict(), "stats": dict(scan.stats),
            "sha256": hashlib.sha256(data).hexdigest()}


def decode_container(blob: bytes, *, key: bytes | None = None) -> dict[str, Any]:
    """Decode a ``.vxdna`` container file to the original bytes."""
    magic = b"\x89VXDNA\r\n"
    if len(blob) < 24 + 32 or blob[:8] != magic:
        raise ReferenceFormatError("not a VNX-DNA container (bad magic or too short)")
    version = int.from_bytes(blob[8:10], "big")
    if version != 1:
        raise ReferenceUnsupportedError(f"unsupported container file version {version}; this decoder reads version 1")
    if int.from_bytes(blob[10:12], "big") != 0:
        raise ReferenceUnsupportedError("container uses reserved flags this decoder does not understand")
    m_len = int.from_bytes(blob[12:16], "big")
    b_len = int.from_bytes(blob[16:24], "big")
    if len(blob) != 24 + m_len + b_len + 32:
        raise ReferenceFormatError("container is truncated or has trailing data")
    if hashlib.sha256(blob[:-32]).digest() != blob[-32:]:
        raise ReferenceIntegrityError("container file checksum mismatch: the file is corrupted")
    manifest_bytes = blob[24:24 + m_len]
    body = blob[24 + m_len:24 + m_len + b_len]
    raw = json.loads(manifest_bytes.decode("utf-8"))
    if canonical_bytes(raw) != manifest_bytes:
        raise ReferenceFormatError("the manifest is not in canonical form")
    manifest = validate_manifest(raw)
    check_manifest_semantics(manifest)
    content, aead_key = _open_content(manifest, key)
    if content is None:
        return {"status": "CONTAINER_ONLY", "data": None, "manifest": manifest.raw,
                "geometry": manifest.geometry.to_dict(), "stats": {},
                "note": "the archive is encrypted and no key was supplied"}
    chunks = []
    offset = 0
    for record in manifest.chunks:
        chunks.append(body[offset:offset + record["stored_size"]])
        offset += record["stored_size"]
    data = _read_plaintext(manifest, chunks, content, aead_key)
    return {"status": "SUCCESS", "data": data, "manifest": manifest.raw,
            "geometry": manifest.geometry.to_dict(), "stats": {},
            "sha256": hashlib.sha256(data).hexdigest()}


def restore_reads(path: str | os.PathLike, *, key: bytes | None = None) -> dict[str, Any]:
    """Decode a read file to the original bytes."""
    return decode_reads(read_sequences(path), key=key)


def restore_container(path: str | os.PathLike, *, key: bytes | None = None) -> dict[str, Any]:
    """Decode a ``.vxdna`` container file to the original bytes."""
    return decode_container(Path(path).read_bytes(), key=key)
