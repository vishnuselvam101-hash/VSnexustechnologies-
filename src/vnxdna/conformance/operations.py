"""Conformance operations (spec §6.1): named functions over byte strings, one per vector ``operation``.

Each takes the parsed ``vector.json`` and the vector directory and returns a JSON-able dict of outputs. An anticipated
failure is raised as a :class:`~vnxdna.core.errors.VNXError`; the runner turns it into the observed ``error`` (code,
category, exit code, retryable). Nothing here keeps state between vectors, and every operation is deterministic.

The operations are the product's own stage functions wrapped to bytes-in, bytes-out; the expected values live only in the
vector files, which are frozen (``tests/conformance/generate_vectors.py`` documents how they were produced).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np

_ASCII = np.frombuffer(b"ACGT", dtype=np.uint8)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_input(vdir: Path, ref: dict) -> bytes:
    """The bytes of one vector input; the recorded SHA-256 is checked first, so a swapped file is an error, not a result."""
    data = (vdir / ref["path"]).read_bytes()
    if "sha256" in ref and _sha(data) != ref["sha256"]:
        raise ValueError(f"input {ref['path']} does not match its recorded SHA-256")
    return data


def _inp(vec: dict, vdir: Path, name: str) -> bytes:
    return read_input(vdir, vec["inputs"][name])


def _layout(params: dict):
    from vnxdna.dnaenc.layout import PROFILES, Layout
    if "profile" in params:
        return PROFILES[params["profile"]][0]
    return Layout(**params["layout"]).validate()


def _nt_string(codes: np.ndarray) -> str:
    return _ASCII[np.asarray(codes, dtype=np.uint8)].tobytes().decode()


def _strand_codes(text: str) -> np.ndarray:
    table = np.full(256, 4, dtype=np.uint8)
    for i, b in enumerate(b"ACGT"):
        table[b] = i
    return table[np.frombuffer(text.encode(), dtype=np.uint8)]


# ============================================================================================ GF(256), CRC, scrambler, mapping
def op_crc32(vec, vdir):
    from vnxdna.core.crc import crc32_rows
    rows = np.frombuffer(bytes.fromhex(vec["params"]["rows_hex"]), dtype=np.uint8).reshape(-1, vec["params"]["row_bytes"])
    return {"crc32": [f"{int(c):08x}" for c in crc32_rows(rows)]}


def op_keystream(vec, vdir):
    from vnxdna.dnaenc.scrambler import keystreams
    p = vec["params"]
    return {"keystream_hex": keystreams(p["span"])[p["variant"]][: p["bytes"]].tobytes().hex()}


def op_scrambler_byte0(vec, vdir):
    """Byte 0 of the keystream of every variant 0 … 255 in one scrambler domain (spec §3.2). The VNX4 domain is the
    product's own keystream table; the two legacy domains (read, never written, by this release) are SHAKE-128(domain ‖ v)
    as §3.2 defines them, because the layer rules keep the legacy modules out of this package."""
    from vnxdna.dnaenc.scrambler import keystreams
    domain = vec["params"]["domain"]
    if domain == "VNX4 scrambler":
        table = keystreams(1)[:, 0]
    elif domain in ("VNX-DNA/4 scrambler", "VNX-DNA/5 scrambler"):
        table = [hashlib.shake_128(domain.encode() + bytes([v])).digest(1)[0] for v in range(256)]
    else:
        raise ValueError(f"unknown scrambler domain {domain!r}")
    return {"byte0_hex": bytes(int(x) for x in table).hex()}


def op_map(vec, vdir):
    from vnxdna.dnaenc.mapping import bytes_to_nt
    nt = bytes_to_nt(np.frombuffer(bytes.fromhex(vec["params"]["bytes_hex"]), dtype=np.uint8)[None, :])[0]
    return {"nt": _nt_string(nt)}


def op_markers_insert(vec, vdir):
    """Frame bases → strand with the rotating sync markers inserted (VNX4 §11)."""
    from vnxdna.dnaenc.mapping import bytes_to_nt
    from vnxdna.dnaenc.markers import MARKER_TABLES, insert_markers
    lay = _layout(vec["params"])
    frame = np.frombuffer(bytes.fromhex(vec["params"]["frame_hex"]), dtype=np.uint8)[None, :]
    strand = insert_markers(lay, bytes_to_nt(frame))[0]
    return {"strand": _nt_string(strand), "markers": lay.markers, "marker_table": MARKER_TABLES[lay.marker_len]}


def op_rs_encode(vec, vdir):
    from vnxdna.codec.codecs import InnerRS
    msg = np.frombuffer(_inp(vec, vdir, "message"), dtype=np.uint8)[None, :]
    return {"parity_hex": InnerRS(vec["params"]["r"]).parity(msg)[0].tobytes().hex()}


def op_rs_decode(vec, vdir):
    """Errors-and-erasures decoding of one codeword. Only vectors within the bound (2e + f <= r) are positive: beyond it a
    bounded-distance decoder may miscorrect, which is why the frame CRC decides (see ``frame4.accept``)."""
    from vnxdna.codec.codecs import InnerRS
    p = vec["params"]
    cw = np.frombuffer(_inp(vec, vdir, "codeword"), dtype=np.uint8)[None, :]
    er = None
    if p.get("erasures"):
        er = np.zeros(cw.shape, dtype=bool)
        er[0, p["erasures"]] = True
    fixed, ok, errata = InnerRS(p["r"]).decode(cw, er)
    return {"ok": bool(ok[0]), "corrected_hex": fixed[0].tobytes().hex(), "errata": int(errata[0])}


def op_rs_golden(vec, vdir):
    """The committed native-RS golden file (``tests/v6/native/native_rs_golden.json``, referenced, not copied). Only its
    within-bound cases are normative (2e + f <= r: the transmitted codeword is returned); beyond the bound the golden
    records this implementation's own answers, which an independent decoder need not reproduce, so they are counted but not
    compared."""
    from vnxdna.native import rs as native_rs
    golden = json.loads(_inp(vec, vdir, "golden"))
    checked, wrong = 0, []
    for i, c in enumerate(golden["cases"]):
        if not c["within_bound"]:
            continue
        recv = np.frombuffer(bytes.fromhex(c["received"]), dtype=np.uint8)[None, :]
        er = np.zeros(recv.shape, dtype=bool)
        er[0, c["erasures"]] = True
        fixed, ok, errata = native_rs.decode_batch(recv, c["nsym"], er)
        checked += 1
        if fixed[0].tobytes().hex() != c["corrected"] or bool(ok[0]) != c["ok"] or int(errata[0]) != c["errata"]:
            wrong.append(i)
    return {"within_bound_cases": checked, "beyond_bound_cases": len(golden["cases"]) - checked, "mismatches": wrong}


# ============================================================================================ frame 4
def op_frame4_build(vec, vdir):
    from vnxdna.dnaenc.constraints import ConstraintConfig
    from vnxdna.dnaenc.frame4 import build_strands
    p = vec["params"]
    lay = _layout(p)
    payload = np.frombuffer(_inp(vec, vdir, "payload"), dtype=np.uint8)[None, :]
    cfg = ConstraintConfig(**p.get("constraints", {}))
    strands, variant = build_strands(lay, cfg, p["tag"], p["kind"], np.array([p["group"]]), np.array([p["symbol"]]), payload)
    s = _nt_string(strands[0])
    return {"strand_sha256": _sha(s.encode()), "variant": int(variant[0])}


def _parse_strand(vec, vdir):
    from vnxdna.dnaenc.frame4 import decode_frames
    from vnxdna.dnaenc.mapping import nt_to_bytes
    from vnxdna.sync.template import strip_markers_exact
    lay = _layout(vec["params"])
    seq = _inp(vec, vdir, "strand").decode().strip()
    fb, _ = strip_markers_exact(lay, _strand_codes(seq)[None, :])
    frames = nt_to_bytes(np.minimum(fb, 3))
    return lay, frames, decode_frames(lay, frames)


def op_frame4_parse(vec, vdir):
    _, _, parsed = _parse_strand(vec, vdir)
    out = {"accepted": bool(parsed.ok[0])}
    if parsed.ok[0]:
        out.update(kind=int(parsed.kind[0]), tag=int(parsed.tag[0]), group=int(parsed.group[0]), symbol=int(parsed.symbol[0]),
                   payload_sha256=_sha(parsed.payload[0].tobytes()), errata=int(parsed.errata[0]))
    return out


def op_frame4_accept(vec, vdir):
    """Strict form of ``frame4.parse``: a frame that is not accepted is an error with a code (never a result).

    The inner RS decode and the CRC decide first, so a corrupted frame is never "repaired" into something it was not. A
    frame whose CRC verifies after correction but that is not accepted is classified from its decoded header: a version
    nibble other than 4 is ``FRAME_VERSION_UNSUPPORTED`` (exit 6), a kind above 1 is ``FORMAT_ERROR`` (exit 3). Any other
    rejected frame is ``INSUFFICIENT_REDUNDANCY`` (exit 5): it is lost, and the outer code has to make up for it."""
    from vnxdna.codec.codecs import InnerRS
    from vnxdna.core.crc import crc32_bytes_be, crc32_rows
    from vnxdna.core.errors import VNXDecodeError, VNXFormatError, VNXUnsupportedVersionError
    from vnxdna.core.version import FRAME_VERSION
    from vnxdna.dnaenc.layout import CRC_BYTES, HEADER_BYTES
    from vnxdna.dnaenc.scrambler import keystreams
    lay, frames, parsed = _parse_strand(vec, vdir)
    if parsed.ok[0]:
        return {"accepted": True, "kind": int(parsed.kind[0]), "tag": int(parsed.tag[0]), "group": int(parsed.group[0]),
                "symbol": int(parsed.symbol[0]), "payload_sha256": _sha(parsed.payload[0].tobytes())}
    fixed, rs_ok, _ = InnerRS(lay.inner_parity).decode(frames)
    p = lay.payload_bytes
    span = HEADER_BYTES - 1 + p + CRC_BYTES
    if rs_ok[0]:
        plain = fixed[0, 1:1 + span] ^ keystreams(span)[fixed[0, 0]]
        if (crc32_bytes_be(crc32_rows(plain[None, : 9 + p])) == plain[9 + p:]).all():
            if plain[0] >> 4 != FRAME_VERSION:
                raise VNXUnsupportedVersionError(f"frame version nibble {int(plain[0] >> 4)} is not supported",
                                                 code="FRAME_VERSION_UNSUPPORTED", stage="D5")
            raise VNXFormatError(f"frame kind {int(plain[0] & 15)} is not defined", stage="D5")
    raise VNXDecodeError("frame not accepted (inner code and CRC)", stage="D5")


# ============================================================================================ superblock
def _superblock_fields(sb) -> dict:
    out = {"version": sb.version, "K": sb.K, "M": sb.M, "repacked_sha256": _sha(sb.pack())}
    if sb.version == 2:
        out.update(stripe_depth=sb.stripe_depth, column_parity=sb.column_parity, strand_order=sb.strand_order)
    return out


def op_superblock_unpack(vec, vdir):
    from vnxdna.dnaenc.superblock import Superblock
    return _superblock_fields(Superblock.unpack(_inp(vec, vdir, "superblock")))


def op_superblock_pack(vec, vdir):
    """Fields → the 96 superblock bytes (version 1 or 2); also unpacks them again (round trip)."""
    from vnxdna.dnaenc.superblock import Superblock
    p = vec["params"]
    sb = Superblock(p["outer_code"], p["K"], p["M"], _layout(p), p.get("lt_distribution", "dense"), p.get("lt_seed", 0),
                    bytes.fromhex(p["archive_id_hex"]), p["container_size"], bytes.fromhex(p["container_sha256_hex"]),
                    p["index_offset"], p["group_count"], p["version"], p.get("stripe_depth", 0), p.get("column_parity", 0),
                    p.get("strand_order", "sequential"))
    raw = sb.pack()
    return {"superblock_hex": raw.hex(), "roundtrip": _superblock_fields(Superblock.unpack(raw))}


# ============================================================================================ outer code
def _rows(raw: bytes, count: int, width: int) -> np.ndarray:
    return np.frombuffer(raw, dtype=np.uint8).reshape(count, width)


def op_outer_row_encode(vec, vdir):
    """One data row of k <= K symbols (shortened code when k < K) → its M parity symbols (Cauchy RS, GF(256))."""
    from vnxdna.codec.outer import row_codewords
    p = vec["params"]
    K, M, P, k = p["K"], p["M"], p["P"], p["k"]
    data = np.zeros((1, K, P), dtype=np.uint8)
    data[0, :k] = _rows(_inp(vec, vdir, "data"), k, P)
    cw = row_codewords(data, K, M)[0]
    return {"parity_hex": cw[K:].tobytes().hex()}


def op_outer_row_decode(vec, vdir):
    """The surviving transmitted symbols of one row (``present`` lists their indices; the input holds them in order)
    → the k data symbols. More than M missing symbols is ``INSUFFICIENT_REDUNDANCY``."""
    from vnxdna.codec.codecs import CauchyRSCodec
    p = vec["params"]
    K, M, P, k = p["K"], p["M"], p["P"], p["k"]
    raw = _rows(_inp(vec, vdir, "symbols"), len(p["present"]), P)
    got = CauchyRSCodec(K, M).decode({idx: raw[i] for i, idx in enumerate(p["present"])}, k, P)
    return {"data_sha256": _sha(got.tobytes()), "data_hex": got.tobytes().hex()}


def op_outer_column_parity(vec, vdir):
    from vnxdna.codec.outer import column_parity_rows
    p = vec["params"]
    D, Mc, K, P, d = p["D"], p["Mc"], p["K"], p["P"], p["d"]
    data = _rows(_inp(vec, vdir, "data"), d * K, P).reshape(d, K, P)
    return {"parity_hex": column_parity_rows(data, D, Mc).tobytes().hex()}


def op_outer_stripe_decode(vec, vdir):
    """Iterative row/column erasure decoding of one stripe. Input: the full (D + Mc) x (K + M) x P symbol array (unknown
    entries arbitrary); ``known`` lists the verified (row, position) pairs as one string per row of 0/1. An erasure
    pattern the iteration cannot resolve is ``INSUFFICIENT_REDUNDANCY``."""
    from vnxdna.codec.outer import decode_stripe
    from vnxdna.core.errors import VNXDecodeError
    p = vec["params"]
    K, M, D, Mc, P = p["K"], p["M"], p["D"], p["Mc"], p["P"]
    cw = _rows(_inp(vec, vdir, "stripe"), (D + Mc) * (K + M), P).reshape(D + Mc, K + M, P)
    known = np.array([[c == "1" for c in row] for row in p["known"]], dtype=bool)
    out, now = decode_stripe(cw, known, K, M, D, Mc)
    if not now.all():
        raise VNXDecodeError(f"{int((~now).sum())} stripe symbols unrecoverable", stage="outer")
    return {"data_sha256": _sha(out[:D, :K].tobytes()), "stripe_sha256": _sha(out.tobytes())}


def op_strand_order(vec, vdir):
    from vnxdna.codec.outer import Geometry
    g = Geometry(**vec["params"]["geometry"]).validate()
    keys = g.order_keys()
    return {"strands": int(keys.shape[0]), "order": keys.tolist(), "superblock_slots": g.superblock_slots(
        vec["params"].get("superblock_strands", 0), g.data_strands())}


# ============================================================================================ superblock / frame level probes
def op_probe(vec, vdir):
    from vnxdna.recovery.options import DecodeOptions
    from vnxdna.recovery.probe import detect_layout
    data = _inp(vec, vdir, "reads")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / Path(vec["inputs"]["reads"]["path"]).name
        p.write_bytes(data)
        lay = detect_layout(p, DecodeOptions())
    return {"layout": lay.to_dict()}


# ============================================================================================ container
def _sections(path: Path) -> dict:
    from vnxdna.archive import container as ct
    _, (body, ctb, ft, rf, mn), _, _ = ct.read_header_trailer(path)
    raw = path.read_bytes()
    cuts, pos = {}, 0
    for name, n in (("header", ct.HEADER_BYTES), ("body", body), ("chunk_table", ctb), ("file_table", ft), ("refs", rf),
                    ("manifest", mn), ("trailer", ct.TRAILER_BYTES)):
        cuts[name] = raw[pos:pos + n]
        pos += n
    return cuts


def op_container_build(vec, vdir):
    """Files → container with a fixed archive ID and salt. The manifest carries the software version, so the answer lists
    the section digests that do not (header, body, tables, Merkle root) and the manifest with the version fields removed."""
    from vnxdna.archive.operations import ArchiveOptions, build_archive
    from vnxdna.core.util import canonical_json
    p = vec["params"]
    opts = ArchiveOptions(chunk_size=p["chunk_size"], compression=p.get("compression", "none"), level=p.get("level", 3),
                          key=bytes.fromhex(p["key_hex"]) if p.get("key_hex") else None, writer_provenance=True)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / p.get("root", "data")
        for f in p["files"]:
            target = root / f["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read_input(vdir, vec["inputs"][f["input"]]))
        out = Path(d) / "out.vnx"
        rep = build_archive([root], out, opts, archive_id=bytes.fromhex(p["archive_id_hex"]),
                            salt=bytes.fromhex(p["salt_hex"]) if p.get("salt_hex") else None)
        sec = _sections(out)
        m = json.loads(sec["manifest"])
        m["encoder"].pop("version", None)
        m["extensions"].pop("vnx", None)
        return {"archive_id": rep.archive_id, "merkle_root": rep.merkle_root, "chunks": rep.unique_chunks,
                "sections_sha256": {k: _sha(sec[k]) for k in ("header", "body", "chunk_table", "file_table", "refs")},
                "manifest_core_sha256": _sha(canonical_json(m)), "format_version": m["format_version"],
                "required_features": m["required_features"]}


def op_container_read(vec, vdir):
    """Open and fully verify a container (structure, manifest, Merkle root, whole-file digest, every chunk and file)."""
    from vnxdna.archive import container as ct
    from vnxdna.archive.operations import verify_container
    p = vec["params"]
    key = bytes.fromhex(p["key_hex"]) if p.get("key_hex") else None
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "in.vnx"
        path.write_bytes(_inp(vec, vdir, "container"))
        c = ct.open_container(path, key=key, passphrase=p.get("passphrase"))
        report = verify_container(path, key=key, passphrase=p.get("passphrase"))
        files = [{"path": r.path, "type": int(r.type), "size": r.size, "sha256": r.sha256.hex()} for r in c.files]
        return {"status": report["status"], "archive_id": c.manifest["archive_id"],
                "merkle_root": c.manifest["integrity"]["merkle_root"], "encrypted": c.encrypted, "files": files}


def op_manifest_canonical(vec, vdir):
    from vnxdna.core.util import parse_canonical_json
    obj = parse_canonical_json(_inp(vec, vdir, "manifest"), "manifest")
    return {"canonical": True, "keys": sorted(obj)}


def op_merkle(vec, vdir):
    """RFC 6962 root and the audit path of every leaf; each path is verified against the root."""
    from vnxdna.archive import merkle
    records = [bytes.fromhex(h) for h in vec["params"]["records_hex"]]
    leaves = [merkle.leaf_hash(r) for r in records]
    root = merkle.root_from_leaves(leaves)
    proofs = {}
    for i in range(len(leaves)):
        proof = merkle.inclusion_proof(leaves, i)
        if not merkle.verify_inclusion(leaves[i], i, len(leaves), proof, root):
            raise AssertionError(f"inclusion proof {i} does not verify")
        proofs[str(i)] = [h.hex() for h in proof]
    return {"root": root.hex(), "proofs": proofs}


def _sealer(p):
    from vnxdna.archive import crypto
    keys = crypto.ArchiveKeys.derive(bytes.fromhex(p["master_hex"]), bytes.fromhex(p["salt_hex"]))
    return keys, crypto.Sealer(keys, bytes.fromhex(p["archive_id_hex"]))


def op_aead_seal(vec, vdir):
    from vnxdna.archive import crypto
    p = vec["params"]
    keys, sealer = _sealer(p)
    data = _inp(vec, vdir, "plaintext")
    return {"key_check_hex": keys.key_check.hex(), "nonce_hex": crypto.nonce(p["domain"], p["index"]).hex(),
            "aad_hex": crypto.aad(bytes.fromhex(p["archive_id_hex"]), p["domain"], p["index"], p["count"]).hex(),
            "ciphertext_hex": sealer.seal(p["domain"], p["index"], p["count"], data).hex(),
            "chunk_id_hex": sealer.chunk_id(data).hex(), "public_chunk_id_hex": crypto.public_chunk_id(data).hex()}


def op_aead_open(vec, vdir):
    p = vec["params"]
    _, sealer = _sealer(p)
    plain = sealer.open(p["domain"], p["index"], p["count"], _inp(vec, vdir, "ciphertext"), "conformance item")
    return {"plaintext_sha256": _sha(plain)}


# ============================================================================================ version, end to end
#: Callables the caller of the runner provides for operations that need a layer-7 sibling (``vnxdna.sdk``): the runner may not
#: import it (layer rule: no cycle sdk -> conformance -> sdk). ``sdk.conformance`` registers ``"version"`` (``vnx version``).
SERVICES: dict = {}


def op_version_report(vec, vdir):
    """``vnx version`` validates against ``vnx.version/1`` and reports every axis the vector pins."""
    from vnxdna.core import schema
    from vnxdna.core.errors import VNXConfigurationError
    if "version" not in SERVICES:
        raise VNXConfigurationError("operation version.report needs the SDK: run it through `vnx conformance` or "
                                    "sdk.conformance()", code="CONFIGURATION_ERROR")
    doc = SERVICES["version"]()
    schema.validate(doc, "vnx.version/1")
    return {"schema": doc["schema"], "valid": True, "spec": doc["spec"], "container_read": doc["container"]["read"],
            "frame_read": doc["frame"]["read"], "frame_write": doc["frame"]["write"],
            "legacy_detected": doc["frame"]["legacy_detected"], "superblock_read": doc["superblock"]["read"],
            "codecs": doc["codecs"]}


def _plain_reads(vec, vdir, tmp: Path) -> Path:
    """The reads input as a file the decoder reads (gzip-stored fixtures are decompressed; the decoder reads no gzip)."""
    ref = vec["inputs"]["reads"]
    data = read_input(vdir, ref)
    name = Path(ref["path"]).name
    if name.endswith(".gz"):
        data, name = gzip.decompress(data), name[:-3]
    path = tmp / name
    path.write_bytes(data)
    return path


def op_e2e_encode(vec, vdir):
    """Container → strands: the strand file's SHA-256 and strand count."""
    from vnxdna.pipeline.encode import DNAOptions, encode_container
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.vnx"
        src.write_bytes(_inp(vec, vdir, "container"))
        rep = encode_container(src, Path(d) / "strands.fasta", DNAOptions(**vec["params"].get("dna_options", {})))
        return {"strands_sha256": _sha((Path(d) / "strands.fasta").read_bytes()), "strands": rep["strands"]}


def op_e2e_decode(vec, vdir):
    """Reads → container. SUCCESS is reported only with the container's SHA-256 and the verified files; anything else is
    the typed error the decoder raised, or a FAILURE/PARTIAL status turned into ``INSUFFICIENT_REDUNDANCY`` (no output)."""
    from vnxdna.archive.operations import list_container
    from vnxdna.core.errors import VNXDecodeError
    from vnxdna.pipeline.decode import decode_reads
    from vnxdna.recovery.options import DecodeOptions
    p = vec["params"]
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        reads = _plain_reads(vec, vdir, tmp)
        out = tmp / "out.vnx"
        kw = {"passphrase": p["passphrase"]} if p.get("passphrase") else {}
        if p.get("key_hex"):
            kw["key"] = bytes.fromhex(p["key_hex"])
        res = decode_reads(reads, out, DecodeOptions(**p.get("decode_options", {})), overwrite=True,
                           allow_unencrypted=bool(p.get("allow_unencrypted")), **kw)
        if res.status != "SUCCESS":
            raise VNXDecodeError(f"decode finished with status {res.status}", stage="outer")
        files = list_container(out, **kw) if (kw or not res.report.get("encrypted")) else []
        return {"status": res.status, "container_sha256": _sha(out.read_bytes()),
                "files": {r["path"]: r.get("sha256") for r in files if r.get("type") == "file"}}


OPERATIONS = {
    "crc32": op_crc32, "scrambler.keystream": op_keystream, "scrambler.byte0": op_scrambler_byte0,
    "mapping.bytes_to_nt": op_map, "markers.insert": op_markers_insert, "rs.encode": op_rs_encode, "rs.decode": op_rs_decode,
    "rs.golden": op_rs_golden,
    "frame4.build": op_frame4_build, "frame4.parse": op_frame4_parse, "frame4.accept": op_frame4_accept,
    "superblock.unpack": op_superblock_unpack, "superblock.pack": op_superblock_pack,
    "outer.row_encode": op_outer_row_encode, "outer.row_decode": op_outer_row_decode,
    "outer.column_parity": op_outer_column_parity, "outer.stripe_decode": op_outer_stripe_decode,
    "strand.order": op_strand_order, "probe": op_probe,
    "container.build": op_container_build, "container.read": op_container_read, "manifest.canonical": op_manifest_canonical,
    "merkle.proofs": op_merkle, "aead.seal": op_aead_seal, "aead.open": op_aead_open,
    "version.report": op_version_report, "e2e.encode": op_e2e_encode, "e2e.decode": op_e2e_decode,
}
