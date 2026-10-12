"""fresh10m.py ROOT CANDIDATE SEED: diag10m.py for a seed without a recorded W1 row (pre-registered held-out seeds):
same archive build, channel, decoder options and report as experiments/v10/w3/diag_10m.py, minus the reads-SHA check."""
import sys, json, time, subprocess, os, dataclasses, hashlib, tempfile
from pathlib import Path
if __name__ == "__main__":  # spawn workers re-import this module
    ROOT, cand, seed = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    HERE = Path(__file__).resolve().parent / "diag10m"
    sys.path[:0] = [str(ROOT / "experiments/v10/w3"), str(ROOT / "tests/nanopore"), str(ROOT / "experiments/v9/consensus"), str(ROOT / "src")]
    import diag_10m as D
    R = D.R
    R.W1.CANDIDATE = cand
    t = time.perf_counter()
    import nanofunnel as nf
    from candidates import cluster_config
    from vnxdna.simulation import engine
    os.environ.pop("VNXDNA_CLUSTER_THREADS", None)
    MX = R.W1._load("v8_matrix_run", "experiments/v8/matrix/run.py")
    case = {"profile": R.W1.PROFILE, "decoder": {"read_clustering": "fallback", "cluster_config": cluster_config(cand)}}
    opts = dataclasses.replace(nf.decoder_options(case), workers=R.W1.WORKERS, stage_counters=True)
    size = 10_000_000
    with tempfile.TemporaryDirectory(prefix="vnx-v10w3d-", dir=R.W1._TMP) as tmp:
        w = Path(tmp)
        arc = R.build(w / "a", size, seed)
        engine.simulate_file_with_truth(arc["strands_path"], w / "r.fastq", MX.case_model(R.W1.CHANNEL, R.W1.COVERAGE), seed)
        reads_sha = hashlib.sha256((w / "r.fastq").read_bytes()).hexdigest()
        n_reads = sum(1 for _ in open(w / "r.fastq")) // 4
        row, rep = R.W2._decode(w, "diag", opts, arc)
    stages = (rep.get("stage_counters") or {}).get("stages") or {}
    out = {"label": R.W1.LABEL, "seed": seed, "size": size, "reads": n_reads, "reads_sha256": reads_sha,
           "outcome": row["outcome"], "decode_seconds": row["decode_seconds"], "load1": row["load1"],
           "clustering": rep.get("clustering"), "outer_v6": rep.get("outer_v6"), "outer_ecc": stages.get("outer_ecc"),
           "stage_names": sorted(stages), "report_keys": sorted(rep), "candidate": cand,
           "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "wall_seconds": round(time.perf_counter() - t, 1)}
    (HERE / "results" / f"diag-{seed}-{cand}.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: out.get(k) for k in ("outcome", "decode_seconds", "wall_seconds", "reads", "load1", "commit")}, default=str))
