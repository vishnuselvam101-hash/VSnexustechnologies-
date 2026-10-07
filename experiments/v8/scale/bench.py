"""V8.13 scale benchmark of the current pipeline (SIMULATED clean channel: every strand read once, no errors).

For each size: random input (worst case for compression) → ``sdk.encode`` (archive with zstd + v4-balanced strands) →
``sdk.decode`` of the strands as reads → byte comparison. Each stage runs in its own child process, so the wall time, CPU
time and peak RSS are that stage's own. Recorded: strands, nucleotides, nucleotides per input byte, container size
(compressed size), parity overhead of the profile, exact recovery. Worker scaling: decode at one size with 1, 2 and 4
workers. Sizes that are not run are reported as NOT RUN; nothing is extrapolated without a label.

    PYTHONPATH=src python experiments/v8/scale/bench.py --sizes 4096,1048576,10485760,104857600 [--max-seconds 3600]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SEED = 83200
STAGE = r"""
import json, sys, time, resource
from pathlib import Path
from vnxdna import sdk
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.recovery.options import DecodeOptions
from vnxdna.archive.operations import ArchiveOptions
stage, a, b, workers = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
def cpu():
    s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime
t, c0 = time.perf_counter(), cpu()
if stage == "encode":
    r = sdk.encode([a], b, dna=DNAOptions(profile="v4-balanced", workers=workers), archive_options=ArchiveOptions(workers=workers),
                   allow_unencrypted=True, keep_archive=b.with_suffix(".vnx"))
else:
    r = sdk.decode(a, b, options=DecodeOptions(workers=workers), allow_unencrypted=True)
ru = resource.getrusage(resource.RUSAGE_SELF)
rc = resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps({"wall_s": time.perf_counter() - t, "cpu_s": cpu() - c0, "peak_rss_mib": ru.ru_maxrss / 1024,
                  "peak_rss_children_mib": rc.ru_maxrss / 1024, "status": r.status}))
"""


def run_stage(stage: str, a: Path, b: Path, workers: int, timeout: float) -> dict:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    t = time.perf_counter()
    p = subprocess.run([sys.executable, "-c", STAGE, stage, str(a), str(b), str(workers)], capture_output=True, text=True,
                       env=env, timeout=timeout, check=False)
    if p.returncode:
        return {"status": "ERROR", "stderr": p.stderr[-800:], "wall_s": time.perf_counter() - t}
    r = json.loads(p.stdout.strip().splitlines()[-1])
    r["cpu_utilisation"] = round(r["cpu_s"] / r["wall_s"], 2) if r["wall_s"] else None
    return r


def count_fasta(p: Path) -> tuple[int, int]:
    n = nt = 0
    with p.open("rb") as fh:
        for line in fh:
            if line.startswith(b">"):
                n += 1
            else:
                nt += len(line.strip())
    return n, nt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="4096,1048576,10485760,104857600")
    ap.add_argument("--scaling-size", type=int, default=10485760)
    ap.add_argument("--max-seconds", type=float, default=3600)
    a = ap.parse_args(argv)
    from vnxdna.dnaenc.layout import PROFILES
    K, M = PROFILES["v4-balanced"][1], PROFILES["v4-balanced"][2]
    out: dict = {"benchmark": "V8.13 scale (current pipeline, v4-balanced, clean channel)", "label": "SIMULATED clean channel",
                 "seed": SEED, "profile": {"name": "v4-balanced", "outer_K": K, "outer_M": M, "outer_parity_overhead": M / K},
                 "cpu": os.cpu_count(), "load_average_start": list(os.getloadavg()), "runs": []}
    sizes = [int(s) for s in a.sizes.split(",")]
    for size in sizes + ([] if (1 << 30) in sizes else [1 << 30]):
        if size not in sizes:
            out["runs"].append({"size": size, "status": "NOT RUN", "note": "1 GiB not run in this benchmark; no extrapolation reported"})
            continue
        with tempfile.TemporaryDirectory(prefix="vnx-v8s-", dir="/root/vnx-dna-lab/tmp") as tmp:
            w = Path(tmp)
            src = w / "input.bin"
            with src.open("wb") as fh:
                left, rnd = size, __import__("random").Random(SEED + size)
                while left:
                    k = min(left, 1 << 20)
                    fh.write(rnd.randbytes(k))
                    left -= k
            enc = run_stage("encode", src, w / "strands.fasta", 4, a.max_seconds)
            row: dict = {"size": size, "encode": enc}
            if enc.get("status") == "ERROR":
                out["runs"].append(row)
                continue
            n, nt = count_fasta(w / "strands.fasta")
            row.update(strands=n, nucleotides=nt, nt_per_byte=round(nt / size, 3),
                       container_bytes=(w / "strands.vnx").stat().st_size if (w / "strands.vnx").exists() else None)
            dec = run_stage("decode", w / "strands.fasta", w / "out.vnx", 4, a.max_seconds)
            row["decode"] = dec
            if (w / "out.vnx").exists() and (w / "strands.vnx").exists():
                row["exact_container"] = hashlib.sha256((w / "out.vnx").read_bytes()).hexdigest() == \
                    hashlib.sha256((w / "strands.vnx").read_bytes()).hexdigest()
            if size == a.scaling_size:
                row["decode_worker_scaling"] = {str(k): run_stage("decode", w / "strands.fasta", w / f"o{k}.vnx", k, a.max_seconds)
                                                for k in (1, 2, 4)}
            out["runs"].append(row)
            print(json.dumps({"size": size, "encode_s": enc.get("wall_s"), "decode_s": dec.get("wall_s"),
                              "exact": row.get("exact_container"), "nt_per_byte": row.get("nt_per_byte")}), flush=True)
    out["load_average_end"] = list(os.getloadavg())
    (HERE / "results").mkdir(parents=True, exist_ok=True)
    (HERE / "results" / "scale.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
