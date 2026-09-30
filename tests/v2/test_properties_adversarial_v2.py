"""Property tests and adversarial fuzzing for V2: round-trip laws, and no input ever yields silently wrong output."""
import hashlib
import json
import random

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from v2_support import FAST, KEY, mixed_bytes, write
from vnxdna.errors import VNXDNAError
from vnxdna.v2 import api, crypto
from vnxdna.v2 import manifest as mf
from vnxdna.v2.archive import restore_file, store_file
from vnxdna.v2.container import ContainerFileV2, header_bytes
from vnxdna.v2.encoder import encode_file
from vnxdna.v2.sequencing import SequencingConfig, sequence_file

SETTINGS = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


@SETTINGS
@given(data=st.binary(min_size=0, max_size=20_000), encrypted=st.booleans())
def test_restore_store_is_identity(tmp_path, data, encrypted):
    src = write(tmp_path / "in.bin", data)
    key = KEY if encrypted else None
    store_file(src, tmp_path / "a.vxdna", options=FAST, key=key, overwrite=True)
    restore_file(tmp_path / "a.vxdna", tmp_path / "o.bin", key=key, overwrite=True)
    assert (tmp_path / "o.bin").read_bytes() == data


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(size=st.integers(0, 12_000), seed=st.integers(0, 10_000))
def test_decode_encode_is_identity_on_containers(tmp_path, size, seed):
    write(tmp_path / "in.bin", mixed_bytes(size, seed))
    store_file(tmp_path / "in.bin", tmp_path / "a.vxdna", options=FAST, overwrite=True)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.vxs", overwrite=True, workers=1)
    api.decode(tmp_path / "s.vxs", tmp_path / "d.vxdna", overwrite=True, workers=1)
    assert (tmp_path / "d.vxdna").read_bytes() == (tmp_path / "a.vxdna").read_bytes()


@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(seed=st.integers(0, 10_000), coverage=st.sampled_from([3, 6, 10]))
def test_full_chain_restores_the_original_under_a_supported_channel(tmp_path, seed, coverage):
    data = mixed_bytes(15_000, seed)
    write(tmp_path / "in.bin", data)
    report = api.pipeline(tmp_path / "in.bin", tmp_path / "out.bin", work_dir=tmp_path / f"w{seed}", options=FAST,
                          channel=SequencingConfig(seed=seed, coverage=coverage, substitution_rate=0.001, insertion_rate=0.0003,
                                                   deletion_rate=0.0003, dropout_rate=0.02, reverse_complement_rate=0.5),
                          workers=1, overwrite=True)
    assert report["bytes_identical"] and (tmp_path / "out.bin").read_bytes() == data


def _reseal(path, mutate):
    """Rewrite a container with a mutated manifest/index and *valid* digests and trailer, to reach the inner checks."""
    cf = ContainerFileV2.open(path)
    body = cf.path.read_bytes()[16:16 + cf.body_bytes]
    raw = json.loads(cf.manifest_bytes)
    index = bytearray(cf.index_bytes)
    raw, index = mutate(raw, index)
    raw["chunk_index"]["sha256"] = hashlib.sha256(bytes(index)).hexdigest()
    payload = mf.digest_payload(raw)
    raw["seal"]["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest = mf.canonical_bytes(raw)
    blob = header_bytes() + body + manifest + bytes(index) + cf.plain_bytes
    tail = (len(body).to_bytes(8, "big") + len(manifest).to_bytes(4, "big") + len(index).to_bytes(4, "big")
            + len(cf.plain_bytes).to_bytes(4, "big") + bytes(4) + b"VXDNAEND")
    blob += tail
    return blob + hashlib.sha256(blob).digest()


def test_resealed_manifest_and_index_mutations_never_produce_wrong_output(tmp_path):
    data = mixed_bytes(30_000, seed=51)
    write(tmp_path / "in.bin", data)
    store_file(tmp_path / "in.bin", tmp_path / "a.vxdna", options=FAST)
    rng = random.Random(1)
    fields = ["stored_size", "chunk_count", "chunk_size", "profile", "created_at", "stored_sha256"]
    outcomes = {"refused": 0, "exact": 0}
    for trial in range(120):
        def mutate(raw, index, trial=trial):
            choice = rng.random()
            if choice < 0.5:
                pos = rng.randrange(len(index))
                index[pos] ^= 1 << rng.randrange(8)
            else:
                field = rng.choice(fields)
                raw[field] = rng.choice([0, 1, -1, 2**40, "x", None, [], raw[field]])
            return raw, index
        (tmp_path / "m.vxdna").write_bytes(_reseal(tmp_path / "a.vxdna", mutate))
        out = tmp_path / "o.bin"
        out.unlink(missing_ok=True)
        try:
            restore_file(tmp_path / "m.vxdna", out)
        except VNXDNAError:
            outcomes["refused"] += 1
            assert not out.exists()
        else:
            outcomes["exact"] += 1
            assert out.read_bytes() == data
    assert outcomes["refused"] > 60


def test_garbage_read_files_never_crash_the_decoder(tmp_path):
    rng = random.Random(2)
    for trial in range(25):
        lines = []
        for _ in range(rng.randrange(1, 40)):
            kind = rng.random()
            if kind < 0.3:
                lines.append(">" + "".join(rng.choice("ab12 :") for _ in range(rng.randrange(0, 10))))
            elif kind < 0.6:
                lines.append("".join(rng.choice("ACGTNXacgt@+") for _ in range(rng.randrange(0, 300))))
            else:
                lines.append("".join(rng.choice("ACGT") for _ in range(188)))
        (tmp_path / "g.fasta").write_text("\n".join(lines) + "\n")
        try:
            api.recover(tmp_path / "g.fasta", tmp_path / "o.bin", workers=1)
        except VNXDNAError:
            pass
        assert not (tmp_path / "o.bin").exists()


def test_extreme_channels_fail_detectably_never_silently(tmp_path):
    data = mixed_bytes(12_000, seed=52)
    write(tmp_path / "in.bin", data)
    store_file(tmp_path / "in.bin", tmp_path / "a.vxdna", options=FAST)
    encode_file(tmp_path / "a.vxdna", tmp_path / "s.fasta")
    extremes = [SequencingConfig(seed=1, coverage=1, coverage_model="fixed", dropout_rate=0.6),
                SequencingConfig(seed=2, coverage=3, substitution_rate=0.08),
                SequencingConfig(seed=3, coverage=3, insertion_rate=0.03, deletion_rate=0.03),
                SequencingConfig(seed=4, coverage=0.3)]
    for config in extremes:
        sequence_file(tmp_path / "s.fasta", tmp_path / "r.fastq", config, overwrite=True)
        out = tmp_path / "o.bin"
        try:
            api.recover(tmp_path / "r.fastq", out, workers=1, overwrite=True)
        except VNXDNAError:
            assert not out.exists()
        else:
            assert out.read_bytes() == data
        out.unlink(missing_ok=True)
