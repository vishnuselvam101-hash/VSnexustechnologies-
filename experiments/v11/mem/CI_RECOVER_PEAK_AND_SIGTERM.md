# CI `test` job on PR #15: V2 recover peak and SIGTERM end-of-run race (2026-10-12)

SIMULATED; one host (Linux 6.8, Python 3.12.3, NumPy 2.5.4), `taskset -c 0,1`, fresh venvs with `pip install -e .[dev,fit]`.
Tree measured: build/v11 @ 97076d6 (before) and the same tree with the pass-2 fix (after); v10.0.0 for comparison.

## 1. `test_peak_memory_does_not_grow_with_file_size` (store → encode → recover, 8 MB vs 64 MB, `--workers 1`)

CI (attempt 2 of run 38168999471, job 114567165774), peak sampled tree RSS in bytes:
`{8000000: [61935616, 90808320, 81502208], 64000000: [63201280, 102244352, 155283456]}` — recover limit
81502208 × 1.25 + 48 MiB = 152.2 MB. Attempt 3 of the same run passed (the 8 MB recover peak on CI varies around the limit).

Local recover peaks (sampled tree RSS, MB):

| tree | 8 MB | 64 MB |
|---|---|---|
| v10.0.0 | 105.5 | 154.7 |
| build/v11 97076d6 | 105.6 / 105.2 / 105.3 | 154.8 / 154.8 / 162.3 |
| with the fix | 105.5 / 105.2 | 105.3 / 105.6 |

In-process `tracemalloc` peak of pass 2 (records → chunks) for the 64 MB archive: 70.9 MB before, 31.3 MB after
(pass 1: 35.2 MB in both). Not a V11 regression: v10.0.0 has the same peak.

Root cause: `v2/decoder.py` pass 2 kept about five spill blocks (13 MB each at `SPILL_BLOCK_RECORDS = 2^18`) alive
while gathering a chunk's records: the raw block, the `filtered()` copy (made even when every record is kept), the
previous joined array (still referenced by the carried tail), the `np.concatenate` result and its big-endian `astype`
copy. An 8 MB input fits in one spill block (no concatenation), a 64 MB input does not, so the recover peak stepped up
by ~50 MB between the two sizes. Fix: `filtered()` returns the block itself when nothing is dropped, and the records
below the chunk's end stripe are copied part by part into one preallocated array (each part is released as it is
copied); about two blocks stay alive. Regression test: `test_pass2_memory_is_bounded_by_two_spill_blocks`
(5.2 blocks before → 2.4 after, bound 3). No format or output change; recovered bytes are identical.

## 2. `test_sigterm_cleans_up_like_ctrl_c[1.0]` (rc -15 instead of 130, empty stderr; CI job 114557442608)

Local reproduction (`recover -j 3` of the test's 12 MB input; unsignalled the run takes 2.1 s, workers appear at
0.55 s). SIGTERM sent N s after the workers appear, 3 trials per delay; (alive at signal, output committed, rc):

- 1.48 s: 3 × (alive, not committed, 130)
- 1.52 s: 2 × 130 (not committed), 1 × (committed, **-15**, stderr empty)
- 1.54 s: 1 × (committed, **-15**), 2 × 130 (not committed)
- 1.56 s: 2 × (committed, **-15**), 1 × finished before the signal (0)
- 1.58 s: 3 × (committed, **-15**)

Every -15 had the output already committed and empty stderr: the command had finished and the interpreter was
shutting down (Python restores the default SIGTERM action during finalisation), which is what the CI failure shows.
Every signal before the commit gave 130 with "vnx-dna: interrupted" and the cleanup. The test's `proc.poll()` check
cannot see "finished but not yet exited", so on a fast runner the 1.0 s case can land in that window. This is a
timing race in the test, not a product defect (the output is complete and verified when it happens); the test is left
unchanged (no skip, no loosening) and a recurrence is retried as a flake.
