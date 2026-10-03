# V5 Phase 4 experiments (soft-decision inner decoding)

All results are **SIMULATED**. Report: `docs/V5_PHASE4_REPORT.md`. Run from the repository root with the project venv
(`.venv/bin/python`), native aligner built (`.venv/bin/pip install -q --no-deps -e .`).

| experiment | command | seeds | approx. time (Xeon Gold 6240) |
|---|---|---|---|
| P4-EXP-01 substitution sweep | `python experiments/v5/phase4/exp_read_level.py subs --strands 4096` | strands 4101, channel 4102, graded Q 4109 | 2 min |
| P4-EXP-03 indel + substitution sweep | `python experiments/v5/phase4/exp_read_level.py indel --strands 4096` | strands 4101, channel 4102, graded Q 4109 | 8 min |
| P4-EXP-02, 04, 05, 06 frame level | `python experiments/v5/phase4/exp_frame_level.py all --frames 500` | 4201 (+2, +4, +5, +6 per experiment) | 4 min |
| P4-EXP-07 identical read files, 128 KiB, 1 worker | `python experiments/v5/phase4/exp_archive.py exp07 --seeds 3` | input 4401, channel 44000 + 10·k | 8 min |
| P4-EXP-08 coverage 1/2/3/5/10, 32 KiB, 8 workers | `python experiments/v5/phase4/exp_archive.py exp08 --seeds 2` | input 4801, channel 44000 + 10·k | 18 min |
| default-path runtime vs Phase 3 | `python experiments/v5/phase4/runtime_default_path.py --baseline-tree <clean 3633dfb worktree> --reps 3` | input 42, channel 1011 | 8 min |
| recovery curves (CSV) | `python experiments/v5/phase4/make_curves.py` | from stored results | seconds |

Each `P4-*/results.json` holds the configuration (and its SHA-256), the provenance (commit under test, CPU, Python,
NumPy, gcc) and, for read and archive experiments, the SHA-256 of every read file, so all decoders are compared on
identical reads. Logs: `log-readlevel.txt`, `log-exp07.txt`, `log-exp08.txt`, `log-runtime.txt` (the final runs);
`run-log-partial.txt` is the log of the first pass, before the combined configurations and the peak-RSS fix were added.

Peak RSS is each decode child's own `VmHWM` (`/proc/self/status`). `ru_maxrss` survives fork + exec on Linux and
reported the parent's peak; the EXP-07/08 results measured that way are kept in `superseded-rss/` for the record.
Only the memory columns changed.
