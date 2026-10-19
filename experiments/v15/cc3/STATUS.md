# CC-3 status (WIP, 2026-10-19, stopped for the server migration) — SIMULATED

## Done (branch work/v15-cc3)
- Ported from work/nano-10mb: over the candidate-pair budget the clustering stage lowers the bucket cap instead of
  stopping (bucket_cap_floor 2, cluster ABI 6) + members grouped by one sort (815f603, a0744b3, b0e44d3, f17ea8a).
- Ported from work/nanopore-f2: preset F hypothesis selection/inner trials and RANSAC codeword scan + eval 4/5 records
  (705b993..5d89c5e). Not ported: v10-oligo300 profile (Q1/CC-5/FZ-3 decision, not needed here).
- New: opt-in decoder preset `nanopore` (= F): `--cluster-preset nanopore`, decode config `cluster_preset`
  (0412030, mypy fix bb9ad56). Tests: tests/v7/test_cluster_preset.py; the cap-lowering proxy test in
  tests/v7/test_read_clustering.py fails on base cfd6ee0 (stage stops: budget_exceeded) and passes now.
- Local repro (base cfd6ee0, seed 93090, 1 MiB D13-F1): default FAILURE, E FAILURE groups [195, 402], F EXACT.
- Focused tests (v7, arch, v9, v10, v11): 671 passed; ruff + mypy clean. Full suite NOT run yet.

## Kaggle CPU results so far (results/kaggle-cc3-*/rows.jsonl; 0 FALSE_SUCCESS over 202 rows)
- R (base, 93090-93099): default 0/10, E 1/10, F 9/10. P (new F, 93090-93109): 19/20, fails 93097 row 92 = eval 5.
- H held-out 93120-93139: new F 15/20, base F 13/20, E (base = new) see rows; no seed where base F/new E wins and
  new F loses (check in summary).
- G regression 93140-93142: clean, illumina-like, indel 3/3 EXACT for every tree x config; nanopore-like model:
  default 0/3, E 0/3, F 3/3 on both trees. 6 rows have status ERROR/CRASH (nanopore-like default/E?) — inspect.

## Still running / queued on Kaggle (outputs stay on Kaggle)
- vnx-job-cc3-m0 / m1 / m2: 10 MB seeds 93100 / 93110 / 93111 (new E,F; base E).
- vnx-job-cc3-c1 (running), vnx-job-cc3-c2 (NOT yet pushed; job dir /root/kaggle-work/jobs/vnx-job-cc3-c2 on the old
  server): amendment 1, d13f1-cp-1m 93150-93159 / 93160-93169, new x {default,E,F}.

## Next steps
1. Fetch: `kaggle kernels output vishnurio/vnx-job-cc3-NAME -p DIR --file-pattern 'out/.*'` for m0 m1 m2 c1; copy
   out/{rows.jsonl,summary.json,commit.txt} to results/kaggle-cc3-NAME/.
2. Push c2: `/root/kaggle-work/kaggle-job.sh cc3-c2 "bash experiments/v15/cc3/kaggle.sh 'd13f1-cp-1m 93160-93169 new default,E,F'" work/v15-cc3`.
3. Inspect the 6 ERROR/CRASH rows in results/kaggle-cc3-g; `python experiments/v15/cc3/run.py summary --out experiments/v15/cc3/results`.
4. If C reaches >= 19/20, consider an opt-in redundancy profile (column parity 128/4) for nanopore-like reads.
5. README with results, then the full suite (gate 4852/5/0) when no CC-1 timing run is active.
