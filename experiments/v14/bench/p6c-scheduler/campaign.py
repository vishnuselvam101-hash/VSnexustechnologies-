"""V14 Phase 6 workstream C: scheduler safety campaign (SIMULATED reads only; docs/V14_SCHEDULER.md §6).

Every pool is built from a declared data pattern, DNA options and channel, simulated with per-read ground truth
(``simulation.engine.simulate_file_with_truth``; shuffled pools are permuted afterwards with the truth permuted the same
way), and decoded three ways by the streaming engine:

    all           engine_dedup=False: every read attempted (the diagnostic mode, the reference)
    strand        the V14 adaptive scheduler (default)
    corroborated  the corrected V13 rule (for comparison only)

Per mode it records (a) pass 1 alone, with the engine's per-read decision trace (VNX_ENG_TRACE): reads attempted and
skipped by reason, FALSE SKIPS (a skipped read whose true strand is not the strand it was counted a copy of), skips whose
evidence names no strand, verified addresses lost against the reference, per-address payload disagreements; and (b) a
whole decode: status, output bytes against the archive, fallbacks, and the recovery records of the report (outer code
counters, duplicate conflicts, consensus counters) against the reference decode.

    PYTHONPATH=src python experiments/v14/bench/p6c-scheduler/campaign.py run [--pools a,b] [--seeds-from N]
    PYTHONPATH=src python experiments/v14/bench/p6c-scheduler/campaign.py summary

Output (VNX_BENCH_OUT, default campaign.jsonl next to this file): one JSON row per (pool, seed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("VNX_BENCH_OUT") or HERE / "campaign.jsonl")
KIB, MIB = 1 << 10, 1 << 20

INDEL = {"sequencing.substitution.rate": 0.01, "sequencing.insertion.rate": 0.002, "sequencing.deletion.rate": 0.002}
HEAVY = {"sequencing.substitution.rate": 0.03, "sequencing.insertion.rate": 0.01, "sequencing.deletion.rate": 0.01}
STRIPES = {"stripe_depth": 4, "column_parity": 2}


def cov(mean, model="fixed"):
    return {"sequencing.coverage": {"model": model, "mean": float(mean), "dispersion": 4.0}}


#: PREREGISTERED (docs/V14_SCHEDULER.md §6, committed before any campaign row was measured):
#: name: (data pattern, bytes, compression, DNA options, channel parameters, shuffled)
POOLS = {
    "random-indel": ("random", 1 * MIB, "zstd", {}, {**INDEL, **cov(5)}, False),
    "random-indel-shuffled": ("random", 1 * MIB, "zstd", {}, {**INDEL, **cov(5)}, True),
    "zeros-stripes-indel": ("zeros", 1 * MIB, "none", STRIPES, {**INDEL, **cov(5)}, False),
    "zeroheavy-indel-shuffled": ("zero-heavy", 1 * MIB, "none", {}, {**INDEL, **cov(3)}, True),
    "repetitive-indel": ("repetitive", 1 * MIB, "none", {}, {**INDEL, **cov(5)}, False),
    "binary-substitution": ("binary", 1 * MIB, "none", STRIPES, {"sequencing.substitution.rate": 0.02, **cov(4)}, True),
    "random-rc-indel": ("random", 512 * KIB, "zstd", {}, {**INDEL, **cov(5), "sequencing.reverse_complement_rate": 0.5},
                        True),
    "random-dropout-uneven": ("random", 1 * MIB, "zstd", {}, {**INDEL, **cov(5, "negative-binomial"),
                                                             "storage.strand_loss.rate": 0.05}, True),
    "zeros-noisy-headers": ("zeros", 512 * KIB, "none", STRIPES, {**HEAVY, **cov(4)}, True),
    "zeros-cov1-neighbours": ("zeros", 512 * KIB, "none", STRIPES, {"sequencing.substitution.rate": 0.005, **cov(1)},
                              False),
    "random-duplicates": ("random", 1 * MIB, "zstd", {}, {**INDEL, **cov(3), "amplification.duplicate_rate": 0.3}, True),
    "zeroheavy-rc-indel": ("zero-heavy", 512 * KIB, "none", {}, {**INDEL, **cov(4), "sequencing.reverse_complement_rate": 0.5},
                           True),
    "zeroheavy-dense-substitution": ("zero-heavy", 512 * KIB, "none", {"profile": "v4-dense"},
                                     {"sequencing.substitution.rate": 0.01, **cov(4)}, True),
    "random-highdropout-profile": ("random", 512 * KIB, "zstd", {"profile": "v6-high-dropout"},
                                   {"sequencing.substitution.rate": 0.005, **cov(3)}, True),
}
SEEDS = (61001, 61002)          # campaign seeds; exploration used other seeds (61900+)
MODES = {"all": {"engine_dedup": False}, "strand": {"engine_scheduler": "strand"},
         "corroborated": {"engine_scheduler": "corroborated"}}
RECORD_KEYS = ("symbols_expected", "symbols_received", "symbols_missing", "rows_with_loss", "rows_failed_row_wise",
               "rows_recovered_by_column_parity", "rows_failed")
#: pass-2 work counters: they count work done on pending reads, so they differ whenever a scheduler leaves a copy
#: unattempted (reported, not compared)
WORK_KEYS = ("consensus_attempted", "consensus_recovered", "addresses_snapped")


def _data(path: Path, pattern: str, size: int, seed: int) -> None:
    from vnxdna.v4 import datagen
    if pattern == "zeros":
        path.write_bytes(bytes(size))
    elif pattern == "zero-heavy":                      # 2 % random non-zero bytes
        rng = np.random.default_rng(seed)
        b = np.zeros(size, np.uint8)
        m = rng.random(size) < 0.02
        b[m] = rng.integers(1, 256, int(m.sum()))
        path.write_bytes(b.tobytes())
    else:
        datagen.generate(path, size, pattern, seed)


def strand_addresses(fasta: Path, lay) -> np.ndarray:
    """(strands, 4) (kind, tag, group, symbol) of every clean strand, read from its header bases."""
    from vnxdna.dnaenc.constraints import iter_fasta, to_codes
    from vnxdna.dnaenc.layout import CRC_BYTES, HEADER_BYTES
    from vnxdna.dnaenc.scrambler import keystreams
    _, fpos = lay.template()
    ks = keystreams(HEADER_BYTES - 1 + lay.payload_bytes + CRC_BYTES)
    out = []
    for _, seq in iter_fasta(fasta):
        nt = to_codes(seq)[fpos[:4 * HEADER_BYTES]].astype(np.int64).reshape(HEADER_BYTES, 4)
        b = (nt[:, 0] << 6) | (nt[:, 1] << 4) | (nt[:, 2] << 2) | nt[:, 3]
        h = b[1:] ^ ks[b[0], :HEADER_BYTES - 1]
        out.append((int(h[0]) & 15, (int(h[1]) << 8) | int(h[2]), int.from_bytes(bytes(h[3:7].tolist()), "big"),
                    (int(h[7]) << 8) | int(h[8])))
    return np.asarray(out, dtype=np.int64).reshape(-1, 4)


def build(work: Path, name: str, seed: int) -> dict:
    from vnxdna.simulation import engine, registry
    from vnxdna.v4 import archive as ar
    from vnxdna.v4 import encoder as en
    pattern, size, comp, dna, channel, shuffled = POOLS[name]
    _data(work / "in.bin", pattern, size, seed)
    ar.build_archive([work / "in.bin"], work / "a.vnx", ar.ArchiveOptions(compression=comp))
    en.encode_container(work / "a.vnx", work / "s.fasta", en.DNAOptions(**dna))
    model = registry.load_model("clean").with_parameters(channel, label=f"p6c {name}")
    truth = engine.simulate_file_with_truth(work / "s.fasta", work / "t.fastq", model, seed)
    src = truth["source"]
    recs = (work / "t.fastq").read_bytes().split(b"\n")
    recs = [b"\n".join(recs[4 * i:4 * i + 4]) + b"\n" for i in range(len(recs) // 4)]
    order = np.random.default_rng([seed, 0x51]).permutation(len(recs)) if shuffled else np.arange(len(recs))
    with open(work / "r.fastq", "wb") as f:
        for i in order.tolist():
            f.write(recs[i])
    (work / "t.fastq").unlink()
    from vnxdna.recovery.options import DecodeOptions
    from vnxdna.recovery.probe import detect_layout
    lay = detect_layout(work / "r.fastq", DecodeOptions())
    addr = strand_addresses(work / "s.fasta", lay)
    true_addr = np.where(src[order, None] >= 0, addr[np.maximum(src[order], 0)], -1)
    return {"reads": len(recs), "strands": len(addr), "lost_strands": len(truth["lost_strands"]), "truth": true_addr,
            "layout": lay}


def _trace(path: Path) -> tuple[np.ndarray, list[str], np.ndarray]:
    idx, dec, adr = [], [], []
    for line in path.read_text().splitlines():
        p = line.split()
        idx.append(int(p[0]))
        dec.append(p[1])
        adr.append([int(x) for x in p[2:6]])
    o = np.argsort(np.asarray(idx))
    return np.asarray(idx)[o], [dec[i] for i in o], np.asarray(adr, dtype=np.int64).reshape(-1, 4)[o]


def _verified(spill: Path, lay, buckets: int) -> dict:
    dt = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"),
                   ("payload", "u1", (lay.payload_bytes,))])
    votes: dict = {}
    for b in range(buckets):
        for r in np.fromfile(spill / f"acc{b}.bin", dtype=dt):
            k = (int(r["kind"]), int(r["tag"]), int(r["group"]), int(r["symbol"]))
            votes.setdefault(k, Counter())[bytes(r["payload"])] += 1
    out = {}
    for k, c in votes.items():
        (top, n1), *rest = c.most_common(2) + [(None, 0)]
        out[k] = top if n1 > (rest[0][1] if rest else 0) else None   # strict majority (None = tie)
    return out


def pass1(work: Path, mode: str, lay, truth: np.ndarray) -> dict:
    from vnxdna.recovery import engine as re_
    from vnxdna.recovery.options import DecodeOptions
    from vnxdna.recovery.spill import Spill
    opt = DecodeOptions(**MODES[mode])
    spill = Path(tempfile.mkdtemp(dir=work))
    Spill(spill, 2, lay.payload_bytes, lay.frame_nt).close()
    tr = work / f"trace-{mode}.txt"
    os.environ["VNX_ENG_TRACE"] = str(tr)
    try:
        t0 = time.perf_counter()
        st = re_.run_pass1(work / "r.fastq", spill, lay, opt, 2, fmt="fastq", threads=1, dedup=opt.engine_dedup)
        sec = time.perf_counter() - t0
    finally:
        del os.environ["VNX_ENG_TRACE"]
    idx, dec, adr = _trace(tr)
    tr.unlink()
    assert idx.tolist() == list(range(len(truth))), "trace does not cover every read once"
    skipped = np.asarray([d.startswith("S") for d in dec])
    known = (adr[:, 0] >= 0)
    wrong = np.any(adr != truth, axis=1)
    accepted = np.asarray([d == "A" for d in dec])
    reasons = Counter(d for d in dec if d.startswith("S"))
    return {"stats": {k: v for k, v in st.items() if not k.startswith("reserved")}, "pass1_seconds": round(sec, 4),
            "skipped": int(skipped.sum()), "skip_reasons": dict(reasons),
            "false_skips": int((skipped & known & wrong).sum()),
            "skips_without_evidence_address": int((skipped & ~known).sum()),
            "accepted_at_wrong_address": int((accepted & wrong).sum()),
            "verified": _verified(spill, lay, 2)}


def decode(work: Path, mode: str) -> dict:
    from vnxdna.recovery.options import DecodeOptions
    from vnxdna.v4 import decoder as de
    out = work / f"o-{mode}.vnx"
    t0 = time.perf_counter()
    try:
        res = de.decode_reads(work / "r.fastq", out, DecodeOptions(engine="auto", workers=1, **MODES[mode]),
                              overwrite=True)
    except Exception as e:  # noqa: BLE001 - a refusal is an outcome of the pool (recorded and compared)
        return {"status": f"ERROR {type(e).__name__}", "seconds": round(time.perf_counter() - t0, 3),
                "output_sha256": None, "matches_archive": False, "rung": None, "fallbacks": [],
                "records": {"error": str(e)[:200]}, "outer": {}, "work": {}}
    sec = time.perf_counter() - t0
    r = res.report
    data = out.read_bytes() if out.exists() else b""
    o6 = r.get("outer_v6") or {}
    return {"status": res.status, "seconds": round(sec, 3), "output_sha256": hashlib.sha256(data).hexdigest(),
            "matches_archive": data == (work / "a.vnx").read_bytes(),
            "rung": (r.get("engine") or {}).get("rung"), "fallbacks": (r.get("engine") or {}).get("fallbacks", []),
            "records": {"duplicate_conflicts": r.get("duplicate_conflicts"), "groups_failed": r.get("groups_failed"),
                        "failed_groups": r.get("failed_groups"), "symbols_expected": o6.get("symbols_expected")},
            "outer": {k: o6.get(k) for k in RECORD_KEYS if k in o6},
            "work": {k: (r.get("reads") or {}).get(k) for k in WORK_KEYS}}


def run(pools: list[str], seeds) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for name in pools:
        for seed in seeds:
            with tempfile.TemporaryDirectory(prefix="p6c-", dir=os.environ.get("VNX_ARCH_TMP")) as tmp:
                work = Path(tmp)
                b = build(work, name, seed)
                p1 = {m: pass1(work, m, b["layout"], b["truth"]) for m in MODES}
                ref = p1["all"]["verified"]
                dec = {m: decode(work, m) for m in MODES}
                row = {"label": "SIMULATED", "pool": name, "seed": seed, "spec": repr(POOLS[name]), "reads": b["reads"],
                       "strands": b["strands"], "lost_strands": b["lost_strands"], "modes": {}}
                for m in MODES:
                    v = p1[m].pop("verified")
                    lost = sorted(set(ref) - set(v))
                    row["modes"][m] = {**p1[m], "verified_addresses": len(v), "lost_vs_reference": len(lost),
                                       "lost_examples": lost[:5],
                                       "payload_disagreements": sum(v[k] != ref[k] for k in set(v) & set(ref)),
                                       "decode": dec[m],
                                       "decode_identical_to_reference": dec[m]["output_sha256"] == dec["all"]["output_sha256"]
                                       and dec[m]["status"] == dec["all"]["status"],
                                       "records_identical_to_reference": dec[m]["records"] == dec["all"]["records"],
                                       "outer_identical_to_reference": dec[m]["outer"] == dec["all"]["outer"],
                                       "outer_delta": {k: (dec[m]["outer"].get(k) or 0) - (dec["all"]["outer"].get(k) or 0)
                                                       for k in dec[m]["outer"]
                                                       if dec[m]["outer"].get(k) != dec["all"]["outer"].get(k)}}
                with open(OUT, "a") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"{name} seed {seed}: " + " | ".join(
                    f"{m} att {r['stats']['attempted']} skip {r['skipped']} false {r['false_skips']} "
                    f"lost {r['lost_vs_reference']} {r['decode']['status']}/{r['decode']['matches_archive']}"
                    f"{'/fb' if r['decode']['fallbacks'] else ''}"
                    for m, r in row["modes"].items()), flush=True)


def summary() -> dict:
    rows = [json.loads(x) for x in OUT.read_text().splitlines()]
    out: dict = {"label": "SIMULATED", "rows": len(rows), "modes": {}}
    for m in MODES:
        ms = [r["modes"][m] for r in rows]
        out["modes"][m] = {
            "false_skips": sum(x["false_skips"] for x in ms),
            "skips_without_evidence_address": sum(x["skips_without_evidence_address"] for x in ms),
            "lost_vs_reference": sum(x["lost_vs_reference"] for x in ms),
            "payload_disagreements": sum(x["payload_disagreements"] for x in ms),
            "decodes": len(ms),
            "decode_success": sum(x["decode"]["status"] == "SUCCESS" for x in ms),
            "false_success": sum(x["decode"]["status"] == "SUCCESS" and not x["decode"]["matches_archive"] for x in ms),
            "decode_identical_to_reference": sum(x["decode_identical_to_reference"] for x in ms),
            "records_identical_to_reference": sum(x["records_identical_to_reference"] for x in ms),
            "outer_identical_to_reference": sum(x["outer_identical_to_reference"] for x in ms),
            "outer_deltas": [(r["pool"], r["seed"], r["modes"][m]["outer_delta"]) for r in rows
                             if r["modes"][m]["outer_delta"]],
            "sched_rescued": sum(x["stats"].get("sched_rescued", 0) for x in ms),
            "engine_fallbacks": sum(bool(x["decode"]["fallbacks"]) for x in ms),
            "reads": sum(r["reads"] for r in rows),
            "attempted": sum(x["stats"]["attempted"] for x in ms),
            "full_attempts": sum(x["stats"]["full_attempts"] for x in ms),
            "pools_with_lost_addresses": sorted({r["pool"] for r in rows if r["modes"][m]["lost_vs_reference"]}),
            "pools_with_fallbacks": sorted({r["pool"] for r in rows if r["modes"][m]["decode"]["fallbacks"]}),
        }
    path = OUT.with_name(OUT.stem + "-summary.json")
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--pools", default=",".join(POOLS))
    ap.add_argument("--seeds", default=",".join(map(str, SEEDS)))
    a = ap.parse_args(argv)
    if a.cmd == "run":
        run(a.pools.split(","), [int(s) for s in a.seeds.split(",")])
    else:
        print(json.dumps(summary(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
