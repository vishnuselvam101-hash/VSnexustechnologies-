# V11 release gates (evidence summary)

Branch `work/v11-release` from `build/v11` @ 7b2beb2, gates run 2026-10-11 at 54fc778 (version 11.0.0) on the lab host
(Ubuntu, Python 3.12, clang 18.1.3, gitleaks 8.30.1, ruff 0.16.10). WP1 (SIMD aligner, ABI 3) and WP3 (memory) were not
yet merged; the gates marked **re-run after merge** must be repeated on the final release commit. Raw logs were kept
outside the repository; only these summaries are committed.

| Gate | Result | Evidence |
|---|---|---|
| Version consistency | PASS | `_version.py` = CHANGELOG head = `vnx-dna --version` = `vnx --version` = `11.0.0` (`tests/v11/test_version_changelog.py`, 6 tests) |
| Wheel build + clean install | PASS | `git archive HEAD` → `pip wheel --no-deps` → `vnx_dna-11.0.0-cp312-cp312-linux_x86_64.whl` (355 files, 7 native extensions packaged); fresh venv: `vnx-dna --version` and `vnx --version` print 11.0.0; `python -m vnxdna.native` loads the packaged libraries; `vnx-dna pipeline` round trip of 200 000 random bytes byte-identical (SHA-256 7cde4f05…). **Re-run after merge** (WP1 changes the align library to ABI 3). |
| Secret scan, branch diff | PASS | gitleaks on `v10.0.0..work/v11-release` (bare clone, no ignore file): 0 findings |
| Secret scan, full history | PASS | bare clone, `--log-opts=--all`: 48 `generic-api-key` findings, 48 fingerprinted in `.gitleaksignore`, 0 unreviewed; triage of all 48 in docs/SECURITY.md |
| pip-audit | PASS (triaged) | lab venv and clean wheel venv: `cryptography` 46.0.7 PYSEC-2026-3552/3553/3554 and GHSA-537c-gmf6-5ccf, `pytest` 8.4.2 PYSEC-2026-1845; all 5 triaged in docs/SECURITY.md, nothing new. The clean venv's own `pip` 24.0 (installer, not a dependency) is noted there too. |
| Fuzz smoke, native parser (`reads`) | PASS | `fuzz/run.sh reads` (libFuzzer + ASan + UBSan, 1 CPU, 4 GiB slice): 1 000 000 execs, 10 204 exec/s, 0 crashes, peak RSS 437 MB |
| Fuzz smoke, `align` harness | clean, time-boxed | 420 s: 276 514 execs, 0 crashes (pre-WP1 `align.c`). **Re-run after merge**; WP1 also fuzzes every SIMD level in its sanitizer script. |
| Fuzz smoke, `rs` harness | clean, time-boxed | 420 s: 61 404 execs (145 exec/s), 0 crashes. Below 1M execs; the RS decoder is not a parser, so the ≥ 1M rule (baseline §9 WP4) does not cover it. |
| ruff | PASS | `ruff check src tests research`: All checks passed |
| mypy | PASS | `mypy` (pyproject package set, 148 files): no issues, after typing one list in `core/guard.py` (54fc778, annotation only) |
| Full test suite | PENDING | run on the final release commit; counts go to docs/TESTING.md and docs/V11_COMPLETION_REPORT.md |
