"""Read-only compatibility decoders for V0.1 archives.

Supported legacy layouts (all produced by the ``v0.1-baseline`` code):

* **V0.1 dataset**: directory with ``manifest.json`` (``format="VNX-DNA"``,
  ``format_version`` 1, 2 or 3) and ``strands.fasta`` whose FASTA headers carry
  the addresses (``VNX1|…`` or ``VNX2|…``).
* **RD-1 archive**: single JSON file whose ``manifest.format == "VNXDNA"``
  and ``format_version == "0.1"``.

Legacy archives are never encoded anymore, and they are never interpreted as
format 4 (or the other way round): :func:`detect` dispatches on the explicit
magic and version values only.

Deliberate behavioural differences from the V0.1 decoder. All are strictly
safer, and none changes a successful V0.1 result:

* every duplicate observation is validated, so a corrupt first copy no longer
  hides a valid later copy;
* for format 2/3 (the original, **non-MDS** Vandermonde generator), recovery
  searches for *any* invertible K-subset of the surviving shards instead of
  only the first K. Patterns that are singular for every subset remain
  unrecoverable, which is a property of the V0.1 code that cannot be fixed on
  read;
* malformed metadata raises structured :class:`MetadataError`.
"""
from __future__ import annotations

import base64
import hashlib
import io
import itertools
import json
import re
import zlib
from pathlib import Path
from typing import Any, Iterator

import zstandard

from ..ecc import gf256
from ..errors import (AuthenticationError, InsufficientRedundancyError, IntegrityError, InvalidDNAError, InvalidInputError,
                     KeyRequiredError, MetadataError, UnsupportedFormatError)

V01_DATASET = "v0.1-dataset"
RD1_ARCHIVE = "rd1-archive"
MAX_LEGACY_DECOMPRESSED = 1 << 34
#: the manifest is untrusted input: every count it declares is capped before it can size an allocation
MAX_LEGACY_STRANDS = 1 << 22
MAX_LEGACY_STRIPES = 1 << 16
MAX_LEGACY_MANIFEST_BYTES = 1 << 20
MAX_LEGACY_ARCHIVE_BYTES = 1 << 30
MAX_LEGACY_LINE_CHARS = 1 << 20
#: the strands file is read line by line, but every record is kept: its size is bounded too
MAX_LEGACY_STRANDS_BYTES = 1 << 30
#: the block size used to read the strands file
_LEGACY_BLOCK = 1 << 20
#: the line breaks ``str.splitlines`` knows, once the universal newline translation has turned ``\r`` and ``\r\n``
#: into ``\n`` (``\x85`` and the unicode ones are not ASCII, so the file cannot hold them)
_LEGACY_BREAK = re.compile(r"[\n\v\f\x1c\x1d\x1e]")


def detect(path: str | Path) -> str | None:
    """Return ``'v0.1-dataset'``, ``'rd1-archive'`` or None (not a legacy archive)."""
    p = Path(path)
    try:
        if p.is_dir() and (p / "manifest.json").is_file():
            if (p / "manifest.json").stat().st_size > MAX_LEGACY_MANIFEST_BYTES:
                return None
            raw = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("format") == "VNX-DNA" and raw.get("format_version") in (1, 2, 3) \
                    and not isinstance(raw.get("format_version"), bool):
                return V01_DATASET
        elif p.is_file() and p.stat().st_size < MAX_LEGACY_ARCHIVE_BYTES:
            with p.open("rb") as handle:
                start = handle.read(64).lstrip()
            if start.startswith(b"{"):
                raw = json.loads(p.read_text(encoding="utf-8"))
                m = raw.get("manifest") if isinstance(raw, dict) else None
                if isinstance(m, dict) and m.get("format") == "VNXDNA" and m.get("format_version") == "0.1":
                    return RD1_ARCHIVE
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return None


def _read_bounded(path: Path, limit: int, what: str) -> bytes:
    """Read a file, refusing one larger than ``limit``: the file is untrusted input, never read whole."""
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as error:
        raise MetadataError(f"{what} unreadable: {error}") from error
    if len(data) > limit:
        raise MetadataError(f"{what} is larger than {limit} bytes")
    return data


def _read_json(path: Path, limit: int, what: str) -> Any:
    """Read a JSON document of at most ``limit`` bytes (never read whole)."""
    data = _read_bounded(path, limit, what)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MetadataError(f"{what} unreadable: {error}") from error


def _missing_indices(total: int, present: dict) -> tuple[int, list[int]]:
    """Count the absent indices of ``range(total)`` and list the first 50 (the list itself stays bounded)."""
    count = 0
    head: list[int] = []
    for i in range(total):
        if i not in present:
            count += 1
            if len(head) < 50:
                head.append(i)
    return count, head


# ------------------------------------------------------------------ shared helpers
def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MetadataError("legacy manifest: " + message)


def _fernet_decrypt(data: bytes, key: bytes | None) -> bytes:
    if key is None:
        raise KeyRequiredError("legacy archive is Fernet-encrypted; supply the key")
    from cryptography.fernet import Fernet, InvalidToken
    try:
        return Fernet(base64.urlsafe_b64encode(key)).decrypt(data)
    except InvalidToken as error:
        raise AuthenticationError("legacy Fernet authentication failed (wrong key or tampered ciphertext)") from error


def _decompress(data: bytes, algorithm: str, expected_size: int) -> bytes:
    """Decompress to exactly the manifest's declared plaintext size (V11-RT-04). ``decompress(max_output_size=...)`` is
    no bound: zstandard allocates the size a frame header declares. The stream readers never produce more than
    ``expected_size + 1`` bytes."""
    if not isinstance(expected_size, int) or not 0 <= expected_size <= MAX_LEGACY_DECOMPRESSED:
        raise MetadataError("legacy manifest: declared size out of range")
    try:
        if algorithm == "none":
            return data              # the callers compare size and SHA-256 and report the details
        if algorithm == "zlib":
            engine = zlib.decompressobj()
            out = engine.decompress(data, expected_size + 1)
            if not engine.eof or engine.unconsumed_tail:
                raise IntegrityError("legacy zlib stream truncated or longer than its declared size")
        elif algorithm == "zstandard":
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=False) as reader:
                out = reader.read(expected_size + 1)
        else:
            raise MetadataError(f"legacy manifest: unknown compression {algorithm!r}")
    except (zlib.error, zstandard.ZstdError) as error:
        raise IntegrityError(f"legacy {algorithm} stream corrupt: {error}") from error
    if len(out) != expected_size:
        raise IntegrityError(f"legacy {algorithm} stream: {len(out)} bytes, the manifest declares {expected_size}")
    return out


def _two_bit_decode(sequence: str) -> bytes:
    table = {"A": 0, "C": 1, "G": 2, "T": 3}
    if len(sequence) % 4:
        raise InvalidDNAError("baseline sequence length not divisible by 4")
    try:
        return bytes((table[sequence[i]] << 6) | (table[sequence[i + 1]] << 4) | (table[sequence[i + 2]] << 2) | table[sequence[i + 3]]
                     for i in range(0, len(sequence), 4))
    except KeyError as error:
        raise InvalidDNAError(f"invalid base {error.args[0]!r}") from None


def _v01_codebook(min_gc: float, max_gc: float, max_run: int | None, motifs: tuple[str, ...]) -> dict[str, int]:
    """Exact reproduction of V0.1 ``encoding.constrained._codebook`` (settings-dependent)."""
    if not min_gc <= 50 <= max_gc:
        raise MetadataError("legacy constrained_v1 requires GC bounds containing 50%")
    limit = max_run if max_run is not None else 8
    words = []
    for middle in itertools.product("ACGT", repeat=6):
        word = "A" + "".join(middle) + "T"
        if word.count("G") + word.count("C") != 4 or any(m.upper() in word for m in motifs):
            continue
        run = best = 1
        for a, b in zip(word, word[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        if best <= limit:
            words.append(word)
    if len(words) < 256:
        raise MetadataError("legacy constrained_v1 constraints cannot provide 256 codewords")
    return {w: i for i, w in enumerate(words[:256])}


# ------------------------------------------------------------------ V0.1 dataset
class _LegacyVandermonde:
    """V0.1 generator [I; (p+1)^c]. NOT MDS: kept only to read old parity."""

    def __init__(self, k: int, m: int):
        self.k, self.m = k, m
        self.rows = [[int(r == c) for c in range(k)] for r in range(k)] + [[gf256.power(p + 1, c) for c in range(k)] for p in range(m)]

    def decode(self, shards: list[bytes | None]) -> list[bytes]:
        available = [i for i, s in enumerate(shards) if s is not None]
        if len(available) < self.k:
            raise InsufficientRedundancyError(f"legacy stripe needs {self.k} shards, {len(available)} valid")
        if all(shards[i] is not None for i in range(self.k)):
            return [shards[i] for i in range(self.k)]  # type: ignore[misc]
        tried = 0
        for subset in itertools.combinations(available, self.k):
            tried += 1
            if tried > 20_000:
                break
            try:
                inverse = gf256.matrix_invert([self.rows[i] for i in subset])
            except ValueError:
                continue
            length = len(shards[subset[0]])  # type: ignore[arg-type]
            out = []
            for row in inverse:
                acc = bytearray(length)
                for coefficient, index in zip(row, subset):
                    if coefficient:
                        for pos, value in enumerate(shards[index]):  # type: ignore[arg-type]
                            acc[pos] ^= gf256.mul(coefficient, value)
                out.append(bytes(acc))
            return out
        raise InsufficientRedundancyError("legacy stripe unrecoverable: every surviving K-subset is singular under the V0.1 (non-MDS) generator")


def _iter_lines(handle, limit: int, total: int) -> Iterator[str]:
    """Yield the lines of a text handle, in bounded memory.

    The breaks are the ones ``str.splitlines`` knows (the file is read with the universal newline translation, so
    only ``\\n``, ``\\v``, ``\\f`` and ``\\x1c``..``\\x1e`` can end a line). A line longer than ``limit`` characters
    is refused instead of being held whole, and the file may not hold more than ``total`` characters.
    """
    buffer = ""
    read = 0
    while True:
        block = handle.read(_LEGACY_BLOCK)
        if not block:
            break
        read += len(block)
        if read > total:
            raise MetadataError(f"legacy strands: the file is larger than {total} characters")
        parts = _LEGACY_BREAK.split(buffer + block)
        buffer = parts.pop()
        if len(buffer) > limit:
            raise MetadataError(f"legacy strands: a line is longer than {limit} characters")
        for part in parts:
            if len(part) > limit:
                raise MetadataError(f"legacy strands: a line is longer than {limit} characters")
            yield part
    if buffer:
        if len(buffer) > limit:
            raise MetadataError(f"legacy strands: a line is longer than {limit} characters")
        yield buffer


def _read_v01_fasta(path: Path) -> list[tuple[list[str], str]]:
    """Read the legacy strands file one line at a time: it is never held in memory as a whole."""
    records: list[tuple[list[str], str]] = []
    header: list[str] | None = None
    seq: list[str] = []
    try:
        with path.open("r", encoding="ascii", newline=None) as handle:
            for line in _iter_lines(handle, MAX_LEGACY_LINE_CHARS, MAX_LEGACY_STRANDS_BYTES):
                if not line:
                    continue
                if line.startswith(">"):
                    if header is not None:
                        records.append((header, "".join(seq).upper()))
                        _require(len(records) <= MAX_LEGACY_STRANDS, f"more than {MAX_LEGACY_STRANDS} strands")
                    header, seq = line[1:].split("|"), []
                elif header is None:
                    raise MetadataError("legacy FASTA sequence before header")
                else:
                    seq.append(line.strip())
    except (OSError, UnicodeDecodeError) as error:
        raise InvalidInputError(f"cannot read legacy strands: {error}") from error
    if header is not None:
        records.append((header, "".join(seq).upper()))
    return records


def decode_v01_dataset(directory: str | Path, key: bytes | None = None) -> tuple[bytes, dict[str, Any]]:
    root = Path(directory)
    m = _read_json(root / "manifest.json", MAX_LEGACY_MANIFEST_BYTES, "legacy manifest")
    _require(isinstance(m, dict), "not an object")
    version = m.get("format_version")
    if m.get("format") != "VNX-DNA" or version not in (1, 2, 3) or isinstance(version, bool):
        raise UnsupportedFormatError("not a V0.1 dataset")
    for name, kind in (("dataset_id", str), ("original_sha256", str), ("original_size", int), ("transformed_size", int),
                       ("strand_count", int), ("config", dict)):
        _require(isinstance(m.get(name), kind) and not isinstance(m.get(name), bool), f"field {name!r} missing or not {kind.__name__}")
    _require(0 <= m["strand_count"] <= MAX_LEGACY_STRANDS,
             f"strand_count {m['strand_count']} exceeds the limit of {MAX_LEGACY_STRANDS}")
    cfg = m["config"]
    for name, kind in (("compression", str), ("encryption", bool), ("ecc", str), ("encoding", str), ("constraints", dict)):
        _require(isinstance(cfg.get(name), kind), f"config.{name} missing or not {kind.__name__}")
    encoding = cfg["encoding"]
    _require(encoding in ("baseline_binary_2bit", "constrained_v1"), f"unknown encoding {encoding!r}")
    if encoding == "constrained_v1":
        c = cfg["constraints"]
        try:
            book = _v01_codebook(float(c["min_gc"]), float(c["max_gc"]), c.get("max_homopolymer_length"), tuple(c.get("forbidden_motifs", ())))
        except (KeyError, TypeError, ValueError) as error:
            raise MetadataError(f"legacy constraints malformed: {error}") from None

        def decode_seq(s: str) -> bytes:
            if len(s) % 8:
                raise InvalidDNAError("constrained_v1 length not divisible by 8")
            try:
                return bytes(book[s[i:i + 8]] for i in range(0, len(s), 8))
            except KeyError:
                raise InvalidDNAError("unknown constrained_v1 codeword") from None
    else:
        decode_seq = _two_bit_decode
    records = _read_v01_fasta(root / m.get("strand_file", "strands.fasta") if isinstance(m.get("strand_file"), str) and "/" not in m["strand_file"] else root / "strands.fasta")
    dataset_id = m["dataset_id"]
    stats = {"legacy_format": V01_DATASET, "format_version": version, "records": len(records), "invalid_copies": 0, "recovered_shards": 0}

    def valid_payload(sequence: str, length: str, checksum: str) -> bytes | None:
        try:
            payload = decode_seq(sequence)
        except InvalidDNAError:
            return None
        if str(len(payload)) != length or hashlib.sha256(payload).hexdigest() != checksum:
            return None
        return payload

    if cfg["ecc"] == "none":
        _require(version in (1, 3), "ecc=none requires format_version 1 or 3")
        total = m["strand_count"]
        chosen: dict[int, bytes] = {}
        for h, seq in records:
            if len(h) != 6 or h[0] != "VNX1" or h[1] != dataset_id:
                continue
            try:
                index, count = int(h[2]), int(h[3])
            except ValueError:
                continue
            if count != total or not 0 <= index < total or index in chosen:
                continue
            payload = valid_payload(seq, h[4], h[5])
            if payload is None:
                stats["invalid_copies"] += 1
                continue
            chosen[index] = payload
        missing_count, missing_head = _missing_indices(total, chosen)
        if missing_count:
            raise InsufficientRedundancyError(f"legacy dataset: {missing_count} strand(s) missing or invalid (no redundancy in V0.1 ecc=none)",
                                              details={"missing": missing_head})
        transformed = b"".join(chosen[i] for i in range(total))
    else:
        _require(cfg["ecc"] == "reed_solomon" and version in (2, 3), "unknown legacy ECC")
        ecc = m.get("ecc")
        _require(isinstance(ecc, dict) and isinstance(m.get("stripe_count"), int), "missing ecc/stripe_count")
        _require(0 <= m["stripe_count"] <= MAX_LEGACY_STRIPES,
                 f"stripe_count {m['stripe_count']} exceeds the limit of {MAX_LEGACY_STRIPES}")
        k, parity = ecc.get("data_shards"), ecc.get("parity_shards")
        _require(isinstance(k, int) and isinstance(parity, int) and 1 <= k and 1 <= parity and k + parity <= 255, "invalid K/M")
        code = _LegacyVandermonde(k, parity)
        groups: dict[int, dict[int, bytes]] = {i: {} for i in range(m["stripe_count"])}
        for h, seq in records:
            if len(h) != 8 or h[0] != "VNX2" or h[1] != dataset_id:
                continue
            try:
                stripe, shard, hk, hm = (int(x) for x in h[2:6])
            except ValueError:
                continue
            if stripe not in groups or hk != k or hm != parity or not 0 <= shard < k + parity or shard in groups[stripe]:
                continue
            payload = valid_payload(seq, h[6], h[7])
            if payload is None:
                stats["invalid_copies"] += 1
                continue
            groups[stripe][shard] = payload
        out = []
        for stripe, shards in groups.items():
            row = [shards.get(i) for i in range(k + parity)]
            stats["recovered_shards"] += sum(row[i] is None for i in range(k))
            try:
                out.extend(code.decode(row))
            except InsufficientRedundancyError as error:
                raise InsufficientRedundancyError(f"legacy stripe {stripe}: {error}") from None
        transformed = b"".join(out)[: m["transformed_size"]]
    if cfg["encryption"]:
        transformed = _fernet_decrypt(transformed, key)
    original = _decompress(transformed, cfg["compression"], m["original_size"])
    recomputed = hashlib.sha256(original).hexdigest()
    if recomputed != m["original_sha256"] or len(original) != m["original_size"]:
        raise IntegrityError("legacy dataset: recovered SHA-256 does not match the manifest",
                             details={"expected_sha256": m["original_sha256"], "recovered_sha256": recomputed})
    return original, {**stats, "status": "RECOVERED" if stats["recovered_shards"] else "SUCCESS",
                      "expected_sha256": m["original_sha256"], "recovered_sha256": recomputed, "size": len(original),
                      "name": m.get("original_name") if isinstance(m.get("original_name"), str) else None}


# ------------------------------------------------------------------ RD-1 JSON archive
def decode_rd1_archive(path: str | Path, key: bytes | None = None, file_id: str | None = None) -> tuple[bytes, dict[str, Any]]:
    archive = _read_json(Path(path), MAX_LEGACY_ARCHIVE_BYTES, "RD-1 archive")
    _require(isinstance(archive, dict) and isinstance(archive.get("manifest"), dict) and isinstance(archive.get("files"), list), "RD-1 structure")
    m = archive["manifest"]
    if m.get("format") != "VNXDNA" or m.get("format_version") != "0.1":
        raise UnsupportedFormatError("not an RD-1 archive")
    files = [f for f in archive["files"] if isinstance(f, dict) and isinstance(f.get("manifest"), dict) and isinstance(f.get("chunks"), list)]
    if file_id is None:
        _require(len(files) == 1, f"archive holds {len(files)} files; choose one with file_id")
        entry = files[0]
    else:
        matches = [f for f in files if f["manifest"].get("file_id") == file_id]
        _require(len(matches) == 1, f"file_id {file_id!r} not found")
        entry = matches[0]
    fm = entry["manifest"]
    for name, kind in (("chunks", int), ("sha256", str), ("original_size", int)):
        _require(isinstance(fm.get(name), kind), f"file manifest {name!r}")
    _require(0 <= fm["chunks"] <= MAX_LEGACY_STRANDS,
             f"file manifest chunks {fm['chunks']} exceeds the limit of {MAX_LEGACY_STRANDS}")
    ecc = m.get("ecc") or {}
    codec = None
    if ecc.get("name") == "reed_solomon":
        import reedsolo
        parity = ecc.get("parity_symbols")
        _require(isinstance(parity, int) and 1 <= parity < 255, "RD-1 parity_symbols")
        codec = reedsolo.RSCodec(parity)
    elif ecc.get("name") != "none":
        raise MetadataError("RD-1: unknown ECC")
    by_id: dict[int, bytes] = {}
    corrected = 0
    for chunk in entry["chunks"]:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("chunk_id"), int) or chunk["chunk_id"] in by_id:
            continue
        try:
            raw = _two_bit_decode(str(chunk.get("dna", "")).upper())
            if codec is not None:
                import reedsolo
                try:
                    decoded, _, errata = codec.decode(raw)
                except reedsolo.ReedSolomonError:
                    continue
                corrected += len(errata)
                raw = bytes(decoded)
        except InvalidDNAError:
            continue
        if len(raw) == chunk.get("payload_length") and hashlib.sha256(raw).hexdigest() == chunk.get("checksum"):
            by_id[chunk["chunk_id"]] = raw
    missing_count, missing_head = _missing_indices(fm["chunks"], by_id)
    if missing_count:
        raise InsufficientRedundancyError(f"RD-1 archive: {missing_count} chunk(s) missing or unrecoverable", details={"missing": missing_head})
    data = b"".join(by_id[i] for i in range(fm["chunks"]))
    enc = m.get("encryption") or {}
    if enc.get("enabled"):
        data = _fernet_decrypt(data, key)
    comp = m.get("compression") or {}
    if comp.get("enabled"):
        data = _decompress(data, comp.get("algorithm", "none"), fm["original_size"])
    recomputed = hashlib.sha256(data).hexdigest()
    if recomputed != fm["sha256"] or len(data) != fm["original_size"]:
        raise IntegrityError("RD-1 archive: recovered SHA-256 mismatch", details={"expected_sha256": fm["sha256"], "recovered_sha256": recomputed})
    return data, {"legacy_format": RD1_ARCHIVE, "status": "RECOVERED" if corrected else "SUCCESS", "symbols_corrected": corrected,
                  "expected_sha256": fm["sha256"], "recovered_sha256": recomputed, "size": len(data),
                  "name": fm.get("name") if isinstance(fm.get("name"), str) else None}


def decode_legacy(path: str | Path, key: bytes | None = None) -> tuple[bytes, dict[str, Any]]:
    kind = detect(path)
    if kind == V01_DATASET:
        return decode_v01_dataset(path, key)
    if kind == RD1_ARCHIVE:
        return decode_rd1_archive(path, key)
    raise UnsupportedFormatError(f"{path} is not a recognised legacy VNX-DNA archive")
