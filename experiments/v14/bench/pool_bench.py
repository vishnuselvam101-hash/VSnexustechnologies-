"""V14 workstream A: decode of a byte stream from ONE shuffled SIMULATED read pool, scaling with the extent count.

    V13_TREE=/root/vnx-dna-lab/v14-base PYTHONPATH=src python experiments/v14/bench/pool_bench.py run \\
        [--extents 4,8,16,32] [--trees v13,v14] [--workers 1] [--reps 3] [--out DIR]
    PYTHONPATH=src python experiments/v14/bench/pool_bench.py one --strands P --manifest M --out O --workers W

Per extent count E: a random stream of E x 1 MiB is encoded by the V13 tree (`encode_stream`, unchanged encoder), its
strands are shuffled into one read pool by the channel simulator (coverage 3, 1 % substitutions, seed 7; the V13
`stream_volume_bench.py` channel), and every tree decodes the pool through `decode_stream` with the pool path forced
(`by_range=False`, one process per decode, PYTHONPATH = the tree). Recorded per decode: wall seconds, the decoder's
report (V14: partition/index seconds, pass-1 and per-extent pass-2 seconds, records per partition), peak RSS of the
decode process tree (`ru_maxrss` of the reaped children), output SHA-256 equality with the input, load at start.
Output <out>/pool.jsonl (rows) and <out>/pool-summary.json (medians of --reps per E and tree, ratios v13/v14).
SIMULATED: the pool is produced by the channel simulator; these are software codec rates on this host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MIB = 1 << 20
CHANNEL = {"seed": 7, "coverage": 3, "substitution_rate": 0.01}


def _git(tree: Path) -> str:
    return subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def one(strands: Path, manifest: Path, out: Path, workers: int) -> dict:
    """One pool decode in this process (the tree on PYTHONPATH); prints a JSON row."""
    import vnxdna
    from vnxdna.pipeline import stream as st
    from vnxdna.recovery.options import DecodeOptions
    row = {"vnxdna_file": vnxdna.__file__, "version": vnxdna.__version__, "load1_start": round(os.getloadavg()[0], 2)}
    t = time.perf_counter()
    try:
        rep = st.decode_stream(strands, manifest, out, options=DecodeOptions(workers=workers), overwrite=True, by_range=False)
        row["outcome"] = "SUCCESS"
        row["report"] = {k: v for k, v in rep.items() if k not in ("output",)}
    except Exception as error:  # noqa: BLE001 - an explicit failure is an outcome, recorded as such
        row["outcome"] = f"ERROR:{type(error).__name__}"
        row["error"] = str(error)[:500]
    row["seconds"] = round(time.perf_counter() - t, 4)
    row["output_sha256"] = _sha(out) if out.is_file() else None
    row["self_maxrss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    row["children_maxrss_mib"] = round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)
    return row


def _prepare(work: Path, extents: int, gen_tree: Path) -> tuple[Path, Path, Path]:
    code = ("import sys; from pathlib import Path; from vnxdna.pipeline import stream as st; from vnxdna.v4 import channel as ch;"
            "from vnxdna.v4 import datagen; w = Path(sys.argv[1]); e = int(sys.argv[2]);"
            "datagen.generate(w / 'in.bin', e << 20, 'random', 2000 + e);"
            "st.encode_stream(w / 'in.bin', w / 's.fasta', options=st.StreamOptions(extent_bytes=1 << 20, workers=4));"
            "ch.simulate_file(w / 's.fasta', w / 'r.fastq', ch.ChannelConfig(seed=7, coverage=3, substitution_rate=0.01))")
    subprocess.run([sys.executable, "-c", code, str(work), str(extents)], check=True,
                   env={**os.environ, "PYTHONPATH": str(gen_tree / "src")})
    return work / "in.bin", work / "r.fastq", work / "s.fasta.stream.json"


def run(a) -> None:
    trees = {"v13": Path(os.environ.get("V13_TREE", "/root/vnx-dna-lab/v14-base")), "v14": ROOT}
    labels = a.trees.split(",")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "pool.jsonl"
    commits = {c: _git(trees[c]) for c in labels}
    for e in [int(x) for x in a.extents.split(",")]:
        with tempfile.TemporaryDirectory(prefix="vnxpool-", dir=os.environ.get("VNX_ARCH_TMP")) as tmp:
            work = Path(tmp)
            src, pool, man = _prepare(work, e, trees["v13"])
            want = _sha(src)
            pool_bytes = pool.stat().st_size
            for r in range(a.reps):
                order = labels if r % 2 == 0 else labels[::-1]
                for c in order:
                    out = work / f"o-{c}.bin"
                    env = {**os.environ, "PYTHONPATH": str(trees[c] / "src")}
                    t = time.perf_counter()
                    p = subprocess.run([sys.executable, str(HERE / "pool_bench.py"), "one", "--strands", str(pool), "--manifest",
                                        str(man), "--out", str(out), "--workers", str(a.workers)], capture_output=True,
                                       text=True, env=env)
                    wall = time.perf_counter() - t
                    if p.returncode != 0:
                        raise RuntimeError(f"{c} E={e}: {p.stderr[-1500:]}")
                    row = json.loads(p.stdout.strip().splitlines()[-1])
                    if not row["vnxdna_file"].startswith(str(trees[c])):
                        raise RuntimeError(f"{c} imported {row['vnxdna_file']}")
                    row.update(label="SIMULATED", config=c, commit=commits[c], extents=e, pool_bytes=pool_bytes,
                               workers=a.workers, rep=r, order=order, wall_with_startup=round(wall, 4),
                               matches_input=row["output_sha256"] == want, channel=CHANNEL, time=time.time())
                    with open(rows_path, "a") as f:
                        f.write(json.dumps(row) + "\n")
                    rep = row.get("report", {})
                    print(f"E={e} {c} rep {r}: {row['seconds']:.2f}s {row['outcome']} match={row['matches_input']} "
                          f"partition={rep.get('partition_seconds')} pass1={rep.get('pass1_seconds')}", flush=True)
                    out.unlink(missing_ok=True)
    summary(out_dir)


def summary(out_dir: Path) -> dict:
    rows = [json.loads(line) for line in (out_dir / "pool.jsonl").read_text().splitlines()]
    by: dict = {}
    for r in rows:
        by.setdefault(r["extents"], {}).setdefault(r["config"], []).append(r)
    out = {"label": "SIMULATED (software codec rates on this host; the pool is produced by the channel simulator)",
           "channel": CHANNEL, "python": platform.python_version(), "platform": platform.platform(),
           "cpu_count": os.cpu_count(), "per_extents": {}}
    for e in sorted(by):
        rec: dict = {}
        for c, rs in by[e].items():
            secs = [r["seconds"] for r in rs]
            rep0 = rs[0].get("report", {})
            rec[c] = {"n": len(rs), "commit": rs[0]["commit"], "workers": rs[0]["workers"], "pool_bytes": rs[0]["pool_bytes"],
                      "seconds_median": round(statistics.median(secs), 3), "seconds_range": [round(min(secs), 3), round(max(secs), 3)],
                      "outcomes": sorted({r["outcome"] for r in rs}), "matches_input": all(r["matches_input"] for r in rs),
                      "false_success": sum(r["outcome"] == "SUCCESS" and not r["matches_input"] for r in rs),
                      "maxrss_mib_median": round(statistics.median(max(r["self_maxrss_mib"], r["children_maxrss_mib"]) for r in rs), 1),
                      "load1_range": [min(r["load1_start"] for r in rs), max(r["load1_start"] for r in rs)],
                      "report_keys": sorted(rep0.keys()),
                      "stages_median": {k: round(statistics.median(r["report"][k] for r in rs if k in r.get("report", {})), 3)
                                        for k in rep0 if isinstance(rep0[k], (int, float)) and k.endswith("_seconds")}}
        if "v13" in rec and "v14" in rec:
            rec["ratio_v13_over_v14_median"] = round(rec["v13"]["seconds_median"] / rec["v14"]["seconds_median"], 3)
        out["per_extents"][str(e)] = rec
    (out_dir / "pool-summary.json").write_text(json.dumps(out, indent=2) + "\n")
    for e, rec in out["per_extents"].items():
        print(f"E={e}: " + "  ".join(f"{c} {v['seconds_median']:.2f}s {v['outcomes']}" for c, v in rec.items() if isinstance(v, dict))
              + (f"  ratio v13/v14 {rec['ratio_v13_over_v14_median']}" if "ratio_v13_over_v14_median" in rec else ""))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "one", "summary"])
    ap.add_argument("--extents", default="4,8,16,32")
    ap.add_argument("--trees", default="v13,v14")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default=str(HERE / "pool"))
    ap.add_argument("--strands")
    ap.add_argument("--manifest")
    a = ap.parse_args(argv)
    if a.cmd == "one":
        print(json.dumps(one(Path(a.strands), Path(a.manifest), Path(a.out), a.workers)))
    elif a.cmd == "run":
        run(a)
    else:
        summary(Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
