"""Single benchmark body, run in its own process by vnxops.profile. Prints one JSON object."""
from __future__ import annotations

import hashlib
import json
import resource
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


def mixed(size: int, seed: int = 1) -> bytes:
    """Half compressible text, half random bytes, so compression and ECC see realistic entropy."""
    rng = np.random.default_rng(seed)
    text = (b"VNX-DNA benchmark line with some repetition 0123456789\n" * (size // 110 + 1))[: size // 2]
    return text + rng.integers(0, 256, size - len(text), dtype=np.uint8).tobytes()


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _mbps(n: int, s: float) -> float:
    return round(n / 2**20 / s, 2) if s > 0 else 0.0


def bench(name: str, size: int) -> dict:
    start = t0 = time.perf_counter()
    r: dict = {"bytes": size}
    if name == "python_loop":
        acc = 0
        for i in range(5_000_000):
            acc += i * i % 7
        r = {"iterations": 5_000_000, "seconds": round(time.perf_counter() - t0, 3)}
    elif name == "sha256":
        data = mixed(size)
        t0 = time.perf_counter()
        hashlib.sha256(data).hexdigest()
        r["MBps"] = _mbps(size, time.perf_counter() - t0)
    elif name == "zstd":
        import zstandard as zstd
        data = mixed(size)
        t0 = time.perf_counter()
        comp = zstd.ZstdCompressor(level=3).compress(data)
        tc = time.perf_counter() - t0
        t0 = time.perf_counter()
        back = zstd.ZstdDecompressor().decompress(comp)
        td = time.perf_counter() - t0
        assert back == data
        r.update({"level": 3, "ratio": round(size / len(comp), 3), "compress_MBps": _mbps(size, tc), "decompress_MBps": _mbps(size, td)})
    elif name == "ecc_outer":
        from vnxdna.ecc.cauchy import CauchyErasureCode
        code = CauchyErasureCode(data_shards=16, parity_shards=4)
        L = 4096
        stripes = max(1, size // (16 * L))
        stripe_data = np.frombuffer(mixed(stripes * 16 * L), dtype=np.uint8).reshape(stripes, 16, L)
        t0 = time.perf_counter()
        parity = code.encode(stripe_data)
        te = time.perf_counter() - t0
        shards = np.concatenate([stripe_data, parity], axis=1)
        present = np.ones((stripes, 20), dtype=bool)
        present[:, [0, 3, 7, 11]] = False
        t0 = time.perf_counter()
        dec = code.decode(shards, present)
        td = time.perf_counter() - t0
        assert np.array_equal(dec, stripe_data)
        r.update({"code": "Cauchy RS (16+4), 4 erasures", "encode_MBps": _mbps(size, te), "decode_MBps": _mbps(size, td)})
    elif name == "ecc_inner":
        from vnxdna.ecc.inner_rs import InnerReedSolomon
        rs = InnerReedSolomon(nsym=8)
        k = 32
        n = size // k
        msgs = np.frombuffer(mixed(n * k), dtype=np.uint8).reshape(n, k)
        t0 = time.perf_counter()
        parity = rs.encode_batch(msgs)
        te = time.perf_counter() - t0
        sample = min(2000, n)
        t0 = time.perf_counter()
        ok = 0
        for i in range(sample):
            cw = bytearray(bytes(msgs[i]) + bytes(parity[i]))
            cw[5] ^= 0x5A
            cw[17] ^= 0x33
            fixed = rs.correct(bytes(cw))
            ok += fixed is not None and fixed[0] == bytes(msgs[i])
        td = time.perf_counter() - t0
        r.update({"code": "RS(40,32) nsym=8, 2 byte errors", "encode_MBps": _mbps(size, te),
                  "correct_codewords_per_s": round(sample / td, 1), "corrected": f"{ok}/{sample}"})
    elif name in ("dna_roundtrip", "stream_container"):
        # The installed CLI (V2/V3 streaming path) is the public interface; time each stage as its own process.
        import os
        import subprocess
        cli = os.path.join(os.path.dirname(sys.executable), "vnx-dna")
        workers = os.environ.get("VNXDNA_BENCH_WORKERS", "4")
        with tempfile.TemporaryDirectory(prefix="vnxbench-", dir="/opt/vnx-dna/run") as tmp:
            t = Path(tmp)
            with open(t / "in.dat", "wb") as f:
                chunk = mixed(min(size, 16 * 2**20))
                left = size
                while left > 0:
                    f.write(chunk[:left])
                    left -= len(chunk)
            stages = [("store", ["store", "in.dat", "-o", "a.vxdna", "--no-encrypt", "--workers", workers])]
            if name == "dna_roundtrip":
                stages += [("encode", ["encode", "a.vxdna", "-o", "a.fasta", "--workers", workers]),
                           ("decode", ["decode", "a.fasta", "-o", "b.vxdna", "--workers", workers]),
                           ("restore", ["restore", "b.vxdna", "-o", "out.dat"])]
            else:
                stages += [("restore", ["restore", "a.vxdna", "-o", "out.dat"])]
            timings = {}
            env = {k: v for k, v in os.environ.items() if k != "VNXDNA_KEY"}
            for stage, argv in stages:
                t0 = time.perf_counter()
                p = subprocess.run([cli, *argv], cwd=tmp, capture_output=True, text=True, env=env)
                if p.returncode != 0:
                    raise SystemExit(f"{stage} failed ({p.returncode}): {p.stderr.strip()[-300:]}")
                timings[f"{stage}_MBps"] = _mbps(size, time.perf_counter() - t0)
            r.update(timings)
            r["sha256_identical"] = _file_sha(t / "in.dat") == _file_sha(t / "out.dat")
            if name == "dna_roundtrip":
                fasta = t / "a.fasta"
                r["fasta_bytes"] = fasta.stat().st_size
                nt = sum(len(line) - 1 for line in open(fasta) if not line.startswith(">"))
                r["nucleotides"] = nt
                r["bits_per_nt"] = round(size * 8 / nt, 4) if nt else None
            r["workers"] = int(workers)
            r["largest_child_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
    else:
        raise SystemExit(f"unknown benchmark {name}")
    r["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    r["wall_s"] = round(time.perf_counter() - start, 3)
    return r


if __name__ == "__main__":
    print(json.dumps(bench(sys.argv[1], int(sys.argv[2]))))
