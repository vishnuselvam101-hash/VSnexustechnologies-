"""Reproduce nanopore corpus cases from their seeds and write their diagnostics (DIAGNOSTIC / SIMULATED).

    PYTHONPATH=src python tests/nanopore/reproduce.py --all                 # every case
    PYTHONPATH=src python tests/nanopore/reproduce.py nanopore-cov10-s82043  # one case
    PYTHONPATH=src python tests/nanopore/reproduce.py --all --update-expected

Writes ``diagnostics/<id>.json`` (the full machine-readable failure artifact: per-strand funnel with the reason of every
lost strand, the ORACLE address test) and, with ``--update-expected``, ``expected/<id>.json`` (the compact part the
regression test pins). Exit 2 if any case is a FALSE SUCCESS.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nanofunnel as nf  # noqa: E402


def _one(args) -> dict:
    case, oracle = args
    with tempfile.TemporaryDirectory(prefix="vnx-nanocase-") as tmp:
        return nf.run_case(case, Path(tmp), oracle=oracle)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tier", choices=("fast", "slow"))
    ap.add_argument("--corpus", default=str(nf.CORPUS))
    ap.add_argument("--no-oracle", action="store_true")
    ap.add_argument("--update-expected", action="store_true")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--diagnostics", default=str(HERE / "diagnostics"))
    a = ap.parse_args(argv)
    cases = nf.load_corpus(Path(a.corpus))
    if not a.all:
        cases = [c for c in cases if c["id"] in a.ids or (a.tier and c["tier"] == a.tier)]
    if not cases:
        ap.error("no case selected")
    diag = Path(a.diagnostics)
    diag.mkdir(parents=True, exist_ok=True)
    false = 0
    with ProcessPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for doc in pool.map(_one, [(c, not a.no_oracle) for c in cases]):
            cid = doc["case"]["id"]
            (diag / f"{cid}.json").write_text(nf.artifact_json(doc))
            exp = nf.expected_of(doc)
            if a.update_expected:
                (HERE / "expected" / f"{cid}.json").write_text(json.dumps(exp, indent=1, sort_keys=True) + "\n")
            false += doc["decode"]["outcome"] == "FALSE_SUCCESS"
            o = doc.get("oracle", {})
            print(f"{cid}: {doc['decode']['outcome']} frames {doc['frames']['data_recovered']}/"
                  f"{doc['frames']['data_total']} rows {doc['rows']['decodable_from_cluster_frames']}/"
                  f"{doc['rows']['total']} first drop {doc['first_large_drop']} oracle "
                  f"{o.get('frames_data_recovered')} {o.get('classes_failed_strands')} {doc['seconds']} s", flush=True)
    return 2 if false else 0


if __name__ == "__main__":
    raise SystemExit(main())
