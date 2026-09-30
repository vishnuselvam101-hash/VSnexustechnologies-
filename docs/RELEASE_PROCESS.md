# Release process

1. **Clean environment:** `python -m venv /tmp/rel && /tmp/rel/bin/pip install -e '.[dev]'` from a clean clone of the
   release candidate commit; `vnx-dna --help` must work.
2. **Full suite:** `python -m pytest` (V1 and V2: unit, integration, property, adversarial, CLI, README, streaming).
   Every test must pass.
3. **Large-file acceptance** on the release candidate commit (outside the repository, on a disk with ≥ 5× the
   largest size free):
   `vnx-dna benchmark scale --sizes 1MB,10MB,100MB,500MB,1GB,2GB,5GB,10GB --work-dir … --output research/results/v2/scale-matrix.json`
   and `vnx-dna benchmark corruption --size 1GB --work-dir … --output research/results/v2/corruption.json`.
   Every run must be `PASS`.
4. **Experiments:** `python research/v2/run_v2_research.py --out research/results/v2`. "Undetected corruption" and
   "internal errors" must be 0 everywhere.
5. **Tables:** `python research/v2/render_v2_tables.py`. Never type measured numbers into the docs by hand.
6. **Hygiene:** `git status` clean; `git ls-files` shows no generated data (no `*.bin`, `*.vxdna`, `*.vxs`, no 1–10 GB
   files); secret scan (the patterns in PROJECT_STATE.md; `gitleaks` if available); no debug leftovers
   (`breakpoint(`, `pdb`, stray `print(` in `src/`).
7. **Version:** `src/vnxdna/_version.py` is the single source. Measure on an `rcN` version, then set the final
   version in the release commit, which may differ from the measured commit only in version and documentation.
   Update CHANGELOG.md, README.md and docs/PROJECT_STATE.md.
8. **Commit and tag:** release commit, then an annotated tag `vX.Y.Z` on it. Use `v2.0.0` only when every V2
   acceptance criterion in PROJECT_STATE.md is met; otherwise use a pre-release version.
9. **Publish:** `git fetch --all --prune`, and inspect `git log --graph` for remote work not present locally.
   Integrate anything found without rewriting history, and rerun step 2. Push the branch and the tag. **Never
   force-push.** Verify that the remote branch and tag point at the release commit.
10. **Fallback:** if the push fails (authentication or permissions), keep the local release intact and report the exact
    error and the command to run next.
