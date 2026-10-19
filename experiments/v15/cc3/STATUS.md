# CC-3 status (2026-10-19) — SIMULATED

Complete for the pre-registered runs. See README.md for the results. Every Kaggle job is finished and fetched:
vnx-job-cc3-r, p, h1-h4, m0-m2, g, c1, c2 (CPU only; no GPU jobs).

Full suite on the new server (8-core EPYC, Linux 7.0): 4869 passed, 8 skipped, 2 failed. The two NBD test files were
excluded (`tests/v14/test_device_nbd.py`, `tests/v14/integration/test_v14_e2e_blockdev.py`; a known Linux 7.0
NBD issue owned separately). The 2 failures are timing tests under -n 8:
- `tests/v3/test_release_review_v3.py::test_sigterm_cleans_up_like_ctrl_c[1.0]`: a known race, see V11 notes.
- `tests/v14/test_pool_parallel.py::test_interrupt_leaves_no_workers[int]`: a 60 s subprocess timeout.

Both pass in isolation (6 passed, 1 skipped). Neither touches the clustering code.

Open:
- the 32 MB scaling step;
- F's 10 MB decode time;
- an optional named redundancy profile with column parity for nanopore-like reads (CC-4).
