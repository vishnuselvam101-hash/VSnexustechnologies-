"""Property-based tests (Hypothesis): the core invariants over generated corpora."""
import hashlib
from collections import Counter

import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import TEST_KEY
from vnxdna.api import store_bytes
from vnxdna.channel import ChannelConfig, simulate
from vnxdna.container import vxdna
from vnxdna.container.builder import StoreOptions, build_container
from vnxdna.container.reader import ContainerReader, body_source, load_manifest
from vnxdna.ecc.cauchy import CauchyErasureCode
from vnxdna.storage.decoder import ReadsSource, scan_reads
from vnxdna.storage.encoder import encode_container, geometry_of

SETTINGS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])

payloads = st.one_of(
    st.binary(max_size=3000),                                                   # random / incompressible
    st.builds(lambda b, n: b * n, st.binary(min_size=1, max_size=8), st.integers(1, 800)),  # highly compressible
    st.just(b""), st.binary(min_size=1, max_size=1),
)
options = st.builds(
    lambda k, m, p, nsym, mapping, comp, cs: StoreOptions(chunk_size=cs, data_shards=k, parity_shards=m, payload_bytes=p,
                                                          inner_parity_bytes=nsym, mapping=mapping, compression=comp,
                                                          compression_level={"none": 0, "zlib": 6, "zstd": 3}[comp]),
    st.integers(1, 12), st.integers(0, 6), st.integers(8, 40), st.sampled_from([0, 2, 4, 8]),
    st.sampled_from(["2bit", "rotation3", "codebook8"]), st.sampled_from(["none", "zlib", "zstd"]), st.integers(64, 2048))


def _restore_container(blob, key=None):
    cf = vxdna.parse(blob)
    loaded = load_manifest(cf.manifest_bytes, key)
    return ContainerReader(loaded, body_source(loaded.manifest, cf.body)).read_all()[0]


@SETTINGS
@given(data=payloads, opts=options, encrypt=st.booleans())
def test_restore_store_is_identity(data, opts, encrypt):
    key = TEST_KEY if encrypt else None
    assert _restore_container(store_bytes(data, opts, key, "f"), key) == data


@SETTINGS
@given(data=payloads, opts=options)
def test_decode_encode_container_is_identity_and_encoding_is_deterministic(data, opts):
    c = build_container(data, opts, None, "f")
    e1 = encode_container(c.manifest, c.manifest_bytes, c.stored_chunks)
    assert e1.sequences == encode_container(c.manifest, c.manifest_bytes, c.stored_chunks).sequences
    scan = scan_reads(list(reversed(e1.sequences)), geometry_of(c.manifest))
    loaded = load_manifest(c.manifest_bytes, None)
    body, _ = ContainerReader(loaded, ReadsSource(scan, c.manifest)).read_body()
    assert vxdna.serialize(c.manifest_bytes, body) == vxdna.serialize(c.manifest_bytes, c.body)


@SETTINGS
@given(data=st.binary(min_size=1, max_size=4000), seed=st.integers(0, 2 ** 31), subs=st.integers(0, 3))
def test_recover_simulate_encode_within_guarantee(data, seed, subs):
    """Damage inside the guarantee: ≤ nsym/2 substitutions per read, ≤ M erasures per stripe, any order/orientation."""
    opts = StoreOptions(chunk_size=1024, data_shards=6, parity_shards=3, payload_bytes=20, inner_parity_bytes=8)
    c = build_container(data, opts, None, "f")
    e = encode_container(c.manifest, c.manifest_bytes, c.stored_chunks)
    rng = np.random.default_rng(seed)
    reads = []
    for s in e.sequences:
        codes = np.frombuffer(s.encode(), dtype=np.uint8).copy()
        pos = rng.choice(len(codes), subs, replace=False)
        for p in pos:
            codes[p] = ord(rng.choice([b for b in "ACGT" if ord(b) != codes[p]]))
        reads.append(codes.tobytes().decode())
    reads, _ = simulate(reads, ChannelConfig(seed=seed % (2 ** 32), shuffle=True, reverse_complement_rate=0.5))
    scan = scan_reads(reads, geometry_of(c.manifest))
    # erase up to M shards per stripe, chosen by the adversary
    victims = Counter()
    kept = {}
    for key, value in scan.candidates.items():
        tag, kind, stripe, shard = key
        if kind == 0 and victims[stripe] < 3 and rng.random() < 0.5:
            victims[stripe] += 1
            continue
        kept[key] = value
    scan.candidates.clear()
    scan.candidates.update(kept)
    loaded = load_manifest(c.manifest_bytes, None)
    assert ContainerReader(loaded, ReadsSource(scan, c.manifest)).read_all()[0] == data


@settings(max_examples=60, deadline=None)
@given(k=st.integers(1, 20), m=st.integers(0, 8), length=st.integers(1, 16), seed=st.integers(0, 10 ** 6))
def test_any_k_of_n_shards_reconstruct(k, m, length, seed):
    code = CauchyErasureCode(k, m)
    rng = np.random.default_rng(seed)
    data = rng.integers(0, 256, (1, k, length), dtype=np.uint8)
    full = np.concatenate([data, code.encode(data)], axis=1)
    present = np.zeros((1, k + m), dtype=bool)
    present[0, rng.choice(k + m, k, replace=False)] = True
    assert np.array_equal(code.decode(full, present), data)


@SETTINGS
@given(data=payloads)
def test_metadata_round_trip_records_true_values(data):
    c = build_container(data, StoreOptions(chunk_size=500), None, "name.bin")
    m = load_manifest(c.manifest_bytes, None).manifest
    assert m.content.size == len(data) and m.content.sha256 == hashlib.sha256(data).hexdigest()
    assert sum(ch.size for ch in m.content.chunks) == len(data)
