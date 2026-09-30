# Release process

1. **Clean environment:** `python -m venv /tmp/rel && /tmp/rel/bin/pip install -e '.[dev]'` from a clean checkout.
2. **Full suite:** `python -m pytest`. Every test must pass: unit, integration, property, adversarial, CLI and clean
   room.
3. **Extended checks:** `python research/experiments/extended_fuzz.py` (exit 0) and
   `python research/experiments/run_experiments.py --out research/results` (every "wrong data" count must be 0).
4. **Benchmarks:** `vnx-dna benchmark --sizes 1K,10K,100K,1M,10M --repeats 3 -o research/results/benchmarks.json`.
   Update docs/BENCHMARKS.md from the file. Never type numbers in by hand.
5. **Hygiene:** `git status` must be clean, and `git ls-files` must show no generated junk. Run a secret scan (the
   grep patterns in PROJECT_STATE.md, and `gitleaks` if available), and check for debug leftovers (`breakpoint(`,
   `pdb`, `print(` in `src/`).
6. **Version:** bump `src/vnxdna/_version.py`, which is the only version source. Update CHANGELOG.md, README.md and
   docs/PROJECT_STATE.md. Confirm the documentation matches the implementation.
7. **Commit and tag:** make a release commit, then an annotated tag `vX.Y.Z` on that commit.
8. **Publish:** `git fetch --all --prune`, and inspect `git log --graph` for remote work not present locally. Integrate
   anything found without rewriting history, and rerun step 2. Push the branch and the tag. **Never force-push.** Then
   verify that the remote branch and tag point at the release commit.
9. **Fallback:** if the push fails (authentication or permissions), leave the local release intact and report the
   exact error and the next command to run.
