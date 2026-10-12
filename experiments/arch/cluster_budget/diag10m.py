"""diag10m.py ROOT CANDIDATE SEED: the V10 W3 10 MB diagnostic (experiments/v10/w3/diag_10m.py) from the clean tree
ROOT with decoder preset CANDIDATE (E or F); writes the report here, not to the repository."""
import sys, json, time, subprocess
from pathlib import Path
if __name__ == "__main__":  # spawn workers re-import this module
    ROOT, cand, seed = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    HERE = Path(__file__).resolve().parent / "diag10m"
    sys.path[:0] = [str(ROOT / "experiments/v10/w3"), str(ROOT / "tests/nanopore"), str(ROOT / "experiments/v9/consensus"), str(ROOT / "src")]
    import diag_10m as D
    D.R.W1.CANDIDATE = cand
    D.HERE = HERE
    t = time.perf_counter()
    out = D.main(seed)
    out["candidate"] = cand
    out["commit"] = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out["wall_seconds"] = round(time.perf_counter() - t, 1)
    (HERE / "results" / f"diag-{seed}-{cand}.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: out.get(k) for k in ("outcome", "decode_seconds", "wall_seconds", "reads", "load1", "commit")}, default=str))
