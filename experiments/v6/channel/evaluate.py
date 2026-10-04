"""End-to-end evaluation: encode a payload → simulate channel model → decode → record outcome (SIMULATED).

    python experiments/v6/channel/evaluate.py --models mixed-mild,dropout-10 --seeds 10 --size 20000 --out results.jsonl
    python experiments/v6/channel/evaluate.py --models all --profile v4-balanced --option strand_order='"interleaved"'

Output is JSON lines: one record per trial plus a final summary record with Wilson 95 % intervals. Every record carries
"classification": "SIMULATED". Outcomes: ``exact`` (SUCCESS and SHA-256 equal), ``failed-detected`` (decoder did not report
SUCCESS), ``FALSE_SUCCESS`` (SUCCESS with a different container; a bug, makes the exit status 2).
A trial's seed is base-seed + trial index; results do not depend on --jobs or the simulator worker count.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import channel as chn  # noqa: E402

from vnxdna.v2.experiment import wilson  # noqa: E402
from vnxdna.v4 import archive as ar  # noqa: E402
from vnxdna.v4 import datagen  # noqa: E402
from vnxdna.v4 import decoder as de  # noqa: E402
from vnxdna.v4 import encoder as en  # noqa: E402
from vnxdna.v4.constraints import iter_fasta  # noqa: E402
from vnxdna.v4.errors import VNXError  # noqa: E402


def prepare(workdir: Path, size: int, data_seed: int, profile: str, options: dict) -> dict:
    """Encode a random payload once; returns paths and encode facts."""
    datagen.generate(workdir / "payload.bin", size, "random", data_seed)
    ar.build_archive([workdir / "payload.bin"], workdir / "a.vnx", ar.ArchiveOptions())
    container = workdir / "a.vnx"
    t = time.perf_counter()
    rep = en.encode_container(container, workdir / "strands.fasta", en.DNAOptions(profile=profile, **options))
    strand_nt = len(next(iter_fasta(workdir / "strands.fasta"))[1])
    return {"container": str(container), "strands": str(workdir / "strands.fasta"),
            "container_sha256": chn.sha256_file(container), "container_bytes": container.stat().st_size,
            "payload_sha256": chn.sha256_file(workdir / "payload.bin"), "strand_count": rep["strands"],
            "encode_seconds": round(time.perf_counter() - t, 3),
            "strand_nt": strand_nt, "nt_per_container_byte": round(rep["strands"] * strand_nt / container.stat().st_size, 4)}


def trial(job: dict) -> dict:
    model = chn.load_model(job["model"])
    work = Path(tempfile.mkdtemp(prefix="vnx-eval-", dir=job.get("tmp")))
    try:
        reads = work / "reads.fastq"
        sc = chn.simulate_with_sidecar(model, job["strands"], reads, job["seed"], workers=job["sim_workers"],
                                       command=job["command"])
        opts = de.DecodeOptions(workers=job["decode_workers"], **job["decode_options"])
        t = time.perf_counter()
        out = work / "out.vnx"
        try:
            res = de.decode_reads(reads, out, opts, overwrite=True, workdir=work)
            status, rep = res.status, res.report
        except VNXError as error:
            status, rep = f"ERROR:{type(error).__name__}", {"error": str(error)}
        dec_s = time.perf_counter() - t
        match = status == "SUCCESS" and out.exists() and chn.sha256_file(out) == job["container_sha256"]
        false_success = status == "SUCCESS" and not match
        outcome = "FALSE_SUCCESS" if false_success else ("exact" if match else "failed-detected")
        stats = {k: v for k, v in rep.items() if isinstance(v, (int, float, str, bool)) or v is None}
        for k in ("reads", "stage_seconds", "failed_groups", "outer_v6"):
            stats[k] = rep.get(k)
        stats["recovery_spent"] = (rep.get("recovery_plan") or {}).get("spent")
        decoder_cfg = json.loads(json.dumps(asdict(opts), default=str))
        result = {"status": status, "outcome": outcome, "sha_match": bool(match), "false_success": bool(false_success),
                  "container_sha256_expected": job["container_sha256"],
                  "container_sha256_decoded": chn.sha256_file(out) if out.exists() else None,
                  "recovery": json.loads(json.dumps(stats, default=str)), "decode_seconds": round(dec_s, 3)}
        sc["decoder"], sc["decode_result"] = decoder_cfg, result
        chn.write_sidecar(reads, sc)
        if job.get("keep"):
            keep = Path(job["keep"]) / f"{model.name}-seed{job['seed']}"
            keep.mkdir(parents=True, exist_ok=True)
            for f in (reads, chn.sidecar_path(reads)):
                shutil.copy(f, keep / f.name)
        return {"record": "trial", "classification": "SIMULATED", "model": model.name, "model_version": model.version,
                "model_sha256": model.sha256, "seed": job["seed"], "profile": job["profile"], "encode_options": job["options"],
                "decoder": decoder_cfg, "reads": sc["output"]["reads"], "reads_sha256": sc["output"]["sha256"],
                "simulate_seconds": sc["run"]["seconds"], "realised_rates": sc["realised_rates"], **result}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def summarize(trials: list[dict]) -> dict:
    out = {}
    for t in trials:
        c = out.setdefault(t["model"], {"trials": 0, "exact": 0, "failed_detected": 0, "false_success": 0, "decode_seconds": []})
        c["trials"] += 1
        c["exact"] += t["outcome"] == "exact"
        c["failed_detected"] += t["outcome"] == "failed-detected"
        c["false_success"] += t["false_success"]
        c["decode_seconds"].append(t["decode_seconds"])
    for c in out.values():
        lo, hi = wilson(c["exact"], c["trials"])
        secs = sorted(c.pop("decode_seconds"))
        c.update({"success_rate": round(c["exact"] / c["trials"], 4), "wilson95_low": round(lo, 4), "wilson95_high": round(hi, 4),
                  "median_decode_seconds": secs[len(secs) // 2]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="mixed-mild", help="comma-separated names/paths, or 'all'")
    ap.add_argument("--seeds", type=int, default=5, help="trials per model")
    ap.add_argument("--base-seed", type=int, default=1000)
    ap.add_argument("--size", type=int, default=20000, help="random payload bytes")
    ap.add_argument("--data-seed", type=int, default=6201)
    ap.add_argument("--profile", default="v4-balanced")
    ap.add_argument("--option", action="append", default=[], help="DNAOptions key=JSON-value (repeatable)")
    ap.add_argument("--decode-option", action="append", default=[], help="DecodeOptions key=JSON-value (repeatable)")
    ap.add_argument("--jobs", type=int, default=1, help="parallel trials")
    ap.add_argument("--sim-workers", type=int, default=1)
    ap.add_argument("--out", help="JSON-lines file (default stdout)")
    ap.add_argument("--keep", help="directory to keep reads + sidecars of every trial")
    a = ap.parse_args(argv)

    def kv(items):
        d = {}
        for it in items:
            k, v = it.split("=", 1)
            d[k] = json.loads(v)
        return d
    options, dec_options = kv(a.option), kv(a.decode_option)
    names = chn.model_names() if a.models == "all" else a.models.split(",")
    command = " ".join(sys.argv)
    work = Path(tempfile.mkdtemp(prefix="vnx-eval-setup-"))
    fh = open(a.out, "w") if a.out else sys.stdout
    try:
        t0 = time.perf_counter()
        setup = prepare(work, a.size, a.data_seed, a.profile, options)
        header = {"record": "header", "classification": "SIMULATED", "statement": chn.STATEMENT, "command": command,
                  "git": chn.git_info(), "payload_bytes": a.size, "data_seed": a.data_seed, "profile": a.profile,
                  "encode_options": options, "decode_options": dec_options, "models": names, "trials_per_model": a.seeds,
                  "base_seed": a.base_seed, "setup": {k: v for k, v in setup.items() if k not in ("container", "strands")}}
        print(json.dumps(header, sort_keys=True), file=fh, flush=True)
        jobs = [{"model": n, "seed": a.base_seed + i, "strands": setup["strands"], "container_sha256": setup["container_sha256"],
                 "profile": a.profile, "options": options, "decode_options": dec_options, "decode_workers": 1,
                 "sim_workers": a.sim_workers, "command": command, "keep": a.keep, "tmp": str(work)}
                for n in names for i in range(a.seeds)]
        if a.jobs > 1:
            with ProcessPoolExecutor(max_workers=a.jobs) as pool:
                trials = list(pool.map(trial, jobs))
        else:
            trials = [trial(j) for j in jobs]
        for t in trials:
            print(json.dumps(t, sort_keys=True), file=fh, flush=True)
        summary = {"record": "summary", "classification": "SIMULATED", "statement": chn.STATEMENT,
                   "false_success_total": sum(t["false_success"] for t in trials), "trials": len(trials),
                   "wall_seconds": round(time.perf_counter() - t0, 2), "per_model": summarize(trials)}
        print(json.dumps(summary, sort_keys=True), file=fh, flush=True)
        if summary["false_success_total"]:
            print("FALSE SUCCESS DETECTED: this is a decoder bug; stop and report", file=sys.stderr)
            return 2
        return 0
    finally:
        if a.out:
            fh.close()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
