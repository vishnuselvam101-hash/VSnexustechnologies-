"""Installed-path decode timing: the same noisy 4 MiB decode in whatever ``vnxdna`` is importable (run it with the python
of a pip-installed virtual environment). SIMULATED data.

Workload (as benchmarks/v6/native_rs/profile_decode.py): 4 MiB random input (seed 42), profile v4-balanced, EXP-0011
channel (0.2 % substitutions, 0.05 % insertions, 0.05 % deletions, 2 % dropout, Poisson coverage 3, seed 1011).
``--prepare`` writes the reads once; later runs reuse that file, so every compared install decodes identical bytes.
Prints one JSON object: the backends each kernel used (from the modules' own ``status()``, which exists in every
version), wall seconds per repetition, median, decode status and the SHA-256 of the decoded container.

usage: python installed_decode.py --workdir DIR [--prepare] [--repeats 5] [--workers 1] [--label TEXT]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
from pathlib import Path

NOISY_CHANNEL = {"substitution_rate": 0.002, "insertion_rate": 0.0005, "deletion_rate": 0.0005, "dropout_rate": 0.02,
                 "coverage": 3, "coverage_model": "poisson", "seed": 1011}
SIZE = 4 << 20


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def prepare(work: Path) -> None:
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import channel as ch
    from vnxdna.v4 import datagen
    from vnxdna.v4 import encoder as en
    work.mkdir(parents=True, exist_ok=True)
    datagen.generate(work / "input.bin", SIZE, "random", 42)
    ar.build_archive([work / "input.bin"], work / "a.vnx", ar.ArchiveOptions(workers=1), overwrite=True)
    en.encode_container(work / "a.vnx", work / "s.fasta", en.DNAOptions(workers=1), overwrite=True)
    ch.simulate_file(work / "s.fasta", work / "r.fastq", ch.ChannelConfig.from_dict(NOISY_CHANNEL), workers=1,
                     overwrite=True)


def backends() -> dict:
    from vnxdna.v5 import native_alignment as na
    from vnxdna.v6 import native_reads as nr
    from vnxdna.v6 import native_rs as nrs
    return {name: {"active_backend": st.get("active_backend"), "library": st.get("library")}
            for name, st in (("align", na.status()), ("reads", nr.status()), ("rs", nrs.status()))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True, type=Path)
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    import vnxdna
    from vnxdna.v4 import decoder as de
    if a.prepare:
        prepare(a.workdir)
    reads = a.workdir / "r.fastq"
    out = a.workdir / f"out-{a.workers}.vnx"
    de.decode_reads(reads, out, de.DecodeOptions(workers=a.workers), overwrite=True)        # warm-up (not timed)
    secs, statuses, shas = [], set(), set()
    for _ in range(a.repeats):
        t = time.perf_counter()
        res = de.decode_reads(reads, out, de.DecodeOptions(workers=a.workers), overwrite=True)
        secs.append(time.perf_counter() - t)
        statuses.add(res.status)
        shas.add(sha256(out))
    print(json.dumps({"label": a.label, "vnxdna_file": vnxdna.__file__, "version": vnxdna.__version__,
                      "python": sys.version.split()[0], "machine": platform.machine(), "workers": a.workers,
                      "reads_sha256": sha256(reads), "reads_bytes": reads.stat().st_size, "backends": backends(),
                      "seconds": [round(s, 3) for s in secs], "median_seconds": round(statistics.median(secs), 3),
                      "status": sorted(statuses), "container_sha256": sorted(shas)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
