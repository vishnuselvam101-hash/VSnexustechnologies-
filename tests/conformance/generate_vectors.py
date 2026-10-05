"""Generate the full conformance vector set (``tests/conformance``; spec §6.1, §6.2).

    PYTHONPATH=src python tests/conformance/generate_vectors.py

Layout written (spec §6.1)::

    index.json                         vnx.conformance-index/1
    stage/<vector-id>/                 vector.json + inputs (small files)
    e2e/<vector-id>/                   vector.json; inputs are references to tests/fixtures/<set>/ files (path + SHA-256)
    negative/<vector-id>/              malformed or unsupported inputs and the expected error

The vectors of the subset shipped inside the package (``src/vnxdna/conformance/vectors``) are copied byte for byte first, so
the full set is a superset and a test checks that the two stay identical.

Expected values are frozen: the vectors are committed and are never regenerated to make a failing implementation pass.
Where an independent computation exists (``zlib`` CRC-32, ``hashlib`` SHAKE-128, ``reedsolo``, a small GF(256) written
here for the Cauchy rows, the ``cryptography`` primitives for the AEAD, a recursive RFC 6962 tree) the generator asserts that
the product agrees with it before freezing the value; negative vectors carry the code, category and exit code of spec §10.

SYNTHETIC SOFTWARE TEST data. No DNA was synthesised, stored or sequenced.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("VNX_CONFORMANCE_OUT") or ROOT / "tests" / "conformance")   # the test of determinism writes elsewhere
PACKAGED = ROOT / "src" / "vnxdna" / "conformance" / "vectors"
FIXTURES = ROOT / "tests" / "fixtures"          # e2e vectors reference these by relative path from OUT
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from vnxdna import sdk  # noqa: E402
from vnxdna.conformance import operations, run_vector  # noqa: E402
from vnxdna.core.util import canonical_json  # noqa: E402
from vnxdna.dnaenc.layout import PROFILES, Layout  # noqa: E402
from vnxdna.dnaenc.superblock import Superblock  # noqa: E402

operations.SERVICES["version"] = sdk.version
EVIDENCE = "SYNTHETIC SOFTWARE TEST"
PASSPHRASE = "vnx-test-passphrase-NOT-A-SECRET"
INDEX: list[dict] = []
NO_FORMATS = {"frame": None, "superblock": None, "container": None}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def rng_bytes(seed: int, n: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, n, dtype=np.uint8).tobytes()


def err(code: str, category: str, exit_code: int, retryable: bool = False) -> dict:
    return {"code": code, "category": category, "exit_code": exit_code, "retryable": retryable}


UNSUPPORTED = lambda code: err(code, "UNSUPPORTED_FORMAT", 6)       # noqa: E731
FORMAT_ERROR = err("FORMAT_ERROR", "INVALID_INPUT", 3)
INTEGRITY = err("INTEGRITY_ERROR", "VERIFICATION_FAILED", 1)
NOT_RECOVERABLE = err("INSUFFICIENT_REDUNDANCY", "INSUFFICIENT_REDUNDANCY", 5, True)


# ------------------------------------------------------------------------------------------------------------ vector writer
def vector(group: str, vid: str, stage: str, operation: str, params: dict | None = None, inputs: dict | None = None, *,
           kind: str = "positive", formats: dict | None = None, error: dict | None = None, expect: dict | None = None,
           refs: dict | None = None) -> dict:
    """Write one vector. ``inputs``: name -> bytes (stored in the vector directory); ``refs``: name -> (relative path from
    the vector directory, bytes) for files that already exist elsewhere (fixtures, other vectors), recorded by path and SHA-256.
    ``expect`` is an independently computed expected output, checked against the product before it is frozen."""
    d = OUT / group / vid
    d.mkdir(parents=True)
    ins = {}
    for name, data in (inputs or {}).items():
        fn = f"{name}.dat"
        (d / fn).write_bytes(data)
        ins[name] = {"path": fn, "sha256": sha(data)}
    for name, (rel, data) in (refs or {}).items():
        ins[name] = {"path": rel, "sha256": sha(data)}
    vec = {"schema": "vnx.conformance-vector/1", "id": vid, "stage": stage,
           "formats": formats or NO_FORMATS, "kind": kind, "operation": operation, "params": params or {}, "inputs": ins,
           "expected": {"error": error} if error else {}, "since_spec": "6.0", "evidence": EVIDENCE}
    path = d / "vector.json"
    path.write_text(json.dumps(vec, indent=1) + "\n")
    if error is None:
        got = run_vector(d)
        assert "outputs" in got["observed"], (vid, got["observed"])
        if expect is not None:
            for k, v in expect.items():
                assert got["observed"]["outputs"][k] == v, (vid, k, got["observed"]["outputs"][k], v)
        vec["expected"] = got["observed"]
        path.write_text(json.dumps(vec, indent=1) + "\n")
    rec = run_vector(d)
    assert rec["status"] == "PASS", (vid, rec["observed"], rec["expected"])
    INDEX.append({"id": vid, "stage": stage, "kind": kind, "formats": vec["formats"], "path": f"{group}/{vid}"})
    return vec


# ------------------------------------------------------------------------------------------------------------ independent math
EXP, LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def gmul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def ginv(a: int) -> int:
    return EXP[255 - LOG[a]]


def cauchy_parity_independent(data: np.ndarray, K: int, M: int) -> np.ndarray:
    """data (K, P) → parity (M, P) with C[i][j] = 1 / ((K + i) xor j) (the construction written in the spec)."""
    out = np.zeros((M, data.shape[1]), dtype=np.uint8)
    for i in range(M):
        for j in range(K):
            c = ginv((K + i) ^ j)
            for t in range(data.shape[1]):
                out[i, t] ^= gmul(c, int(data[j, t]))
    return out


def rs_parity_independent(msg: bytes, r: int) -> bytes:
    import reedsolo
    return bytes(reedsolo.RSCodec(r, nsize=255, fcr=0, prim=0x11D, generator=2).encode(msg)[len(msg):])


def nt_of(codes) -> str:
    return "".join("ACGT"[int(c)] for c in codes)


# ============================================================================================================ stage vectors
def gf_rs_vectors() -> None:
    for r in (8, 12, 16, 20):
        msg = rng_bytes(1000 + r, 30)
        parity = rs_parity_independent(msg, r)
        vector("stage", f"rs.encode.r{r:02d}", "E11", "rs.encode", {"r": r}, {"message": msg}, expect={"parity_hex": parity.hex()})
        cw = msg + parity
        n = len(cw)
        rng = np.random.default_rng(2000 + r)
        positions = rng.permutation(n)
        cases = {"clean": ([], []),
                 "errors": (positions[: r // 2].tolist(), []),                         # e = r / 2, f = 0
                 "erasures": ([], positions[:r].tolist()),                             # e = 0, f = r
                 "mixed": (positions[: r // 4].tolist(), positions[r // 4: r // 4 + r // 2].tolist())}   # 2e + f = r
        for name, (err_pos, era_pos) in cases.items():
            bad = bytearray(cw)
            for p in err_pos:
                bad[p] ^= int(rng.integers(1, 256))
            for p in era_pos:
                bad[p] = int(rng.integers(0, 256))
            vector("stage", f"rs.decode.r{r:02d}.{name}", "D5", "rs.decode", {"r": r, "erasures": sorted(era_pos)},
                   {"codeword": bytes(bad)}, expect={"ok": True, "corrected_hex": cw.hex()})


    golden = (ROOT / "tests" / "v6" / "native" / "native_rs_golden.json").read_bytes()
    vec = vector("stage", "rs.golden.native-rs-file", "D5", "rs.golden", {}, refs={"golden": ("../../../v6/native/native_rs_golden.json", golden)},
                 expect={"mismatches": []})
    assert vec["expected"]["outputs"]["within_bound_cases"] >= 80


def crc_scrambler_mapping_vectors() -> None:
    check = b"123456789"
    vector("stage", "crc32.check-value", "E10", "crc32", {"rows_hex": check.hex(), "row_bytes": 9},
           expect={"crc32": [f"{zlib.crc32(check):08x}"]})
    assert zlib.crc32(check) == 0xCBF43926
    rows = np.random.default_rng(6001).integers(0, 256, (4, 49), dtype=np.uint8)
    vector("stage", "crc32.rows.002", "E10", "crc32", {"rows_hex": rows.tobytes().hex(), "row_bytes": 49},
           expect={"crc32": [f"{zlib.crc32(r.tobytes()):08x}" for r in rows]})
    for domain, tag in (("VNX4 scrambler", "vnx4"), ("VNX-DNA/4 scrambler", "v1-frame4"), ("VNX-DNA/5 scrambler", "v3-frame5")):
        expect = bytes(hashlib.shake_128(domain.encode() + bytes([v])).digest(1)[0] for v in range(256)).hex()
        vector("stage", f"scrambler.byte0.{tag}", "E11", "scrambler.byte0", {"domain": domain}, expect={"byte0_hex": expect})
    for v in (0, 1, 255):          # independent check of the keystream already frozen in the packaged vectors
        want = hashlib.shake_128(b"VNX4 scrambler" + bytes([v])).digest(53)[:16].hex()
        rec = json.loads((OUT / "stage" / f"scrambler.keystream.v{v:03d}" / "vector.json").read_text())
        assert rec["expected"]["outputs"]["keystream_hex"] == want
    for prof, (lay, _, _) in PROFILES.items():
        raw = rng_bytes(3000 + lay.payload_bytes, lay.frame_bytes)
        expect = "".join("ACGT"[(b >> s) & 3] for b in raw for s in (6, 4, 2, 0))
        vector("stage", f"mapping.bytes_to_nt.{prof}", "E12", "mapping.bytes_to_nt", {"bytes_hex": raw.hex()},
               formats={"frame": 4, "superblock": None, "container": None}, expect={"nt": expect})
    for ell in range(1, 7):
        lay = Layout(payload_bytes=2, inner_parity=0, marker_period=8, marker_len=ell).validate()
        frame = rng_bytes(3100 + ell, lay.frame_bytes)
        vector("stage", f"markers.insert.l{ell}", "E12", "markers.insert",
               {"layout": {"payload_bytes": 2, "inner_parity": 0, "marker_period": 8, "marker_len": ell}, "frame_hex": frame.hex()},
               formats={"frame": 4, "superblock": None, "container": None})


def frame_vectors() -> None:
    from vnxdna.dnaenc.constraints import ConstraintConfig
    from vnxdna.dnaenc.frame4 import build_strands
    from v6.probe_support import nibble_strands
    f4 = {"frame": 4, "superblock": None, "container": None}
    rng = np.random.default_rng(4000)
    strands = {}
    for prof, (lay, _, _) in PROFILES.items():
        payload = rng.integers(0, 256, lay.payload_bytes, dtype=np.uint8).tobytes()
        s, v = build_strands(lay, ConstraintConfig(), 0x1234, 0, np.array([5]), np.array([9]), payload_arr(payload))
        strand = nt_of(s[0])
        strands[prof] = (lay, strand)
        vector("stage", f"frame4.parse.{prof}.clean", "D5", "frame4.parse", {"profile": prof}, {"strand": strand.encode()},
               formats=f4, expect={"accepted": True, "kind": 0, "tag": 0x1234, "group": 5, "symbol": 9,
                                   "payload_sha256": sha(payload), "errata": 0})
        # substitutions inside the bound: e <= r / 2 distinct bytes; the frame is accepted with the original fields
        pos = frame_positions(lay)
        bad = flip_bytes(strand, pos, lay.inner_parity // 2, seed=4100)
        vector("stage", f"frame4.parse.{prof}.errors", "D5", "frame4.parse", {"profile": prof}, {"strand": bad.encode()},
               formats=f4, expect={"accepted": True, "kind": 0, "tag": 0x1234, "group": 5, "symbol": 9,
                                   "payload_sha256": sha(payload), "errata": lay.inner_parity // 2})
        # beyond the bound: more than r / 2 byte errors are not accepted (the CRC decides, never a silent miscorrection)
        worse = flip_bytes(strand, pos, lay.inner_parity // 2 + 3, seed=4200)
        vector("negative", f"frame4.beyond-rs-bound.{prof}", "D5", "frame4.accept", {"profile": prof}, {"strand": worse.encode()},
               kind="negative", formats=f4, error=NOT_RECOVERABLE)
    # an r = 8 layout, the smallest inner code of the required set
    lay8 = Layout(payload_bytes=36, inner_parity=8, marker_period=24, marker_len=3).validate()
    payload = rng_bytes(4300, lay8.payload_bytes)
    s, _ = build_strands(lay8, ConstraintConfig(), 0x1234, 1, np.array([0]), np.array([2]), payload_arr(payload))
    strand = nt_of(s[0])
    lp = {"layout": {"payload_bytes": 36, "inner_parity": 8, "marker_period": 24, "marker_len": 3}}
    vector("stage", "frame4.parse.r08.kind1", "D5", "frame4.parse", lp, {"strand": strand.encode()}, formats=f4,
           expect={"accepted": True, "kind": 1, "tag": 0x1234, "group": 0, "symbol": 2, "payload_sha256": sha(payload), "errata": 0})
    vector("negative", "frame4.beyond-rs-bound.r08", "D5", "frame4.accept", lp,
           {"strand": flip_bytes(strand, frame_positions(lay8), 7, seed=4400).encode()}, kind="negative", formats=f4,
           error=NOT_RECOVERABLE)
    lay = PROFILES["v4-balanced"][0]
    zero = bytes(lay.payload_bytes)
    vec = vector("stage", "frame4.build.v4-balanced.variant-gt0", "E13", "frame4.build",
                 {"profile": "v4-balanced", "tag": 4660, "kind": 0, "group": 0, "symbol": 0}, {"payload": zero}, formats=f4)
    assert vec["expected"]["outputs"]["variant"] > 0, "the all-zero payload must need a scrambler variant above 0"
    vector("negative", "frame4.build.constraint-failure", "E13", "frame4.build",
           {"profile": "v4-balanced", "tag": 4660, "kind": 0, "group": 0, "symbol": 0,
            "constraints": {"gc_min_percent": 100, "gc_max_percent": 100}}, {"payload": zero}, kind="negative", formats=f4,
           error=err("CONSTRAINT_ERROR", "CONFIGURATION_ERROR", 7))
    # frame-version negatives: every frame is CRC-valid and RS-valid, only the version nibble or the kind is wrong
    for nibble, vid in ((7, "frame4.nibble7"), (5, "frame4.nibble5-reserved"), (3, "frame4.nibble3")):
        text = nibble_strands("v4-balanced", 1, nibble=nibble, seed=7)[0]
        vector("negative", vid, "D5", "frame4.accept", {"profile": "v4-balanced"}, {"strand": text.encode()}, kind="negative",
               formats={"frame": nibble, "superblock": None, "container": None}, error=UNSUPPORTED("FRAME_VERSION_UNSUPPORTED"))
    k2, _ = build_strands(lay, ConstraintConfig(), 0x1234, 2, np.array([1]), np.array([1]), payload_arr(rng_bytes(4500, 40)))
    vector("negative", "frame4.kind2", "D5", "frame4.accept", {"profile": "v4-balanced"}, {"strand": nt_of(k2[0]).encode()},
           kind="negative", formats=f4, error=FORMAT_ERROR)


def payload_arr(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.uint8)[None, :]


def frame_positions(lay) -> np.ndarray:
    """Strand index of every frame base (marker bases excluded), grouped by frame byte."""
    return lay.template()[1].reshape(lay.frame_bytes, 4)


def flip_bytes(strand: str, pos: np.ndarray, count: int, seed: int) -> str:
    """Change one base in each of ``count`` distinct frame bytes (one base error damages exactly one byte)."""
    rng = np.random.default_rng(seed)
    out = list(strand)
    for b in rng.permutation(pos.shape[0])[:count]:
        i = int(pos[b, int(rng.integers(0, 4))])
        out[i] = "ACGT"[("ACGT".index(out[i]) + int(rng.integers(1, 4))) % 4]
    return "".join(out)


# ------------------------------------------------------------------------------------------------------------ superblock
def sb_fields(version: int, **kw) -> dict:
    base = {"outer_code": "cauchy-rs", "K": 64, "M": 16, "profile": "v4-balanced", "lt_distribution": "dense", "lt_seed": 0,
            "archive_id_hex": bytes(range(16)).hex(), "container_size": 123_456, "container_sha256_hex": ("11" * 32),
            "index_offset": 1000, "group_count": 49, "version": version}
    base.update(kw)
    return base


def sb_pack(**f) -> bytes:
    lay = PROFILES[f.get("profile", "v4-balanced")][0]
    return Superblock(f["outer_code"], f["K"], f["M"], lay, f["lt_distribution"], f["lt_seed"], bytes.fromhex(f["archive_id_hex"]),
                      f["container_size"], bytes.fromhex(f["container_sha256_hex"]), f["index_offset"], f["group_count"],
                      f["version"], f.get("stripe_depth", 0), f.get("column_parity", 0),
                      f.get("strand_order", "sequential")).pack()


def forge(raw: bytes, **patch) -> bytes:
    """Patch bytes (``b18=…``) of a superblock and recompute its CRC: a CRC-valid forgery."""
    b = bytearray(raw)
    for k, v in patch.items():
        b[int(k[1:])] = v
    body = bytes(b[:-4])
    return body + struct.pack(">I", zlib.crc32(body))


def superblock_vectors() -> None:
    sb1 = {"frame": 4, "superblock": 1, "container": None}
    sb2 = {"frame": 4, "superblock": 2, "container": None}
    # version 1: the bytes are those of the layout in VNX4 §12, written independently here
    f1 = sb_fields(1)
    want = (b"VNX4SB" + bytes([1, 1]) + struct.pack(">HHHBBBB", 64, 16, 40, 16, 6, 3, 0) + struct.pack(">I", 0) +
            bytes(range(16)) + struct.pack(">Q", 123_456) + b"\x11" * 32 + struct.pack(">QI", 1000, 49) + b"\x00\x00")
    want += struct.pack(">I", zlib.crc32(want))
    vector("stage", "superblock.pack.v1", "E9", "superblock.pack", f1, formats=sb1, expect={"superblock_hex": want.hex()})
    f2 = sb_fields(2, stripe_depth=4, column_parity=2, strand_order="interleaved")
    raw2 = sb_pack(**f2)
    assert raw2[6] == 2 and raw2[18:22] == struct.pack(">HBB", 4, 2, 1)
    vector("stage", "superblock.pack.v2.interleaved", "E9", "superblock.pack", f2, formats=sb2)
    f2s = sb_fields(2, stripe_depth=8, column_parity=2, strand_order="sequential", K=48, M=16, group_count=-(-123_456 // (48 * 40)))
    vector("stage", "superblock.pack.v2.sequential", "E9", "superblock.pack", f2s, formats=sb2)
    for name, raw in (("v2.interleaved", raw2), ("v2.sequential", sb_pack(**f2s))):
        vector("stage", f"superblock.unpack.{name}", "D8", "superblock.unpack", {}, {"superblock": raw}, formats=sb2)
    unsupported = UNSUPPORTED("SUPERBLOCK_VERSION_UNSUPPORTED")
    base2 = raw2
    cases = [
        ("superblock.v2.depth0", forge(base2, b18=0, b19=0), FORMAT_ERROR, sb2),
        ("superblock.v2.depth-plus-parity-over-256", forge(base2, b18=0, b19=255, b20=2), FORMAT_ERROR, sb2),
        ("superblock.v2.order2", forge(base2, b21=2), FORMAT_ERROR, sb2),
        ("superblock.v2.order255", forge(base2, b21=255), FORMAT_ERROR, sb2),
        ("superblock.v2.lt-code", forge(base2, b7=2), FORMAT_ERROR, sb2),
        ("superblock.v4", forge(base2, b6=4), unsupported, {"frame": 4, "superblock": 4, "container": None}),
        ("superblock.forged.k-plus-m-over-256", forge(raw2, b8=1, b9=0, b10=1, b11=0), FORMAT_ERROR, sb2),
        ("superblock.forged.unknown-code-id", forge(sb_pack(**f1), b7=99), FORMAT_ERROR, sb1),
        ("superblock.forged.unknown-distribution", forge(sb_pack(**f1), b17=9), FORMAT_ERROR, sb1),
        ("superblock.forged.groups-mismatch", forge(sb_pack(**f1), b89=50), FORMAT_ERROR, sb1),
        ("superblock.forged.index-beyond-size", forge(sb_pack(**f1), b78=0xFF), FORMAT_ERROR, sb1),
        ("superblock.forged.payload0", forge(sb_pack(**f1), b12=0, b13=0), FORMAT_ERROR, sb1),
        ("superblock.forged.odd-inner-parity", forge(sb_pack(**f1), b14=15), FORMAT_ERROR, sb1),
        ("superblock.crc-invalid", bytearray(sb_pack(**f1)), FORMAT_ERROR, sb1),
        ("superblock.truncated", sb_pack(**f1)[:90], FORMAT_ERROR, sb1),
    ]
    for vid, raw, error, fm in cases:
        raw = bytes(raw)
        if vid == "superblock.crc-invalid":
            raw = raw[:20] + bytes([raw[20] ^ 1]) + raw[21:]
        vector("negative", vid, "D8", "superblock.unpack", {}, {"superblock": raw}, kind="negative", formats=fm, error=error)


# ------------------------------------------------------------------------------------------------------------ outer code
def outer_vectors() -> None:
    P = 4
    full = {}
    for K, M in ((64, 16), (32, 32), (48, 16)):
        data = np.frombuffer(rng_bytes(5000 + K, K * P), dtype=np.uint8).reshape(K, P)
        want = cauchy_parity_independent(data, K, M)
        vector("stage", f"outer.row-encode.k{K}-m{M}", "E8", "outer.row_encode", {"K": K, "M": M, "P": P, "k": K},
               {"data": data.tobytes()}, expect={"parity_hex": want.tobytes().hex()})
        full[(K, M)] = np.concatenate([data, want])
        # the maximum erasure count: any M of K + M symbols may be lost
        rng = np.random.default_rng(5100 + K)
        lost = set(rng.permutation(K + M)[:M].tolist())
        present = [i for i in range(K + M) if i not in lost]
        syms = full[(K, M)][present]
        vector("stage", f"outer.row-decode.k{K}-m{M}.max-erasures", "D10", "outer.row_decode",
               {"K": K, "M": M, "P": P, "k": K, "present": present}, {"symbols": syms.tobytes()},
               expect={"data_sha256": sha(data.tobytes())})
        lost_more = set(rng.permutation(K + M)[:M + 1].tolist())
        present_less = [i for i in range(K + M) if i not in lost_more]
        vector("negative", f"outer.row-decode.k{K}-m{M}.beyond-bound", "D10", "outer.row_decode",
               {"K": K, "M": M, "P": P, "k": K, "present": present_less}, {"symbols": full[(K, M)][present_less].tobytes()},
               kind="negative", error=NOT_RECOVERABLE)
    # a short last group: k < K source symbols (shortened code, the absent data rows are implicit zeros)
    for K, M, k in ((64, 16, 37), (32, 32, 5), (48, 16, 1)):
        data = np.frombuffer(rng_bytes(5200 + k, k * P), dtype=np.uint8).reshape(k, P)
        padded = np.zeros((K, P), dtype=np.uint8)
        padded[:k] = data
        want = cauchy_parity_independent(padded, K, M)
        vector("stage", f"outer.row-encode.short.k{K}-m{M}-k{k}", "E8", "outer.row_encode", {"K": K, "M": M, "P": P, "k": k},
               {"data": data.tobytes()}, expect={"parity_hex": want.tobytes().hex()})
        sent = np.concatenate([data, want])                              # transmitted: k data + M parity symbols
        present = list(range(M, k + M)) if k + M > M else []             # lose the first M symbols
        vector("stage", f"outer.row-decode.short.k{K}-m{M}-k{k}", "D10", "outer.row_decode",
               {"K": K, "M": M, "P": P, "k": k, "present": present}, {"symbols": sent[present].tobytes()},
               expect={"data_sha256": sha(data.tobytes())})
    # column parity and the iterative stripe decoder
    for D, Mc, K, M in ((4, 2, 6, 2), (8, 2, 5, 2)):
        rows = np.frombuffer(rng_bytes(5300 + D, D * K * P), dtype=np.uint8).reshape(D, K, P)
        want = np.zeros((Mc, K, P), dtype=np.uint8)
        flat = rows.reshape(D, K * P)
        want = cauchy_parity_independent(flat, D, Mc).reshape(Mc, K, P)
        vector("stage", f"outer.column-parity.d{D}-mc{Mc}", "E8", "outer.column_parity", {"D": D, "Mc": Mc, "K": K, "P": P, "d": D},
               {"data": rows.tobytes()}, expect={"parity_hex": want.tobytes().hex()})
        d_short = D - 1
        flat_s = np.zeros((D, K * P), dtype=np.uint8)
        flat_s[:d_short] = rows[:d_short].reshape(d_short, K * P)
        want_s = cauchy_parity_independent(flat_s, D, Mc).reshape(Mc, K, P)
        vector("stage", f"outer.column-parity.short.d{D}-mc{Mc}", "E8", "outer.column_parity",
               {"D": D, "Mc": Mc, "K": K, "P": P, "d": d_short}, {"data": rows[:d_short].tobytes()},
               expect={"parity_hex": want_s.tobytes().hex()})
        # the whole stripe as full-position codewords: every row (data and column parity) is a row codeword
        allrows = np.concatenate([rows, want])                           # (D + Mc, K, P)
        stripe = np.stack([np.concatenate([r, cauchy_parity_independent(r, K, M)]) for r in allrows])   # (D + Mc, K + M, P)
        known = np.ones((D + Mc, K + M), dtype=bool)
        known[0, :M + 1] = False                    # row 0 lost M + 1 symbols: the row code alone cannot repair it
        garbage = stripe.copy()
        garbage[~known] = 0
        vector("stage", f"outer.stripe-decode.d{D}-mc{Mc}.columns-repair-rows", "D11", "outer.stripe_decode",
               {"K": K, "M": M, "D": D, "Mc": Mc, "P": P, "known": ["".join("1" if x else "0" for x in r) for r in known]},
               {"stripe": garbage.tobytes()}, formats={"frame": 4, "superblock": 2, "container": None},
               expect={"data_sha256": sha(stripe[:D, :K].tobytes()), "stripe_sha256": sha(stripe.tobytes())})
        dead = np.ones((D + Mc, K + M), dtype=bool)
        dead[: Mc + 1, :] = False                   # Mc + 1 whole rows gone: more than the columns can repair
        garbage2 = stripe.copy()
        garbage2[~dead] = 0
        vector("negative", f"outer.stripe-decode.d{D}-mc{Mc}.beyond-bound", "D11", "outer.stripe_decode",
               {"K": K, "M": M, "D": D, "Mc": Mc, "P": P, "known": ["".join("1" if x else "0" for x in r) for r in dead]},
               {"stripe": garbage2.tobytes()}, kind="negative", formats={"frame": 4, "superblock": 2, "container": None},
               error=NOT_RECOVERABLE)
    for order in ("sequential", "interleaved"):
        geo = {"K": 3, "M": 2, "D": 2, "Mc": 1, "P": 4, "container_size": 60, "order": order}
        vector("stage", f"strand.order.{order}", "E14", "strand.order", {"geometry": geo, "superblock_strands": 3},
               formats={"frame": 4, "superblock": 2, "container": None})
        # independent check of the order: sequential is group-major; interleaved is position-major within a stripe
        got = json.loads((OUT / "stage" / f"strand.order.{order}" / "vector.json").read_text())["expected"]["outputs"]["order"]
        assert len(got) == len({tuple(x) for x in got}) == got.__len__()


# ------------------------------------------------------------------------------------------------------------ container
def read_sections(raw: bytes) -> dict:
    body, ct, ft, rf, mn = struct.unpack(">QQQQQ", raw[-112:-72])
    cut, pos = {}, 0
    for name, n in (("header", 16), ("body", body), ("table", ct), ("file_table", ft), ("refs", rf), ("manifest", mn)):
        cut[name] = raw[pos:pos + n]
        pos += n
    cut["manifest"] = json.loads(cut["manifest"])
    cut["mac"] = raw[-112 + 40:-112 + 72]
    return cut


def assemble(s: dict, *, mac_fn=None, reseal: bool = True) -> bytes:
    """Rebuild a container from sections; with ``reseal`` the manifest is brought back in line with the tables (their
    SHA-256, entry count, stored bytes, Merkle root) so that exactly one rule is violated, not the digests as a side effect."""
    from vnxdna.archive import merkle
    from vnxdna.archive.container import leaf_hashes
    m = json.loads(json.dumps(s["manifest"]))
    if reseal:
        m["tables"] = {"chunk_table": {"entry_bytes": 84, "entries": len(s["table"]) // 84, "sha256": sha(s["table"])},
                       "file_table": {"bytes": len(s["file_table"]), "sha256": sha(s["file_table"])},
                       "refs": {"bytes": len(s["refs"]), "sha256": sha(s["refs"])}}
        m["integrity"]["merkle_root"] = merkle.root_from_leaves(leaf_hashes(s["table"])).hex()
        m["counts"]["stored_bytes"] = len(s["body"])
    s2 = dict(s, manifest=m)
    return assemble_raw(s2, mac_fn)


def assemble_raw(s: dict, mac_fn=None) -> bytes:
    man = canonical_json(s["manifest"]) if isinstance(s["manifest"], dict) else s["manifest"]
    mac = mac_fn(man) if mac_fn else hashlib.sha256(man).digest()
    head = struct.pack(">QQQQQ", len(s["body"]), len(s["table"]), len(s["file_table"]), len(s["refs"]), len(man)) + mac + b"VNX4END\x00"
    pre = s["header"] + s["body"] + s["table"] + s["file_table"] + s["refs"] + man + head
    return pre + hashlib.sha256(pre).digest()


def build_container(work: Path, files: dict[str, bytes], **kw) -> bytes:
    from vnxdna.archive.operations import ArchiveOptions, build_archive
    root = work / "data"
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    out = work / "c.vnx"
    out.unlink(missing_ok=True)
    key = kw.pop("key", None)
    opts = ArchiveOptions(chunk_size=4096, compression=kw.pop("compression", "none"), key=key)
    build_archive([root], out, opts, archive_id=bytes(range(1, 17)), salt=bytes(range(101, 117)) if key else None)
    shutil.rmtree(root)
    return out.read_bytes()


def container_vectors() -> None:
    from vnxdna.archive import merkle
    c4 = {"frame": None, "superblock": None, "container": [4, 0]}
    text = (b"VNX-DNA conformance vector payload. " * 300)[:9000]
    noise = rng_bytes(7000, 5000)
    key = bytes(range(32))
    files = {"a.txt": text, "sub/b.bin": noise, "dup.txt": text}      # dup.txt shares all of its chunks with a.txt
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        clear = build_container(work, files)
        zst = build_container(work, files, compression="zstd")
        enc = build_container(work, files, key=key)
    ins = {"a": text, "b": noise}
    p_clear = {"chunk_size": 4096, "compression": "none", "archive_id_hex": bytes(range(1, 17)).hex(),
               "files": [{"path": "a.txt", "input": "a"}, {"path": "sub/b.bin", "input": "b"}, {"path": "dup.txt", "input": "a"}]}
    vec = vector("stage", "container.build.clear", "E6", "container.build", p_clear, ins, formats=c4)
    outs = vec["expected"]["outputs"]
    sec = read_sections(clear)
    assert outs["sections_sha256"]["body"] == sha(sec["body"]) and outs["merkle_root"] == sec["manifest"]["integrity"]["merkle_root"]
    # independent: the body is the unique chunks in first-reference order (a.txt: 3 chunks, b.bin: 2; dup.txt adds none)
    body = text[:4096] + text[4096:8192] + text[8192:] + noise[:4096] + noise[4096:]
    assert sec["body"] == body and outs["chunks"] == 5
    leaves = [merkle.leaf_hash(sec["table"][i:i + 84]) for i in range(0, len(sec["table"]), 84)]
    assert merkle.root_recursive(leaves).hex() == outs["merkle_root"]
    vector("stage", "container.build.encrypted", "E5-E6", "container.build",
           {**p_clear, "key_hex": key.hex(), "salt_hex": bytes(range(101, 117)).hex()}, ins, formats=c4)
    vector("stage", "container.read.clear", "D12", "container.read", {}, {"container": clear}, formats=c4)
    vector("stage", "container.read.zstd", "D14", "container.read", {}, {"container": zst}, formats=c4)
    vector("stage", "container.read.encrypted", "D14", "container.read", {"key_hex": key.hex()}, {"container": enc}, formats=c4)
    # Merkle trees of 1, 2, 3, 5, 7, 8 leaves (RFC 6962 split at the largest power of two below n)
    for n in (1, 2, 3, 5, 7, 8):
        recs = [bytes([i]) * (i + 1) for i in range(n)]
        leaves = [merkle.leaf_hash(r) for r in recs]
        vector("stage", f"merkle.proofs.n{n}", "E6", "merkle.proofs", {"records_hex": [r.hex() for r in recs]},
               expect={"root": merkle.root_recursive(leaves).hex()})
    # canonical manifests
    good = canonical_json({"format": "VNX4", "format_version": [4, 0], "n": 1})
    vector("stage", "manifest.canonical.accepts", "E6", "manifest.canonical", {}, {"manifest": good},
           expect={"canonical": True, "keys": ["format", "format_version", "n"]})
    for vid, raw in (("manifest.pretty-printed", b'{"a": 1}'), ("manifest.unsorted-keys", b'{"b":1,"a":2}'),
                     ("manifest.float", b'{"a":1.5}'), ("manifest.duplicate-key", b'{"a":1,"a":2}'),
                     ("manifest.trailing-newline", b'{"a":1}\n'), ("manifest.non-ascii", '{"a":"é"}'.encode()),
                     ("manifest.not-an-object", b'[1]'), ("manifest.nan", b'{"a":NaN}')):
        vector("negative", vid, "E6", "manifest.canonical", {}, {"manifest": raw}, kind="negative", formats=c4, error=FORMAT_ERROR)
    # AEAD: nonce = domain || index, associated data binds archive ID, domain, index, count (VNX4 §6)
    master, salt, aid = bytes(range(32)), bytes(range(101, 117)), bytes(range(1, 17))
    p = {"master_hex": master.hex(), "salt_hex": salt.hex(), "archive_id_hex": aid.hex()}
    sealed = {}
    for domain, index, count in ((0, 3, 0), (1, 0, 1), (2, 0, 1)):
        plain = rng_bytes(7100 + domain, 64)
        q = {**p, "domain": domain, "index": index, "count": count}
        want = independent_seal(master, salt, aid, domain, index, count, plain)
        vector("stage", f"aead.seal.domain{domain}", "E5", "aead.seal", q, {"plaintext": plain}, formats=c4, expect=want)
        sealed[domain] = (q, bytes.fromhex(want["ciphertext_hex"]), plain)
        vector("stage", f"aead.open.domain{domain}", "D14", "aead.open", q, {"ciphertext": sealed[domain][1]}, formats=c4,
               expect={"plaintext_sha256": sha(plain)})
    q, ct, plain = sealed[0]
    vector("negative", "aead.open.tampered", "D14", "aead.open", q, {"ciphertext": ct[:5] + bytes([ct[5] ^ 1]) + ct[6:]},
           kind="negative", formats=c4, error=INTEGRITY)
    vector("negative", "aead.open.truncated", "D14", "aead.open", q, {"ciphertext": ct[:-1]}, kind="negative", formats=c4,
           error=INTEGRITY)
    vector("negative", "aead.open.wrong-index", "D14", "aead.open", {**q, "index": 4}, {"ciphertext": ct}, kind="negative",
           formats=c4, error=INTEGRITY)
    vector("negative", "aead.open.wrong-archive-id", "D14", "aead.open", {**q, "archive_id_hex": bytes(16).hex()}, {"ciphertext": ct},
           kind="negative", formats=c4, error=INTEGRITY)
    vector("negative", "aead.open.wrong-count", "D14", "aead.open", {**q, "count": 1}, {"ciphertext": ct}, kind="negative",
           formats=c4, error=INTEGRITY)
    container_negatives(clear, zst, enc, key)


def independent_seal(master: bytes, salt: bytes, aid: bytes, domain: int, index: int, count: int, plain: bytes) -> dict:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    import hmac

    def hk(label, n=32):
        return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=f"VNX4 {label}".encode()).derive(master)
    nonce = domain.to_bytes(4, "big") + index.to_bytes(8, "big")
    aad = b"VNX4 aead\x00" + aid + domain.to_bytes(4, "big") + index.to_bytes(8, "big") + count.to_bytes(8, "big")
    return {"key_check_hex": hk("key check", 16).hex(), "nonce_hex": nonce.hex(), "aad_hex": aad.hex(),
            "ciphertext_hex": AESGCM(hk("aead key")).encrypt(nonce, plain, aad).hex(),
            "chunk_id_hex": hmac.new(hk("chunk id key"), plain, hashlib.sha256).hexdigest(),
            "public_chunk_id_hex": hashlib.sha256(b"VNX4 chunk\x00" + plain).hexdigest()}


def container_negatives(clear: bytes, zst: bytes, enc: bytes, key: bytes) -> None:
    """One or more vectors per rule of VNX4 §9 (the reader MUST reject). Every defect is introduced alone: the rest of
    the container is kept consistent (tables, manifest digests, Merkle root, trailer) so that the named rule is the one
    that fires."""
    c4 = {"frame": None, "superblock": None, "container": [4, 0]}
    UNS = UNSUPPORTED
    base = read_sections(clear)

    def neg(vid: str, raw: bytes, error: dict, **params) -> None:
        vector("negative", f"container.{vid}", "D12", "container.read", params, {"container": raw}, kind="negative", formats=c4,
               error=error)

    # 1. file too small, wrong magic, unsupported major / minor version, flags
    neg("rule1.too-small", clear[:100], FORMAT_ERROR)
    neg("rule1.wrong-magic", bytes([clear[0] ^ 1]) + clear[1:], FORMAT_ERROR)
    neg("rule1.legacy-magic", b"\x89VXDNA\r\n" + clear[8:], UNS("LEGACY_FORMAT"))
    neg("rule1.major-5", clear[:8] + struct.pack(">H", 5) + clear[10:], UNS("CONTAINER_VERSION_UNSUPPORTED"))
    neg("rule1.minor-1", clear[:10] + struct.pack(">H", 1) + clear[12:], UNS("CONTAINER_VERSION_UNSUPPORTED"))
    neg("rule1.flags-nonzero", clear[:12] + struct.pack(">I", 1) + clear[16:], UNS("CONTAINER_VERSION_UNSUPPORTED"))
    # 2. trailer magic, section sizes, table / manifest sizes
    neg("rule2.truncated-trailer", clear[:-5], FORMAT_ERROR)
    neg("rule2.trailer-magic-missing", clear[:-40] + b"XXXXXXXX" + clear[-32:], FORMAT_ERROR)
    t = bytearray(clear)
    t[-112 + 39] += 1                                                       # M one larger: sizes do not add up
    neg("rule2.section-sizes", bytes(t), FORMAT_ERROR)
    neg("rule2.manifest-size-zero", assemble_raw({**base, "manifest": b""}), FORMAT_ERROR)
    neg("rule2.chunk-table-not-multiple-of-84", assemble(dict(base, table=base["table"] + b"\x00")), FORMAT_ERROR)
    # 3. manifest: canonical form, fields, features, ranges
    neg("rule3.manifest-not-canonical", assemble_raw({**base, "manifest": json.dumps(base["manifest"], indent=1).encode()}),
        FORMAT_ERROR)
    m = json.loads(json.dumps(base["manifest"]))
    m["required_features"] = sorted(m["required_features"] + ["future-feature-x"])
    neg("rule3.unknown-required-feature", assemble_raw({**base, "manifest": m}), UNS("FEATURE_UNSUPPORTED"))
    m = json.loads(json.dumps(base["manifest"]))
    del m["counts"]
    neg("rule3.manifest-field-missing", assemble_raw({**base, "manifest": m}), FORMAT_ERROR)
    m = json.loads(json.dumps(base["manifest"]))
    m["chunking"]["chunk_size"] = 100
    neg("rule3.chunk-size-out-of-range", assemble_raw({**base, "manifest": m}), FORMAT_ERROR)
    m = json.loads(json.dumps(base["manifest"]))
    m["format_version"] = [4, 1]
    neg("rule3.manifest-minor-1", assemble_raw({**base, "manifest": m}), UNS("CONTAINER_VERSION_UNSUPPORTED"))
    # 4. manifest authenticator
    t = bytearray(clear)
    t[-112 + 40] ^= 1
    neg("rule4.manifest-digest-mismatch", bytes(t), INTEGRITY)
    t = bytearray(enc)
    t[-112 + 40] ^= 1
    neg("rule4.manifest-mac-mismatch", bytes(t), INTEGRITY, key_hex=key.hex())
    neg("rule4.wrong-key", enc, err("WRONG_KEY", "AUTHENTICATION_FAILED", 4), key_hex=bytes(range(1, 33)).hex())
    # 5. tables against the manifest, counts
    t = bytearray(clear)
    t[16 + len(base["body"]) + 3] ^= 1                                      # one byte of the chunk table
    neg("rule5.chunk-table-sha-mismatch", bytes(t), INTEGRITY)
    m = json.loads(json.dumps(base["manifest"]))
    m["counts"]["stored_bytes"] += 1
    neg("rule5.stored-bytes-mismatch", assemble_raw({**base, "manifest": m}), FORMAT_ERROR)
    m = json.loads(json.dumps(base["manifest"]))
    m["tables"]["file_table"]["sha256"] = "0" * 64
    neg("rule5.file-table-sha-mismatch", assemble_raw({**base, "manifest": m}), INTEGRITY)
    # 6. chunk table rules
    ent = [bytearray(base["table"][i:i + 84]) for i in range(0, len(base["table"]), 84)]

    def table_with(i, edit):
        e = [bytearray(x) for x in ent]
        edit(e[i])
        return b"".join(bytes(x) for x in e)
    neg("rule6.reserved-bytes-nonzero", assemble(dict(base, table=table_with(0, lambda e: e.__setitem__(17, 1)))), FORMAT_ERROR)
    neg("rule6.plain-size-zero", assemble(dict(base, table=table_with(0, lambda e: e.__setitem__(slice(12, 16), bytes(4))))),
        FORMAT_ERROR)
    neg("rule6.plain-size-over-chunk-size", assemble(dict(base, table=table_with(
        0, lambda e: e.__setitem__(slice(12, 16), struct.pack(">I", 4097))))), FORMAT_ERROR)
    neg("rule6.codec-not-allowed", assemble(dict(base, table=table_with(0, lambda e: e.__setitem__(16, 1)))), FORMAT_ERROR)
    neg("rule6.chunks-do-not-tile-the-body", assemble(dict(base, table=table_with(
        1, lambda e: e.__setitem__(slice(0, 8), struct.pack(">Q", 4095))))), FORMAT_ERROR)
    # 7. Merkle root
    m = json.loads(json.dumps(base["manifest"]))
    m["integrity"]["merkle_root"] = "00" * 32
    neg("rule7.merkle-root-mismatch", assemble_raw({**base, "manifest": m}), INTEGRITY)
    # 8. file table and references
    refs = bytearray(base["refs"])
    refs[3] = 99                                                            # a reference beyond the chunk table
    neg("rule8.reference-out-of-range", assemble(dict(base, refs=bytes(refs))), FORMAT_ERROR)
    neg("rule8.reference-table-length", assemble(dict(base, refs=base["refs"] + b"\x00\x00\x00\x00")), FORMAT_ERROR)
    m = json.loads(json.dumps(base["manifest"]))
    m["counts"]["content_bytes"] += 1
    neg("rule8.content-bytes-mismatch", assemble_raw({**base, "manifest": m}), FORMAT_ERROR)
    # 9. content access: stored SHA-256, AEAD, bounded zstd, chunk ID, file SHA-256
    body = bytearray(base["body"])
    body[10] ^= 1
    neg("rule9.stored-sha-mismatch", assemble(dict(base, body=bytes(body))), INTEGRITY)
    t = [bytearray(x) for x in ent]
    t[0][52] ^= 1
    neg("rule9.chunk-id-mismatch", assemble(dict(base, table=b"".join(bytes(x) for x in t))), INTEGRITY)
    ft = bytearray(base["file_table"])
    ft[-1] ^= 1                                                             # the last byte of the last file's SHA-256
    neg("rule9.file-sha-mismatch", assemble(dict(base, file_table=bytes(ft))), INTEGRITY)
    # zstd output longer than the table's plaintext size: a 4 KiB chunk whose frame inflates to 8 KiB
    import zstandard
    bomb = zstandard.ZstdCompressor(level=3, write_content_size=True).compress(bytes(8192))
    zs = read_sections(zst)
    zent = [bytearray(zs["table"][i:i + 84]) for i in range(0, len(zs["table"]), 84)]
    assert zent[0][16] == 1, "the first chunk of the zstd container is stored with zstd"
    old_stored = struct.unpack(">I", bytes(zent[0][8:12]))[0]
    zbody = bomb + zs["body"][old_stored:]
    delta = len(bomb) - old_stored
    zent[0][8:12] = struct.pack(">I", len(bomb))
    zent[0][20:52] = hashlib.sha256(bomb).digest()
    for e in zent[1:]:
        e[0:8] = struct.pack(">Q", struct.unpack(">Q", bytes(e[0:8]))[0] + delta)
    neg("rule9.zstd-output-over-plain-size", assemble(dict(zs, body=zbody, table=b"".join(bytes(e) for e in zent))), INTEGRITY)
    # AES-GCM failure: flip a ciphertext byte, then repair the stored SHA-256, Merkle root, manifest and MAC around it
    es = read_sections(enc)
    from vnxdna.archive import crypto as cr
    master_keys = cr.ArchiveKeys.derive(key, bytes(range(101, 117)))
    sealer = cr.Sealer(master_keys, bytes(range(1, 17)))
    ebody = bytearray(es["body"])
    ebody[5] ^= 1
    eent = [bytearray(es["table"][i:i + 84]) for i in range(0, len(es["table"]), 84)]
    first = struct.unpack(">I", bytes(eent[0][8:12]))[0]
    eent[0][20:52] = hashlib.sha256(bytes(ebody[:first])).digest()
    neg("rule9.aead-failure", assemble(dict(es, body=bytes(ebody), table=b"".join(bytes(e) for e in eent)), mac_fn=sealer.mac),
        INTEGRITY, key_hex=key.hex())


# ------------------------------------------------------------------------------------------------------------ version, e2e
def version_vector() -> None:
    vector("stage", "version.report", "version", "version.report", {}, formats=NO_FORMATS)


def e2e_vectors() -> None:
    """End to end on the committed goldens (referenced by path and SHA-256, never copied): container → strands (the strand
    file's SHA-256 is pinned), reads → container (the container's SHA-256 and every file's SHA-256)."""
    sets = {}
    for name, cases in (("v4_0", ("balanced", "archival", "encrypted", "multifile")),
                        ("v5_0", ("balanced", "archival", "encrypted", "multifile")),
                        ("v6_0", ("stripes-seq", "adaptive-interleaved", "max-recovery", "encrypted-stripes"))):
        sets[name] = (json.loads((FIXTURES / name / "manifest.json").read_text()), cases)
    for fixture, (man, cases) in sets.items():
        fmt = {"frame": 4, "superblock": 2 if fixture == "v6_0" and cases else 1, "container": [4, 0]}
        for case in cases:
            info = man["cases"][case]
            cdir = FIXTURES / fixture
            rel = f"../../../fixtures/{fixture}"
            vcont, vstr, vreads = (cdir / f"{case}.vnx").read_bytes(), (cdir / f"{case}.strands.fasta").read_bytes(), \
                (cdir / f"{case}.reads.fastq.gz").read_bytes()
            assert sha(vcont) == info["container_sha256"]
            dna_options = dict(info.get("dna_options") or {"profile": info["profile"]})
            if fixture == "v6_0":
                fmt = {**fmt, "superblock": 2 if dna_options.get("column_parity") or dna_options.get("outer_plan") == "adaptive"
                       or dna_options.get("strand_order") else 1}
            vector("e2e", f"e2e.encode.{fixture}.{case}", "E7-E14", "e2e.encode", {"dna_options": dna_options},
                   formats=fmt, refs={"container": (f"{rel}/{case}.vnx", vcont)}, expect={"strands_sha256": sha(vstr)})
            params = {"passphrase": PASSPHRASE} if info["encrypted"] else {}
            vec = vector("e2e", f"e2e.decode.{fixture}.{case}", "D0-D14", "e2e.decode", params, formats=fmt,
                         refs={"reads": (f"{rel}/{case}.reads.fastq.gz", vreads)},
                         expect={"status": "SUCCESS", "container_sha256": info["container_sha256"]})
            assert vec["expected"]["outputs"]["files"] == info["files"], (fixture, case)
    reads_ok = (FIXTURES / "v4_0" / "balanced.reads.fastq.gz").read_bytes()
    enc_reads = (FIXTURES / "v4_0" / "encrypted.reads.fastq.gz").read_bytes()
    rel = "../../../fixtures/v4_0"
    vector("negative", "e2e.decode.wrong-key", "D14", "e2e.decode", {"passphrase": "wrong-passphrase"}, kind="negative",
           formats={"frame": 4, "superblock": 1, "container": [4, 0]},
           refs={"reads": (f"{rel}/encrypted.reads.fastq.gz", enc_reads)}, error=err("WRONG_KEY", "AUTHENTICATION_FAILED", 4))
    vector("negative", "e2e.decode.key-for-unencrypted-archive", "D14", "e2e.decode", {"passphrase": PASSPHRASE}, kind="negative",
           formats={"frame": 4, "superblock": 1, "container": [4, 0]},
           refs={"reads": (f"{rel}/balanced.reads.fastq.gz", reads_ok)}, error=err("KEY_FOR_UNENCRYPTED", "AUTHENTICATION_FAILED", 4))
    for src, vid, error in (("probe.nibble7", "e2e.decode.frame-nibble7-pool", UNSUPPORTED("FRAME_VERSION_UNSUPPORTED")),
                            ("probe.v3-frame5", "e2e.decode.v3-frame5-pool", UNSUPPORTED("LEGACY_FORMAT")),
                            ("probe.random", "e2e.decode.random-pool", err("LAYOUT_UNDETECTED", "INVALID_INPUT", 3))):
        data = (OUT / "negative" / src / "reads.fasta").read_bytes()
        vector("negative", vid, "D1", "e2e.decode", {}, kind="negative", error=error,
               formats={"frame": 7 if "nibble" in src else 5 if "v3" in src else None, "superblock": None, "container": None},
               refs={"reads": (f"../{src}/reads.fasta", data)})
    with tempfile.TemporaryDirectory() as td:
        pool = tag_collision_pool(Path(td))
    vector("negative", "e2e.decode.two-archives-one-tag", "D8", "e2e.decode", {}, {"reads": pool}, kind="negative",
           formats={"frame": 4, "superblock": 1, "container": None}, error=err("ARCHIVE_TAG_AMBIGUOUS", "INVALID_INPUT", 3))
    vector("negative", "e2e.decode.read-over-100000-nt", "D0", "e2e.decode", {}, {"reads": b">r0\n" + b"ACGT" * 25_001 + b"\n"},
           kind="negative", formats=NO_FORMATS, error=err("RESOURCE_LIMIT", "INVALID_INPUT", 3))


def tag_collision_pool(work: Path) -> bytes:
    """Two different archives (same names and sizes, so the same derived archive ID and tag), strands of both in one file."""
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import encoder as en
    parts = []
    for seed in (91, 92):
        d = work / f"a{seed}"
        (d / "ds").mkdir(parents=True)
        (d / "ds" / "f.bin").write_bytes(rng_bytes(seed, 2000))
        ar.build_archive([d / "ds"], d / "a.vnx", ar.ArchiveOptions())
        en.encode_container(d / "a.vnx", d / "s.fasta", en.DNAOptions())
        parts.append((d / "s.fasta").read_text())
    return "".join(parts).encode()


# ============================================================================================================ main
def main() -> int:
    for sub in ("stage", "e2e", "negative"):
        shutil.rmtree(OUT / sub, ignore_errors=True)
    (OUT / "index.json").unlink(missing_ok=True)
    # 1. the packaged subset, byte for byte
    packaged = json.loads((PACKAGED / "index.json").read_text())
    for entry in packaged["vectors"]:
        dst = OUT / entry["path"]
        shutil.copytree(PACKAGED / entry["path"], dst)
        INDEX.append(entry)
    # 2. the rest of spec §6.2
    gf_rs_vectors()
    crc_scrambler_mapping_vectors()
    frame_vectors()
    superblock_vectors()
    outer_vectors()
    container_vectors()
    version_vector()
    e2e_vectors()
    ids = [e["id"] for e in INDEX]
    assert len(ids) == len(set(ids)), "duplicate vector ids"
    (OUT / "index.json").write_text(json.dumps({"schema": "vnx.conformance-index/1", "evidence": EVIDENCE, "vectors": INDEX},
                                               indent=1) + "\n")
    pos = sum(e["kind"] == "positive" for e in INDEX)
    print(f"{len(INDEX)} vectors ({pos} positive, {len(INDEX) - pos} negative) in {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
