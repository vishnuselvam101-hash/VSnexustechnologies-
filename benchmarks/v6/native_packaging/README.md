# Installed-path decode: pip install of 081697b vs work/native-packaging (MEASURED, SIMULATED data)

Question: how much faster is a decode in a plain `pip install` now that the install builds all three native kernels,
instead of only the V5 aligner?

## Setup

- Host: the development VPS, Intel Xeon Gold 6240 (8 vCPUs), 31 GiB RAM, Python 3.12.3, gcc 13.3.0. Other workloads
  were running on the host during the measurement: load averages before each run are recorded in the results file
  (6.5 to 14.2).
- Two fresh virtual environments, each a plain `pip install` of a clean `git archive` export:
  ```bash
  git archive --prefix=src-081697b/ 081697b | tar -x -C .tmp-venvs
  python3 -m venv .tmp-venvs/before && .tmp-venvs/before/bin/pip install .tmp-venvs/src-081697b
  git archive --prefix=src-456e9de/ 456e9de | tar -x -C .tmp-venvs
  python3 -m venv .tmp-venvs/after  && .tmp-venvs/after/bin/pip install .tmp-venvs/src-456e9de
  ```
  Backends that ran (recorded by the script from each module's `status()`):
  - before: aligner native, read parser reference, RS reference;
  - after: aligner native, read parser native, RS native (AVX2).
- Workload: 4 MiB random input (seed 42), profile v4-balanced, EXP-0011 channel (0.2 % substitutions, 0.05 %
  insertions, 0.05 % deletions, 2 % dropout, Poisson coverage 3, seed 1011). It was generated once
  (`--prepare`, with the after environment), so both environments decode the same reads file
  (sha256 `155b56a1fa36…`, in the results file).
- Decode: `vnxdna.v4.decoder.decode_reads` with `workers=1`, one untimed warm-up, then 5 timed repetitions per run.
  The runs alternated before/after for 3 rounds:
  ```bash
  cd .tmp-venvs
  after/bin/python  ../benchmarks/v6/native_packaging/installed_decode.py --workdir bench --prepare --repeats 1
  for round in 1 2 3; do for v in before after; do
    $v/bin/python ../benchmarks/v6/native_packaging/installed_decode.py --workdir bench --repeats 5 --workers 1 --label $v
  done; done
  ```

## Results (`results/installed_decode.jsonl`)

| round | load avg (1 min) before / after run | before: median s | after: median s | before / after |
|---|---|---|---|---|
| 1 | 14.22 / 12.65 | 14.819 (range 10.9–17.8) | 5.366 | 2.76 |
| 2 | 9.10 / 7.83 | 10.824 | 5.311 | 2.04 |
| 3 | 6.52 / 8.50 | 10.862 | 5.322 | 2.04 |
| all 15 repetitions | | 10.907 | 5.322 | 2.05 |

Every run returned `SUCCESS` with the same container (sha256 `0203ccff26e4…`) in both environments.

Interpretation: on this host and workload, a single-worker decode in a plain pip install went from 10.8 s to 5.3 s
(2.04x, rounds 2 and 3; round 1's before-run was slowed by other load on the host and is not used for
the ratio). This is the combined effect of the native read parser and the native RS decoder on the installed path. It is
one machine, one workload, one worker count; other CPUs, channels and worker counts were not measured.
