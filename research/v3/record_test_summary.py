"""Run the full test suite and record its result as JSON (so the audit report types no test count by hand).

Usage::

    PATH=$PWD/.venv/bin:$PATH python research/v3/record_test_summary.py --out research/results/v3/test-summary.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from vnxdna.provenance import environment


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    env = environment()  # commit and dirty flag *before* the run
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", "-q", f"--junitxml={junit}"]
        started = time.perf_counter()
        r = subprocess.run(command, capture_output=True, text=True)
        seconds = time.perf_counter() - started
        root = ET.parse(junit).getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        counts = {k: int(suite.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")}
        failed = [f"{c.get('classname')}::{c.get('name')}" for c in suite.iter("testcase")
                  if c.find("failure") is not None or c.find("error") is not None]
    result = {"command": "pytest (full suite, vnx-dna on PATH)", "exit_code": r.returncode, "tests": counts["tests"],
              "passed": counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"], "failed": counts["failures"],
              "errors": counts["errors"], "skipped": counts["skipped"], "failed_tests": failed, "seconds": round(seconds, 1),
              "commit": env["git"]["commit"], "dirty": env["git"]["dirty"], "environment": env,
              "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "environment"}, indent=2))
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
