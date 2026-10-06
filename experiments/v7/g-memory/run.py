"""G-MEM: peak RSS of encode and decode versus input size on noisy channels (SIMULATED channel; time and memory
MEASURED on this host, which is shared: the load average is recorded before and after every measured command).

    PYTHONPATH=src python experiments/v7/g-memory/run.py --sizes 1,4,16 --out experiments/v7/g-memory/results
    PYTHONPATH=src python experiments/v7/g-memory/run.py --sizes 64 --out experiments/v7/g-memory/results

Per size S (MiB): a random payload (seed 7000 + S) is encoded by `vnx encode --workers 1 --compression none` in a fresh
measured process (the container is kept to judge exactness). For each channel model (shipped models illumina-like,
nanopore-like, deletion-heavy; all unfitted, SIMULATED) the strands are run through `vnx channel simulate` (seed
71000 + S; not measured), then decoded by `vnx decode --workers 1 --events` in a fresh measured process. Every
measured process reports its own VmHWM (see measure.py); with one worker the whole decode runs in that process. The
RSS timeline is sampled every 50 ms and attributed to decode stages with the event stream.

One results file per size: <out>/size-<S>MiB.json. The reads file is deleted after its decode (it is 200-300 bytes per
payload byte at the models' coverage of about 10).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
import measure  # noqa: E402

MODELS = ["illumina-like", "nanopore-like", "deletion-heavy"]
STATEMENT = ("SIMULATED: software strands through the shipped, unfitted channel models; no DNA was synthesised, stored "
             "or sequenced. Wall time and peak RSS are MEASURED on this host under shared load.")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def git(*a: str) -> str | None:
    import subprocess

    r = subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def host() -> dict:
    cpu = next((line.split(":", 1)[1].strip() for line in open("/proc/cpuinfo") if line.startswith("model name")), None)
    mem = next((int(line.split()[1]) * 1024 for line in open("/proc/meminfo") if line.startswith("MemTotal")), None)
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "mem_total_bytes": mem, "python": sys.version.split()[0],
            "kernel": os.uname().release}


def measured(args: list[str], work: Path, **kw) -> dict:
    la0 = os.getloadavg()
    r = measure.run_vnx(args, work, env={"PYTHONPATH": str(REPO / "src")}, **kw)
    r["load_average_before"] = [round(x, 2) for x in la0]
    r["load_average_after"] = [round(x, 2) for x in os.getloadavg()]
    return r


def slim(r: dict) -> dict:
    """The JSON result of a vnx command is large; keep the fields this experiment reads."""
    res = r.pop("result", None) or {}
    keep = {k: res.get(k) for k in ("status", "seconds", "stage_seconds", "reads", "groups_decoded", "groups_failed",
                                    "strands", "container_sha256", "error")}
    r["vnx_result"] = {k: v for k, v in keep.items() if v is not None}
    return r


def git_state() -> dict:
    return {"commit": git("rev-parse", "HEAD"),
            "dirty_tracked": bool(git("status", "--porcelain", "--untracked-files=no"))}


def run_size(size_mib: int, work: Path, workers_sim: int, models: list[str], save) -> dict:
    payload, strands, container = work / "payload.bin", work / "strands.fasta", work / "a.vnx"
    import subprocess

    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    gen = [sys.executable, "-c", "from vnxdna.commands import main; main()", "generate", str(payload), "--size",
           f"{size_mib}MiB", "--pattern", "random", "--seed", str(7000 + size_mib)]
    subprocess.run(gen, check=True, env=env, capture_output=True)
    enc = measured(["encode", str(payload), str(strands), "--workers", "1", "--compression", "none",
                         "--keep-archive", str(container), "--force"], work)
    enc.pop("_raw_samples", None)
    enc = slim(enc)
    enc.update(op="encode", size_mib=size_mib, payload_sha256=sha256_file(payload),
               strands_sha256=sha256_file(strands), strands_bytes=strands.stat().st_size,
               container_sha256=sha256_file(container), container_bytes=container.stat().st_size)
    rows = [enc]
    save(rows)
    print(json.dumps({k: enc[k] for k in ("op", "size_mib", "exit", "wall_seconds", "peak_rss_bytes")}), flush=True)
    for model in models:
        reads, meta = work / f"reads-{model}.fastq", work / f"sim-{model}.json"
        t = time.perf_counter()
        sim = subprocess.run([*gen[:3], "channel", "simulate", str(strands), str(reads), "--model", model, "--seed",
                              str(71000 + size_mib), "--workers", str(workers_sim), "--metadata", str(meta), "--force"],
                             env=env, capture_output=True, text=True)
        sim_secs = time.perf_counter() - t
        if sim.returncode != 0:
            raise SystemExit(f"simulate {model} failed: {sim.stderr[-2000:]}")
        sm = json.loads(meta.read_text())
        events, o = work / f"events-{model}.jsonl", work / "decoded.vnx"
        free_before = shutil.disk_usage(work).free
        dec = measured(["decode", str(reads), "-o", str(o), "--workers", "1", "--events", str(events), "--force"], work)
        raw = dec.pop("_raw_samples")
        dec["stage_attribution"] = measure.stage_of_peak(raw, events)
        dec = slim(dec)
        dec.update(op="decode", size_mib=size_mib, model=model, reads_bytes=reads.stat().st_size,
                   simulate_seconds=round(sim_secs, 1),
                   simulation={"model": sm.get("model"), "seed": sm.get("seed"), "reads": sm.get("output", {}).get("reads"),
                               "reads_sha256": sm.get("output", {}).get("sha256"), "realised_rates": sm.get("realised_rates")},
                   exact=o.exists() and sha256_file(o) == enc["container_sha256"],
                   disk_free_bytes_before_decode=free_before)
        rows.append(dec)
        save(rows)
        print(json.dumps({k: dec.get(k) for k in ("op", "size_mib", "model", "exit", "exact", "wall_seconds",
                                                  "peak_rss_bytes")}), flush=True)
        for p in (reads, o, events):
            if p.exists():
                p.unlink()
    return {"rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", default="1,4,16,64")
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--work", default=None, help="scratch directory for strands and reads (needs ~20 GB at 64 MiB)")
    ap.add_argument("--sim-workers", type=int, default=6)
    ap.add_argument("--models", default=",".join(MODELS),
                    help="subset of the models; a subset writes size-<S>MiB-<models>.json (disk: one reads file at a time)")
    a = ap.parse_args(argv)
    models = a.models.split(",")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    git_at_start = git_state()   # provenance of the code that runs, captured before the first measured command
    for size in [int(x) for x in a.sizes.split(",")]:
        name = f"size-{size}MiB.json" if models == MODELS else f"size-{size}MiB-{'+'.join(models)}.json"
        t0 = time.time()

        def save(rows, size=size, name=name, t0=t0):
            doc = {"experiment": "G-MEM", "classification": "SIMULATED channel; wall time and peak RSS MEASURED",
                   "statement": STATEMENT, "size_mib": size, "models": models, "workers": 1,
                   "method": "fresh process per command; peak_rss_bytes = the process's own VmHWM (measure.py)",
                   "git": git_at_start, "git_at_save": (now := git_state()),
                   "git_changed_during_run": now != git_at_start,
                   "host": host(), "started": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t0)),
                   "elapsed_seconds": round(time.time() - t0, 1), "complete": len(rows) == 1 + len(models), "rows": rows}
            (out / name).write_text(json.dumps(doc, indent=1) + "\n")

        with tempfile.TemporaryDirectory(prefix="vnx-g-mem-", dir=a.work) as d:
            run_size(size, Path(d), a.sim_workers, models, save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
