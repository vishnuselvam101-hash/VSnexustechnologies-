"""Writes ``native_polish_golden.json``: digests of ``polish.edit_costs_reference`` on fixed-seed cases (the reference is
the specification; the native kernel must reproduce these). Run: PYTHONPATH=src:. python tests/v9/make_polish_golden.py"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tests.v7.native_cluster_support import digest
from tests.v9.test_native_polish import polish_case, run  # noqa: E402

OUT = Path(__file__).resolve().parent / "native_polish_golden.json"

if __name__ == "__main__":
    seeds = range(20261040, 20261090)
    cases = {str(s): digest(*run(polish_case(np.random.default_rng(s)), "reference")) for s in seeds}
    OUT.write_text(json.dumps({"generator": "tests/v9/make_polish_golden.py", "backend": "reference", "cases": cases},
                              indent=1, sort_keys=True) + "\n")
    print(len(cases), "cases ->", OUT)
