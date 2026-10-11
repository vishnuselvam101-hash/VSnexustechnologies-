"""Nanopore-like channel lane for the benchmark lab: run VNX-DNA's software channel simulator on any codec's strands.

    python channel_vnxsim.py STRANDS_TXT READS_TXT SEED COVERAGE [MODEL]

STRANDS_TXT: one strand per line (the harness's sequences file). READS_TXT: one read per line, order shuffled with SEED.
MODEL: ``d13-f1`` (default) = the V8 F1 model fitted to public ONT R9.4 data (experiments/v8/d13, adequacy UNVALIDATED),
with negative-binomial coverage (mean COVERAGE, dispersion 4) exactly as ``experiments/v8/matrix/run.py case_model``.
The same model, seed and coverage are applied to every codec's strands; it is a SIMULATED channel, codec-agnostic (no
reverse complements, no dropout in this model; substitutions, run-length insertions/deletions, homopolymer context).
Called by the lab driver (outside the repository) as a separate process; prints a JSON summary.
"""
from __future__ import annotations

import importlib.util
import json
import random
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def model(name: str, coverage: float):
    spec = importlib.util.spec_from_file_location("v8_matrix", REPO / "experiments/v8/matrix/run.py")
    mx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mx)
    return mx.case_model(name, coverage)


def main(argv: list[str]) -> int:
    strands, reads, seed, cov = Path(argv[0]), Path(argv[1]), int(argv[2]), float(argv[3])
    name = argv[4] if len(argv) > 4 else "d13-f1"
    from vnxdna.simulation import engine
    seqs = [s.strip() for s in strands.read_text().splitlines() if s.strip()]
    with tempfile.TemporaryDirectory() as tmp:
        fa = Path(tmp) / "strands.fasta"
        fa.write_text("".join(f">s{i}\n{s}\n" for i, s in enumerate(seqs)))
        out = Path(tmp) / "reads.fasta"
        meta = engine.simulate_file(fa, out, model(name, int(cov) if cov == int(cov) else cov), seed, fmt="fasta")
        lines = [ln for ln in out.read_text().splitlines() if ln and not ln.startswith(">")]
    random.Random(seed).shuffle(lines)
    reads.write_text("\n".join(lines) + "\n")
    n_in = sum(map(len, seqs))
    print(json.dumps({"model": name, "coverage_mean": cov, "seed": seed, "strands": len(seqs), "reads": len(lines),
                      "read_nt": sum(map(len, lines)), "strand_nt": n_in,
                      "events": {k: v for k, v in meta.items() if k != "metadata"}}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
