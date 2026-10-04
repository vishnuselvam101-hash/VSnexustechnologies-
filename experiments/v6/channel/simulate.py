"""CLI: simulate a mixed-error channel over a strand FASTA (SIMULATED; not biological validation).

    python experiments/v6/channel/simulate.py --model mixed-mild --strands in.fasta --seed 7 --out reads.fastq

Writes the reads and a sidecar ``<out>.json`` with provenance (model, parameters, model SHA-256, seed, checksums, git state).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import channel as chn  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True, help="named model (see --list) or path to a model JSON")
    ap.add_argument("--strands", help="input strand FASTA (equal-length strands)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", help="output reads (.fastq, or .fasta/.fa for FASTA)")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--list", action="store_true", help="list named models and exit")
    a = ap.parse_args(argv)
    if a.list:
        print("\n".join(chn.model_names()))
        return 0
    if not (a.strands and a.out):
        ap.error("--strands and --out are required")
    model = chn.load_model(a.model)
    sc = chn.simulate_with_sidecar(model, a.strands, a.out, a.seed, workers=a.workers, command=" ".join(sys.argv))
    print(json.dumps({"classification": sc["classification"], "model": model.name, "reads": sc["output"]["reads"],
                      "output_sha256": sc["output"]["sha256"], "sidecar": str(chn.sidecar_path(a.out))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
