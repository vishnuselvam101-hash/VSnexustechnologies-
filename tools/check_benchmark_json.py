#!/usr/bin/env python3
"""Benchmark smoke check (CI job `benchmark-smoke`): the output of ``vnx benchmark --output FILE`` is valid JSON of the
expected shape. No timing thresholds: throughput numbers only have to be finite and non-negative. Every end-to-end
case must report SUCCESS with the output hash equal to the input hash.

usage: python tools/check_benchmark_json.py FILE.json     exit 0 = valid, 1 = invalid (reasons printed)
"""
from __future__ import annotations

import json
import math
import sys


def _reject_constant(name: str):
    raise ValueError(f"non-standard JSON constant {name}")


def problems(doc) -> list[str]:
    out: list[str] = []
    if not isinstance(doc, dict):
        return ["top level is not an object"]
    for key in ("vnx_benchmark", "environment", "performance_profile", "results"):
        if key not in doc:
            out.append(f"missing key {key!r}")
    if not isinstance(doc.get("vnx_benchmark"), int):
        out.append("vnx_benchmark is not an integer schema version")
    results = doc.get("results")
    if not isinstance(results, list) or not results:
        return out + ["results is not a non-empty list"]
    cases = [r.get("case") if isinstance(r, dict) else None for r in results]
    if "stages" not in cases:
        out.append("no 'stages' case")
    if "end_to_end" not in cases:
        out.append("no 'end_to_end' case")
    for i, r in enumerate(results):
        if not isinstance(r, dict) or not isinstance(r.get("case"), str):
            out.append(f"results[{i}] is not an object with a 'case' string")
            continue
        for k, v in r.items():
            if isinstance(v, float) and (not math.isfinite(v) or v < 0):
                out.append(f"results[{i}].{k} = {v} (not a finite non-negative number)")
        if r["case"] == "end_to_end":
            if r.get("status") != "SUCCESS":
                out.append(f"results[{i}] end_to_end status {r.get('status')!r} (error {r.get('error')!r})")
            if not r.get("input_sha256") or r.get("output_sha256") != r.get("input_sha256"):
                out.append(f"results[{i}] end_to_end output hash differs from the input hash")
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__.strip().splitlines()[-1])
        return 2
    try:
        with open(args[0], encoding="utf-8") as f:
            doc = json.load(f, parse_constant=_reject_constant)
    except (OSError, ValueError) as error:
        print(f"{args[0]}: invalid JSON: {error}")
        return 1
    found = problems(doc)
    for p in found:
        print(f"{args[0]}: {p}")
    if not found:
        print(f"{args[0]}: valid benchmark JSON ({len(doc['results'])} cases)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
