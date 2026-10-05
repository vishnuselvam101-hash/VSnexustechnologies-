"""Python fuzz targets for VNX-DNA V6 (Phase 6). One function per target: ``fn(data: bytes) -> None``.

The same functions are driven by three engines:

* atheris, for long coverage-guided campaigns (``fuzz/run.sh <target> <seconds>`` -> ``python -m fuzz.atheris_run``);
* hypothesis, for the fast smoke tests in ``tests/fuzz/test_fuzz_smoke.py`` (seconds, part of the normal suite);
* replay of the seed corpus (``fuzz/corpus/<target>/``) and the regression corpus (``fuzz/regressions/<target>/``).

A target returns normally when the input was handled correctly: accepted with the right result, or refused with an
anticipated error (:class:`vnxdna.errors.VNXDNAError`, the base of every V3/V4/V6 error). Anything else propagates and
is a finding: another exception type, a failed invariant (``FuzzInvariantError``), a crash, a hang or excessive memory.
The invariants are true properties of the code, never guesses: "accepted ⇒ the bytes are the original ones", "native ==
reference", "an accepted superblock re-packs to the same bytes", "RS within its radius recovers the codeword".

All inputs here are synthetic. The archive key is a TEST-ONLY key derived from an obviously fake text at run time.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import os
import shutil
import struct
import tempfile
import zlib
from pathlib import Path

import numpy as np

from vnxdna.errors import VNXDNAError

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "fuzz" / "corpus"
REGRESSIONS = ROOT / "fuzz" / "regressions"

TEST_ONLY_FUZZ_KEY = hashlib.sha256(b"vnx-dna TEST ONLY fuzz key - protects nothing").digest()
TEST_ONLY_SALT = bytes(range(16))
TEST_ONLY_ARCHIVE_ID = bytes(range(100, 116))
SEED_FILES = {"d/a.txt": b"VNX-DNA fuzz seed file A\n" * 160, "d/b.dat": bytes(range(256)) * 9, "d/e": b""}


class FuzzInvariantError(AssertionError):
    """A true invariant of the code under test does not hold: always a finding."""


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise FuzzInvariantError(msg)


# ------------------------------------------------------------------------------------------------ input consumer
class Input:
    """Deterministic consumer of fuzz bytes (a small FuzzedDataProvider that hypothesis can drive as well)."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def u8(self) -> int:
        if self.pos >= len(self.data):
            return 0
        self.pos += 1
        return self.data[self.pos - 1]

    def u16(self) -> int:
        return self.u8() << 8 | self.u8()

    def u32(self) -> int:
        return self.u16() << 16 | self.u16()

    def take(self, n: int) -> bytes:
        out = self.data[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def rest(self) -> bytes:
        return self.take(len(self.data))

    @property
    def left(self) -> int:
        return len(self.data) - self.pos


# ------------------------------------------------------------------------------------------------ shared fixtures
_STATE: dict = {}


def workdir() -> Path:
    """Process-wide scratch directory (tmpfs when available), removed at exit."""
    if "dir" not in _STATE:
        base = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
        d = Path(tempfile.mkdtemp(prefix="vnx-fuzz-", dir=base))
        import atexit
        atexit.register(shutil.rmtree, d, True)
        _STATE["dir"] = d
    return _STATE["dir"]


def seed_archives() -> dict:
    """Two tiny deterministic VNX4 containers holding SEED_FILES: ``plain`` and ``enc`` (TEST-ONLY key)."""
    if "archives" not in _STATE:
        from vnxdna.v4 import archive as ar
        d = workdir() / "seed-archives"
        src = d / "in"
        for rel, data in SEED_FILES.items():
            (src / rel).parent.mkdir(parents=True, exist_ok=True)
            (src / rel).write_bytes(data)
        ar.build_archive([src / "d"], d / "plain.vnx", ar.ArchiveOptions(chunk_size=4096))
        ar.build_archive([src / "d"], d / "enc.vnx", ar.ArchiveOptions(chunk_size=4096, key=TEST_ONLY_FUZZ_KEY),
                         archive_id=TEST_ONLY_ARCHIVE_ID, salt=TEST_ONLY_SALT)
        _STATE["archives"] = {"plain": (d / "plain.vnx").read_bytes(), "enc": (d / "enc.vnx").read_bytes()}
    return _STATE["archives"]


def _write(name: str, data: bytes) -> Path:
    p = workdir() / name
    p.write_bytes(data)
    return p


def _fresh_dir(name: str) -> Path:
    p = workdir() / name
    if p.exists():
        shutil.rmtree(p)
    p.mkdir()
    return p


def _check_extracted(out: Path, records) -> None:
    """Accepted ⇒ every extracted file is byte-identical to the seed file of the same path (never wrong data)."""
    for rec in records:
        if rec.type != 0:
            continue
        want = SEED_FILES.get(rec.path)
        check(want is not None, f"accepted archive contains a file that no seed has: {rec.path!r}")
        check((out / rec.path).read_bytes() == want, f"accepted archive extracted different bytes for {rec.path!r}")


# ================================================================================================= targets
def fuzz_container(data: bytes) -> None:
    """Arbitrary bytes as a ``.vnx`` file: open (with and without the key), verify, extract.

    Invariants: only VNXDNAError; ``verify`` VERIFIED ⇒ the input is byte-identical to a seed (a forgery would need
    SHA-256 second preimages; FC-8 forgeries need a rewritten trailer, which random mutation cannot produce);
    ``extract`` success ⇒ the files are exactly the seed files."""
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import container as ct
    seeds = seed_archives()
    path = _write("container.vnx", data)
    for kw in ({}, {"key": TEST_ONLY_FUZZ_KEY}, {"key": TEST_ONLY_FUZZ_KEY, "allow_unencrypted": True}):
        try:
            c = ct.open_container(path, **kw)
        except VNXDNAError:
            continue
        try:
            rep = ar.verify_container(path, **kw)
            if rep["status"] == "VERIFIED" and "key" not in kw:
                check(data == seeds["plain"], "a mutated container verified as VERIFIED")
            elif rep["status"] == "VERIFIED":
                check(data in (seeds["plain"], seeds["enc"]), "a mutated container verified as VERIFIED with the key")
        except VNXDNAError:
            pass
        if c.encrypted and c.sealer is None:
            continue
        out = _fresh_dir("container-out")
        try:
            ar.extract(path, out, overwrite=True, **kw)
        except VNXDNAError:
            continue
        _check_extracted(out, c.files)


# manifest values used to replace fields: type confusion, boundaries, malformed hex and unicode
_VALUES = [None, True, False, 0, 1, -1, 2, 4096, 1 << 20, (1 << 62) + 1, 1 << 64, "", "x", "none", "zstd", "AES-256-GCM",
           "scrypt-hkdf-sha256", "key-file-hkdf-sha256", "fixed", "rfc6962-sha256", "zz" * 16, "00" * 16, "é" * 32,
           "0" * 32, "ff" * 32, [], [1], [4, 0], [4, 9], ["vnx4-container"], {}, {"n": 2, "r": 1, "p": 1},
           {"n": 1 << 20, "r": 32, "p": 16}, {"bytes": 0, "sha256": "00" * 32}, {"entries": 0, "entry_bytes": 84}]


def _paths(obj, prefix=()):
    yield prefix
    if isinstance(obj, dict):
        for k in sorted(obj):
            yield from _paths(obj[k], prefix + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _paths(v, prefix + (i,))


def _mutate(m: dict, inp: Input) -> dict:
    m = copy.deepcopy(m)
    for _ in range(1 + inp.u8() % 4):
        paths = [p for p in _paths(m) if p]
        if not paths:
            break
        path = paths[inp.u16() % len(paths)]
        parent = m
        for k in path[:-1]:
            parent = parent[k]
        op = inp.u8() % 8
        last = path[-1]
        if op == 0 and isinstance(parent, dict):
            del parent[last]
        elif op == 1 and isinstance(parent, dict):
            parent["x" + str(inp.u8())] = _VALUES[inp.u8() % len(_VALUES)]
        elif op == 2:
            parent[last] = inp.u32() - (1 << 31)
        elif op == 3:
            n = inp.u8() % 40
            parent[last] = inp.take(n).decode("latin-1")
        else:
            parent[last] = copy.deepcopy(_VALUES[inp.u8() % len(_VALUES)])
    return m


def _rewrap(src: bytes, man: bytes, mac: bytes) -> bytes:
    from vnxdna.v4 import container as ct
    body, c, f, r, _ = struct.unpack(">QQQQQ", src[-ct.TRAILER_BYTES:-ct.TRAILER_BYTES + 40])
    head = src[:ct.HEADER_BYTES + body + c + f + r]
    trailer = struct.pack(">QQQQQ", body, c, f, r, len(man)) + mac + ct.TRAILER_MAGIC
    out = head + man + trailer
    return out + hashlib.sha256(out).digest()


def _manifest_mac(manifest_obj, man: bytes, original_salt: bytes) -> bytes:
    """The manifest HMAC for the TEST-ONLY key, with the (possibly mutated) salt when it is still usable."""
    from vnxdna.v4 import crypto
    salt = original_salt
    try:
        s = manifest_obj["encryption"]["salt"]
        if isinstance(s, str) and len(s) == 32:
            salt = bytes.fromhex(s)
    except (KeyError, TypeError, ValueError):
        pass
    keys = crypto.ArchiveKeys.derive(TEST_ONLY_FUZZ_KEY, salt)
    return hmac.new(keys.mac, b"VNX4 manifest\x00" + man, hashlib.sha256).digest()


def fuzz_manifest(data: bytes) -> None:
    """Structure-aware manifest mutation behind a *valid* trailer digest / manifest HMAC (the TEST-ONLY key), so that
    every semantic check of ``validate_manifest`` and ``open_container`` is reached. Byte 0 bit 0 picks the plain or
    the encrypted seed; bit 1 replaces the manifest by raw fuzz bytes. Invariants: only VNXDNAError; accepted ⇒
    extract yields exactly the seed files."""
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import container as ct
    from vnxdna.v4.util import canonical_json
    seeds = seed_archives()
    inp = Input(data)
    sel = inp.u8()
    src = seeds["enc" if sel & 1 else "plain"]
    good = ct.parse_canonical_json(src[-ct.TRAILER_BYTES - struct.unpack(">Q", src[-ct.TRAILER_BYTES + 32:
                                                                                  -ct.TRAILER_BYTES + 40])[0]:
                                       -ct.TRAILER_BYTES], "manifest")
    if sel & 2:
        man = inp.rest()[:ct.MAX_MANIFEST_BYTES]
        obj = None
    else:
        obj = _mutate(good, inp)
        try:
            man = canonical_json(obj)
        except (TypeError, ValueError):
            return  # not representable as canonical JSON (cannot occur in a real file)
    if not man:
        man = b"{}"
    mac = _manifest_mac(obj, man, TEST_ONLY_SALT) if sel & 1 else hashlib.sha256(man).digest()
    path = _write("manifest.vnx", _rewrap(src, man, mac))
    for kw in ({}, {"key": TEST_ONLY_FUZZ_KEY}, {"key": TEST_ONLY_FUZZ_KEY, "allow_unencrypted": True}):
        try:
            c = ct.open_container(path, **kw)
            ar.inspect_container(path, **kw)
        except VNXDNAError:
            continue
        if c.encrypted and c.sealer is None:
            continue
        out = _fresh_dir("manifest-out")
        try:
            ar.extract(path, out, overwrite=True, **kw)
        except VNXDNAError:
            continue
        _check_extracted(out, c.files)


def fuzz_superblock(data: bytes) -> None:
    """``Superblock.unpack`` (versions 1 and 2). Byte 0 bit 0: recompute the CRC so the field checks are reached.

    Invariants: only VNXDNAError; an accepted superblock re-packs to the same 96 bytes except the two reserved bytes
    (unpack ignores them, see V6_SECURITY_MODEL.md); its derived geometry never raises a non-VNX error."""
    from vnxdna.v4 import encoder as en
    inp = Input(data)
    mode = inp.u8()
    raw = bytearray(inp.rest()[:en.SB_BYTES + 8])
    if mode & 1 and len(raw) >= en.SB_BYTES:
        if mode & 2:
            raw[:6] = en.SB_MAGIC
        if mode & 4:
            raw[6] = 1 + (mode >> 7)
        raw[en.SB_BYTES - 4:en.SB_BYTES] = struct.pack(">I", zlib.crc32(bytes(raw[:en.SB_BYTES - 4])))
    try:
        sb = en.Superblock.unpack(bytes(raw))
    except VNXDNAError:
        return
    packed = sb.pack()
    expect = bytearray(raw[:en.SB_BYTES])
    expect[en.SB_BYTES - 6:en.SB_BYTES - 4] = b"\x00\x00"
    expect[en.SB_BYTES - 4:] = struct.pack(">I", zlib.crc32(bytes(expect[:-4])))
    check(packed == bytes(expect), "accepted superblock does not re-pack to its own bytes")
    total = sb.total_groups
    check(total >= sb.group_count, "total groups below the data groups")
    try:
        if sb.version == en.SB_VERSION_V6:
            g = sb.geometry()
            for grp in {0, max(0, g.G - 1), max(0, total - 1)}:
                g.symbols_of(grp)
                g.stripe_of(grp)
        elif sb.group_count:
            en.group_k(sb.container_size, sb.K, sb.layout.payload_bytes, sb.group_count - 1)
    except VNXDNAError:
        pass


def _frame_profile(sel: int):
    from vnxdna.v4.frame import PROFILES
    names = sorted(PROFILES)
    return PROFILES[names[sel % len(names)]][0]


def fuzz_frame(data: bytes) -> None:
    """Inner RS + CRC frame decoding (``decode_frames``) on fuzzed frames, half of them built from valid frames with
    fuzz-chosen corruption. Invariants: an accepted frame re-encodes (same variant byte, parsed fields) to a codeword
    within the RS radius of the received frame; for a valid frame with at most r/2 byte errors, the frame is
    accepted with the original fields."""
    from vnxdna.v4.codecs import InnerRS
    from vnxdna.v4.frame import CRC_BYTES, HEADER_BYTES, decode_frames, keystreams, plain_rows
    inp = Input(data)
    lay = _frame_profile(inp.u8())
    fb = lay.frame_bytes
    r = lay.inner_parity
    span = HEADER_BYTES - 1 + lay.payload_bytes + CRC_BYTES
    inner = InnerRS(r)
    n = 1 + inp.u8() % 8
    built = inp.u8() & 1
    if built:
        rng = np.random.default_rng(inp.u32())
        pay = rng.integers(0, 256, (n, lay.payload_bytes), dtype=np.uint8)
        plain = plain_rows(int(rng.integers(0, 65536)), int(rng.integers(0, 2)), rng.integers(0, 1 << 32, n),
                           rng.integers(0, 1 << 16, n), pay)
        var = rng.integers(0, 256, n).astype(np.uint8)
        msg = np.concatenate([var[:, None], plain ^ keystreams(span)[var]], axis=1)
        clean = np.concatenate([msg, inner.parity(msg)], axis=1)
        frames = clean.copy()
        nerr = np.zeros(n, dtype=np.int64)
        for i in range(n):
            for _ in range(inp.u8() % (r + 2)):
                pos = inp.u8() % fb
                val = inp.u8()
                if frames[i, pos] != (clean[i, pos] ^ val) and val:
                    frames[i, pos] = clean[i, pos] ^ val
            nerr[i] = int((frames[i] != clean[i]).sum())
    else:
        raw = inp.take(n * fb).ljust(n * fb, b"\x00")
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(n, fb).copy()
    erasures = None
    if inp.u8() & 1:
        bits = np.unpackbits(np.frombuffer(inp.take(-(-n * fb // 8)).ljust(-(-n * fb // 8), b"\x00"), np.uint8))
        erasures = bits[:n * fb].reshape(n, fb).astype(bool)
    p = decode_frames(lay, frames, erasures)
    ks = keystreams(span)
    for i in np.flatnonzero(p.ok).tolist():
        rows = plain_rows(int(p.tag[i]), int(p.kind[i]), p.group[i:i + 1], p.symbol[i:i + 1], p.payload[i:i + 1])
        if int(p.errata[i]) == 0:
            # CRC-first acceptance ignores the parity bytes, but the message bytes must be exactly the received ones
            m0 = np.concatenate([[frames[i, 0]], rows[0] ^ ks[frames[i, 0]]]).astype(np.uint8)
            check(bytes(m0) == bytes(frames[i, :m0.size]), f"accepted clean frame {i} differs from its fields")
            continue
        best = None                     # the variant byte is RS-protected too: the corrected one may differ
        for v in [int(frames[i, 0])] + [v for v in range(256) if v != frames[i, 0]]:
            m = np.concatenate([[v], rows[0] ^ ks[v]]).astype(np.uint8)[None, :]
            d = int((np.concatenate([m, inner.parity(m)], axis=1)[0] != frames[i]).sum())
            best = d if best is None else min(best, d)
            if d <= r:
                break
        check(best is not None and best <= r, f"accepted frame {i} is {best} symbols from its codeword (r = {r})")
    if built and erasures is None:
        for i in range(n):
            if nerr[i] <= r // 2:
                check(bool(p.ok[i]), f"frame {i} with {nerr[i]} byte errors (r = {r}) was not recovered")
                check(bytes(p.payload[i]) == bytes(pay[i]), f"frame {i} recovered a wrong payload")


@contextlib.contextmanager
def _reads_limits(block: int, max_nt: int):
    from vnxdna.v4 import reads as ref
    old = ref.BLOCK, ref.MAX_READ_NT
    ref.BLOCK, ref.MAX_READ_NT = block, max_nt
    try:
        yield
    finally:
        ref.BLOCK, ref.MAX_READ_NT = old


def _native_reads_ready() -> bool:
    if "reads_native" not in _STATE:
        from vnxdna.v6 import native_reads as nr
        _STATE["reads_native"] = nr.available()
    return _STATE["reads_native"]


def fuzz_reads(data: bytes) -> None:
    """FASTA / FASTQ / plain read files: the V4 reference parser and the V6 native streaming parser must give the same
    batches and the same exception (class, message, stage), for fuzz-chosen block size, record cap, batch size,
    max_reads and read chunk sizes. Without the native library only the reference runs (only VNXDNAError allowed)."""
    from vnxdna.v4 import reads as ref
    from vnxdna.v6 import native_reads as nr
    inp = Input(data)
    sel = inp.u8()
    prefix = (b"", b"@", b">", b"ACGT\n", b"@r\nACGT\n+\nIIII\n")[sel % 5]
    block = (1, 3, 64, 4096, 8 << 20)[inp.u8() % 5]
    max_nt = (0, 4, 64, 100_000)[inp.u8() % 4]
    batch = inp.u8() % 9 - 1
    max_reads = (None, 0, 3)[inp.u8() % 3]
    sizes = [1 + inp.u8() for _ in range(1 + inp.u8() % 4)]
    path = _write("reads.txt", prefix + inp.rest())
    with _reads_limits(block, max_nt):
        def run(factory):
            batches, err = [], None
            try:
                for b in factory():
                    batches.append(b)
            except Exception as error:  # noqa: BLE001 - compared below; non-VNX classes are findings
                err = (type(error), str(error), getattr(error, "stage", None))
            return batches, err
        r = run(lambda: ref.iter_reads(path, batch, max_reads=max_reads))
        if r[1] is not None:
            check(issubclass(r[1][0], VNXDNAError), f"reference parser raised {r[1][0].__name__}: {r[1][1]}")
        if not _native_reads_ready():
            return
        for s in (None, sizes):
            n = run(lambda: nr.iter_reads_native(path, batch, max_reads=max_reads, read_sizes=s))
            check(r[1] == n[1], f"native/reference exceptions differ: {r[1]} vs {n[1]}")
            check(len(r[0]) == len(n[0]), "native/reference batch counts differ")
            for x, y in zip(r[0], n[0]):
                for f in ("codes", "lengths", "invalid"):
                    a, b = getattr(x, f), getattr(y, f)
                    check(a.dtype == b.dtype and np.array_equal(a, b), f"native/reference batch field {f} differs")
                check((x.quals is None) == (y.quals is None), "quals presence differs")
                if x.quals is not None:
                    check(np.array_equal(x.quals, y.quals), "native/reference quals differ")


def fuzz_vxs(data: bytes) -> None:
    """VXS packed strand files (``\\x89VXSTRD\\n`` header, packed 2-bit records, trailer) through the V4 read path
    (``vnxdna.v4.reads.iter_reads`` -> ``vnxdna.v2.strandio``). Byte 0 bit 0: rebuild a consistent header/trailer
    around fuzzed records. Invariants: only VNXDNAError; accepted reads have length strand_nt and codes 0..3."""
    from vnxdna.v2 import strandio as sio
    from vnxdna.v4 import reads as ref
    inp = Input(data)
    mode = inp.u8()
    if mode & 1:
        nt = 1 + inp.u16() % 400
        rb = -(-nt // 4)
        body = inp.rest()
        count = len(body) // rb
        if mode & 2:
            count += inp.u8() % 3 - 1 if len(body) else 0
        records = body[:count * rb]
        head = sio.VXS_MAGIC + (1).to_bytes(2, "big") + bytes(2) + nt.to_bytes(4, "big") + rb.to_bytes(4, "big") + bytes(12)
        tail = max(count, 0).to_bytes(8, "big") + bytes(8) + sio.VXS_END + hashlib.sha256(records).digest()
        blob = head + records + tail
    else:
        blob = sio.VXS_MAGIC + inp.rest() if mode & 2 else inp.rest()
    path = _write("reads.vxs", blob)
    try:
        info = sio.vxs_info(path) if blob.startswith(sio.VXS_MAGIC) else None
        for b in ref.iter_reads(path, 1 + mode % 7):
            if info is not None:
                check(bool(np.all(b.lengths == info.strand_nt)), "VXS read length differs from strand_nt")
            check(b.codes.size == 0 or int(b.codes.max()) <= 4, "read code outside 0..4")
    except VNXDNAError:
        pass


def _rs_backends() -> list[str]:
    if "rs_levels" not in _STATE:
        from vnxdna.v6 import native_rs
        _STATE["rs_levels"] = [lv for lv in native_rs.supported_levels() if lv != "reference"] if native_rs.available() else []
    return _STATE["rs_levels"]


def fuzz_rs(data: bytes) -> None:
    """RS(n, n - r) errors-and-erasures decoding: V3 reference (``ecc.rs_batch``), V4 ``rs_fast`` and every native
    SIMD level must agree exactly (corrected words, ok, errata). Within the radius (2e + f <= r) the original
    codeword is recovered; any word reported ok is a codeword (all syndromes zero)."""
    from vnxdna.ecc import rs_batch
    from vnxdna.v4 import rs_fast
    from vnxdna.v4.codecs import InnerRS
    from vnxdna.v6 import native_rs
    inp = Input(data)
    n = 2 + inp.u8() % 254
    r = 1 + inp.u8() % min(64, n - 1)
    words = 1 + inp.u8() % 6
    mode = inp.u8()
    rng = np.random.default_rng(inp.u32())
    if mode & 1:
        cw = np.frombuffer(inp.take(words * n).ljust(words * n, b"\x00"), np.uint8).reshape(words, n).copy()
        clean = None
    else:
        msg = rng.integers(0, 256, (words, n - r), dtype=np.uint8)
        clean = np.concatenate([msg, InnerRS(r).parity(msg)], axis=1)
        cw = clean.copy()
    erase = np.zeros((words, n), dtype=bool)
    for i in range(words):
        for _ in range(inp.u8() % (r + 3)):
            pos = inp.u8() % n
            if inp.u8() & 1:
                erase[i, pos] = True
                if inp.u8() & 1:
                    cw[i, pos] ^= 1 + rng.integers(0, 255)
            else:
                cw[i, pos] ^= inp.u8()
    er = erase if erase.any() or mode & 2 else None
    ref = rs_batch.decode_batch(cw, r, er)
    fast = rs_fast.decode_batch(cw, r, er)
    outs = {"rs_fast": fast}
    for lv in _rs_backends():
        outs[lv] = native_rs.decode_batch(cw, r, er, backend=lv)
    for name, o in outs.items():
        for a, b, f in zip(ref, o, ("corrected", "ok", "errata")):
            check(a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b),
                  f"RS {name} differs from the reference in {f} (n={n}, r={r})")
    out, ok, errata = ref
    if ok.any():
        check(not rs_batch.syndromes(out[ok], r).any(), "a word reported ok is not a codeword")
    if clean is not None:
        for i in range(words):
            e = int(((cw[i] != clean[i]) & ~erase[i]).sum())
            f = int(erase[i].sum()) if er is not None else 0
            if 2 * e + f <= r:
                check(bool(ok[i]) and np.array_equal(out[i], clean[i]), f"word {i}: 2e+f = {2 * e + f} <= {r} not recovered")


def fuzz_align(data: bytes) -> None:
    """Native banded template aligner vs the V4 NumPy reference: every Projection field bit for bit, for fuzz-chosen
    layout, band, costs, minimum quality, qualities and reads (valid strands with edits, or raw bytes including
    out-of-alphabet values). Skips when the native library is unavailable."""
    from vnxdna.v4.frame import Layout, PROFILES
    from vnxdna.v4.sync import SyncCosts, TemplateAligner
    from vnxdna.v5 import native_alignment as na
    if not na.available():
        return
    layouts = [PROFILES["v4-balanced"][0], Layout(10, 16, 24, 3), PROFILES["v4-dense"][0], PROFILES["v4-indel"][0],
               Layout(1, 0, 8, 1), Layout(5, 2, 8, 6), Layout(40, 16, 32, 2)]
    inp = Input(data)
    lay = layouts[inp.u8() % len(layouts)]
    band = (0, 1, 2, 3, 4, 6, 8, 12, 20)[inp.u8() % 9]
    costs = SyncCosts(*(inp.u8() % 10 for _ in range(4)), inp.u8() % 3)
    minq = inp.u8() % 45
    tpl, _ = lay.template()
    reads, quals = [], []
    use_q = inp.u8() & 1
    for _ in range(1 + inp.u8() % 12):
        kind = inp.u8() % 4
        if kind == 0:
            s = np.frombuffer(inp.take(inp.u16() % (tpl.size + 2 * band + 8)), np.uint8).copy()
        else:
            rng = np.random.default_rng(inp.u32())
            s = rng.integers(0, 4, tpl.size).astype(np.uint8)
            s[tpl >= 0] = tpl[tpl >= 0].astype(np.uint8)
            for _ in range(inp.u8() % 6):
                pos = inp.u16() % max(1, s.size)
                op = inp.u8() % 3
                if op == 0 and s.size:
                    s[pos % s.size] = inp.u8() % 5
                elif op == 1:
                    s = np.insert(s, pos % (s.size + 1), inp.u8() % 4).astype(np.uint8)
                elif s.size:
                    s = np.delete(s, pos % s.size).astype(np.uint8)
        reads.append(s)
        quals.append(np.frombuffer(inp.take(s.size).ljust(s.size, b"\x28"), np.uint8) % 60 if inp.u8() % 8 else None)
    q = quals if use_q else None
    ref = TemplateAligner(lay, band, costs, backend="reference").project(reads, q, minq)
    nat = TemplateAligner(lay, band, costs, backend="native").project(reads, q, minq)
    for f in ("bases", "erased", "ok", "insertions", "deletions", "marker_mismatches", "cost"):
        a, b = getattr(ref, f), getattr(nat, f)
        check(a.dtype == b.dtype and a.shape == b.shape and np.array_equal(a, b), f"aligner field {f} differs")


def fuzz_bomb(data: bytes) -> None:
    """Bounded decompression (decompression bombs): ``v4.archive.bounded_zstd`` and the V3 ``container.compression``
    zstd / zlib paths on fuzzed streams, including real frames of highly compressible data with a declared content size
    far above the recorded plaintext size. Invariants: the output is exactly the recorded size or an IntegrityError;
    never more than ``expected + 1`` bytes are produced (bounded memory)."""
    import zstandard

    from vnxdna.container import compression
    from vnxdna.v4 import archive as ar
    inp = Input(data)
    mode = inp.u8()
    expected = inp.u16() * (1 + (mode >> 6))
    if mode & 1:
        big = (1 + inp.u16()) * (64 if mode & 2 else 1)       # up to ~4 MiB of zeros in a few hundred bytes
        if mode & 4:
            stream = zlib.compress(b"\x00" * big, 9)
        else:
            stream = zstandard.ZstdCompressor(level=3, write_content_size=bool(mode & 8)).compress(b"\x00" * big)
        stream += inp.rest()[:64] if mode & 16 else b""
    else:
        stream = inp.rest()
    algo = "zlib" if mode & 4 else "zstd"
    try:
        out = ar.bounded_zstd(stream, expected, "chunk") if algo == "zstd" and mode & 32 else \
            compression.decompress(stream, algo, expected)
    except VNXDNAError:
        return
    check(len(out) == expected, f"decompressed {len(out)} bytes for a recorded size of {expected}")


def _decode_seed() -> dict:
    if "decode" not in _STATE:
        from vnxdna.v4 import encoder as en
        d = workdir() / "decode-seed"
        d.mkdir(exist_ok=True)
        container = seed_archives()["plain"]
        (d / "a.vnx").write_bytes(container)
        en.encode_container(d / "a.vnx", d / "a.fasta", en.DNAOptions(profile="v4-balanced"))
        _STATE["decode"] = {"container": container, "fasta": (d / "a.fasta").read_bytes()}
    return _STATE["decode"]


def fuzz_decode(data: bytes) -> None:
    """End to end: a read file (FASTA built from the plain seed archive's strands, then mutated, truncated or replaced
    by fuzz bytes) through ``vnxdna.v4.decoder.decode_reads``. Invariants: only VNXDNAError; SUCCESS ⇒ the output
    container is byte-identical to the original (the decoder never returns wrong data)."""
    from vnxdna.v4 import decoder as de
    seed = _decode_seed()
    inp = Input(data)
    mode = inp.u8()
    if mode & 1:
        records = [b">" + r for r in seed["fasta"].split(b">") if r]
        rng = np.random.default_rng(inp.u32())
        keep = [r for r in records if inp.u8() % 16 != 15]             # drop strands (dropout)
        if mode & 2:
            keep = [keep[i] for i in rng.permutation(len(keep))]       # reorder strands
            keep += [keep[i] for i in rng.integers(0, len(keep), inp.u8() % 8)] if keep else []  # duplicates
        blob = bytearray(b"".join(keep))
        for _ in range(inp.u8() % 64):                                  # byte edits: substitutions, junk, indels
            if not blob:
                break
            pos = inp.u16() % len(blob)
            op = inp.u8() % 3
            if op == 0:
                blob[pos] = b"ACGTN>\n"[inp.u8() % 7]
            elif op == 1:
                blob.insert(pos, b"ACGT"[inp.u8() % 4])
            else:
                del blob[pos]
        if mode & 4:
            blob = blob[: inp.u32() % (len(blob) + 1)]
        blob = bytes(blob)
    else:
        blob = inp.rest()
    path = _write("decode.fasta", blob)
    out = workdir() / "decode-out.vnx"
    out.unlink(missing_ok=True)
    try:
        res = de.decode_reads(path, out, de.DecodeOptions(workers=1), overwrite=True, workdir=workdir())
    except VNXDNAError:
        return
    if res.status == "SUCCESS":
        check(out.read_bytes() == seed["container"], "decode reported SUCCESS with a container that differs")


# Python targets carry a "py-" prefix so that they never collide with a native libFuzzer harness in fuzz/native/.
TARGETS = {
    "py-container": fuzz_container,
    "py-manifest": fuzz_manifest,
    "py-superblock": fuzz_superblock,
    "py-frame": fuzz_frame,
    "py-reads": fuzz_reads,
    "py-vxs": fuzz_vxs,
    "py-rs": fuzz_rs,
    "py-align": fuzz_align,
    "py-bomb": fuzz_bomb,
    "py-decode": fuzz_decode,
}


def corpus_files(target: str) -> list[Path]:
    out = []
    for base in (CORPUS / target, REGRESSIONS / target):
        if base.is_dir():
            out += sorted(p for p in base.iterdir() if p.is_file() and not p.name.startswith("."))
    return out


def run_one(target: str, data: bytes) -> None:
    TARGETS[target](data)

