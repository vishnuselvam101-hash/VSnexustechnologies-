"""Reproducible stage-by-stage benchmarks (``vnx-dna benchmark``).

Every number is measured on the running machine, and nothing is hard-coded.
Each stage is timed ``repeats`` times with ``time.perf_counter`` (wall) and
``time.process_time`` (CPU), and the median, minimum and maximum are reported.
Peak memory is measured in a separate untimed run with :mod:`tracemalloc`
(Python/numpy allocations only). The input is deterministic for a given seed:
half PRNG bytes (incompressible), half repeated text (compressible).
"""
from __future__ import annotations

import hashlib
import statistics
import time
import tracemalloc
from collections import Counter
from typing import Any, Callable

import numpy as np

from .channel import ChannelConfig, simulate
from .container import compression, crypto
from .container.builder import StoreOptions, build_container
from .container.reader import ContainerReader, body_source, load_manifest
from .ecc.cauchy import CauchyErasureCode
from .provenance import environment
from .storage.decoder import DecodeOptions, ReadsSource, scan_reads
from .storage.encoder import encode_container, geometry_of


def parse_sizes(text: str) -> list[int]:
    sizes = []
    for part in text.split(","):
        part = part.strip().upper()
        if not part:
            continue
        mult = {"K": 1000, "M": 1000 ** 2}.get(part[-1], 1)
        number = part[:-1] if part[-1] in "KM" else part
        if not number.isdigit() or int(number) * mult > 256 * 1000 ** 2:
            raise ValueError(f"invalid size {part!r} (use e.g. 1K, 10K, 1M; max 256M)")
        sizes.append(int(number) * mult)
    return sizes


def synthetic_input(size: int, seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    half = size // 2
    text = (b"VNX-DNA benchmark line: the quick brown fox jumps over the lazy dog 0123456789\n" * (size // 80 + 1))[: size - half]
    return rng.integers(0, 256, half, dtype=np.uint8).tobytes() + text


def _time(fn: Callable[[], Any], repeats: int) -> tuple[dict[str, float], Any]:
    walls, cpus, result = [], [], None
    for _ in range(repeats):
        c0, w0 = time.process_time(), time.perf_counter()
        result = fn()
        walls.append(time.perf_counter() - w0)
        cpus.append(time.process_time() - c0)
    return {"wall_s_median": statistics.median(walls), "wall_s_min": min(walls), "wall_s_max": max(walls),
            "cpu_s_median": statistics.median(cpus), "repeats": repeats}, result


def _peak(fn: Callable[[], Any]) -> int:
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def run_one(size: int, repeats: int, seed: int, options: StoreOptions = StoreOptions()) -> dict[str, Any]:
    data = synthetic_input(size, seed)
    key = hashlib.sha256(b"benchmark-only key, not secret").digest()
    stages: dict[str, Any] = {}

    def record(name: str, fn: Callable[[], Any], nbytes: int) -> Any:
        timing, result = _time(fn, repeats)
        timing["throughput_MBps"] = (nbytes / 1e6) / timing["wall_s_median"] if timing["wall_s_median"] > 0 else None
        timing["peak_traced_bytes"] = _peak(fn)
        stages[name] = timing
        return result

    record("compression_zstd9", lambda: compression.compress(data, "zstd", 9), len(data))
    compressed = compression.compress(data, "zstd", 9)
    keys = crypto.ArchiveKeys.derive(key, bytes(16))
    record("encryption_aes256gcm", lambda: crypto.seal(keys, bytes(16), 0, 0, 1, compressed), len(compressed))
    container = record("store_container", lambda: build_container(data, options, key, "bench.bin"), len(data))
    code = CauchyErasureCode(options.data_shards, options.parity_shards)
    stripes = max(1, -(-container.manifest.stored_size // (options.data_shards * options.payload_bytes)))
    shards = np.random.default_rng(seed).integers(0, 256, (stripes, options.data_shards, options.payload_bytes), dtype=np.uint8)
    parity = record("ecc_outer_encode", lambda: code.encode(shards), shards.size)
    full = np.concatenate([shards, parity], axis=1)
    present = np.ones(full.shape[:2], dtype=bool)
    rng = np.random.default_rng(seed + 1)
    for s in range(stripes):
        present[s, rng.choice(code.total_shards, options.parity_shards, replace=False)] = False
    record("ecc_outer_decode_max_erasures", lambda: code.decode(full, present), shards.size)
    encoded = record("dna_encode", lambda: encode_container(container.manifest, container.manifest_bytes, container.stored_chunks),
                     container.manifest.stored_size)
    geometry = geometry_of(container.manifest)
    record("dna_decode_clean", lambda: scan_reads(encoded.sequences, geometry), container.manifest.stored_size)
    channel = ChannelConfig(seed=seed, substitution_rate=0.002, dropout_rate=0.02, shuffle=True)
    reads, sim_report = record("simulate_channel", lambda: simulate(encoded.sequences, channel), container.manifest.stored_size)
    scan = record("dna_decode_damaged", lambda: scan_reads(reads, geometry, DecodeOptions()), container.manifest.stored_size)
    loaded = load_manifest(container.manifest_bytes, key)

    def full_recovery() -> bytes:
        return ContainerReader(loaded, ReadsSource(scan, loaded.manifest)).read_all()[0]
    recovered = record("recovery_outer_decrypt_decompress_verify", full_recovery, len(data))
    assert recovered == data
    record("restore_from_container", lambda: ContainerReader(loaded, body_source(loaded.manifest, container.body)).read_all(),
           len(data))
    record("random_access_one_chunk_from_reads",
           lambda: ContainerReader(loaded, ReadsSource(scan, loaded.manifest)).read_chunks([len(loaded.manifest.chunks) // 2]),
           min(options.chunk_size, len(data)))

    def end_to_end() -> bool:
        c = build_container(data, options, key, "bench.bin")
        e = encode_container(c.manifest, c.manifest_bytes, c.stored_chunks)
        r, _ = simulate(e.sequences, channel)
        sc = scan_reads(r, geometry)
        lm = load_manifest(c.manifest_bytes, key)
        return ContainerReader(lm, ReadsSource(sc, lm.manifest)).read_all()[0] == data
    record("end_to_end_store_encode_simulate_recover", end_to_end, len(data))
    stats = encoded.stats
    return {"input_bytes": len(data), "input_sha256": hashlib.sha256(data).hexdigest(), "stored_bytes": container.manifest.stored_size,
            "compressed_bytes": len(compressed), "strands": len(encoded.sequences), "strand_nt": stats["strand_nt"],
            "dna_bases": stats["dna_bases_total"], "bases_per_input_byte": stats["dna_bases_total"] / len(data),
            "net_bits_per_base": 8 * len(data) / stats["dna_bases_total"],
            "outer_parity_bytes": stats["outer_parity_bytes"], "redundancy_overhead_percent": stats.get("redundancy_overhead_percent"),
            "frame_overhead_bytes": stats["frame_overhead_bytes"],
            "channel_observed": {k: sim_report[k] for k in ("strands_dropped", "substitutions", "reads_out")},
            "stages": stages}


def run_benchmarks(sizes: list[int], repeats: int = 3, seed: int = 1) -> dict[str, Any]:
    options = StoreOptions()
    return {"benchmark": "vnx-dna stage benchmark v1", "seed": seed, "repeats": repeats,
            "store_options": options.public_dict(), "environment": environment(),
            "results": [run_one(size, repeats, seed + i, options) for i, size in enumerate(sizes)]}


def format_table(results: dict[str, Any]) -> str:
    lines = [f"vnx-dna benchmark (median of {results['repeats']} runs; seed {results['seed']}; "
             f"python {results['environment']['python']}, {results['environment']['machine']}, {results['environment']['cpu_count']} CPUs)"]
    for r in results["results"]:
        lines.append(f"\ninput {r['input_bytes']:,} B -> stored {r['stored_bytes']:,} B -> {r['strands']:,} strands, "
                     f"{r['dna_bases']:,} nt ({r['bases_per_input_byte']:.3f} nt/B)")
        lines.append(f"  {'stage':44s} {'wall s':>10s} {'cpu s':>10s} {'MB/s':>9s} {'peak MiB':>9s}")
        for name, t in r["stages"].items():
            mbps = f"{t['throughput_MBps']:.2f}" if t["throughput_MBps"] else "-"
            lines.append(f"  {name:44s} {t['wall_s_median']:10.4f} {t['cpu_s_median']:10.4f} {mbps:>9s} {t['peak_traced_bytes'] / 2**20:9.1f}")
    return "\n".join(lines)
