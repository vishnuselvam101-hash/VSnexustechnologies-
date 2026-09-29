"""Reproducible VNX-DNA channel experiments (V1 evaluation).

Usage (from the repository root, with vnx-dna installed):

    python research/experiments/run_experiments.py --out research/results

Each experiment records its ID, git commit, version, Python, OS, hardware,
dataset and dataset SHA-256, full configuration, seeds, observed channel
events, outcome and runtime in machine-readable JSON. A Markdown summary is
written alongside. Nothing is estimated: every row is a real decode attempt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from vnxdna.channel import ChannelConfig, simulate
from vnxdna.container.builder import StoreOptions, build_container
from vnxdna.container.reader import ContainerReader, load_manifest
from vnxdna.errors import VNXDNAError
from vnxdna.provenance import environment
from vnxdna.storage.decoder import DecodeOptions, ReadsSource, scan_reads
from vnxdna.storage.encoder import encode_container, geometry_of

SEEDS = range(10)


def dataset(size: int = 60_000) -> bytes:
    import random
    r = random.Random(4242)
    return r.randbytes(size // 2) + (b"VNX-DNA experiment dataset line.\n" * (size // 64 + 1))[: size - size // 2]


def attempt(pool, container, channel: ChannelConfig, decode: DecodeOptions, data: bytes) -> dict:
    t0 = time.perf_counter()
    reads, report = simulate(pool, channel)
    try:
        scan = scan_reads(reads, geometry_of(container.manifest), decode)
        loaded = load_manifest(container.manifest_bytes, None)
        out, stats = ContainerReader(loaded, ReadsSource(scan, loaded.manifest)).read_all()
        outcome = "recovered" if out == data else "WRONG-DATA"
        recovery = {**dict(scan.stats), **stats}
    except VNXDNAError as error:
        outcome, recovery = f"failed:{error.category}", {"message": str(error)}
    observed = {k: report[k] for k in ("strands_in", "strands_dropped", "strands_surviving", "substitutions", "insertions",
                                       "deletions", "reads_out", "observed_dropout_rate", "observed_substitution_rate",
                                       "observed_insertion_rate", "observed_deletion_rate")}
    return {"seed": channel.seed, "outcome": outcome, "runtime_s": round(time.perf_counter() - t0, 4), "observed": observed,
            "recovery": {k: v for k, v in recovery.items() if k in ("reads_valid", "reads_inner_corrected", "reads_rejected",
                                                                    "reads_length_mismatch", "reads_indel_repaired",
                                                                    "stripes_outer_recovered", "shards_erased",
                                                                    "max_erasures_in_a_stripe", "message")}}


def sweep(name: str, parameter: str, values, base: dict, decode: DecodeOptions, container, pool, data) -> dict:
    rows = []
    for value in values:
        trials = [attempt(pool, container, ChannelConfig(**{**base, parameter: value, "seed": seed, "shuffle": True}), decode, data)
                  for seed in SEEDS]
        ok = sum(t["outcome"] == "recovered" for t in trials)
        wrong = sum(t["outcome"] == "WRONG-DATA" for t in trials)
        rows.append({"value": value, "recovered": ok, "trials": len(trials), "wrong_data": wrong, "trials_detail": trials})
        print(f"  {name:28s} {parameter}={value:<8} recovered {ok}/{len(trials)} wrong={wrong}", file=sys.stderr)
    return {"experiment_id": name, "parameter": parameter, "base_channel": base, "decode_options": decode.__dict__, "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research/results")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = dataset()
    options = StoreOptions()
    container = build_container(data, options, None, "experiment.bin")
    pool = encode_container(container.manifest, container.manifest_bytes, container.stored_chunks).sequences
    plain = DecodeOptions()
    repair = DecodeOptions(indel_repair=True, max_indel=1)
    experiments = [
        sweep("X1-dropout", "dropout_rate", [0.0, 0.01, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30], {}, plain, container, pool, data),
        sweep("X2-substitution", "substitution_rate", [0.0, 0.001, 0.005, 0.01, 0.015, 0.02, 0.03], {}, plain, container, pool, data),
        sweep("X3-sub+dropout", "substitution_rate", [0.001, 0.005, 0.01], {"dropout_rate": 0.1}, plain, container, pool, data),
        sweep("X4-indel-no-repair", "deletion_rate", [0.0002, 0.0005, 0.001, 0.002],
              {"insertion_rate": 0.0}, plain, container, pool, data),
        sweep("X5-indel-with-repair", "deletion_rate", [0.0002, 0.0005, 0.001, 0.002],
              {"insertion_rate": 0.0}, repair, container, pool, data),
        sweep("X6-mixed-indel-repair", "insertion_rate", [0.0005, 0.001],
              {"deletion_rate": 0.0005, "substitution_rate": 0.002}, repair, container, pool, data),
    ]
    record = {"suite": "VNX-DNA V1 channel experiments", "environment": environment(),
              "dataset": {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "generator": "dataset() in this script"},
              "store_options": options.public_dict(), "strands": len(pool), "strand_nt": container.manifest.strand.strand_nt,
              "seeds": list(SEEDS), "experiments": experiments}
    (out / "channel_experiments.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    lines = ["# VNX-DNA channel experiments (generated)", "",
             f"Commit `{record['environment']['git']['commit']}` (dirty={record['environment']['git']['dirty']}), "
             f"vnx-dna {record['environment']['vnxdna_version']}, Python {record['environment']['python']}, "
             f"{record['environment']['platform']}, {record['environment']['cpu_count']} CPUs.", "",
             f"Dataset: {len(data):,} B (sha256 `{record['dataset']['sha256'][:16]}…`), default profile "
             f"({options.data_shards}+{options.parity_shards} outer, {options.payload_bytes} B payload, {options.inner_parity_bytes} B inner parity, "
             f"{container.manifest.strand.strand_nt} nt strands), {len(pool)} strands. {len(SEEDS)} seeds per point.", "",
             "Outcome counts are real decode attempts; *wrong* counts runs that returned incorrect bytes (must be 0).", ""]
    for e in experiments:
        lines += [f"## {e['experiment_id']} (varying `{e['parameter']}`; base {e['base_channel'] or '{}'}; "
                  f"indel repair {'on' if e['decode_options']['indel_repair'] else 'off'})", "",
                  "| value | recovered | wrong data | mean observed dropped strands | mean observed events (sub/ins/del) |",
                  "|---|---|---|---|---|"]
        for row in e["rows"]:
            t = row["trials_detail"]
            mean = lambda key: sum(x["observed"][key] for x in t) / len(t)  # noqa: E731
            lines.append(f"| {row['value']} | {row['recovered']}/{row['trials']} | {row['wrong_data']} | {mean('strands_dropped'):.1f} | "
                         f"{mean('substitutions'):.0f} / {mean('insertions'):.0f} / {mean('deletions'):.0f} |")
        lines.append("")
    (out / "channel_experiments.md").write_text("\n".join(lines))
    print(f"wrote {out/'channel_experiments.json'} and .md", file=sys.stderr)


if __name__ == "__main__":
    main()
