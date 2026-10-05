"""EXP-PROBE-1 follow-up (SIMULATED): version-nibble metrics of every n = 64 sample, for the criterion conflict in
README.md. Run: PYTHONPATH=src:tests python experiments/v6/phase2/EXP-PROBE-1/followup_n64.py OUT.json
(smaller pools, 120 kB, same models, coverages and seeds)."""
import sys, json, math, tempfile
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "experiments/v6/channel")); sys.path.insert(0, str(REPO / "tests")); sys.path.insert(0, str(REPO / "experiments/v6/phase2/EXP-PROBE-1"))
import run as R
import channel as chm
R.CONFIG["pool_bytes"] = 120000

def cell(t):
    pool, f, cls, model_name, cov, seed, work = t
    from vnxdna.recovery.probe import probe_reads, _unique
    m = chm.load_model(model_name); m.channel["coverage"] = float(cov)
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        tmp = Path(tmp); sub = tmp / "s.fasta"
        R._subset(Path(f), sub, int(math.ceil(64 / cov / max(0.05, 1 - m.loss["dropout"]) * 1.3)) + 64)
        chm.compose(m, sub, tmp / "r.fastq", seed)
        R._first_reads(tmp / "r.fastq", tmp / "r64.fastq", 64)
        p = probe_reads(tmp / "r64.fastq")
        u = _unique(p)
        return {"pool": pool, "cls": cls, "model": model_name, "cov": cov, "seed": seed, "cands": len(p.candidates),
                "verified": any(p.scores.values()), "raw": p.dominant("VNX4 scrambler"), "uniq": u.dominant("VNX4 scrambler"), "n_uniq": u.n}

if __name__ == "__main__":
    work = Path(tempfile.mkdtemp())
    pools = R.build_pools(work / "p")
    tasks = [(p, str(f), cls, m, c, 7200 + s, str(work)) for p, (f, cls, want) in pools.items() for m in R.CONFIG["models"] for c in (1, 3, 10) for s in range(20)]
    with ProcessPoolExecutor(6) as ex:
        rows = list(ex.map(cell, tasks, chunksize=8))
    json.dump(rows, open(sys.argv[1], "w"))
