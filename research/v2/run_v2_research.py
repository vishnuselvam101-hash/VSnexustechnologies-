"""Run every V2 measurement that the documentation reports, and save raw JSON.

Usage::

    python research/v2/run_v2_research.py --out research/results/v2 [--only NAME ...] [--workers N]

Experiments (each is a list of ``vnx-dna experiment run``-equivalent calls via
:func:`vnxdna.v2.experiment.run_experiment`, plus the stage benchmark):

* ``stages``     per-stage benchmark A–H at 100 KB, 1 MB, 10 MB
* ``profiles``   density/speed of each storage profile + Monte Carlo robustness under one harsh channel
* ``coverage``   exact-recovery rate vs sequencing coverage (1x … 50x)
* ``errors``     vs substitution rate and vs indel rate, with and without cluster + consensus
* ``abundance``  vs uneven abundance (log-normal sigma)
* ``montecarlo`` 1,000 trials of the canonical channel with consensus and 10,000 trials of direct decoding
* ``chunks``     chunk size selection: store/encode/recover/extract time and peak RAM for 64 KiB … 16 MiB (200 MB input, real CLI)

Everything is SOFTWARE SIMULATION. ``render_v2_tables.py`` turns the JSON
into the tables in ``docs/``. No number in the docs is typed by hand.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from vnxdna.provenance import environment
from vnxdna.v2.experiment import run_experiment
from vnxdna.v2.profiles import PROFILES, options_for
from vnxdna.v2.scale import generate_file
from vnxdna.v2.sequencing import SequencingConfig
from vnxdna.v2.stages import run_stages

CANONICAL = SequencingConfig(seed=42, coverage=10, substitution_rate=0.001, insertion_rate=0.0001, deletion_rate=0.0001,
                             dropout_rate=0.02)


def _experiment(src: Path, work: Path, name: str, channel: SequencingConfig, trials: int, options, consensus, workers: int) -> dict:
    out = work / name
    r = run_experiment(src, out, channel=channel, trials=trials, options=options, use_consensus=consensus, workers=workers, overwrite=True)
    summary = r["summary"]
    shutil.rmtree(out, ignore_errors=True)
    return {"name": name, "channel": channel.to_dict(), "profile": options.profile, "consensus": consensus, "trials": trials,
            "summary": summary}


def run(out: Path, only: set[str] | None, workers: int, quick: bool = False) -> None:
    scale = (lambda n: max(2, n // 20)) if quick else (lambda n: n)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="vnxdna-research-"))
    try:
        def want(name: str) -> bool:
            return only is None or name in only

        def save(name: str, payload: dict) -> None:
            payload = {"generated_by": "research/v2/run_v2_research.py", "simulation": "SOFTWARE SIMULATION",
                       "environment": environment(), **payload}
            (out / f"{name}.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
            print(f"wrote {out / (name + '.json')}", flush=True)

        small = work / "small.bin"
        generate_file(small, 20_000, "mixed", 7)
        tiny = work / "tiny.bin"
        generate_file(tiny, 4_000, "mixed", 8)
        options = options_for("balanced", chunk_size=8192)

        if want("stages"):
            t = time.perf_counter()
            save("stages", {"results": run_stages([100_000] if quick else [100_000, 1_000_000, 10_000_000], seed=1, workers=workers),
                            "runtime_s": time.perf_counter() - t})

        if want("profiles"):
            from vnxdna.v2.archive import store_file
            from vnxdna.v2.encoder import encode_file
            from vnxdna.v2.api import _recover_v2
            from vnxdna.v2.decoder import DecodeOptionsV2
            medium = work / "medium.bin"
            generate_file(medium, 400_000 if quick else 4_000_000, "mixed", 9)
            rows = []
            harsh = SequencingConfig(seed=100, coverage=3, substitution_rate=0.005, insertion_rate=0.0005, deletion_rate=0.0005,
                                     dropout_rate=0.08)
            for name in PROFILES:
                opts = PROFILES[name]
                t = time.perf_counter()
                st = store_file(medium, work / f"{name}.vxdna", options=opts, workers=workers, overwrite=True)
                t_store = time.perf_counter() - t
                t = time.perf_counter()
                enc = encode_file(work / f"{name}.vxdna", work / f"{name}.vxs", workers=workers, overwrite=True)
                t_encode = time.perf_counter() - t
                t = time.perf_counter()
                _recover_v2(work / f"{name}.vxs", work / f"{name}.out", key=None, options=DecodeOptionsV2(), workers=workers,
                            overwrite=True, temp_dir=work)
                t_decode = time.perf_counter() - t
                robust = _experiment(small, work, f"profile-{name}", harsh, scale(100), replace(opts, chunk_size=min(opts.chunk_size, 8192)),
                                     True, workers)
                rows.append({"profile": name, "options": opts.public_dict(), "expected_overhead": opts.overhead(),
                             "input_bytes": st["original_bytes"], "stored_bytes": st["stored_bytes"], "strands": enc["strands"],
                             "dna_bases": enc["dna_bases"], "strand_nt": enc["efficiency"]["strand_nt"],
                             "bases_per_original_byte": enc["efficiency"]["bases_per_original_byte"],
                             "bases_per_stored_byte": enc["efficiency"]["bases_per_stored_byte"],
                             "store_s": t_store, "encode_s": t_encode, "decode_s": t_decode, "harsh_channel": robust})
                for suffix in ("vxdna", "vxs", "vxs.vxidx", "out"):
                    (work / f"{name}.{suffix}").unlink(missing_ok=True)
            save("profiles", {"input": "4,000,000 B mixed pattern (seed 9); robustness on 20,000 B (seed 7), 100 trials",
                              "harsh_channel": harsh.to_dict(), "rows": rows})

        if want("coverage"):
            rows = []
            for cov in (1, 2, 5, 10, 20, 50):
                ch = replace(CANONICAL, coverage=float(cov), seed=1000 + cov)
                rows.append(_experiment(small, work, f"cov-{cov}", ch, scale(40), options, cov > 1, workers))
                rows.append(_experiment(small, work, f"cov-{cov}-direct", ch, scale(40), options, False, workers)) if cov > 1 else None
            save("coverage", {"input": "20,000 B mixed (seed 7), profile balanced (8 KiB chunks), 40 trials per point",
                              "rows": [r for r in rows if r]})

        if want("errors"):
            rows = []
            for sub in (0.001, 0.005, 0.01, 0.02, 0.04, 0.06):
                ch = replace(CANONICAL, substitution_rate=sub, insertion_rate=0.0, deletion_rate=0.0, seed=2000 + int(sub * 1e4))
                rows.append({"axis": "substitution", "rate": sub, **_experiment(small, work, f"sub-{sub}", ch, scale(30), options, True, workers)})
                rows.append({"axis": "substitution", "rate": sub,
                             **_experiment(small, work, f"sub-{sub}-direct", ch, scale(30), options, False, workers)})
            for indel in (0.0001, 0.0005, 0.001, 0.003, 0.005, 0.01):
                ch = replace(CANONICAL, insertion_rate=indel, deletion_rate=indel, seed=3000 + int(indel * 1e5))
                rows.append({"axis": "indel", "rate": indel, **_experiment(small, work, f"indel-{indel}", ch, scale(30), options, True, workers)})
                rows.append({"axis": "indel", "rate": indel,
                             **_experiment(small, work, f"indel-{indel}-direct", ch, scale(30), options, False, workers)})
            save("errors", {"input": "20,000 B mixed (seed 7), coverage 10 (poisson), dropout 2 %, 30 trials per point", "rows": rows})

        if want("abundance"):
            rows = []
            for sigma in (0.0, 0.5, 1.0, 1.5):
                ch = replace(CANONICAL, coverage=5.0, coverage_model="lognormal", abundance_sigma=sigma, seed=4000 + int(sigma * 10))
                rows.append({"sigma": sigma, **_experiment(small, work, f"abundance-{sigma}", ch, scale(40), options, True, workers)})
            save("abundance", {"input": "20,000 B mixed (seed 7), mean coverage 5 with log-normal abundance, 40 trials per point",
                               "rows": rows})

        if want("chunks"):
            from vnxdna.v2.scale import _cli, run_measured
            big = work / "chunks.bin"
            generate_file(big, 20_000_000 if quick else 200_000_000, "mixed", 11)
            rows = []
            for chunk in (64 << 10, 256 << 10, 1 << 20, 4 << 20, 16 << 20):
                row = {"chunk_size": chunk, "stages": {}}
                for name, args in (("store", ["store", str(big), "-o", str(work / "c.vxdna"), "--chunk-size", str(chunk), "--force", "--json"]),
                                   ("encode", ["encode", str(work / "c.vxdna"), "-o", str(work / "c.vxs"), "--force", "--json"]),
                                   ("extract_container", ["extract", str(work / "c.vxdna"), "--offset", str(big.stat().st_size // 2), "--length", "4096",
                                                          "-o", str(work / "x.bin"), "--force", "--json"]),
                                   ("extract_dna", ["extract", str(work / "c.vxs"), "--offset", str(big.stat().st_size // 2), "--length", "4096",
                                                    "-o", str(work / "y.bin"), "--force", "--temp-dir", str(work), "--json"]),
                                   ("recover", ["recover", str(work / "c.vxs"), "-o", str(work / "c.out"), "--force", "--temp-dir",
                                                str(work), "--json"])):
                    r = run_measured(_cli() + args + (["--workers", str(workers)] if workers and name in ("store", "encode", "recover") else []))
                    if r["returncode"] != 0:
                        raise RuntimeError(f"chunk {chunk} {name}: {r['stderr_tail']}")
                    row["stages"][name] = {k: r[k] for k in ("wall_s", "cpu_utilisation", "peak_rss_tree_bytes")}
                    row["stages"][name]["report"] = {k: (r["report"] or {}).get(k) for k in
                                                     ("stored_bytes", "container_bytes", "strands", "dna_bases", "container_bytes_read",
                                                      "strands_processed", "chunks")}
                row["identical"] = (work / "c.out").read_bytes() == big.read_bytes()
                rows.append(row)
                for name in ("c.vxdna", "c.vxs", "c.vxs.vxidx", "c.out", "x.bin", "y.bin"):
                    (work / name).unlink(missing_ok=True)
            save("chunks", {"input": "200,000,000 B mixed (seed 11), profile balanced except chunk size; random access = 4,096 B at offset 100,000,000",
                            "rows": rows})

        if want("montecarlo"):
            t = time.perf_counter()
            canonical = _experiment(small, work, "mc-canonical", CANONICAL, scale(1000), options, True, workers)
            direct_channel = replace(CANONICAL, seed=50_000, insertion_rate=0.0, deletion_rate=0.0, coverage=5.0)
            direct = _experiment(tiny, work, "mc-direct", direct_channel, scale(10_000), options_for("balanced", chunk_size=4096), False, workers)
            save("montecarlo", {"canonical": canonical, "direct": direct, "runtime_s": time.perf_counter() - t,
                                "inputs": {"canonical": "20,000 B mixed (seed 7)", "direct": "4,000 B mixed (seed 8)"}})
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default="research/results/v2")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--quick", action="store_true", help="smoke test: tiny trial counts and inputs (results are not for the docs)")
    args = parser.parse_args()
    run(Path(args.out), set(args.only) if args.only else None, args.workers, args.quick)


if __name__ == "__main__":
    sys.exit(main())
