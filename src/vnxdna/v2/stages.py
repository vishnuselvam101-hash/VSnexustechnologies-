"""Per-stage benchmark (``vnx-dna benchmark stages``): A storage … H end to end.

Each stage runs once per size in this process, timed with ``perf_counter``
(wall) and ``process_time`` (CPU of this process; worker processes are not
included, so utilisation above 1 is not visible here; ``benchmark scale``
measures whole process trees). Inputs are generated with the ``mixed``
pattern. Everything is measured on the running machine and nothing is
hard-coded.

Stages: A store + restore (container), B DNA encode, C DNA decode (clean
strands), D simulated sequencing (10x, sub 0.1 %, ins/del 0.01 %, dropout 2 %),
E clustering, F consensus, G outer ECC alone (encode, then decode with M
erasures in every group), H end to end (recover from consensus).
"""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..provenance import environment
from .scale import generate_file, parse_size


def parse_sizes(text: str) -> list[int]:
    return [parse_size(s) for s in text.split(",") if s.strip()]


def _timed(fn, *args, **kwargs) -> tuple[Any, float, float]:
    w, c = time.perf_counter(), time.process_time()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - w, time.process_time() - c


def run_stages(sizes: list[int], *, seed: int = 1, workers: int = 0) -> dict[str, Any]:
    from ..ecc.cauchy import CauchyErasureCode
    from .api import _recover_v2
    from .archive import restore_file, store_file
    from .cluster import cluster_file
    from .consensus import consensus_file
    from .decoder import DecodeOptionsV2
    from .encoder import cauchy_parity, encode_file
    from .sequencing import SequencingConfig, sequence_file
    results: dict[str, Any] = {"benchmark": "vnx-dna benchmark stages", "environment": environment(), "runs": []}
    for size in sizes:
        work = Path(tempfile.mkdtemp(prefix="vnxdna-stages-"))
        try:
            src = work / "input.bin"
            generate_file(src, size, "mixed", seed)
            stages: dict[str, Any] = {}

            def record(name: str, wall: float, cpu: float, nbytes: int, **extra) -> None:
                stages[name] = {"wall_s": wall, "cpu_s": cpu, "mb_s": nbytes / wall / 1e6 if wall else None, **extra}

            store, w, c = _timed(store_file, src, work / "a.vxdna", workers=workers)
            record("A_store", w, c, size, stored_bytes=store["stored_bytes"])
            _, w, c = _timed(restore_file, work / "a.vxdna", work / "r.bin", workers=workers)
            record("A_restore", w, c, size)
            enc, w, c = _timed(encode_file, work / "a.vxdna", work / "s.fasta", workers=workers)
            record("B_dna_encode", w, c, size, strands=enc["strands"], bases=enc["dna_bases"])
            _, w, c = _timed(_recover_v2, work / "s.fasta", work / "r2.bin", key=None, options=DecodeOptionsV2(), workers=workers,
                             overwrite=True, temp_dir=work)
            record("C_dna_decode_clean", w, c, size)
            config = SequencingConfig(seed=seed, coverage=10, substitution_rate=0.001, insertion_rate=0.0001, deletion_rate=0.0001,
                                      dropout_rate=0.02)
            seq, w, c = _timed(sequence_file, work / "s.fasta", work / "reads.fastq", config, temp_dir=work)
            record("D_sequencing_10x", w, c, size, reads=seq["reads"], bases=seq["bases_out"])
            cl, w, c = _timed(cluster_file, work / "reads.fastq", work / "c.jsonl", workers=workers, temp_dir=work)
            record("E_clustering", w, c, size, clusters=cl["clusters"], reads=seq["reads"])
            co, w, c = _timed(consensus_file, work / "c.jsonl", work / "cons.fasta")
            record("F_consensus", w, c, size, sequences=co["consensus_sequences"])
            rng = np.random.default_rng(seed)
            code = CauchyErasureCode(64, 16)
            stripes = max(1, store["stored_bytes"] // (64 * 40))
            data = rng.integers(0, 256, (stripes, 64, 40), dtype=np.uint8)
            parity, w, c = _timed(cauchy_parity, code, data)
            record("G_ecc_outer_encode", w, c, data.nbytes)
            shards = np.concatenate([data, parity], axis=1)
            present = np.ones((stripes, 80), dtype=bool)
            for s in range(stripes):
                present[s, rng.choice(80, 16, replace=False)] = False
            decoded, w, c = _timed(code.decode, shards, present)
            record("G_ecc_outer_decode_16_erasures_per_group", w, c, data.nbytes, exact=bool((decoded == data).all()))
            rec, w, c = _timed(_recover_v2, work / "cons.fasta", work / "r3.bin", key=None, options=DecodeOptionsV2(), workers=workers,
                               overwrite=True, temp_dir=work)
            end_to_end = sum(stages[k]["wall_s"] for k in ("A_store", "B_dna_encode", "D_sequencing_10x", "E_clustering", "F_consensus")) + w
            record("H_recover_from_consensus", w, c, size, sha256_match=rec["sha256_match"])
            stages["H_end_to_end_total"] = {"wall_s": end_to_end, "mb_s": size / end_to_end / 1e6, "cpu_s": None,
                                            "identical": (work / "r3.bin").read_bytes() == src.read_bytes()}
            results["runs"].append({"size": size, "stages": stages})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return results


def format_stages(results: dict[str, Any]) -> str:
    lines = []
    for run in results["runs"]:
        lines.append(f"input {run['size']:,} B (mixed pattern)")
        lines.append(f"  {'stage':<44}{'wall s':>10}{'cpu s':>10}{'MB/s':>10}")
        for name, st in run["stages"].items():
            cpu = f"{st['cpu_s']:.3f}" if st.get("cpu_s") is not None else "-"
            mb = f"{st['mb_s']:.2f}" if st.get("mb_s") else "-"
            lines.append(f"  {name:<44}{st['wall_s']:>10.3f}{cpu:>10}{mb:>10}")
    return "\n".join(lines)
