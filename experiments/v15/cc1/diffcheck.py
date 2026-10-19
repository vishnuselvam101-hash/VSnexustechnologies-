"""V15 CC-1 differential check: output bytes AND decode reports identical between two trees, and across worker counts.

    PYTHONPATH=src python experiments/v15/cc1/diffcheck.py prep --dir D [--groups target,channel,...] [--seeds 2]
    PYTHONPATH=src python experiments/v15/cc1/diffcheck.py run --dir D --base TREE --new TREE --workers 1,4 --out rows.jsonl

Inputs: every workload of the P6E campaign manifest at smoke size (<= 1 MiB, first ``--seeds`` seeds), prepared once by
the suite's own ``prepare``. ``run`` decodes (or encodes) each input once per (tree, workers) in a fresh process and
compares the output SHA-256, the outcome (status or error type + code) and the full decode report with timing, RSS and
thread-count fields removed (``normalize``). A difference between the trees, or between worker counts of one tree, is
a FAILURE of the check (exit 1). SIMULATED inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ab  # noqa: E402  (same directory)

#: report keys that are timings, memory or host facts (removed before comparing); ``threads`` follows the worker count
VOLATILE = {"seconds", "stage_seconds", "peak_rss_bytes", "threads", "elapsed", "pass1_worker_cpu", "library_origin"}


def normalize(o):
    if isinstance(o, dict):
        return {k: normalize(v) for k, v in o.items()
                if k not in VOLATILE and not k.startswith("ns_") and not k.endswith("_seconds")}
    if isinstance(o, list):
        return [normalize(v) for v in o]
    return o


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def one(work: Path, workers: int, kind: str) -> dict:
    import vnxdna
    from vnxdna.v4 import decoder as de
    from vnxdna.v4 import encoder as en
    st = vnxdna.native_status()["kernels"]
    row = {"vnxdna_file": vnxdna.__file__, "all_native": all(v["backend"] == "native" for v in st.values()),
           "stale": [k for k, v in st.items() if v.get("library_older_than_source")]}
    tag = f"{os.getpid()}"
    if kind == "encode":
        out = work / f"s-diff-{tag}.fasta"
        en.encode_container(work / "a.vnx", out, en.DNAOptions(workers=workers))
        row.update(outcome="ENCODED", output_sha256=_sha(out), report=None)
        out.unlink()
        return row
    out = work / f"o-diff-{tag}.vnx"
    try:
        res = de.decode_reads(work / "r.fastq", out, de.DecodeOptions(workers=workers))
        row["outcome"] = res.status
        row["report"] = normalize(res.report)
    except Exception as error:  # noqa: BLE001 - an explicit failure is an outcome
        row["outcome"] = f"ERROR:{type(error).__name__}:{getattr(error, 'code', '')}"
        row["error"] = str(error).replace(str(work), "<work>")[:500]
        row["report"] = None
    row["output_sha256"] = _sha(out) if out.is_file() else None
    row["matches_input"] = row["output_sha256"] == _sha(work / "a.vnx")
    out.unlink(missing_ok=True)
    return row


def prep(a) -> None:
    S = ab._suite()
    m = S.load_manifest()
    sm = m["method"]["smoke"]
    groups = set(a.groups.split(",")) if a.groups else None
    base = Path(os.environ.get("BASE_TREE", "/root/vnx-dna-lab/v13-base"))
    for w in m["workloads"]:
        if groups and w["group"] not in groups:
            continue
        for seed in S.seeds_of(w, smoke={**sm, "seeds": a.seeds}):
            work = a.dir / f"{w['name']}-{seed}"
            if (work / "prep.json").is_file():
                continue
            work.mkdir(parents=True, exist_ok=True)
            info = S.prepare(work, m, w, seed, base, S.effective_size(w, sm))
            info["kind"] = w["kind"]
            (work / "prep.json").write_text(json.dumps(info, indent=1) + "\n")
            print("prepared", work.name, flush=True)


def _run_one(tree: Path, work: Path, workers: int, kind: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(tree / "src")}
    p = subprocess.run([sys.executable, str(Path(__file__)), "one", "--work", str(work), "--workers", str(workers),
                        "--kind", kind], env=env, capture_output=True, text=True, timeout=3600)
    if p.returncode != 0:
        return {"outcome": f"CRASH:{p.returncode}", "error": p.stderr[-1500:], "report": None, "output_sha256": None}
    row = json.loads(p.stdout.strip().splitlines()[-1])
    if not row["vnxdna_file"].startswith(str(tree)):
        raise SystemExit(f"{tree}: imported {row['vnxdna_file']}")
    if not row["all_native"] or row["stale"]:
        raise SystemExit(f"{tree}: native kernels missing or stale: {row}")
    return row


def run(a) -> int:
    trees = {"base": Path(a.base).resolve(), "new": Path(a.new).resolve()}
    commits = {k: ab._git(t) for k, t in trees.items()}
    workers = [int(x) for x in a.workers.split(",")]
    dirs = sorted(d for d in a.dir.iterdir() if (d / "prep.json").is_file())
    if a.only:
        dirs = [d for d in dirs if any(d.name.startswith(x) for x in a.only.split(","))]
    bad = 0
    for d in dirs:
        info = json.loads((d / "prep.json").read_text())
        kind = info.get("kind", "decode")
        res = {}
        for c, t in trees.items():
            for w in workers if kind == "decode" else workers[:1]:
                res[(c, w)] = _run_one(t, d, w, kind)
        keys = list(res)
        ref = res[keys[0]]
        diffs = [f"{c}/w{w}" for (c, w), r in res.items()
                 if (r["outcome"], r["output_sha256"], r["report"]) != (ref["outcome"], ref["output_sha256"], ref["report"])]
        row = {"input": d.name, "kind": kind, "commits": commits, "workers": workers, "identical": not diffs,
               "differs": diffs, "outcome": ref["outcome"], "matches_input": ref.get("matches_input"),
               "output_sha256": ref["output_sha256"],
               "report_sha256": hashlib.sha256(json.dumps(ref["report"], sort_keys=True).encode()).hexdigest(),
               "false_success": any(r["outcome"] == "SUCCESS" and not r.get("matches_input") for r in res.values()
                                    if kind == "decode")}
        bad += bool(diffs) or row["false_success"]
        with open(a.out, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{d.name:44s} {ref['outcome']:24s} identical={not diffs} {' '.join(diffs)}", flush=True)
    print(f"{len(dirs)} inputs, {bad} with differences or false successes")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("--dir", type=Path, required=True)
    p.add_argument("--groups", default="")
    p.add_argument("--seeds", type=int, default=2)
    p = sub.add_parser("run")
    p.add_argument("--dir", type=Path, required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--workers", default="1,4")
    p.add_argument("--only", default="")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("one")
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--kind", default="decode")
    a = ap.parse_args(argv)
    if a.cmd == "one":
        print(json.dumps(one(a.work, a.workers, a.kind)))
        return 0
    if a.cmd == "prep":
        prep(a)
        return 0
    return run(a)


if __name__ == "__main__":
    raise SystemExit(main())
