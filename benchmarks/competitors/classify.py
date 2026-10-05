"""Comparability of published DNA-storage results with VNX-DNA measurements, computed from record metadata.

Every VNX-DNA channel result is SIMULATED (software strands, software channel). A competitor value is classified per
metric, never per record:

    DIRECTLY COMPARABLE    same metric definition, same workload/channel/decoder constraints, competitor value from a
                           published in-silico protocol that VNX-DNA has re-run unchanged (scenario IDs recorded on
                           both sides), and for time metrics the same hardware class
    PARTIALLY COMPARABLE   same kind of metric, but at least one condition differs or needs an adjustment that the
                           report must state; both numbers may be shown side by side, never ranked
    NOT COMPARABLE         wet-lab recovery or physical quantities versus simulation, unverified (secondary) values,
                           or no data

No ratio, ranking or "better than" statement is produced by this tool for anything that is not DIRECTLY COMPARABLE.

Usage::

    python benchmarks/competitors/classify.py                 # markdown matrix to stdout
    python benchmarks/competitors/classify.py --json out.json  # machine-readable
    python benchmarks/competitors/classify.py --protocol-runs runs.json   # VNX-DNA runs of a published protocol
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIRECT, PARTIAL, NOT = "DIRECTLY COMPARABLE", "PARTIALLY COMPARABLE", "NOT COMPARABLE"
METRICS = ("logical_density", "redundancy", "error_tolerance", "software_time", "physical")


def load(path: Path = HERE / "records.json") -> dict:
    data = json.loads(path.read_text())
    ids = [r["id"] for r in data["records"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate record id")
    for r in data["records"]:
        for k in ("medium", "evidence", "verification", "source", "publication_date"):
            if k not in r:
                raise ValueError(f"record {r.get('id')} lacks {k}")
        if r["medium"] not in ("wet_lab", "in_silico", "mixed"):
            raise ValueError(f"record {r['id']}: bad medium {r['medium']!r}")
        if r["verification"] not in ("primary_fulltext", "secondary", "abstract_only"):
            raise ValueError(f"record {r['id']}: bad verification {r['verification']!r}")
    return data


def classify(rec: dict, metric: str, protocol_runs: dict | None = None) -> tuple[str, str]:
    """(label, reason) for one record and metric. ``protocol_runs`` maps a protocol record ID to the VNX-DNA run
    metadata ({"scenarios": [...], "hardware_class": ..., "results": path}) when VNX-DNA re-ran that protocol."""
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}")
    if metric == "physical":
        return NOT, "VNX-DNA has no physical (synthesis/storage/sequencing) results"
    if rec["verification"] != "primary_fulltext":
        return NOT, f"competitor value not verified from the primary full text ({rec['verification']}); context only"
    run = (protocol_runs or {}).get(rec["id"]) if rec.get("protocol") else None
    if metric == "logical_density":
        if not rec.get("density"):
            return NOT, "no density reported"
        if run:
            return DIRECT, "same protocol code rates re-run by VNX-DNA; overhead accounting fixed by the protocol"
        return PARTIAL, "logical density only: align primer/index/CRC accounting (incl./excl. primers) before showing side by side"
    if metric == "redundancy":
        if not rec.get("redundancy"):
            return NOT, "no redundancy reported"
        if run:
            return DIRECT, "same protocol, same redundancy definition"
        return PARTIAL, "redundancy definitions differ between studies (logical vs nucleotide fraction); state the definition"
    if metric == "error_tolerance":
        if rec["medium"] == "wet_lab":
            return NOT, "physical wet-lab recovery versus VNX-DNA SIMULATED channel"
        if run and set(run.get("scenarios", [])):
            return DIRECT, f"VNX-DNA re-ran the published scenarios {sorted(run['scenarios'])} unchanged"
        return PARTIAL, "in-silico tolerance under a different channel model; VNX-DNA has not re-run this protocol"
    # software_time
    if not rec.get("software_time"):
        return NOT, "no software encode/decode time reported"
    if run and run.get("hardware_class") and run.get("hardware_class") == rec.get("hardware_class"):
        return DIRECT, "same protocol constraints and hardware class"
    return PARTIAL, f"different hardware ({rec.get('hardware') or 'not stated'}); indicative only"


def matrix(data: dict, protocol_runs: dict | None = None) -> list[dict]:
    rows = []
    for rec in data["records"]:
        row = {"id": rec["id"], "system": rec["system"], "medium": rec["medium"], "verification": rec["verification"],
               "source": rec["source"].get("doi") or (rec["source"].get("urls") or [None])[0], "metrics": {}}
        for m in METRICS:
            label, why = classify(rec, m, protocol_runs)
            row["metrics"][m] = {"label": label, "reason": why}
        rows.append(row)
    return rows


def markdown(rows: list[dict]) -> str:
    short = {DIRECT: "DIRECT", PARTIAL: "PARTIAL", NOT: "NOT"}
    out = ["| record | medium | verification | " + " | ".join(METRICS) + " |",
           "|---|---|---|" + "---|" * len(METRICS)]
    for r in rows:
        out.append(f"| {r['id']} | {r['medium']} | {r['verification']} | "
                   + " | ".join(short[r["metrics"][m]["label"]] for m in METRICS) + " |")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--records", type=Path, default=HERE / "records.json")
    ap.add_argument("--protocol-runs", type=Path, help="JSON: {protocol record id: {scenarios, hardware_class, results}}")
    ap.add_argument("--json", type=Path, help="write the full matrix with reasons")
    a = ap.parse_args(argv)
    runs = json.loads(a.protocol_runs.read_text()) if a.protocol_runs else None
    rows = matrix(load(a.records), runs)
    if a.json:
        a.json.write_text(json.dumps({"statement": "comparability computed from metadata; VNX-DNA results are SIMULATED",
                                      "rows": rows}, indent=2) + "\n")
    sys.stdout.write(markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
