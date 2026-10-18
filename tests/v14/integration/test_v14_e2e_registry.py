"""V14 Phase 9: the §19 matrix registry agrees with the plan document and with the code that implements each row.

Also runs the Rust half of the matrix (``cargo test --release --test e2e_matrix``) and checks that exactly the rows
marked ``run`` passed and exactly the ``pending`` ones were ignored (with their reason), so a row can neither vanish
nor be silently counted as a pass.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from v14_e2e_matrix import PENDING_PREFIXES, ROWS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CRATE = ROOT / "src/vnxdna/native/rust/vnxd"
RUST = (CRATE / "tests/e2e_matrix.rs").read_text()
PLAN = (ROOT / "docs/V14_VALIDATION_PLAN.md").read_text()


def _rust_fns() -> dict[str, str | None]:
    """test fn name -> ignore reason (None when the test runs)."""
    out = {}
    for m in re.finditer(r"#\[test\]\n(#\[ignore = \"([^\"]*)\"\]\n)?fn (\w+)\(", RUST):
        out[m.group(3)] = m.group(2)
    return out


def _py_tests(file: str) -> dict[str, list[str]]:
    """test fn name -> decorator sources."""
    tree = ast.parse((HERE / file).read_text())
    return {n.name: [ast.unparse(d) for d in n.decorator_list] for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


def test_rows_are_unique_and_complete():
    ids = [r.id for r in ROWS]
    assert len(ids) == len(set(ids))
    for r in ROWS:
        assert all((r.title, r.interface, r.fault, r.expected, r.depends)), r.id
        assert r.status in ("run", "pending"), r.id


def test_every_row_is_in_the_plan_with_its_expected_outcome_class():
    table = {m.group(1): m.group(0) for m in re.finditer(r"^\| (R\d+[ab]?(?:-hw)?|M\d+|B\d+) \|.*$", PLAN, re.M)}
    assert set(table) == {r.id for r in ROWS}, set(table) ^ {r.id for r in ROWS}
    for r in ROWS:
        assert ("pending" in table[r.id]) == (r.status == "pending"), r.id


def test_every_row_has_its_implementation_and_skip_state():
    rust = _rust_fns()
    covered = set()
    for r in ROWS:
        kind, ref = r.impl.split(":", 1)
        if kind == "rust":
            assert ref in rust, f"{r.id}: no Rust test {ref}"
            reason = rust[ref]
            covered.add(ref)
        else:
            file, fn = ref.split("::")
            tests = _py_tests(file)
            assert fn in tests, f"{r.id}: no test {ref}"
            skips = [d for d in tests[fn] if "skip" in d]
            reason = skips[0] if skips else None
            if file == "test_v14_e2e_blockdev.py" and fn != "test_r24hw_pi_memory_on_hardware":
                reason = "needs P7/P8 integration" if any(d in ("needs_device", "needs_fs") for d in tests[fn]) else None
        if r.status == "run":
            assert reason is None, f"{r.id} is marked run but is skipped/ignored"
        else:
            assert reason and any(p in reason for p in PENDING_PREFIXES), f"{r.id}: pending without a reason ({reason})"
    assert covered == set(rust), f"Rust tests outside the matrix: {set(rust) - covered}"


def test_rust_matrix_runs_exactly_the_run_rows():
    """Runs the Rust rows, or (under tools/v14-e2e.sh) checks the log of the run the runner just made."""
    rust = [r for r in ROWS if r.impl.startswith("rust:")]
    if os.environ.get("VNX_E2E_INTEGRATION") == "1":  # the lead has turned the P7 rows on: nothing may be ignored
        want_run, want_ignored = len(rust), 0
    else:
        want_run = sum(1 for r in rust if r.status == "run")
        want_ignored = len(rust) - want_run
    log = os.environ.get("VNX_E2E_RUST_LOG")
    if log:
        out = Path(log).read_text()
    else:
        cargo = shutil.which("cargo")
        if cargo is None:
            pytest.skip("cargo is not installed: the vnxd crate cannot be built here")
        r = subprocess.run([cargo, "test", "--release", "--offline", "--locked", "--test", "e2e_matrix", "--", "--test-threads=2"],
                           cwd=CRATE, capture_output=True, text=True, timeout=1800)
        assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-4000:]
        out = r.stdout
    assert f"test result: ok. {want_run} passed; 0 failed; {want_ignored} ignored" in out, out[-3000:]
