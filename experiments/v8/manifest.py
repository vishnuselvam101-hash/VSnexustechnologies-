"""V8 reproducibility manifest (V8.0): writes experiments/v8/V8_MANIFEST.json.

Records the git commit and V7 parent, every dataset file V8 uses (id, version, SHA-256), every model V8 uses or produces
(canonical SHA-256), configuration files and their hashes, pre-registered seeds, software and dependency versions, the C
compiler, the hardware and the benchmark parameters. Regenerated at each phase; the committed file is the state at that
commit. Reads no data: only hashes already in the dataset manifest and files in the repository.

    PYTHONPATH=src python experiments/v8/manifest.py
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
V7_PARENT = "0fd7c54d1b35178abb38520913797873d1cb22bc"
DATASETS = ("d13-lopez-nanopore",)
#: models used as comparison baselines (V8.6) and decoder channels (V8.8), by repository path
BASELINE_MODELS = (
    "src/vnxdna/simulation/models/nanopore-like.json", "src/vnxdna/simulation/models/clean.json",
    "src/vnxdna/simulation/models/substitution-heavy.json", "src/vnxdna/simulation/models/insertion-heavy.json",
    "src/vnxdna/simulation/models/deletion-heavy.json", "src/vnxdna/simulation/models/mixed-harsh.json",
    "experiments/v7/fit-nano/d03/models/ont-guppy-hac-pass-fwd-fit.json",
    "experiments/v7/fit-nano/d03-a7c/models/ont-guppy-hac-pass-fwd-fit.json",
)
SEEDS = {"d13_fit": 20261010, "d13_validation": 20261011, "precheck": 20261012, "decoder_matrix": list(range(83000, 83010))}
BENCHMARK = {"coverages": [3, 5, 10], "profiles": ["v4-balanced", "v7-lowcov"], "consensus_template": "full",
             "scale_sizes_bytes": [4096, 1 << 20, 10 << 20, 100 << 20, 1 << 30], "workers_max": 4}
DEPENDENCIES = ("numpy", "edlib", "cryptography", "reedsolo", "zstandard", "pytest", "hypothesis", "ruff", "mypy")


def sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=REPO).stdout.strip()
    except OSError:
        return ""


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def model_entry(rel: str) -> dict:
    from vnxdna.simulation import model as cm
    p = REPO / rel
    m, _ = cm.from_doc(json.loads(p.read_text()))
    return {"path": rel, "name": m.doc["name"], "version": m.doc["version"], "schema": m.doc["schema"],
            "canonical_sha256": m.sha256, "file_sha256": sha256_file(p)}


def build() -> dict:
    man = json.loads((REPO / "experiments/v7/datasets/MANIFEST.json").read_text())
    datasets = {}
    for ds in DATASETS:
        d = man["datasets"][ds]
        datasets[ds] = {"publication": d["publication"], "source": d["source"], "version": d["source_commit"],
                        "files": [{"path": f["path"], "sha256": f["sha256"], "bytes": f["bytes"]} for f in d["files"]]}
    produced = sorted(str(p.relative_to(REPO)) for p in (REPO / "experiments/v8").glob("**/models/*.json"))
    configs = sorted([*(str(p.relative_to(REPO)) for p in (REPO / "experiments/v8").glob("**/config*.json")),
                      *(str(p.relative_to(REPO)) for p in (REPO / "docs").glob("V8_PREREGISTRATION*.md"))])
    deps = {}
    for name in DEPENDENCIES:
        try:
            deps[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            deps[name] = None
    cpu = ""
    try:
        cpu = next((ln.split(":", 1)[1].strip() for ln in Path("/proc/cpuinfo").read_text().splitlines()
                    if ln.startswith("model name")), "")
    except OSError:
        pass
    mem_kb = 0
    try:
        mem_kb = int(next(ln.split()[1] for ln in Path("/proc/meminfo").read_text().splitlines() if ln.startswith("MemTotal")))
    except (OSError, StopIteration, ValueError):
        pass
    return {"schema": "vnx.v8-manifest/1", "evidence_class": "provenance only (no result)",
            "git": {"commit": sh("git", "rev-parse", "HEAD"), "branch": sh("git", "rev-parse", "--abbrev-ref", "HEAD"),
                    "dirty_tracked": bool(sh("git", "status", "--porcelain", "--untracked-files=no")),
                    "v7_parent": V7_PARENT,
                    "v7_parent_is_ancestor": subprocess.run(["git", "merge-base", "--is-ancestor", V7_PARENT, "HEAD"],
                                                            cwd=REPO, check=False).returncode == 0},
            "datasets": datasets,
            "models": {"baselines": [model_entry(r) for r in BASELINE_MODELS], "produced": [model_entry(r) for r in produced]},
            "configurations": [{"path": c, "sha256": sha256_file(REPO / c)} for c in configs],
            "seeds": SEEDS, "benchmark": BENCHMARK,
            "software": {"python": sys.version.split()[0], "implementation": platform.python_implementation(),
                         "dependencies": deps, "vnxdna_source_version": (REPO / "src/vnxdna/_version.py").read_text().split("__version__ = ")[1].split()[0].strip("\"")},
            "compiler": sh("cc", "--version").splitlines()[0] if sh("cc", "--version") else None,
            "hardware": {"machine": platform.machine(), "cpu": cpu, "logical_cpus": os.cpu_count(),
                         "memory_gib": round(mem_kb / 1024 / 1024, 1), "os": platform.platform()}}


def _has(name: str) -> bool:
    try:
        metadata.version(name)
        return True
    except metadata.PackageNotFoundError:
        return False


def main() -> int:
    out = build()
    p = HERE / "V8_MANIFEST.json"
    p.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(p, out["git"]["commit"][:12], len(out["models"]["baselines"]), "baseline models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
