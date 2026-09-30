# V0.1 forensic probes

Research-only scripts used to establish `docs/V0.1_BASELINE.md`. They exercise the
`v0.1-baseline` tag (cf7d1f5) and are **not** part of the production test suite.

Run from the repository root with the package installed (`pip install -e '.[dev]'`):

    python research/v0_1_forensics/probe_rs_mds.py            # slow (~minutes); output in probe_rs_mds.out
    python research/v0_1_forensics/probe_pipeline_behaviour.py  # output in probe_pipeline_behaviour.out

`baseline_perf.json` holds the single-run timing measurements quoted in the baseline document.
