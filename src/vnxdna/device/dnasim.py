"""``vnx.codec-ipc/1`` codec worker of the DNA software simulator (docs/V14_MEDIA_ADAPTERS.md §2). SIMULATED.

    python -m vnxdna.device.dnasim --staging DIR

The worker behind ``vnxd``'s ``dna-sim`` and ``dna-lab`` media adapters. It serves ``hello``, ``encode_extent`` and
``decode_extent`` with the EXISTING VNX-DNA codec (``vnxdna.pipeline.stream.encode_extent``/``decode_extent``: the
unchanged archive container, encoder, inner/outer ECC and decoder) and, on request, the EXISTING staged channel
simulator (``vnxdna.simulation.engine`` with a shipped ``vnx.channel-model/1``). Nothing here is a measurement of a
physical medium: every pool file it writes carries ``"evidence": "SIMULATED"``. It holds no key; the sealing
operations are refused (PROTOCOL) and are served by :mod:`vnxdna.device.worker`.

Pool file ``vnx.dna-pool/1`` (the encoder's output, and what ``decode_extent`` reads)::

    VNXPOOL/1\\n
    <canonical JSON header>\\n
    <body: FASTA strands ("body": "strands") or FASTA/FASTQ reads ("body": "reads")>

Header keys (exactly): ``format`` ("vnx.dna-pool/1"), ``evidence`` ("SIMULATED"), ``extent``, ``generation``,
``body``, ``body_sha256`` (SHA-256 of the body), ``record`` (the stream extent record: ``length``, ``sha256``,
``archive_id``, ``archive_tag``, ``strands``, ``bases``, ``strands_sha256``, ``container_sha256``).

``decode_extent`` options (exact keys): ``{}`` decodes the body as it is; ``{"channel": {"model": NAME, "seed": N}}``
first passes a ``strands`` body through the shipped channel model NAME with seed N (a simulated synthesis, storage,
amplification and sequencing run) and decodes the simulated reads. The decoded bytes must match ``expect_bytes`` and
``expect_sha256`` (and the record's own SHA-256, checked by the stream decoder) or the request fails CORRUPTION:
bytes that did not verify are never written.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import protocol as pr
from .contracts import DeviceError
from .worker import Worker, check_staging

SERVER = "vnxdna.device.dnasim"
POOL_MAGIC = b"VNXPOOL/1\n"
FORMAT = "vnx.dna-pool/1"
EVIDENCE = "SIMULATED"
MAX_HEADER = 64 << 10
MAX_POOL = 1 << 30
_MODEL = re.compile(r"[a-z0-9][a-z0-9._+-]{0,63}(@[0-9A-Za-z.+-]{1,32})?")
#: INVALID_ARGUMENT messages about a request's options start with this (vnxd passes only those on as permanent)
OPTIONS = "options: "


def _transient(e: BaseException) -> bool:
    """Failures of the worker's own resources (disk full, output errors, OS errors): retryable, never damage."""
    from vnxdna.core.errors import VNXOutputError, VNXResourceError
    return isinstance(e, (VNXOutputError, VNXResourceError, OSError))


def _shipped_model(ref: str):
    """A shipped ``vnx.channel-model/1`` by name or name@version, never a file (the registry would read a file of
    that name from the current directory)."""
    from vnxdna.simulation import registry
    name, _, version = ref.partition("@")
    versions = sorted((v for n, v in registry.available() if n == name), key=registry._semver_key)
    if not versions or (version and version not in versions):
        raise DeviceError("INVALID_ARGUMENT", f"{OPTIONS}unknown channel model {ref!r}; shipped: {registry.model_names()}")
    return registry._load_shipped(name, version or versions[-1])
_RECORD = {"length": int, "sha256": str, "archive_id": str, "archive_tag": str, "strands": int, "bases": int,
           "strands_sha256": str, "container_sha256": str}
_HEADER = {"format", "evidence", "extent", "generation", "body", "body_sha256", "record"}


def build_pool(header: dict[str, Any], body: bytes) -> bytes:
    return POOL_MAGIC + pr.canonical(header).encode("ascii") + b"\n" + body


def parse_pool(data: bytes) -> tuple[dict[str, Any], bytes]:
    """(header, body) of a ``vnx.dna-pool/1`` file; anything malformed is CORRUPTION."""
    if not data.startswith(POOL_MAGIC):
        raise DeviceError("CORRUPTION", "not a vnx.dna-pool/1 file")
    end = data.find(b"\n", len(POOL_MAGIC), len(POOL_MAGIC) + MAX_HEADER + 1)
    if end < 0:
        raise DeviceError("CORRUPTION", "pool header missing or too long")
    try:
        h = pr.parse(data[len(POOL_MAGIC):end].decode("ascii"))
    except (DeviceError, UnicodeDecodeError) as e:
        raise DeviceError("CORRUPTION", f"pool header is not canonical JSON: {e}") from None
    if not isinstance(h, dict) or set(h) != _HEADER or h["format"] != FORMAT or h["evidence"] != EVIDENCE:
        raise DeviceError("CORRUPTION", "pool header keys, format or evidence label are wrong")
    rec = h["record"]
    if (not isinstance(rec, dict) or set(rec) != set(_RECORD)
            or any(not isinstance(rec[k], t) or isinstance(rec[k], bool) for k, t in _RECORD.items())):
        raise DeviceError("CORRUPTION", "pool header record is malformed")
    if h["body"] not in ("strands", "reads") or not isinstance(h["body_sha256"], str):
        raise DeviceError("CORRUPTION", "pool header body is malformed")
    body = data[end + 1:]
    if hashlib.sha256(body).hexdigest() != h["body_sha256"]:
        raise DeviceError("CORRUPTION", "pool body does not match its SHA-256")
    return h, body


def _reads_suffix(body: bytes) -> str:
    first = body[:1]
    if first == b">":
        return ".fasta"
    if first == b"@":
        return ".fastq"
    raise DeviceError("CORRUPTION", "pool body is neither FASTA nor FASTQ")


class CodecWorker(Worker):
    """Serves the codec operations of ``vnx.codec-ipc/1`` with the VNX-DNA codec and channel simulator."""

    def __init__(self, staging: Path) -> None:
        super().__init__(b"", staging)
        self._sweep()

    def _sweep(self) -> None:
        """Remove the scratch directories of codec workers that no longer run (they died mid-request)."""
        for p in self.staging.glob(".dsimw-*"):
            pid = p.name.split("-")[1] if p.name.count("-") >= 2 else ""
            try:
                alive = Path(f"/proc/{int(pid, 16)}").exists()
            except ValueError:
                alive = False
            if not alive and p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)

    def _tmp(self) -> Path:
        return Path(tempfile.mkdtemp(prefix=f".dsimw-{os.getpid():x}-", dir=self.staging))

    def _encode(self, a: dict[str, Any]) -> dict[str, Any]:
        from vnxdna.archive.operations import ArchiveOptions
        from vnxdna.core.errors import VNXError
        from vnxdna.pipeline.encode import DNAOptions
        from vnxdna.pipeline.stream import encode_extent

        if a["options"] != {}:
            raise DeviceError("INVALID_ARGUMENT", f"{OPTIONS}encode_extent options must be {{}} (got keys {sorted(a['options'])})")
        data = self._read_input(a["input"], a["input_sha256"])
        tmp = self._tmp()
        try:
            src = tmp / "extent.bin"
            src.write_bytes(data)
            try:
                rec = encode_extent(src, tmp / "strands.fasta", DNAOptions(), ArchiveOptions(archive_id="content"), work=tmp)
            except (VNXError, OSError) as e:
                if _transient(e):
                    raise DeviceError("BACKEND_UNAVAILABLE", f"encoder: {type(e).__name__}: {e}") from None
                raise DeviceError("INVALID_ARGUMENT", f"the encoder refused the extent: {e}") from None
            body = (tmp / "strands.fasta").read_bytes()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        header = {"format": FORMAT, "evidence": EVIDENCE, "extent": a["extent"], "generation": a["generation"],
                  "body": "strands", "body_sha256": hashlib.sha256(body).hexdigest(),
                  "record": {k: rec[k] for k in _RECORD}}
        return {"job": a["job"], **self._write_output(a["output"], build_pool(header, body))}

    def _channel(self, options: dict[str, Any]) -> tuple[str, int] | None:
        if options == {}:
            return None
        if set(options) != {"channel"} or not isinstance(options["channel"], dict) \
                or set(options["channel"]) != {"model", "seed"}:
            raise DeviceError("INVALID_ARGUMENT", OPTIONS + 'decode_extent options must be {} or {"channel": {"model", "seed"}}')
        model, seed = options["channel"]["model"], options["channel"]["seed"]
        if not isinstance(model, str) or not _MODEL.fullmatch(model):
            raise DeviceError("INVALID_ARGUMENT", f"{OPTIONS}channel model must be the name of a shipped model (no paths)")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 63:
            raise DeviceError("INVALID_ARGUMENT", f"{OPTIONS}channel seed must be a non-negative 63-bit integer")
        return model, seed

    def _decode(self, a: dict[str, Any]) -> dict[str, Any]:
        from vnxdna.core.errors import VNXError
        from vnxdna.recovery.options import DecodeOptions
        from vnxdna.pipeline.stream import decode_extent
        from vnxdna.simulation import engine

        channel = self._channel(a["options"])
        raw = self._read_pool(a["input"])
        h, body = parse_pool(raw)
        if h["extent"] != a["extent"] or h["generation"] != a["generation"]:
            raise DeviceError("CORRUPTION", f"pool of extent {h['extent']} generation {h['generation']} handed in for "
                                            f"extent {a['extent']} generation {a['generation']}")
        if channel is not None and h["body"] != "strands":
            raise DeviceError("INVALID_ARGUMENT", f"{OPTIONS}a channel can only be applied to strands, not to reads")
        model = _shipped_model(channel[0]) if channel is not None else None
        tmp = self._tmp()
        try:
            reads = tmp / ("body" + _reads_suffix(body))
            reads.write_bytes(body)
            try:
                if channel is not None:
                    assert model is not None  # set together with channel above
                    seq = tmp / "reads.fastq"
                    engine.simulate_file(reads, seq, model, channel[1], fmt="fastq")
                    reads = seq
                data = decode_extent(reads, h["record"], DecodeOptions(), work=tmp)
            except (VNXError, OSError) as e:
                if _transient(e):
                    raise DeviceError("BACKEND_UNAVAILABLE", f"codec worker resources: {type(e).__name__}: {e}") from None
                raise DeviceError("CORRUPTION", f"the extent did not decode: {type(e).__name__}: {e}") from None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if len(data) != a["expect_bytes"] or hashlib.sha256(data).hexdigest() != a["expect_sha256"]:
            raise DeviceError("CORRUPTION", "the decoded extent does not match the expected length and SHA-256")
        return {"job": a["job"], **self._write_output(a["output"], data)}

    def _read_pool(self, name: str) -> bytes:
        p = self.staging / name
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as e:
            raise DeviceError("NOT_FOUND", f"staging input {name}: {e.strerror}") from None
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise DeviceError("INVALID_ARGUMENT", f"staging input {name} is not a regular file")
            data = f.read(MAX_POOL + 1)
        if len(data) > MAX_POOL:
            raise DeviceError("INVALID_ARGUMENT", f"staging input {name} exceeds {MAX_POOL} bytes")
        return data

    def run(self, op: str, a: dict[str, Any]) -> dict[str, Any]:
        if op == "hello":
            if pr.VERSION not in a["versions"]:
                raise DeviceError("VERSION_MISMATCH", f"vnx.codec-ipc versions {a['versions']}; supported [{pr.VERSION}]")
            return {"server": SERVER, "version": pr.VERSION}
        if op == "encode_extent":
            return self._encode(a)
        if op == "decode_extent":
            return self._decode(a)
        raise DeviceError("PROTOCOL", f"operation {op} is not served by the codec worker (sealing: vnxdna.device.worker)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vnxdna.device.dnasim")
    ap.add_argument("--staging", required=True)
    args = ap.parse_args(argv)
    staging = Path(args.staging)
    try:
        check_staging(staging)
    except DeviceError as e:
        print(f"vnxdna.device.dnasim: {e.device_message}", file=sys.stderr)
        return 2
    w = CodecWorker(staging)
    for line in sys.stdin:
        sys.stdout.write(w.handle(line.rstrip("\n")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
