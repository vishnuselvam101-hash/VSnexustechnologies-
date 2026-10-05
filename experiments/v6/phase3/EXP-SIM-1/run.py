"""EXP-SIM-1 (V6_ARCHITECTURE §9, Phase 3): do the migrated channel models reproduce? SIMULATED: software strands and
software channel models; no DNA was synthesised, stored or sequenced.

    PYTHONPATH=src python experiments/v6/phase3/EXP-SIM-1/run.py

For every model (14) × strand file (3) × seed (5) the reads are simulated three times: by the current simulator (the
Phase 1 composer ``experiments/v6/channel/channel.py``: ``vnxdna.simulation.loss`` + ``vnxdna.simulation.channel``), and
by the staged simulator ``vnxdna.simulation.engine`` with the model read as ``vnx.channel-model/0`` and as the shipped
``/1`` conversion. Pass: the three read files have the same SHA-256 in every cell. Writes ``results.json``.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / "experiments" / "v6" / "channel"))

import channel as chn  # noqa: E402
import vnxdna  # noqa: E402
from vnxdna.core.provenance import git_state  # noqa: E402
from vnxdna.simulation import engine, registry  # noqa: E402
from vnxdna.simulation import model as cm  # noqa: E402

CONFIG = json.loads((HERE / "config.json").read_text())


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_random(path: Path, seed: int, n: int, length: int) -> Path:
    rows = np.random.default_rng(seed).integers(0, 4, (n, length))
    with open(path, "w") as f:
        for i, row in enumerate(rows):
            f.write(f">s{i}\n{''.join('ACGT'[b] for b in row)}\n")
    return path


def main() -> int:
    if not vnxdna.__file__.startswith(str(REPO / "src")):
        raise SystemExit(f"vnxdna is imported from {vnxdna.__file__}, not this tree; set PYTHONPATH={REPO / 'src'}")
    t0 = time.time()
    git = git_state(REPO)
    cells = []
    with tempfile.TemporaryDirectory(prefix="exp-sim-1-") as tmp:
        tmp = Path(tmp)
        files = {"random-2500x120": write_random(tmp / "a.fasta", 91001, 2500, 120),
                 "random-300x80": write_random(tmp / "b.fasta", 91002, 300, 80),
                 "v6-max-recovery": REPO / "tests" / "fixtures" / "v6_0" / "max-recovery.strands.fasta"}
        for name in chn.model_names():
            m0, _ = cm.read_file(chn.MODEL_DIR / f"{name}.json")
            m1 = registry.load_model(name)
            for fname, src in files.items():
                for seed in CONFIG["seeds"]:
                    chn.compose(chn.load_model(name), src, tmp / "ref.fastq", seed)
                    engine.simulate_file(src, tmp / "v0.fastq", m0, seed, overwrite=True)
                    engine.simulate_file(src, tmp / "v1.fastq", m1, seed, overwrite=True)
                    ref, a, b = sha(tmp / "ref.fastq"), sha(tmp / "v0.fastq"), sha(tmp / "v1.fastq")
                    cells.append({"model": name, "model_version": m1.version, "model_sha256_v1": m1.sha256,
                                  "strands": fname, "seed": seed, "reference_sha256": ref, "v0_sha256": a,
                                  "v1_sha256": b, "reads_bytes": (tmp / "ref.fastq").stat().st_size,
                                  "identical": ref == a == b})
                    (tmp / "ref.fastq").unlink()
        inputs = {k: {"sha256": sha(v), "strands": sum(1 for ln in open(v) if ln.startswith(">"))} for k, v in files.items()}
    identical = sum(c["identical"] for c in cells)
    doc = {"id": CONFIG["id"], "evidence_class": "SIMULATED", "data_source": "SIMULATED", "config": CONFIG,
           "git": git, "software": vnxdna.__version__, "simulator_version": engine.SIMULATOR_VERSION,
           "versions": engine.versions(), "python": platform.python_version(), "inputs": inputs,
           "cells": cells, "summary": {"cells": len(cells), "simulations": 3 * len(cells), "identical": identical,
                                       "different": len(cells) - identical, "pass": identical == len(cells)},
           "seconds": round(time.time() - t0, 1),
           "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))}
    (HERE / "results.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(json.dumps(doc["summary"]), f"git={git}")
    return 0 if doc["summary"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
