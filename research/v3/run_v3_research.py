"""Run every V3 measurement the documentation reports, V2 baseline included, and save raw JSON.

Usage::

    python research/v3/run_v3_research.py --out research/results/v3 \\
        --baseline-python /path/to/v2.0.0/.venv/bin/python [--only NAME ...] [--large]

``--baseline-python`` is an interpreter with VNX-DNA 2.0.0 installed (for example a venv on a checkout of tag
``v2.0.0``); the current interpreter runs VNX-DNA 3. Both run the same commands on the same inputs and the same
machine, one after the other. Measurements:

* ``scale``     the storage pipeline with the real CLI at 1 MB, 10 MB, 100 MB and 1 GB (``vnx-dna benchmark scale``:
                time, CPU, peak RAM of the process tree, sizes, random access), V2 and V3; ``--large`` adds 5 GB (V3 only)
* ``stages``    the in-process stage benchmark A–H at 100 KB and 1 MB, V2 and V3
* ``noisy``     restore from coverage-1 reads with substitutions (10 MB), V2 and V3 on identical read files
* ``indel``     single-read indel repair: success and time per read, V2 and V3
* ``compat``    VNX-DNA 2.0.0 reading archives and strands written by V3
* ``sweep1``    V3 error sweep at coverage 1 (every error type), decoder repairs off vs on
* ``sweep5``    V3 error sweep at coverage 5 with clustering and consensus

Everything is SOFTWARE SIMULATION. ``research/v3/render_v3_tables.py`` turns the JSON into the tables in ``docs/``.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SWEEP_COV1 = ["substitution=0,0.002,0.004,0.006,0.008", "insertion=0.0005,0.001,0.002", "deletion=0.0005,0.001,0.002",
              "dropout=0.05,0.1,0.15", "duplication=0.5", "n=0.005,0.01", "truncation=0.02,0.05", "reverse-complement=0.5",
              "burst-substitution=0.2,0.5", "burst-deletion=0.1,0.2,0.5", "burst-insertion=0.2,0.5", "burst-mixed=0.3",
              "mixed=substitution:0.002+deletion:0.0005+dropout:0.05"]
SWEEP_COV5 = ["substitution=0.005,0.01,0.02", "insertion=0.002,0.005,0.01", "deletion=0.002,0.005,0.01", "dropout=0.1,0.2",
              "burst-deletion=0.2,0.5", "burst-mixed=0.5", "mixed=substitution:0.005+insertion:0.002+deletion:0.002+dropout:0.05"]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if r.returncode:
        print(r.stdout[-2000:], r.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"command failed with exit {r.returncode}: {' '.join(cmd)}")
    return r


def cli(python: str) -> list[str]:
    return [python, "-m", "vnxdna"]


def version(python: str) -> str:
    return run(cli(python) + ["version"]).stdout.strip()


def load_average() -> list[float]:
    return list(os.getloadavg())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("research/results/v3"))
    ap.add_argument("--baseline-python", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--large", action="store_true", help="also measure 5 GB (V3 only)")
    ap.add_argument("--sweep-trials", type=int, default=20)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    impls = {"v2": a.baseline_python, "v3": sys.executable}
    versions = {k: version(v) for k, v in impls.items()}
    assert versions["v2"].startswith("vnx-dna 2.0.0"), versions
    want = (lambda name: a.only is None or name in a.only)
    meta = {"versions": versions, "machine": {"platform": platform.platform(), "cpus": os.cpu_count(),
                                              "python": platform.python_version()}, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    work = Path(tempfile.mkdtemp(prefix="vnxdna-v3-research-"))
    try:
        if want("scale"):
            for name, python in impls.items():
                sizes = "1MB,10MB,100MB,1GB" + (",5GB" if a.large and name == "v3" else "")
                meta[f"scale_{name}_load_before"] = load_average()
                run(cli(python) + ["benchmark", "scale", "--sizes", sizes, "--work-dir", str(work / f"scale-{name}"),
                                   "-o", str(a.out / f"scale-{name}.json")])
        if want("stages"):
            for name, python in impls.items():
                run(cli(python) + ["benchmark", "stages", "--sizes", "100KB,1MB", "-o", str(a.out / f"stages-{name}.json")])
        if want("noisy"):
            v3_cli = str(Path(sys.executable).with_name("vnx-dna"))
            v2_cli = str(Path(a.baseline_python).with_name("vnx-dna"))
            run([sys.executable, str(HERE / "noisy_decode_bench.py"), "--vnx", v3_cli, "--vnx-baseline", v2_cli, "--size", "10MB",
                 "--rates", "0,0.002,0.004,0.006", "--out", str(a.out / "noisy-decode.json")])
        if want("compat"):
            run([sys.executable, str(HERE / "check_v2_reads_v3.py"), "--v2-cli", str(Path(a.baseline_python).with_name("vnx-dna")),
                 "--out", str(a.out / "v2-reads-v3.json")])
        if want("indel"):
            for name, python in impls.items():
                run([python, str(HERE / "indel_repair_bench.py"), "--reads", "40", "--out", str(a.out / f"indel-{name}.json")])
        if want("sweep1") or want("sweep5"):
            src = work / "sweep-input.bin"
            run(cli(sys.executable) + ["benchmark", "generate", "--size", "100KB", "--pattern", "mixed", "--seed", "7", "-o", str(src)])
            common = ["--trials", str(a.sweep_trials), "--seed", "1000", "--force", "--json"]
            if want("sweep1"):
                for label, extra in (("off", []), ("on", ["--indel-repair", "--max-indel", "2", "--burst-repair", "24"])):
                    args = sum((["--sweep", s] for s in SWEEP_COV1), [])
                    out = work / f"sweep1-{label}"
                    run(cli(sys.executable) + ["simulate-errors", str(src), "-o", str(out), "--coverage", "1", "--coverage-model", "fixed"]
                        + args + common + extra)
                    shutil.copy(out / "sweep.json", a.out / f"sweep-cov1-repairs-{label}.json")
            if want("sweep5"):
                args = sum((["--sweep", s] for s in SWEEP_COV5), [])
                out = work / "sweep5"
                run(cli(sys.executable) + ["simulate-errors", str(src), "-o", str(out), "--coverage", "5", "--coverage-model", "poisson",
                                           "--consensus", "--trials", str(max(5, a.sweep_trials // 2)), "--seed", "2000", "--force",
                                           "--json"] + args)
                shutil.copy(out / "sweep.json", a.out / "sweep-cov5-consensus.json")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        meta["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        meta["only"] = a.only
        previous = json.loads((a.out / "run-meta.json").read_text()) if (a.out / "run-meta.json").exists() else {}
        runs = previous.pop("runs", [])
        runs.append(meta)
        (a.out / "run-meta.json").write_text(json.dumps({**meta, "runs": runs}, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
