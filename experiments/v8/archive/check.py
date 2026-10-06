"""V8.12 archive / random-access / integrity checks, clean and noisy channels (SIMULATED).

A multi-file archive (two distinct random files, one exact duplicate for deduplication, one text file) is built with
AES-256-GCM (key file), encoded with v4-balanced, sent through a clean channel and through the V6 ``nanopore-like`` stress
channel (coverage 10), and decoded with Phase 1 full consensus. Checked: the decoded container's SHA-256 equals the
original; ``verify`` (Merkle tree and AEAD) passes; full extraction reproduces every file byte for byte; single-file
extraction (``select``) and ``locate`` (random access) work. Negative controls: a wrong key, a tampered container byte and
a read file with too few reads must be refused (explicit failure), never accepted (no false success).

    PYTHONPATH=src python experiments/v8/archive/check.py
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 83100


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def refused(fn) -> dict:
    try:
        fn()
    except Exception as e:                                        # noqa: BLE001 - every refusal type is recorded
        return {"refused": True, "error": type(e).__name__, "code": getattr(e, "code", None)}
    return {"refused": False}


def main() -> int:
    from vnxdna import sdk
    from vnxdna.archive.operations import ArchiveOptions
    from vnxdna.pipeline.encode import DNAOptions
    from vnxdna.recovery.cluster import ClusterConfig
    from vnxdna.recovery.options import DecodeOptions
    from vnxdna.simulation import registry
    t0 = time.time()
    out: dict = {"experiment": "V8.12 archive / random access / integrity", "label": "SIMULATED channels", "seed": SEED, "runs": {}}
    with tempfile.TemporaryDirectory(prefix="vnx-v8a-") as tmp:
        w = Path(tmp)
        src = w / "in"
        src.mkdir()
        rnd = __import__("random").Random(SEED)
        (src / "a.bin").write_bytes(bytes(rnd.getrandbits(8) for _ in range(6000)))
        (src / "b.bin").write_bytes(bytes(rnd.getrandbits(8) for _ in range(5000)))
        (src / "a-copy.bin").write_bytes((src / "a.bin").read_bytes())                     # deduplicated content
        (src / "notes.txt").write_text("VNX-DNA V8 archive check. " * 120)
        sdk.keygen(w / "key")
        key, _ = sdk.load_keys(w / "key")
        arch = sdk.archive([src], w / "a.vnx", options=ArchiveOptions(key=key))
        out["archive"] = {"files": 4, "container_sha256": sha(w / "a.vnx"), "bytes": (w / "a.vnx").stat().st_size,
                          "dedup_report": {k: v for k, v in arch.body.items() if "dedup" in k or "unique" in k or "chunks" in k}}
        out["verify_original"] = sdk.verify(w / "a.vnx", key=key).body.get("status")
        sdk.encode(w / "a.vnx", w / "strands.fasta", dna=DNAOptions(profile="v4-balanced"), key=key)
        opts = DecodeOptions(read_clustering="fallback", cluster_config=ClusterConfig(consensus_template="full"))
        for name, model, cov in (("clean", registry.load_model("clean"), 5), ("nanopore-like", registry.load_model("nanopore-like"), 10)):
            m = model.with_parameters({"sequencing.coverage": {"model": "negative-binomial", "mean": float(cov), "dispersion": 4.0}})
            reads = w / f"{name}.fastq"
            mp = w / f"{name}.model.json"
            mp.write_text(m.dumps())
            sdk.simulate(w / "strands.fasta", reads, model=str(mp), seed=SEED)
            dec_p = w / f"{name}.vnx"
            r: dict = {"coverage": cov, "model_sha256": model.sha256, "reads_sha256": sha(reads)}
            try:
                d = sdk.decode(reads, dec_p, options=opts, key=key)
                r["decode_status"] = d.status
            except Exception as e:                                # noqa: BLE001
                r["decode_status"] = f"REFUSED {type(e).__name__}"
            r["container_identical"] = dec_p.exists() and sha(dec_p) == out["archive"]["container_sha256"]
            if r["container_identical"]:
                r["verify"] = sdk.verify(dec_p, key=key).body.get("status")
                ex = w / f"{name}-all"
                sdk.extract(dec_p, ex, key=key)
                r["extract_all_identical"] = all(sha(ex / "in" / f.name) == sha(f) if (ex / "in" / f.name).exists()
                                                 else sha(ex / f.name) == sha(f) for f in src.iterdir())
                one = w / f"{name}-one"
                sdk.extract(dec_p, one, key=key, files=["in/b.bin"])
                got = [p for p in one.rglob("*") if p.is_file()]
                r["extract_one"] = {"files": len(got), "identical": len(got) == 1 and sha(got[0]) == sha(src / "b.bin")}
                loc = sdk.locate(dec_p, "in/b.bin", dna_profile="v4-balanced", key=key).body
                r["locate"] = {k: loc[k] for k in loc if k in ("status", "strands", "frames", "chunks", "addresses") or "strand" in k}
            out["runs"][name] = r
        # negative controls
        bad_key = os.urandom(32)
        out["negative"] = {"wrong_key_verify": refused(lambda: sdk.verify(w / "a.vnx", key=bad_key)),
                           "wrong_key_extract": refused(lambda: sdk.extract(w / "a.vnx", w / "x1", key=bad_key))}
        t = bytearray((w / "a.vnx").read_bytes())
        t[len(t) // 2] ^= 0x01
        (w / "tampered.vnx").write_bytes(bytes(t))
        out["negative"]["tampered_verify"] = refused(lambda: sdk.verify(w / "tampered.vnx", key=key))
        out["negative"]["tampered_extract"] = refused(lambda: sdk.extract(w / "tampered.vnx", w / "x2", key=key))
        lines = (w / "nanopore-like.fastq").read_text().split("\n")
        (w / "few.fastq").write_text("\n".join(lines[: 4 * 200]) + "\n")                    # 200 reads only
        try:
            d = sdk.decode(w / "few.fastq", w / "few.vnx", options=opts, key=key)
            out["negative"]["too_few_reads_decode"] = {"refused": d.status != "SUCCESS", "status": d.status}
        except Exception as e:                                    # noqa: BLE001
            out["negative"]["too_few_reads_decode"] = {"refused": True, "error": type(e).__name__, "code": getattr(e, "code", None)}
        out["negative"]["too_few_reads_decode"]["output_written"] = (w / "few.vnx").exists()
    # a false success = a negative control that was accepted (returned SUCCESS / produced output) instead of refused
    out["false_success"] = sum(1 for v in out["negative"].values() if isinstance(v, dict) and (not v.get("refused") or v.get("output_written")))
    out["seconds"] = round(time.time() - t0, 1)
    (HERE / "results").mkdir(parents=True, exist_ok=True)
    (HERE / "results" / "check.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: out[k] for k in ("runs", "negative", "false_success", "verify_original")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
