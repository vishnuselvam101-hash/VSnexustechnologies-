"""Regenerate examples/synthetic_software_test/ (SYNTHETIC SOFTWARE TEST; no DNA is involved).

    python experiments/v6/physical/examples/make_synthetic_example.py

Pipeline, all in software: random payload -> VNX archive -> strand FASTA -> illumina-like channel model (SIMULATED)
-> reads FASTQ -> VNX decode -> record. Checksums in the record are computed from the files written here.
The record's date fields are the day the example was generated (UTC); they are not laboratory dates.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "experiments" / "v6" / "channel"))
import channel as chn  # noqa: E402
import validate as val  # noqa: E402

from vnxdna import __version__  # noqa: E402
from vnxdna.v4 import archive as ar  # noqa: E402
from vnxdna.v4 import datagen  # noqa: E402
from vnxdna.v4 import decoder as de  # noqa: E402
from vnxdna.v4 import encoder as en  # noqa: E402

OUT = HERE / "synthetic_software_test"
PROFILE, MODEL, SEED, PAYLOAD_BYTES, DATA_SEED = "v4-balanced", "illumina-like", 7, 200, 11


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    git = chn.git_info()
    with tempfile.TemporaryDirectory(prefix="vnx-phys-ex-") as td:
        td = Path(td)
        datagen.generate(td / "payload.bin", PAYLOAD_BYTES, "random", DATA_SEED)
        ar.build_archive([td / "payload.bin"], td / "a.vnx", ar.ArchiveOptions())
        en.encode_container(td / "a.vnx", OUT / "strands.fasta", en.DNAOptions(profile=PROFILE), overwrite=True)
        sc = chn.simulate_with_sidecar(chn.load_model(MODEL), OUT / "strands.fasta", OUT / "reads.fastq", SEED, workers=1,
                                       command=f"simulate.py --model {MODEL} --strands strands.fasta --seed {SEED} --out reads.fastq")
        opts = de.DecodeOptions(workers=1)
        res = de.decode_reads(OUT / "reads.fastq", td / "out.vnx", opts, overwrite=True, workdir=td)
        status = res.status
        expected, recovered = sha(td / "a.vnx"), (sha(td / "out.vnx") if (td / "out.vnx").exists() else None)
        report = {"status": status, "model": MODEL, "seed": SEED, "reads": sc["output"]["reads"],
                  "decoder_options": json.loads(json.dumps(asdict(opts), default=str)),
                  "report": json.loads(json.dumps({k: v for k, v in res.report.items() if isinstance(v, (int, float, str, bool)) or v is None}, default=str))}
    (OUT / "decode_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    # keep only the deterministic part of the simulator sidecar (no absolute paths)
    (OUT / "reads.fastq.json").write_text(json.dumps(chn.deterministic_part(sc), indent=2, sort_keys=True, default=str) + "\n")
    fq = OUT / "reads.fastq"
    nreads = fq.read_bytes().count(b"\n") // 4
    ok = status == "SUCCESS" and recovered == expected
    quals = [ln.rstrip("\n") for i, ln in enumerate(fq.read_text().splitlines()) if i % 4 == 3]
    qv = [ord(c) - 33 for q in quals for c in q]
    q30, meanq = round(100 * sum(x >= 30 for x in qv) / len(qv), 4), round(sum(qv) / len(qv), 4)
    nt = sc["input"]["strand_length"]
    NS = "none (simulation)"
    att = {"performed_by": "software pipeline (no laboratory step)", "organisation": "VNX-DNA lab workspace", "date": today}
    rec = {
        "record_version": "1", "record_id": "SYNTH-SOFTWARE-TEST-0001",
        "description": f"Software-only round trip: {PAYLOAD_BYTES}-byte random payload, VNX {PROFILE}, simulated '{MODEL}' channel (seed {SEED}). Not a physical experiment.",
        "evidence_classification": "SYNTHETIC SOFTWARE TEST", "statement": val.STATEMENT,
        "synthesis": {
            "provider_name": NS, "order_id": NS, "synthesis_platform": NS, "oligo_pool_id": "none (simulation)",
            "library_ids": ["SIM-LIB-0001"], "strand_ids": [f"s{i}" for i in range(sc["input"]["strands"])],
            "strand_count": sc["input"]["strands"], "strand_length_nt": nt, "synthesis_date": today, "delivery_date": today,
            "provider_qc": {"summary": "none (simulation): no synthesis was performed"},
            "ordered_fasta": {"file_name": "strands.fasta", "sha256": sha(OUT / "strands.fasta"), "strand_count": sc["input"]["strands"], "path": "strands.fasta"},
            "vnx_source": {"vnx_version": __version__, "commit": git["commit"], "working_tree_dirty": git["dirty"], "profile": PROFILE,
                           "encoder_options": {"profile": PROFILE}, "encode_command": "vnxdna.v4.encoder.encode_container (DNAOptions(profile='v4-balanced'))"}},
        "sample": {"sample_id": "SIM-SAMPLE-0001", "library_id": "SIM-LIB-0001", "description": "simulated read set; no physical sample",
                   "chain_of_custody": [{"step": "software simulation", "date": today, "performed_by": "software pipeline", "notes": "no physical custody"}]},
        "storage": {"temperature_c": None, "relative_humidity_percent": None, "medium": "none (simulation)", "encapsulation": "none (simulation)",
                    "start_date": today, "end_date": today, "duration_days": 0, "notes": "no physical storage; temperature and humidity are not applicable"},
        "sequencing": {
            "provider_name": NS, "platform": f"simulated channel model '{MODEL}' v{sc['model']['version']} (not fitted to any instrument)",
            "instrument": NS, "chemistry_kit": NS, "run_id": f"sim-seed-{SEED}", "run_date": today, "read_length": nt, "layout": "single",
            "fastq_files": [{"file_name": "reads.fastq", "sha256": sha(fq), "size_bytes": fq.stat().st_size, "read_count": nreads,
                             "read_role": "single", "path": "reads.fastq"}],
            "read_counts": {"total": nreads, "passing_filter": nreads},
            "quality": {"percent_q30": q30, "mean_q": meanq, "error_rate_estimate": None,
                        "error_estimate_method": f"Q30 and mean Q computed from the simulated quality strings (Phred+33); error rates: configured model rates in reads.fastq.json (key model.parameters); realised: {json.dumps(sc['realised_rates'], sort_keys=True)}"}},
        "decode": {"vnx_version": __version__, "commit": git["commit"], "working_tree_dirty": git["dirty"],
                   "decoder_config": report["decoder_options"], "backend": "python (vnxdna.v4.decoder.decode_reads)", "workers": 1,
                   "command": "vnxdna.v4.decoder.decode_reads(reads.fastq, out.vnx, DecodeOptions(workers=1))",
                   "report_file": "decode_report.json", "report_sha256": sha(OUT / "decode_report.json"), "decode_status": status},
        "result": {"recovered_sha256": recovered, "expected_sha256": expected, "verification_result": "SUCCESS" if ok else "FAILURE",
                   "files_expected": 1, "files_recovered": 1 if recovered else 0,
                   "notes": "SHA-256 of the VNX container (a.vnx) before encoding versus after decoding; the container is not stored in this directory."},
        "attestations": {"synthesis": att, "storage": att, "sequencing": att, "decode": att,
                         "signed_off_by": {"name": "not applicable (software test)", "role": "n/a", "date": today},
                         "raw_data_location": "this directory (examples/synthetic_software_test/)"},
    }
    (OUT / "record.json").write_text(json.dumps(rec, indent=2) + "\n")
    r = val.validate_record(rec, OUT)
    print(json.dumps({"status": r["status"], "result": rec["result"]["verification_result"], "reads": nreads,
                      "missing": r["missing_fields"], "errors": r["schema_errors"] + r["rule_errors"]}, indent=2))
    return 0 if r["status"] in ("VALID", "INCOMPLETE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
