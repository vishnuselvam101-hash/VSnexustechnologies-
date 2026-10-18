"""V14 Phase 6 workstream E: realistic simulated-channel benchmark suite (SIMULATED channel data only).

Extends the V11/V13/V14 end-to-end harness (experiments/v14/bench/bench.py -> experiments/v11/bench/bench.py ->
experiments/arch/compare.py, process-tree RSS sampler of experiments/v10/opt/bench.py) and its statistics (paired per
seed, alternating order, one discarded warm-up per configuration, percentile bootstrap CI of the median ratio). The
workloads, seeds, configurations, CI-extension rule and verdict rules are PREREGISTERED in workloads.json (committed
before any run); this script refuses a manifest that does not validate.

    # validate the preregistered manifest (no runs)
    PYTHONPATH=src python experiments/v14/bench/p6e-channels/suite.py validate
    # harness validation: every workload once at reduced size (timings are NOT results)
    PYTHONPATH=src python experiments/v14/bench/p6e-channels/suite.py run --smoke --out <dir>
    # the full measurement (see README.md for the governed command), then the summary
    BASE_TREE=<6bf7749> V13_TREE=<58171b8> PYTHONPATH=src python experiments/v14/bench/p6e-channels/suite.py run --out <dir>
    PYTHONPATH=src python experiments/v14/bench/p6e-channels/suite.py summary --out <dir>
    # one configuration on one prepared input (a reproducer)
    PYTHONPATH=src python experiments/v14/bench/p6e-channels/suite.py repro --workload W --seed S --config v14 [--smoke]

Configurations: v12 (BASE_TREE, default /root/vnx-dna-lab/v13-base = 6bf7749), v13 (V13_TREE, default
/root/vnx-dna-lab/v14-base = 58171b8), v14 (V14_TREE, default the tree holding this file) and v14aa (the v14 tree
again, a separate paired configuration: the in-run A/A noise band). EXPECT_V12_COMMIT / EXPECT_V13_COMMIT /
EXPECT_V14_COMMIT (commit prefixes) make the run refuse a tree at any other commit or with uncommitted changes under src/ (``+dirty``). Outputs in --out: rows.jsonl (every measured run),
warmup.jsonl (discarded runs), prep.jsonl (per-input provenance), summary.json; anomalous inputs are kept in --keep.
The module top level imports only the standard library: ``one`` runs inside each tree's own interpreter environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MANIFEST = HERE / "workloads.json"
MODEL_DIR = ROOT / "experiments" / "v6" / "channel" / "models"
SCHEMA = "vnx.p6e.workloads/1"
MIB = 1 << 20

CONFIGS = ("v12", "v13", "v14", "v14aa")
RATIOS = (("v12", "v14"), ("v13", "v14"), ("v12", "v13"), ("v14", "v14aa"))
AA = ("v14", "v14aa")
TIERS = (2.0, 5.0, 10.0)
EXTEND_REL_WIDTH = 0.25
EXPECTS = ("success", "no-false-success", "explicit-failure")
KINDS = ("decode", "encode")
PAYLOADS = ("random", "text", "repetitive", "binary", "mixed", "zero-heavy")
GROUPS = ("target", "channel", "large", "payload", "adversarial")
ARCHIVE_OPTION_KEYS = {"compression", "dedup", "chunk_size", "level"}
LOSS_KEYS = {"dropout", "burst_count", "burst_length"}
#: adversary type -> required numeric parameters (name -> (min, max))
ADVERSARIES = {
    "foreign-mix": {"fraction": (0.0, 0.95)},
    "truncate": {"keep_fraction": (0.0, 1.0)},
    "junk-reads": {"fraction": (0.0, 5.0)},
    "malformed": {"fraction": (0.0, 1.0)},
    "jackpot": {"fraction": (0.0, 1.0), "copies": (2, 1000)},
    "poison": {"fraction": (0.0, 1.0), "mutations": (1, 200), "copies": (1, 1000)},
    "empty": {},
}
#: the channel simulator's parameters (vnxdna.simulation.channel.ChannelConfig, without ``seed``); kept literal here so
#: the manifest validates without importing vnxdna; tests/v14 checks it against the dataclass
CHANNEL_FIELDS = {
    "substitution_rate": 0.0, "insertion_rate": 0.0, "deletion_rate": 0.0, "dropout_rate": 0.0, "coverage": 1.0,
    "coverage_model": "fixed", "coverage_dispersion": 5.0, "duplication_rate": 0.0, "homopolymer_min_run": 3,
    "homopolymer_indel_multiplier": 1.0, "homopolymer_substitution_multiplier": 1.0, "gc_bias_strength": 0.0,
    "gc_bias_optimum": 0.5, "burst_rate": 0.0, "burst_max_len": 0, "n_rate": 0.0, "reverse_complement_rate": 0.0,
    "quality_correct": 35, "quality_error": 12, "quality_informative": 0.0, "shuffle_window": 0,
}
SMOKE_LABEL = "harness validation, contended host"


class ManifestError(ValueError):
    """The workload manifest is malformed; nothing is run."""


# ================================================================================================== manifest
def _req(cond: bool, msg: str) -> None:
    if not cond:
        raise ManifestError(msg)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _validate_channel(m: dict, w: dict) -> None:
    name, ch = w["name"], w.get("channel")
    _req(isinstance(ch, dict), f"{name}: decode workloads need a channel object")
    _req(set(ch) <= {"ref", "params", "model", "overrides", "loss"}, f"{name}: unknown channel keys {sorted(set(ch))}")
    _req(not ("model" in ch and "ref" in ch), f"{name}: channel takes either 'model' or 'ref', not both")
    _req("model" in ch or "ref" in ch or "params" in ch, f"{name}: channel needs 'ref', 'params' or 'model'")
    _req(not ("overrides" in ch and "model" not in ch), f"{name}: 'overrides' only applies to a model")
    _req(not ("params" in ch and "model" in ch), f"{name}: use 'overrides' with a model")
    if "ref" in ch:
        _req(ch["ref"] in m.get("channels", {}), f"{name}: unknown channel ref {ch['ref']!r}")
    if "model" in ch:
        _req(isinstance(ch["model"], str) and (MODEL_DIR / f"{ch['model']}.json").is_file(),
             f"{name}: unknown channel model {ch.get('model')!r}")
    for key in ("params", "overrides"):
        bad = set(ch.get(key, {})) - set(CHANNEL_FIELDS)
        _req(not bad, f"{name}: unknown channel parameters {sorted(bad)}")
    bad = set(ch.get("loss", {})) - LOSS_KEYS
    _req(not bad, f"{name}: unknown loss parameters {sorted(bad)}")
    for k, v in ch.get("loss", {}).items():
        _req(_num(v) and v >= 0, f"{name}: loss {k} must be a non-negative number")
    _req(0 <= ch.get("loss", {}).get("dropout", 0) <= 1, f"{name}: loss dropout must be in [0, 1]")


def validate_manifest(m: dict) -> dict:
    """Structural validation of a workload manifest; returns it unchanged or raises ManifestError."""
    _req(isinstance(m, dict), "manifest must be a JSON object")
    _req(m.get("schema") == SCHEMA, f"schema must be {SCHEMA!r}")
    _req(m.get("label") == "SIMULATED", "label must be SIMULATED")
    _req(isinstance(m.get("workloads"), list) and m["workloads"], "workloads must be a non-empty list")
    for ref, params in m.get("channels", {}).items():
        bad = set(params) - set(CHANNEL_FIELDS)
        _req(not bad, f"channel ref {ref}: unknown parameters {sorted(bad)}")
    names = set()
    for w in m["workloads"]:
        _req(isinstance(w, dict) and isinstance(w.get("name"), str) and w["name"], "every workload needs a name")
        name = w["name"]
        _req(name not in names, f"duplicate workload name {name!r}")
        names.add(name)
        _req(w.get("group") in GROUPS, f"{name}: group must be one of {GROUPS}")
        _req(w.get("kind") in KINDS, f"{name}: kind must be one of {KINDS}")
        _req(isinstance(w.get("size"), int) and not isinstance(w["size"], bool) and w["size"] > 0,
             f"{name}: size must be a positive integer")
        _req(isinstance(w.get("workers"), int) and not isinstance(w["workers"], bool) and w["workers"] >= 1,
             f"{name}: workers must be a positive integer")
        _req(w.get("payload") in PAYLOADS, f"{name}: payload must be one of {PAYLOADS}")
        _req(w.get("expect") in EXPECTS, f"{name}: expect must be one of {EXPECTS}")
        s = w.get("seeds")
        _req(isinstance(s, dict) and set(s) == {"start", "n", "extend_to"}, f"{name}: seeds needs start, n, extend_to")
        _req(all(isinstance(s[k], int) and not isinstance(s[k], bool) for k in s), f"{name}: seeds values are integers")
        _req(s["start"] >= 0 and s["n"] >= 1 and s["extend_to"] >= s["n"], f"{name}: need start >= 0, n >= 1, extend_to >= n")
        _req(not (set(w.get("archive_options", {})) - ARCHIVE_OPTION_KEYS),
             f"{name}: archive_options keys must be within {sorted(ARCHIVE_OPTION_KEYS)}")
        if w["kind"] == "encode":
            _req("channel" not in w and "adversary" not in w, f"{name}: encode workloads take no channel or adversary")
        else:
            _validate_channel(m, w)
        if "adversary" in w:
            a = w["adversary"]
            _req(isinstance(a, dict) and a.get("type") in ADVERSARIES, f"{name}: adversary type must be one of "
                 f"{sorted(ADVERSARIES)}")
            spec = ADVERSARIES[a["type"]]
            _req(set(a) - {"type"} == set(spec), f"{name}: adversary {a['type']} takes exactly {sorted(spec)}")
            for k, (lo, hi) in spec.items():
                _req(_num(a[k]) and lo <= a[k] <= hi, f"{name}: adversary {k} must be in [{lo}, {hi}]")
    return m


def load_manifest(path: Path | str = MANIFEST) -> dict:
    try:
        m = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ManifestError(f"cannot read manifest {path}: {error}") from None
    return validate_manifest(m)


def workload(m: dict, name: str) -> dict:
    for w in m["workloads"]:
        if w["name"] == name:
            return w
    raise ManifestError(f"unknown workload {name!r}")


def seeds_of(w: dict, *, extended: bool = False, smoke: dict | None = None) -> list[int]:
    s = w["seeds"]
    if smoke:
        return list(range(s["start"], s["start"] + min(s["n"], int(smoke["seeds"]))))
    return list(range(s["start"], s["start"] + (s["extend_to"] if extended else s["n"])))


def extension_seeds(w: dict) -> list[int]:
    s = w["seeds"]
    return list(range(s["start"] + s["n"], s["start"] + s["extend_to"]))


def effective_size(w: dict, smoke: dict | None) -> int:
    return min(w["size"], int(smoke["max_input_bytes"])) if smoke else w["size"]


def channel_spec(m: dict, w: dict) -> dict:
    """Resolve a workload's channel: full parameter dict, loss dict, model provenance and the simulator path.

    ``simulator`` is ``v4`` (vnxdna.v4.channel.simulate_file on ChannelConfig(seed=..., **params), exactly the V13
    harness path) when no model and no strand loss are involved, else ``v6-compose`` (experiments/v6/channel: strand
    loss, then the read channel) on a model document holding every parameter explicitly."""
    ch = w["channel"]
    if "model" in ch:
        doc = json.loads((MODEL_DIR / f"{ch['model']}.json").read_text())
        params = {**doc["channel"], **ch.get("overrides", {})}
        loss = {**doc["loss"], **ch.get("loss", {})}
        model = {"name": doc["name"], "version": doc["version"],
                 "sha256": hashlib.sha256((MODEL_DIR / f"{ch['model']}.json").read_bytes()).hexdigest()}
    else:
        params = {**m.get("channels", {}).get(ch.get("ref"), {}), **ch.get("params", {})}
        loss = {"dropout": 0.0, "burst_count": 0, "burst_length": 0, **ch.get("loss", {})}
        model = None
    lossy = bool(loss.get("dropout")) or bool(loss.get("burst_count") and loss.get("burst_length"))
    return {"params": params, "loss": loss, "model": model,
            "simulator": "v6-compose" if (model or lossy) else "v4"}


def model_document(name: str, spec: dict) -> dict:
    """A complete, explicit v6 channel-model document for ``spec`` (every channel field filled from the defaults)."""
    return {"name": name, "version": "1.0.0", "classification": "SIMULATED",
            "description": "generated by experiments/v14/bench/p6e-channels/suite.py from workloads.json",
            "loss": {"dropout": 0.0, "burst_count": 0, "burst_length": 0, **spec["loss"]},
            "channel": {**CHANNEL_FIELDS, **spec["params"]}}


# ================================================================================================== statistics
def boot_ci(x: list[float], n: int = 10000, seed: int = 11) -> list[float]:
    """Percentile bootstrap 95 % CI of the median: the same algorithm, resample count and RNG seed as
    experiments/v11/bench/bench.py::_boot_ci (tests/v14 checks they agree)."""
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(x, k=len(x))) for _ in range(n))
    return [round(meds[int(0.025 * n)], 3), round(meds[int(0.975 * n) - 1], 3)]


def ratio_record(a: list[float], b: list[float]) -> dict:
    """Paired per-seed ratios a/b (> 1 = b faster), their median and bootstrap CI."""
    if len(a) != len(b) or not a:
        raise ValueError("paired ratio needs equal, non-empty sample lists")
    r = [x / y for x, y in zip(a, b)]
    med, ci = statistics.median(r), boot_ci(r)
    return {"median": round(med, 3), "ci95": ci, "n": len(r), "per_seed": [round(x, 3) for x in r],
            "rel_ci_width": round((ci[1] - ci[0]) / med, 3) if med else None}


def overlaps(a: list[float], b: list[float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def noise_verdict(ratio: dict, aa: dict | None) -> str:
    """'faster beyond noise' / 'slower beyond noise' / 'within noise' against the A/A CI (V13 compare_aa rule)."""
    if aa is None:
        return "no A/A"
    if overlaps(ratio["ci95"], aa["ci95"]):
        return "within noise"
    return "faster beyond noise" if ratio["ci95"][0] > aa["ci95"][1] else "slower beyond noise"


def tier_of(ratio: dict, verdict: str) -> int:
    """Highest tier k (1..3) whose threshold the CI lower bound clears, only when faster beyond noise; else 0."""
    if verdict != "faster beyond noise":
        return 0
    return sum(ratio["ci95"][0] >= t for t in TIERS)


def needs_extension(ratios: dict, threshold: float = EXTEND_REL_WIDTH) -> bool:
    """Preregistered rule: extend when any of v12/v14, v13/v14 or v14/v14aa has (hi - lo) / median > threshold."""
    keys = ("v12/v14", "v13/v14", "v14/v14aa")
    return any(ratios.get(k) and ratios[k]["rel_ci_width"] is not None and ratios[k]["rel_ci_width"] > threshold
               for k in keys)


def classify(row: dict, expect: str) -> dict:
    """Correctness class of one run against the workload's preregistered expectation."""
    out = str(row.get("outcome"))
    success = out in ("SUCCESS", "ENCODED")
    matches = row.get("matches_input")
    crashed = out.startswith(("CRASH", "TIMEOUT"))
    false_success = out == "SUCCESS" and matches is False and not row.get("output_is_foreign")
    wrong_archive = out == "SUCCESS" and bool(row.get("output_is_foreign"))
    if crashed or false_success or wrong_archive:
        met = False
    elif expect == "success":
        met = success and matches is not False
    else:
        met = True
    note = ("false success" if false_success else "decoded the foreign archive" if wrong_archive else
            "crash/timeout" if crashed else
            "unexpectedly recovered" if expect == "explicit-failure" and success else
            "failed" if expect == "success" and not met else "ok")
    return {"false_success": false_success, "wrong_archive": wrong_archive, "crash": crashed,
            "expectation_met": met, "note": note}


def summarize(rows: list[dict], m: dict) -> dict:
    by: dict = {}
    for r in rows:
        by.setdefault(r["workload"], {}).setdefault(r["seed"], {})[r["config"]] = r
    labels_any = sorted({r.get("run_label", "") for r in rows})
    out = {"label": "SIMULATED", "run_labels": labels_any,
           "timings_are_results": labels_any == ["measurement"], "manifest_schema": m["schema"],
           "method": "paired per seed, alternating order, 1 warm-up per config per workload discarded; ratio a/b = a "
                     "seconds / b seconds (> 1 = b faster); 95 % CI = percentile bootstrap of the median ratio (10000 "
                     "resamples, seed 11); verdict against the in-run A/A ratio v14/v14aa (docs/V13_BENCHMARK_REPORT.md)",
           "workloads": {}, "totals": {}}
    if not out["timings_are_results"]:
        out["timing_status"] = (f"{SMOKE_LABEL}: harness validation only; no timing or ratio here is a V14 result "
                                "(correctness findings stand)")
    totals = {"env_crash_retries": sum(bool(r.get("retried_env_crash")) for r in rows), "runs": 0, "false_success": 0, "wrong_archive": 0, "crash": 0, "expectation_failures": 0,
              "nonidentical_outputs": 0}
    for w in m["workloads"]:
        seeds = by.get(w["name"])
        if not seeds:
            out["workloads"][w["name"]] = {"status": "NOT RUN"}
            continue
        labels = [c for c in CONFIGS if any(c in s for s in seeds.values())]
        runs = [s for _, s in sorted(seeds.items()) if set(labels) <= set(s)]
        incomplete = len(seeds) - len(runs)
        rec: dict = {"group": w["group"], "kind": w["kind"], "workers": w["workers"], "expect": w["expect"],
                     "target": w.get("target"), "input_bytes": sorted({r[labels[0]]["size"] for r in runs}),
                     "n": len(runs), "incomplete_seeds": incomplete, "seeds": [r[labels[0]]["seed"] for r in runs],
                     "labels": labels, "commits": {c: runs[0][c]["commit"] for c in labels} if runs else {},
                     "run_labels": sorted({r[c].get("run_label", "") for r in runs for c in labels})}
        if not runs:
            out["workloads"][w["name"]] = {**rec, "status": "INCOMPLETE"}
            continue
        cls = {c: [classify(r[c], w["expect"]) for r in runs] for c in labels}
        rec["outcomes"] = {c: sorted({str(r[c]["outcome"]) for r in runs}) for c in labels}
        rec["false_success"] = {c: sum(x["false_success"] for x in cls[c]) for c in labels}
        rec["wrong_archive"] = {c: sum(x["wrong_archive"] for x in cls[c]) for c in labels}
        rec["crash_or_timeout"] = {c: sum(x["crash"] for x in cls[c]) for c in labels}
        rec["expectation_failures"] = {c: [r[c]["seed"] for r, x in zip(runs, cls[c]) if not x["expectation_met"]]
                                       for c in labels}
        rec["notes"] = {c: sorted({x["note"] for x in cls[c]}) for c in labels}
        if w["kind"] == "decode":
            rec["matches_input"] = {c: sum(r[c].get("matches_input") is True for r in runs) for c in labels}
        rec["identical_outcome_and_output"] = all(
            len({(r[c].get("output_sha256"), str(r[c]["outcome"])) for c in labels}) == 1 for r in runs)
        sec = {c: [r[c]["seconds"] for r in runs] for c in labels}
        rec["seconds_median"] = {c: round(statistics.median(v), 3) for c, v in sec.items()}
        rec["seconds_range"] = {c: [round(min(v), 3), round(max(v), 3)] for c, v in sec.items()}
        rec["tree_rss_peak_mib_median"] = {c: round(statistics.median(r[c]["tree_rss_peak_bytes"] / MIB for r in runs), 1)
                                           for c in labels}
        rec["tree_rss_peak_mib_max"] = {c: round(max(r[c]["tree_rss_peak_bytes"] / MIB for r in runs), 1) for c in labels}
        rec["report_peak_rss_mib_median"] = {
            c: (round(statistics.median(v) / MIB, 1) if v else None)
            for c in labels for v in [[r[c]["report"]["peak_rss_bytes"] for r in runs
                                       if (r[c].get("report") or {}).get("peak_rss_bytes")]]}
        stages = {}
        for c in labels:
            keys = sorted({k for r in runs for k in ((r[c].get("report") or {}).get("stage_seconds") or {})})
            stages[c] = {k: round(statistics.median([(r[c].get("report") or {}).get("stage_seconds", {}).get(k, 0.0)
                                                     for r in runs]), 4) for k in keys}
        rec["stage_seconds_median"] = stages
        rec["engine_rungs"] = {c: sorted({str(((r[c].get("report") or {}).get("engine") or {}).get("rung")) for r in runs})
                               for c in labels}
        rec["engine_fallbacks"] = {c: sum(len(((r[c].get("report") or {}).get("engine") or {}).get("fallbacks") or [])
                                          for r in runs) for c in labels}
        rec["groups_failed_total"] = {c: sum(int((r[c].get("report") or {}).get("groups_failed") or 0) for r in runs)
                                      for c in labels}
        rec["load1_range"] = [round(min(r[c]["load1_start"] for r in runs for c in labels), 2),
                              round(max(r[c]["load1_start"] for r in runs for c in labels), 2)]
        ratios = {}
        for a, b in RATIOS:
            if a in labels and b in labels:
                ratios[f"{a}/{b}"] = ratio_record(sec[a], sec[b])
        aa = ratios.get("v14/v14aa")
        for k, v in ratios.items():
            if k != "v14/v14aa":
                v["verdict"] = noise_verdict(v, aa)
        if "v12/v14" in ratios:
            ratios["v12/v14"]["tier"] = tier_of(ratios["v12/v14"], ratios["v12/v14"]["verdict"])
        rec["ratios"] = ratios
        rec["ci_too_wide"] = needs_extension(ratios)
        out["workloads"][w["name"]] = rec
        totals["runs"] += len(runs) * len(labels)
        totals["false_success"] += sum(rec["false_success"].values())
        totals["wrong_archive"] += sum(rec["wrong_archive"].values())
        totals["crash"] += sum(rec["crash_or_timeout"].values())
        totals["expectation_failures"] += sum(len(v) for v in rec["expectation_failures"].values())
        totals["nonidentical_outputs"] += 0 if rec["identical_outcome_and_output"] else 1
    out["totals"] = totals
    return out


# ================================================================================================== payload + adversary
def zero_heavy(n: int, seed: int) -> bytes:
    """Deterministic zero-heavy payload: 64 KiB blocks, half all-zero, the rest ~2 % random non-zero bytes."""
    rng = random.Random(seed)
    out = bytearray(n)
    block = 1 << 16
    for start in range(0, n, block):
        if rng.random() < 0.5:
            continue
        end = min(n, start + block)
        for _ in range(max(1, (end - start) // 50)):
            out[rng.randrange(start, end)] = rng.randrange(1, 256)
    return bytes(out)


def read_fastq(path: Path) -> list[list[bytes]]:
    lines = Path(path).read_bytes().split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return [lines[i:i + 4] for i in range(0, len(lines) - len(lines) % 4, 4)]


def write_fastq(path: Path, records: list[list[bytes]]) -> None:
    Path(path).write_bytes(b"".join(b"\n".join(r) + b"\n" for r in records))


def _read_len(records: list[list[bytes]]) -> int:
    lens = sorted(len(r[1]) for r in records[:2000]) or [100]
    return lens[len(lens) // 2] or 100


def adversary_transform(a: dict, reads: Path, seed: int, *, foreign: Path | None = None,
                        strands: Path | None = None) -> dict:
    """Apply the preregistered adversary ``a`` to the FASTQ file ``reads`` in place; deterministic in ``seed``."""
    rng = random.Random(seed * 1_000_003 + 17)
    t = a["type"]
    if t == "empty":
        Path(reads).write_bytes(b"")
        return {"type": t}
    if t == "truncate":
        data = Path(reads).read_bytes()
        cut = int(len(data) * a["keep_fraction"])
        Path(reads).write_bytes(data[:cut])
        return {"type": t, "bytes_before": len(data), "bytes_after": cut}
    recs = read_fastq(reads)
    n0, L = len(recs), _read_len(recs)
    info: dict = {"type": t, "records_before": n0}
    if t == "foreign-mix":
        frecs = read_fastq(foreign)
        k = min(len(frecs), int(round(n0 * a["fraction"] / (1 - a["fraction"]))))
        recs += [[b"@foreign_%d" % i, *r[1:]] for i, r in enumerate(rng.sample(frecs, k))]
        info["foreign_records"] = k
    elif t == "junk-reads":
        k = int(n0 * a["fraction"])
        for i in range(k):
            kind = i % 5
            if kind == 0:
                seq = bytes(rng.choice(b"ACGT") for _ in range(L))
            elif kind == 1:
                seq = bytes(rng.choice(b"ACGT") for _ in range(rng.randint(1, 3 * L)))
            elif kind == 2:
                seq = b"N" * L
            elif kind == 3:
                seq = b""
            else:
                seq = b"A" * L
            recs.append([b"@junk_%d" % i, seq, b"+", b"I" * len(seq)])
        info["junk_records"] = k
    elif t == "malformed":
        k = max(1, int(n0 * a["fraction"]))
        idx = rng.sample(range(n0), min(k, n0))
        for j, i in enumerate(idx):
            r = recs[i]
            kind = j % 4
            if kind == 0:
                r[3] = r[3][:-1]                         # quality shorter than the sequence
            elif kind == 1:
                recs[i] = [r[0], r[1], r[3]]             # '+' line missing (the stream misaligns after it)
            elif kind == 2 and r[1]:
                p = rng.randrange(len(r[1]))
                r[1] = r[1][:p] + b"X" + r[1][p + 1:]    # a non-ACGTN byte
            elif r[1]:
                p = rng.randrange(len(r[1]))
                r[1] = r[1][:p] + b"\x00" + r[1][p + 1:]  # a NUL byte
        info["malformed_records"] = len(idx)
        Path(reads).write_bytes(b"".join(b"\n".join(r) + b"\n" for r in recs))
        return info
    elif t == "jackpot":
        k = int(n0 * a["fraction"])
        for i in rng.sample(range(n0), k):
            recs += [list(recs[i]) for _ in range(int(a["copies"]) - 1)]
        info["jackpot_reads"] = k
    elif t == "poison":
        seqs = [line for line in Path(strands).read_bytes().split(b"\n") if line and not line.startswith(b">")]
        k = max(1, int(len(seqs) * a["fraction"]))
        for i in rng.sample(range(len(seqs)), k):
            s = bytearray(seqs[i])
            for p in rng.sample(range(len(s)), min(int(a["mutations"]), len(s))):
                s[p] = rng.choice([c for c in b"ACGT" if c != s[p]])
            recs += [[b"@poison_%d_%d" % (i, j), bytes(s), b"+", b"I" * len(s)] for j in range(int(a["copies"]))]
        info["poisoned_strands"] = k
    else:
        raise ManifestError(f"unknown adversary {t!r}")
    rng.shuffle(recs)
    info["records_after"] = len(recs)
    write_fastq(reads, recs)
    return info


# ================================================================================================== subprocess side
def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def report_excerpt(rep: dict | None) -> dict:
    """The parts of a decode report the suite records: stage timings, RSS, recovery and fallback counters."""
    rep = rep or {}
    plan = rep.get("recovery_plan") or {}
    out = {k: rep.get(k) for k in ("seconds", "peak_rss_bytes", "stage_seconds", "reads", "engine", "groups_decoded",
                                   "groups_failed", "superblock_conflicts", "duplicate_conflicts") if k in rep}
    if "archive_tags_seen" in rep:
        out["archive_tags_seen"] = len(rep["archive_tags_seen"] or [])
    if plan:
        out["budget_exhausted"] = plan.get("budget_exhausted")
        out["stages_run"] = [d.get("stage") for d in plan.get("decisions") or [] if d.get("run")]
    out.update({k: v for k, v in rep.items() if "fallback" in k and k not in out})
    return out


def one(step: str, work: Path, label: str, workers: int) -> dict:
    import vnxdna
    from vnxdna.v4 import decoder as de
    from vnxdna.v4 import encoder as en
    status = vnxdna.native_status()["kernels"]
    row = {"vnxdna_file": vnxdna.__file__, "native": {k: v["backend"] for k, v in status.items()},
           "stale_libraries": [k for k, v in status.items() if v.get("library_older_than_source")]}
    t = time.perf_counter()
    if step == "encode":
        out = work / f"s-{label}.fasta"
        en.encode_container(work / "a.vnx", out, en.DNAOptions(workers=workers))
        row.update(seconds=time.perf_counter() - t, outcome="ENCODED", output_sha256=_sha(out), matches_input=None)
        out.unlink()
        return row
    out = work / f"o-{label}.vnx"
    try:
        res = de.decode_reads(work / "r.fastq", out, de.DecodeOptions(workers=workers))
        row["outcome"] = res.status
        row["report"] = report_excerpt(res.report)
    except Exception as error:  # noqa: BLE001 - an explicit failure is an outcome, recorded as such
        row["outcome"] = f"ERROR:{type(error).__name__}"
        row["error"] = str(error).replace(str(work), "<work>")[:500]      # no host-specific temporary paths in rows
        row["error_code"] = str(getattr(error, "code", "") or "")
    row["seconds"] = time.perf_counter() - t
    if out.is_file():
        row["output_sha256"] = _sha(out)
        row["matches_input"] = row["output_sha256"] == _sha(work / "a.vnx")
        row["output_is_foreign"] = (work / "foreign.vnx").is_file() and row["output_sha256"] == _sha(work / "foreign.vnx")
        out.unlink()
    else:
        row["output_sha256"], row["matches_input"], row["output_is_foreign"] = None, False, False
    return row


# ================================================================================================== driver side
def _load(name: str, rel: str):
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def trees(labels: list[str]) -> dict:
    v14 = Path(os.environ.get("V14_TREE") or ROOT).resolve()
    paths = {"v12": Path(os.environ.get("BASE_TREE", "/root/vnx-dna-lab/v13-base")),
             "v13": Path(os.environ.get("V13_TREE", "/root/vnx-dna-lab/v14-base")), "v14": v14, "v14aa": v14}
    for c in labels:
        if not (paths[c] / "src" / "vnxdna").is_dir():
            raise SystemExit(f"configuration {c}: {paths[c]} is not a VNX-DNA checkout")
    return {c: paths[c].resolve() for c in labels}


def check_commits(commits: dict, expected: dict) -> None:
    """Refuse to run when a configuration's tree is not at the commit the operator pinned (prefix match; ``+dirty``
    trees never match). ``expected`` maps configuration -> commit prefix, from EXPECT_<CONFIG>_COMMIT variables."""
    for c, want in expected.items():
        got = commits.get(c, "")
        if want and (not got.startswith(want) or got.endswith("+dirty")):
            raise SystemExit(f"configuration {c}: tree is at {got or 'unknown'}, expected {want}")


def _git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True).stdout.strip()


def _build_archive(work: Path, base: Path, name: str, payload: str, size: int, seed: int, options: dict) -> str:
    data = work / f"{name}.bin"
    if payload == "zero-heavy":
        data.write_bytes(zero_heavy(size, seed))
        gen = ""
    else:
        gen = f"datagen.generate(w / '{name}.bin', {size}, {payload!r}, {seed}); "
    code = ("import sys, json; from pathlib import Path; from vnxdna.v4 import archive as ar, datagen; "
            f"w = Path(sys.argv[1]); {gen}"
            f"ar.build_archive([w / '{name}.bin'], w / '{name}.vnx', ar.ArchiveOptions(**json.loads(sys.argv[2])))")
    subprocess.run([sys.executable, "-c", code, str(work), json.dumps(options)], check=True, cwd=work,
                   env={**os.environ, "PYTHONPATH": str(base / "src")})
    (work / f"{name}.bin").unlink()
    return _sha(work / f"{name}.vnx")


def _simulate(work: Path, spec: dict, strands: Path, reads: Path, seed: int, model_name: str) -> dict:
    from vnxdna.v4 import channel as ch
    if spec["simulator"] == "v4":
        stats = ch.simulate_file(strands, reads, ch.ChannelConfig(seed=seed, **spec["params"]))
        return {"reads": stats.get("reads") if isinstance(stats, dict) else None}
    V6 = _load("v6_channel_compose", "experiments/v6/channel/channel.py")
    path = work / f"{model_name}.model.json"
    path.write_text(json.dumps(model_document(model_name, spec), indent=1))
    rep = V6.compose(V6.load_model(path), strands, reads, seed, workdir=work)
    path.unlink()
    return {"reads": rep["channel"].get("reads"), "strand_loss": rep["loss"].get("lost"), "strands": rep["loss"].get("strands")}


def prepare(work: Path, m: dict, w: dict, seed: int, base: Path, size: int) -> dict:
    """Inputs shared by every configuration (V13 method: archive from the V12 tree, strands + reads from the driver)."""
    t0 = time.perf_counter()
    opts = w.get("archive_options", {})
    info = {"workload": w["name"], "seed": seed, "size": size, "payload": w["payload"], "archive_options": opts,
            "archive_sha256": _build_archive(work, base, "a", w["payload"], size, seed, opts)}
    info["archive_bytes"] = (work / "a.vnx").stat().st_size
    if w["kind"] == "decode":
        from vnxdna.v4 import encoder as en
        spec = channel_spec(m, w)
        info.update(channel=spec["params"], loss=spec["loss"], model=spec["model"], simulator=spec["simulator"],
                    channel_seed=seed + 7)
        en.encode_container(work / "a.vnx", work / "s.fasta", en.DNAOptions())
        info["channel_stats"] = _simulate(work, spec, work / "s.fasta", work / "r.fastq", seed + 7, w["name"])
        a = w.get("adversary")
        if a:
            foreign = None
            if a["type"] == "foreign-mix":
                fseed = seed + 500_000
                info["foreign_archive_sha256"] = _build_archive(work, base, "foreign", w["payload"], size, fseed, opts)
                en.encode_container(work / "foreign.vnx", work / "fs.fasta", en.DNAOptions())
                _simulate(work, spec, work / "fs.fasta", work / "fr.fastq", fseed + 7, w["name"] + "-foreign")
                foreign = work / "fr.fastq"
            info["adversary"] = {**a, **adversary_transform(a, work / "r.fastq", seed, foreign=foreign,
                                                            strands=work / "s.fasta")}
            for f in ("fs.fasta", "fr.fastq"):
                (work / f).unlink(missing_ok=True)
        info["reads_bytes"] = (work / "r.fastq").stat().st_size
        info["reads_sha256"] = _sha(work / "r.fastq")
    info["prepare_seconds"] = round(time.perf_counter() - t0, 3)
    return info


#: start-up failures caused by the host, not by the code under test (thread/process limits of a shared systemd
#: slice: OpenBLAS cannot create its threads while numpy is imported, before any VNX-DNA code runs); retried up to
#: 3 times with a growing pause, every attempt recorded in ``retried_env_crash``; a persistent one stays a CRASH row
ENV_CRASH_MARKERS = ("OpenBLAS blas_thread_init: pthread_create failed", "can't start new thread")


def env_crash(stderr: str) -> bool:
    """True when a non-zero exit is a host resource failure at interpreter start-up (retried once, recorded)."""
    return any(marker in (stderr or "") for marker in ENV_CRASH_MARKERS)


def step(c: str, tree: Path, kind: str, work: Path, workers: int, timeout: float, label: str | None = None,
         _retries: int = 3) -> dict:
    H = _load("v10_opt_bench", "experiments/v10/opt/bench.py")
    env = {**os.environ, "PYTHONPATH": str(tree / "src")}
    load = os.getloadavg()[0]
    cmd = [sys.executable, str(Path(__file__)), "one", "--step", kind, "--work", str(work), "--label", label or c,
           "--workers", str(workers)]
    base = {"config": c, "load1_start": load, "time": time.time()}
    t = time.perf_counter()
    try:
        p, peak = H._run_sampled(cmd, env, timeout=timeout)
    except H.HarnessError as error:
        return {**base, "outcome": "TIMEOUT", "error": str(error), "seconds": time.perf_counter() - t,
                "tree_rss_peak_bytes": 0, "output_sha256": None, "matches_input": False}
    if p.returncode != 0 and _retries > 0 and env_crash(p.stderr):
        time.sleep(15 * (4 - _retries))            # let the shared slice's task count drop, then retry
        again = step(c, tree, kind, work, workers, timeout, label, _retries=_retries - 1)
        first = {"returncode": p.returncode, "stderr_tail": p.stderr[-600:]}
        return {**again, "retried_env_crash": [first, *(again.get("retried_env_crash") or [])]}
    if p.returncode != 0:
        return {**base, "outcome": f"CRASH:{p.returncode}", "error": p.stderr[-2000:], "seconds": time.perf_counter() - t,
                "tree_rss_peak_bytes": peak, "output_sha256": None, "matches_input": False}
    row = json.loads(p.stdout.strip().splitlines()[-1])
    if not row["vnxdna_file"].startswith(str(tree)):
        raise SystemExit(f"{c} imported {row['vnxdna_file']}, not its tree {tree}")
    return {**base, "tree_rss_peak_bytes": peak, **row}


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.is_file() else []


def _append(path: Path, rows: list[dict]) -> None:
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _keep(work: Path, keep: Path | None, w: dict, seed: int, why: list[str], smoke: bool) -> None:
    if keep is None:
        return
    dest = keep / f"{w['name']}-{seed}"
    size = sum(p.stat().st_size for p in work.iterdir() if p.is_file())
    dest.mkdir(parents=True, exist_ok=True)
    if size < 512 * MIB:
        for p in work.iterdir():
            if p.is_file():
                shutil.copy2(p, dest / p.name)
    cmd = f"python experiments/v14/bench/p6e-channels/suite.py repro --workload {w['name']} --seed {seed}" + \
          (" --smoke" if smoke else "") + " --config <v12|v13|v14>"
    (dest / "reproducer.json").write_text(json.dumps({"workload": w["name"], "seed": seed, "why": why,
                                                       "kept_inputs": size < 512 * MIB, "command": cmd}, indent=1))


def run_seed(m: dict, w: dict, seed: int, tr: dict, *, out: Path, smoke: dict | None, warm: bool, timeout: float,
             keep: Path | None, commits: dict, run_label: str) -> dict:
    size = effective_size(w, smoke)
    with tempfile.TemporaryDirectory(prefix="vnxp6e-", dir=os.environ.get("VNX_ARCH_TMP")) as tmp:
        work = Path(tmp)
        prep = prepare(work, m, w, seed, tr.get("v12") or next(iter(tr.values())), size)
        _append(out / "prep.jsonl", [{**prep, "run_label": run_label}])
        if warm:
            for c, tree in tr.items():
                _append(out / "warmup.jsonl", [{"workload": w["name"], "seed": seed, "run_label": run_label,
                                                **step(c, tree, w["kind"], work, w["workers"], timeout, f"warm-{c}")}])
        order = list(tr) if seed % 2 == 0 else list(tr)[::-1]
        rows = {}
        for c in order:
            rows[c] = {"workload": w["name"], "group": w["group"], "kind": w["kind"], "seed": seed, "size": size,
                       "workers": w["workers"], "expect": w["expect"], "commit": commits[c], "order": order,
                       "run_label": run_label, "reads_sha256": prep.get("reads_sha256"),
                       **step(c, tr[c], w["kind"], work, w["workers"], timeout)}
        _append(out / "rows.jsonl", [rows[c] for c in order])
        cls = {c: classify(r, w["expect"]) for c, r in rows.items()}
        same = len({(r.get("output_sha256"), str(r["outcome"])) for r in rows.values()}) == 1
        why = [f"{c}: {x['note']}" for c, x in cls.items() if not x["expectation_met"]] + ([] if same else ["configs differ"])
        if why:
            _keep(work, keep, w, seed, why, bool(smoke))
        print(f"{w['name']} seed {seed} ({size} B): " + " | ".join(
            f"{c} {r['seconds']:.2f}s {r['outcome']}{'' if cls[c]['expectation_met'] else ' !' + cls[c]['note']}"
            for c, r in rows.items()) + f" | identical={same}", flush=True)
        return rows


def run(m: dict, names: list[str], labels: list[str], *, out: Path, smoke: bool, timeout: float, keep: Path | None,
        extend: bool = True) -> None:
    tr = trees(labels)
    commits = {c: _git(t, "rev-parse", "HEAD") + ("+dirty" if _git(t, "status", "--porcelain", "--untracked-files=no", "--", "src")
                                                   else "") for c, t in tr.items()}
    check_commits(commits, {c: os.environ.get(f"EXPECT_{c.upper()}_COMMIT", "") for c in tr})
    sm = m["method"]["smoke"] if smoke else None
    run_label = SMOKE_LABEL if smoke else "measurement"
    out.mkdir(parents=True, exist_ok=True)
    (out / "environment.json").write_text(json.dumps({"label": "SIMULATED", "run_label": run_label, "commits": commits,
                                                      "trees": {c: str(t) for c, t in tr.items()},
                                                      "host": _host(), "manifest_sha256": _sha(MANIFEST),
                                                      "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, indent=1) + "\n")
    done = {(r["workload"], r["seed"], r["config"]) for r in _read_jsonl(out / "rows.jsonl")}
    for name in names:
        w = workload(m, name)
        todo = seeds_of(w, smoke=sm)
        warm = True
        for seed in todo:
            if all((name, seed, c) in done for c in tr):
                warm = False
                continue
            run_seed(m, w, seed, tr, out=out, smoke=sm, warm=warm, timeout=timeout, keep=keep, commits=commits,
                     run_label=run_label)
            warm = False
        if smoke or not extend or w["group"] == "adversarial":
            continue
        rec = summarize([r for r in _read_jsonl(out / "rows.jsonl") if r["workload"] == name],
                        {**m, "workloads": [w]})["workloads"][name]
        if rec.get("ci_too_wide"):
            print(f"{name}: CI too wide (preregistered rule) -> extension seeds {extension_seeds(w)}", flush=True)
            for seed in extension_seeds(w):
                if not all((name, seed, c) in done for c in tr):
                    run_seed(m, w, seed, tr, out=out, smoke=None, warm=False, timeout=timeout, keep=keep,
                             commits=commits, run_label=run_label)


def _host() -> dict:
    import platform
    cpu = next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")), platform.processor())
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "affinity_cpus": len(os.sched_getaffinity(0)),
            "python": platform.python_version(), "kernel": platform.release(), "load1": os.getloadavg()[0]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "run", "summary", "one", "repro"])
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--workloads", default="")
    ap.add_argument("--groups", default="")
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--keep", type=Path)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--no-extend", action="store_true")
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--step")
    ap.add_argument("--work", type=Path)
    ap.add_argument("--label")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--workload")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--config", default="v14")
    a = ap.parse_args(argv)
    if a.cmd == "one":
        print(json.dumps(one(a.step, a.work, a.label, a.workers)))
        return 0
    m = load_manifest(a.manifest)
    timeout = a.timeout or float(m["method"].get("timeout_s", 1800))
    if a.cmd == "validate":
        print(f"{a.manifest}: {len(m['workloads'])} workloads valid")
        for w in m["workloads"]:
            ch = channel_spec(m, w)["simulator"] if w["kind"] == "decode" else "-"
            print(f"  {w['name']:34s} {w['group']:11s} {w['kind']:6s} {w['size'] / MIB:6.2f} MiB w{w['workers']} "
                  f"n={w['seeds']['n']} expect={w['expect']} sim={ch}")
        return 0
    if a.cmd == "repro":
        w = workload(m, a.workload)
        sm = m["method"]["smoke"] if a.smoke else None
        tr = trees(["v12", a.config] if a.config != "v12" else ["v12"])
        with tempfile.TemporaryDirectory(prefix="vnxp6e-repro-") as tmp:
            prep = prepare(Path(tmp), m, w, a.seed, tr["v12"], effective_size(w, sm))
            row = step(a.config, tr[a.config], w["kind"], Path(tmp), w["workers"], timeout)
            print(json.dumps({"prep": prep, "row": row, "class": classify(row, w["expect"])}, indent=1))
        return 0
    if a.out is None:
        raise SystemExit("--out is required")
    if a.cmd == "summary":
        rows = _read_jsonl(a.out / "rows.jsonl")
        s = summarize(rows, m)
        (a.out / "summary.json").write_text(json.dumps(s, indent=1) + "\n")
        for name, r in s["workloads"].items():
            if "ratios" not in r:
                print(f"{name:34s} {r.get('status')}")
                continue
            rat = "  ".join(f"{k} {v['median']:.3f} {v['ci95']}" + (f" {v['verdict']}" if "verdict" in v else "")
                            for k, v in r["ratios"].items())
            print(f"{name:34s} n={r['n']} out={r['outcomes']} fs={sum(r['false_success'].values())} "
                  f"expfail={ {c: v for c, v in r['expectation_failures'].items() if v} } "
                  f"identical={r['identical_outcome_and_output']}  {rat}")
        print("totals:", json.dumps(s["totals"]))
        return 0
    names = [x for x in a.workloads.split(",") if x] or [w["name"] for w in m["workloads"]
                                                          if not a.groups or w["group"] in a.groups.split(",")]
    run(m, names, [c for c in a.configs.split(",") if c], out=a.out, smoke=a.smoke, timeout=timeout, keep=a.keep,
        extend=not a.no_extend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
