"""Fuzzing parsers and decoders: every outcome is exact recovery or a structured VNXDNAError."""
import json
import random

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import FAST, TEST_KEY, mixed_bytes
from vnxdna import api
from vnxdna.container import manifest as mf
from vnxdna.container import vxdna
from vnxdna.container.builder import build_container
from vnxdna.container.reader import ContainerReader, body_source, load_manifest
from vnxdna.dna.reads import read_sequences
from vnxdna.errors import VNXDNAError
from vnxdna.storage.decoder import ReadsSource, discover_geometry, recover_manifest_from_dna, scan_reads
from vnxdna.storage.encoder import encode_container

DATA = mixed_bytes(6000, 2)
PLAIN = api.store_bytes(DATA, FAST, None, "f")
SEALED = api.store_bytes(DATA, FAST, TEST_KEY, "f")
FUZZ = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _restore(blob, key):
    cf = vxdna.parse(blob)
    loaded = load_manifest(cf.manifest_bytes, key)
    return ContainerReader(loaded, body_source(loaded.manifest, cf.body)).read_all()[0]


def _mutate(blob: bytes, rng: random.Random, n: int) -> bytes:
    b = bytearray(blob)
    for _ in range(n):
        op = rng.randrange(4)
        i = rng.randrange(len(b)) if b else 0
        if op == 0 and b:
            b[i] ^= 1 << rng.randrange(8)
        elif op == 1 and b:
            del b[i:i + rng.randrange(1, 40)]
        elif op == 2:
            b[i:i] = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 20)))
        else:
            b = b[: rng.randrange(len(b) + 1)]
    return bytes(b)


@FUZZ
@given(seed=st.integers(0, 2 ** 32), n=st.integers(1, 6), sealed=st.booleans(),
       mode=st.sampled_from(["raw", "body-resealed", "manifest-resealed"]))
def test_mutated_containers_never_yield_wrong_data(seed, n, sealed, mode):
    """Resealed modes rebuild the file trailer so mutations reach the inner verification layers."""
    blob = SEALED if sealed else PLAIN
    key = TEST_KEY if sealed else None
    rng = random.Random(seed)
    if mode == "raw":
        candidate = _mutate(blob, rng, n)
    else:
        cf = vxdna.parse(blob)
        candidate = (vxdna.serialize(cf.manifest_bytes, _mutate(cf.body, rng, n)) if mode == "body-resealed"
                     else vxdna.serialize(_mutate(cf.manifest_bytes, rng, n), cf.body))
    try:
        out = _restore(candidate, key)
    except VNXDNAError:
        return
    assert out == DATA  # a mutation that happens to be harmless must still give the exact bytes


@FUZZ
@given(seed=st.integers(0, 2 ** 32))
def test_mutated_manifest_json_is_rejected_structurally(seed):
    rng = random.Random(seed)
    raw = json.loads(vxdna.parse(PLAIN).manifest_bytes)
    paths = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [i])
        paths.append(path)
    walk(raw, [])
    path = rng.choice([p for p in paths if p])
    parent = raw
    for step in path[:-1]:
        parent = parent[step]
    choice = rng.randrange(4)
    if choice == 0 and isinstance(parent, dict):
        parent.pop(path[-1])
    else:
        parent[path[-1]] = rng.choice([None, True, -1, 2 ** 70, "x", [], {}, 0, "0" * 64])
    try:
        load_manifest(mf.canonical_bytes(raw), None)
    except VNXDNAError:
        return
    # the value may coincide with the original (e.g. 0 -> 0); then the manifest must be unchanged
    assert mf.canonical_bytes(raw) == vxdna.parse(PLAIN).manifest_bytes


@FUZZ
@given(text=st.text(alphabet="ACGTN>@+\n acgtXYZ;\t", max_size=600))
def test_read_parser_and_scanner_accept_arbitrary_text(tmp_path_factory, text):
    path = tmp_path_factory.mktemp("r") / "reads.txt"
    path.write_text(text)
    try:
        seqs = read_sequences(path)
        geometry = discover_geometry(seqs)
        recover_manifest_from_dna(scan_reads(seqs, geometry))
    except VNXDNAError:
        return


def test_corrupted_reads_at_every_level_never_yield_wrong_data():
    c = build_container(DATA, FAST, None, "f")
    e = encode_container(c.manifest, c.manifest_bytes, c.stored_chunks)
    rng = random.Random(5)
    for trial in range(25):
        reads = []
        for s in e.sequences:
            s = list(s)
            for _ in range(rng.choice([0, 0, 1, 3, 8, 30])):
                s[rng.randrange(len(s))] = rng.choice("ACGTN")
            if rng.random() < 0.05:
                s = s[: rng.randrange(len(s))]
            reads.append("".join(s))
        rng.shuffle(reads)
        try:
            scan = scan_reads(reads, discover_geometry(reads))
            loaded = load_manifest(recover_manifest_from_dna(scan), None)
            out = ContainerReader(loaded, ReadsSource(scan, loaded.manifest)).read_all()[0]
        except VNXDNAError:
            continue
        assert out == DATA


@pytest.mark.parametrize("junk", [b"", b"\x00" * 100, b"{}", b'{"manifest":{}}', b">\n\n>\n", b"@x\nACGT\n+\n", "é".encode()])
def test_junk_inputs_give_structured_errors(tmp_path, junk):
    p = tmp_path / "junk"
    p.write_bytes(junk)
    for fn in (lambda: api.info(p), lambda: api.restore(p, tmp_path / "o"), lambda: api.decode(p, tmp_path / "o2"),
               lambda: api.encode(p, tmp_path / "o3")):
        with pytest.raises(VNXDNAError):
            fn()
    assert api.verify(p)["status"] == "FAIL" or p.stat().st_size == 0
