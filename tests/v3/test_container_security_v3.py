"""V3 regressions for the container, crypto and store fixes found in the V2 audit (docs/V3_AUDIT.md, C1–C12, S1–S2)."""
from __future__ import annotations

import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from v2_support import FAST, KEY, mixed_bytes, write
from vnxdna.errors import ConfigurationError, InvalidInputError, MetadataError, OutputError
from vnxdna.v2 import archive as arc
from vnxdna.v2 import container as cont
from vnxdna.v2 import manifest as mf
from vnxdna.v2.profiles import StoreOptionsV2, options_for

CS = 4096
RAW = options_for("balanced", chunk_size=CS, compression="none", compression_level=0)
HEADER = 16


class Stop(BaseException):
    """Simulated interruption (like SIGKILL between two chunks)."""


def stop_at(k: int):
    def progress(done: int, n: int) -> None:
        if done == k:
            raise Stop()
    return progress


def split(data: bytes) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    tail = data[-64:]
    body, m, i, j = (int.from_bytes(tail[a:b], "big") for a, b in ((0, 8), (8, 12), (12, 16), (16, 20)))
    return (data[:HEADER], data[HEADER:HEADER + body], data[HEADER + body:HEADER + body + m],
            data[HEADER + body + m:HEADER + body + m + i], data[HEADER + body + m + i:HEADER + body + m + i + j])


def build(head: bytes, body: bytes, m: bytes, i: bytes, j: bytes) -> bytes:
    tail = (len(body).to_bytes(8, "big") + len(m).to_bytes(4, "big") + len(i).to_bytes(4, "big") + len(j).to_bytes(4, "big")
            + bytes(4) + b"VXDNAEND")
    pre = head + body + m + i + j + tail
    return pre + hashlib.sha256(pre).digest()


def keep_mtime(path: Path, data: bytes) -> None:
    st = path.stat()
    path.write_bytes(data)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


# ---------------------------------------------------------------- C1: nonce reuse across two resumes
def test_a_second_resume_never_reuses_the_first_resumes_nonces(tmp_path):
    src, out = tmp_path / "in.bin", tmp_path / "a.vxdna"
    src.write_bytes(os.urandom(CS * 16))
    with pytest.raises(Stop):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, checkpoint_interval=4, progress=stop_at(6))
    with pytest.raises(Stop):  # resume 1 seals chunks 4.. in epoch 1 and stops before its first periodic checkpoint
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, resume=True, checkpoint_interval=4, progress=stop_at(7))
    ckpt = json.loads((tmp_path / "a.vxdna.partial.ckpt").read_text())
    assert ckpt["epoch"] == 1  # VNX-DNA 2.0 still said 0 here
    chunk4 = slice(HEADER + 4 * (CS + 16), HEADER + 5 * (CS + 16))
    c1, p1 = (tmp_path / "a.vxdna.partial").read_bytes()[chunk4], src.read_bytes()[4 * CS:5 * CS]
    data = bytearray(src.read_bytes())
    data[4 * CS:5 * CS] = os.urandom(CS)
    keep_mtime(src, bytes(data))  # changed input, same size and mtime (coarse-mtime file systems)
    with pytest.raises(Stop):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, resume=True, checkpoint_interval=4, progress=stop_at(5))
    c2, p2 = (tmp_path / "a.vxdna.partial").read_bytes()[chunk4], bytes(data[4 * CS:5 * CS])
    assert p1 != p2
    assert xor(c1[:CS], c2[:CS]) != xor(p1, p2), "same keystream: an AES-GCM nonce was reused"


def test_the_checkpoint_epoch_cannot_be_rolled_back_without_the_key(tmp_path):
    src, out = tmp_path / "in.bin", tmp_path / "a.vxdna"
    src.write_bytes(os.urandom(CS * 12))
    with pytest.raises(Stop):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, checkpoint_interval=4, progress=stop_at(6))
    with pytest.raises(Stop):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, resume=True, checkpoint_interval=4, progress=stop_at(9))
    path = tmp_path / "a.vxdna.partial.ckpt"
    state = json.loads(path.read_text())
    assert state["checkpoint_hmac"]
    body = {k: v for k, v in state.items() if k not in ("checkpoint_sha256", "checkpoint_hmac")}
    body["epoch"] = 0  # roll back and recompute the unkeyed digest, as someone with write access could
    forged = {**body, "checkpoint_sha256": hashlib.sha256(mf.canonical_bytes(body)).hexdigest(),
              "checkpoint_hmac": state["checkpoint_hmac"]}
    path.write_text(json.dumps(forged, sort_keys=True))
    with pytest.raises(InvalidInputError, match="not authenticated"):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, resume=True, checkpoint_interval=4)


# ---------------------------------------------------------------- C2: final records after a re-finalisation
def test_refinalising_a_changed_input_uses_a_new_epoch_for_the_sealed_records(tmp_path, monkeypatch):
    src, out = tmp_path / "in.bin", tmp_path / "a.vxdna"
    src.write_bytes(os.urandom(CS * 8))
    real = cont.publish

    def crash(tmp, target, *, overwrite):  # crash after the footer is fsynced, before the rename
        raise KeyboardInterrupt

    monkeypatch.setattr(cont, "publish", crash)
    with pytest.raises(KeyboardInterrupt):
        arc.store_file(src, out, options=RAW, key=KEY, workers=1, checkpoint_interval=4)
    monkeypatch.setattr(cont, "publish", real)
    j1 = split((tmp_path / "a.vxdna.partial").read_bytes())[4]
    before = src.read_bytes()
    data = bytearray(before)
    data[6 * CS:7 * CS] = os.urandom(CS)
    keep_mtime(src, bytes(data))
    arc.store_file(src, out, options=RAW, key=KEY, workers=1, resume=True, checkpoint_interval=4)
    j2 = split(out.read_bytes())[4]

    def plain_index(blob: bytes) -> bytes:  # the sealed plaintext index: (size, SHA-256) per chunk
        return b"".join(CS.to_bytes(4, "big") + hashlib.sha256(blob[c * CS:(c + 1) * CS]).digest() for c in range(8))

    pi1, pi2 = plain_index(before), plain_index(bytes(data))
    assert pi1 != pi2
    assert xor(j1[:-16], j2[:-16]) != xor(pi1, pi2), "same keystream: the sealed plaintext index reused a nonce"
    _, loaded = arc.open_container(out, KEY)
    assert mf.FEATURE_FINAL_SEAL_EPOCH in loaded.manifest.required_features
    arc.restore_file(out, tmp_path / "o.bin", key=KEY)
    assert (tmp_path / "o.bin").read_bytes() == bytes(data)


def test_fresh_archives_do_not_declare_the_final_epoch_feature(tmp_path):
    src = write(tmp_path / "in.bin", mixed_bytes(20_000, 1))
    arc.store_file(src, tmp_path / "a.vxdna", options=FAST, key=KEY, workers=1)
    _, loaded = arc.open_container(tmp_path / "a.vxdna", KEY)
    assert mf.FEATURE_FINAL_SEAL_EPOCH not in loaded.manifest.required_features  # byte-compatible with VNX-DNA 2.0 readers


# ---------------------------------------------------------------- C3/C4/C5: body length, stored SHA-256, truncation
@pytest.fixture
def encrypted_container(tmp_path):
    src = write(tmp_path / "in.bin", os.urandom(10_000))
    out = tmp_path / "a.vxdna"
    arc.store_file(src, out, options=options_for("balanced", chunk_size=CS), key=KEY, workers=1)
    return out


def test_a_padded_body_fails_verify_and_restore(tmp_path, encrypted_container):
    h, body, m, i, j = split(encrypted_container.read_bytes())
    padded = tmp_path / "pad.vxdna"
    padded.write_bytes(build(h, body + b"JUNK" * 1000, m, i, j))
    report = arc.verify_container(padded, key=KEY, workers=2)
    assert report["status"] == "FAIL" and report["exit_code"] == 3
    with pytest.raises(MetadataError, match="stored_size"):
        arc.restore_file(padded, tmp_path / "o.bin", key=KEY)
    assert not (tmp_path / "o.bin").exists()


def test_verify_checks_the_stored_body_sha256(encrypted_container):
    report = arc.verify_container(encrypted_container, key=KEY)
    names = {c["check"]: c["result"] for c in report["checks"]}
    assert names["stored-body-sha256"] == "PASS" and report["status"] == "PASS"


def test_a_truncated_body_yields_a_fail_report_not_an_exception(tmp_path, encrypted_container):
    h, body, m, i, j = split(encrypted_container.read_bytes())
    short = tmp_path / "short.vxdna"
    short.write_bytes(build(h, body[:-10], m, i, j))
    report = arc.verify_container(short, key=KEY, workers=3)
    assert report["status"] == "FAIL" and report["checks"]


def test_restore_still_detects_a_flipped_body_byte_even_with_a_resealed_trailer(tmp_path, encrypted_container):
    h, body, m, i, j = split(encrypted_container.read_bytes())
    bad = bytearray(body)
    bad[100] ^= 1
    flipped = tmp_path / "flip.vxdna"
    flipped.write_bytes(build(h, bytes(bad), m, i, j))
    with pytest.raises(Exception) as info:
        arc.restore_file(flipped, tmp_path / "o.bin", key=KEY)
    assert "chunk" in str(info.value).lower()
    assert not (tmp_path / "o.bin").exists()


# ---------------------------------------------------------------- C6: descriptor leak
def test_parallel_verify_does_not_leak_file_descriptors(encrypted_container):
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(40):
        arc.verify_container(encrypted_container, key=KEY, workers=4)
    assert len(os.listdir("/proc/self/fd")) <= before + 1


# ---------------------------------------------------------------- C8 / C12 / S1: store validation
def test_a_non_utf8_file_name_is_stored_with_replacement_characters(tmp_path):
    name = os.fsdecode(b"bad\xffname.bin")
    src = write(tmp_path / name, b"hello")
    r = arc.store_file(src, tmp_path / "a.vxdna", options=FAST, workers=1)
    assert r["status"] == "SUCCESS"
    _, loaded = arc.open_container(tmp_path / "a.vxdna", None)
    assert loaded.content.name == "bad�name.bin"


def test_profile_names_are_validated_before_any_work():
    with pytest.raises(ConfigurationError):
        StoreOptionsV2(profile="My Profile").validate()
    assert options_for("balanced", chunk_size=CS, profile_name="lab-run_1").profile == "lab-run_1"


def test_a_chunk_count_the_trailer_cannot_address_is_refused_up_front(tmp_path):
    src = tmp_path / "sparse.bin"
    with src.open("wb") as handle:
        handle.truncate(0xFFFFFFFF // 56 + 1)
    with pytest.raises(ConfigurationError, match="format limit"):
        arc.store_file(src, tmp_path / "a.vxdna", options=options_for("balanced", chunk_size=1), workers=1)
    assert not list(tmp_path.glob("a.vxdna*"))


# ---------------------------------------------------------------- S2: no silent clobbering
def test_an_output_created_while_the_command_runs_is_not_overwritten(tmp_path):
    target = tmp_path / "o.bin"
    out = arc.AtomicOutput(target)
    out.write(b"new")
    target.write_bytes(b"someone else's file")
    with pytest.raises(OutputError, match="created while"):
        out.commit()
    assert target.read_bytes() == b"someone else's file"


# ---------------------------------------------------------------- C7/C9/C10/C11: CLI contract
def _cli(*args, **kwargs):
    return subprocess.run([sys.executable, "-m", "vnxdna", *map(str, args)], capture_output=True, text=True, **kwargs)


def test_compression_none_works_without_an_explicit_level(tmp_path):
    src = write(tmp_path / "in.bin", b"x" * 1000)
    r = _cli("store", src, "-o", tmp_path / "a.vxdna", "--compression", "none")
    assert r.returncode == 0, r.stderr


def test_info_prints_a_human_readable_summary(tmp_path):
    src = write(tmp_path / "in.bin", b"y" * 1000)
    assert _cli("store", src, "-o", tmp_path / "a.vxdna").returncode == 0
    r = _cli("info", tmp_path / "a.vxdna")
    assert r.returncode == 0 and "archive id" in r.stdout and not r.stdout.lstrip().startswith("{")


def test_verify_of_an_encrypted_archive_without_the_key_exits_4_and_reports_the_ignored_file(tmp_path):
    key_file = tmp_path / "k"
    assert _cli("keygen", "-o", key_file).returncode == 0
    src = write(tmp_path / "in.bin", os.urandom(5000))
    assert _cli("store", src, "-o", tmp_path / "a.vxdna", "--key-file", key_file).returncode == 0
    r = _cli("verify", tmp_path / "a.vxdna", "--file", src, "--json")
    assert r.returncode == 4
    checks = {c["check"]: c for c in json.loads(r.stdout)["checks"]}
    assert checks["file-matches-archive"]["result"] == "FAIL" and "key" in checks["file-matches-archive"]["detail"]
    assert checks["stored-chunks"]["result"] == "PASS"


def _limit_file_size():
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1_000_000, 1_000_000))


def test_a_full_disk_is_an_output_error_and_leaves_no_unresumable_partial(tmp_path):
    src = write(tmp_path / "in.bin", os.urandom(3_000_000))
    r = _cli("store", src, "-o", tmp_path / "a.vxdna", "--chunk-size", "64KiB", preexec_fn=_limit_file_size)
    assert r.returncode == 8, r.stderr
    assert "OUTPUT_ERROR" in r.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.bin"]
