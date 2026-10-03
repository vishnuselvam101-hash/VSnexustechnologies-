"""Write experiments/EXP-*/README.md from config.json + results.json (tables generated, never typed).

    python research/v4/write_experiment_readmes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_v4_results as R  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
METHOD = {
    "sweep": "The input is archived and DNA-encoded once. For every channel point and trial, the V4 channel simulator runs with "
             "a seed derived from the base seed, point and trial, and `vnx decode` reconstructs the archive from the reads with one "
             "worker. A trial is SUCCESS only if the reconstructed container matches the SHA-256 in the superblock and validates "
             "structurally; every other outcome is counted in its own column.",
    "codec_compare": "Erasure-only comparison of outer codes on identical data with i.i.d. symbol loss, the same seeds and the same "
                     "parity/data budget for every scheme. A trial succeeds when every data symbol is recovered and equals the original.",
    "constraints": "Random frames are built once. 'Before' violations use one random scrambler variant per strand, without screening; "
                   "'after' uses the encoder's screening (first variant that satisfies every rule). Every screened strand is decoded "
                   "back to verify recoverability.",
    "stages": "Single-core throughput of each pipeline stage on the same deterministic data, in a fresh process.",
    "scaling": "The same end-to-end run (generate → archive → encode → channel → decode → extract → SHA-256) with 1, 2, 4 and 8 "
               "workers, each in a fresh process (peak RSS per process).",
    "memory": "Clean end-to-end DNA path at increasing input sizes, each in a fresh process; peak RSS of the process and of its "
              "largest worker.",
    "v3_vs_v4": "V3 (unchanged CLI, default balanced profile) and V4 (v4-balanced) receive the same input, the same channel "
                "implementation, parameters and per-trial seeds, and the same success criterion (SHA-256-identical output). V3 at "
                "coverage 1 runs both with defaults and with its single-read indel + burst repair; at coverage > 1 it runs cluster → "
                "consensus → decode.",
}
BODY = {"sweep": R.sweep_body("results"), "codec_compare": R.codec_body, "constraints": R.constraints_body, "stages": R.stages_body,
        "scaling": R.scaling_body, "memory": R.memory_body, "v3_vs_v4": R.v3v4_body}


def main() -> None:
    for d in sorted((ROOT / "experiments").glob("EXP-*")):
        cfg = json.loads((d / "config.json").read_text())
        lines = [f"# {d.name}", "", f"**Purpose.** {cfg.get('purpose', '')}", "",
                 "**Scope.** SIMULATED: software-generated DNA through the configurable V4 channel. Not physically validated.", "",
                 f"**Method.** {METHOD[cfg['type']]}", "", "**Reproduce.**", "", "```bash",
                 f"vnx experiment run experiments/{d.name}/config.json      # re-run (overwrites results.json)",
                 f"vnx experiment reproduce experiments/{d.name}            # re-run in scratch and compare deterministic fields",
                 "```", ""]
        res_path = d / "results.json"
        if res_path.exists():
            res = json.loads(res_path.read_text())
            meta = res.get("experiment", {})
            lines += ["## Results", "", f"Commit `{meta.get('git_commit')}`, {meta.get('timestamp_utc')}; environment in "
                      "`environment.json`; every trial in `results.json`.", ""]
            BODY[cfg["type"]](res, lines)
        else:
            lines += ["## Results", "", "_Not run yet._", ""]
        lines += ["## Limitations", "",
                  "* The channel is a stress model, not fitted to any synthesis or sequencing platform.",
                  "* Trial counts are small (5–10 per point for sweeps); success rates near thresholds have wide uncertainty "
                  "(with 10 trials, an observed 10/10 is consistent with a true failure rate up to ~26 %, one-sided 95 % Clopper–Pearson bound).",
                  "* Timings depend on the machine (`environment.json`) and on concurrent load; the deterministic fields are what "
                  "`reproduce` compares.", ""]
        (d / "README.md").write_text("\n".join(lines))
        print("wrote", d / "README.md")


if __name__ == "__main__":
    main()
