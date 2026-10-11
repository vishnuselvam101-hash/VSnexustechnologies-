"""V10 W3 diagnostic: why the 10 MB cases decode only 1 of 4032 rows (SIMULATED).

Not an evaluation case and not a rerun of one: the committed rows in ``results/w3.jsonl`` stand as recorded. This
rebuilds a case exactly as ``run.py`` does (same build, channel, seed, decoder options), checks that the reads hash
equals the recorded row's, decodes once and writes the report's read-clustering and outer-ECC sections, including the
work budgets that stopped a stage, to ``results/diag-<seed>.json``.

    PYTHONPATH=src python experiments/v10/w3/diag_10m.py 93100
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run as R  # noqa: E402


def main(seed: int) -> dict:
    import nanofunnel as nf
    from candidates import cluster_config
    from vnxdna.simulation import engine
    rec = next(json.loads(x) for x in R.OUT.read_text().splitlines() if json.loads(x)["seed"] == seed)
    os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
    MX = R.W1._load("v8_matrix_run", "experiments/v8/matrix/run.py")
    case = {"profile": R.W1.PROFILE, "decoder": {"read_clustering": "fallback",
                                                 "cluster_config": cluster_config(R.W1.CANDIDATE)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=R.W1.WORKERS, stage_counters=True)
    with tempfile.TemporaryDirectory(prefix="vnx-v10w3d-", dir=R.W1._TMP) as tmp:
        w = Path(tmp)
        arc = R.build(w / "a", rec["size"], seed)
        engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq", MX.case_model(R.W1.CHANNEL, R.W1.COVERAGE),
                                        seed)
        reads_sha = hashlib.sha256((w / "r.fastq").read_bytes()).hexdigest()
        if reads_sha != rec["reads_sha256"]:
            raise SystemExit(f"reads differ from the recorded case: {reads_sha} != {rec['reads_sha256']}")
        n_reads = sum(1 for _ in open(w / "r.fastq")) // 4
        row, rep = R.W2._decode(w, "diag", opts, arc)
    stages = (rep.get("stage_counters") or {}).get("stages") or {}
    out = {"label": R.W1.LABEL, "seed": seed, "size": rec["size"], "reads": n_reads, "reads_sha256": reads_sha,
           "outcome": row["outcome"], "decode_seconds": row["decode_seconds"], "load1": row["load1"],
           "clustering": rep.get("clustering"), "outer_v6": rep.get("outer_v6"),
           "outer_ecc": stages.get("outer_ecc"), "stage_names": sorted(stages), "report_keys": sorted(rep)}
    (HERE / "results" / f"diag-{seed}.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    return out


if __name__ == "__main__":
    print(json.dumps(main(int(sys.argv[1])), indent=1, default=str)[:4000])
