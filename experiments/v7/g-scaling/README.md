# G-SCALE: encode and decode wall time at 1, 2, 4 and 8 workers (16 MiB)

**SIMULATED channel; wall time and peak RSS MEASURED on a shared host.** Software strands were run through the shipped,
unfitted channel models. No DNA was synthesised, stored or sequenced. The timings are comparable only within this
file. The host was shared with other work during the run: the 1-minute load average ranged from 3.1 to 16.2 on 8
logical CPUs, and it is recorded for every run.

- Script: `run.py` (measurement helper: `../g-memory/measure.py`). Results: `results/scaling-16MiB.json`, which holds
  every run with its exit code, output SHA-256, stage seconds, load average, the main process's VmHWM and the sampled
  process-tree RSS.
- Commit `f5dcddf` (clean tracked tree); Intel Xeon Gold 6240, 8 logical CPUs, 31 GiB; Python 3.12.3; native kernels
  (align, reads, rs AVX2) active.
- Input: a 16 MiB random payload (seed 7016), `--compression none`, profile v4-balanced. illumina-like reads (seed
  71016, 3.36 GB FASTQ): the decode succeeds. nanopore-like reads (seed 71016): unfitted, the decode fails (exit 5)
  after the full recovery effort. That is the expected V6 outcome for this model, and here it serves as a heavy decode
  workload.
- Encode: 3 repetitions per worker count. Decode: illumina-like 3 repetitions, nanopore-like 1. The worker order is
  rotated for each repetition. Speed-up = median wall time at W=1 / median wall time at W.

## Results

| command | W | runs | median wall s (min-max) | speed-up | efficiency | main VmHWM MiB (max) | tree RSS peak MiB (sampled, max) | load 1-min |
|---|---|---|---|---|---|---|---|---|
| encode | 1 | 3 | 6.55 (6.40-6.84) | 1.00 | 1.00 | 62.4 | 60.7 | 3.3-4.6 |
| encode | 2 | 3 | 3.61 (3.33-3.82) | 1.82 | 0.91 | 57.4 | 141.1 | 3.2-5.4 |
| encode | 4 | 3 | 2.09 (2.07-2.18) | 3.14 | 0.78 | 60.4 | 251.0 | 3.1-3.3 |
| encode | 8 | 3 | 1.54 (1.51-2.45) | 4.26 | 0.53 | 71.8 | 467.3 | 3.1-4.3 |
| decode illumina-like | 1 | 3 | 50.92 (50.01-53.90) | 1.00 | 1.00 | 127.9 | 131.2 | 7.2-10.5 |
| decode illumina-like | 2 | 3 | 35.88 (30.18-36.22) | 1.42 | 0.71 | 116.5 | 290.5 | 5.5-12.9 |
| decode illumina-like | 4 | 3 | 17.09 (17.00-31.46) | 2.98 | 0.74 | 143.6 | 485.7 | 7.5-13.4 |
| decode illumina-like | 8 | 3 | 21.62 (13.19-27.11) | 2.36 | 0.29 | 168.7 | 859.1 | 7.2-16.2 |
| decode nanopore-like | 1 | 1 | 193.80 | 1.00 | 1.00 | 109.4 | 109.4 | 4.0-7.0 |
| decode nanopore-like | 2 | 1 | 97.11 | 2.00 | 1.00 | 114.4 | 299.5 | 3.1-4.0 |
| decode nanopore-like | 4 | 1 | 49.39 | 3.92 | 0.98 | 137.4 | 495.4 | 3.1-4.4 |
| decode nanopore-like | 8 | 1 | 31.21 | 6.21 | 0.78 | 166.2 | 876.0 | 4.4-6.6 |

Outputs do not depend on the worker count. All 12 encodes produced the same strands SHA-256, and all 12 illumina-like
decodes returned the same container, equal to the encoded one. All 4 nanopore-like decodes failed the same way, with
no output.

## Reading

- **Decode work is constant; the wall time is not.** The summed pass-1 worker CPU of the illumina-like decode is
  45.6-49.3 s at every worker count (`stage_seconds.pass1_worker_cpu`). The work does not grow with W; the wall
  time depends on how many CPUs the shared host left free. The same configuration varied by up to 2.1x between
  repetitions (W=8: 13.2-27.1 s; W=4: 17.0-31.5 s). The repetitions that ran at load ≈ 7 reached 2.98x (W=4) and 3.86x
  (W=8, best run). The illumina-like medians at W=8 are not a measurement of the decoder's scaling limit.
- **Serial parts.** Pass 2 (outer decode, 2.5-5.2 s) and verification run in the parent at every W, so 16 MiB
  illumina-like decodes cannot go below about 3 s at any worker count. By Amdahl's law this alone caps the speed-up at
  about 50.9 / 3 ≈ 17x. The reader in the parent, which parses reads and hands out batches, is the other serial part
  (not separated in the stage seconds).
- **The recovery-heavy decode scales best.** nanopore-like spends most of its time in per-read work inside the workers:
  6.21x at W=8, measured at a lower load (4.4-6.6). This is 1 repetition per W, so the spread is unknown.
- **Encode** reaches 4.26x at W=8, with efficiency falling from 0.91 at W=2 to 0.53 at W=8. At 1.5 s the fixed costs
  (interpreter and pool start-up, archive build, the serial writer) are a large share of the run.
- **Memory grows with W.** Each worker is a separate process. The decode workers' VmHWM is 87-92 MiB and the encode
  workers' is 48-53 MiB (`max_worker_hwm_bytes`). Forked workers start with the parent's high-water mark at fork time,
  so these figures are upper bounds. Two runs also saw one short-lived extra child. The sampled tree RSS grows roughly
  linearly, from 131 to 859 MiB for illumina-like decode between W=1 and W=8. This sum counts pages shared
  copy-on-write with the parent once per worker and can miss spikes, so it is an estimate, not a bound. The main
  process's own VmHWM also rises somewhat with W, from 128 to 169 MiB, with more batches in flight. A proportional
  set size (PSS) measurement would remove the double counting; it was not taken here.

## For V8

Pool start-up and the per-worker interpreter cost (about 100 MiB each) dominate small inputs. The parent's serial
read parsing and pass 2 bound large ones. A streaming V8 pipeline with a pipelined reader and an outer decode that
overlaps pass 1 would address both. Re-measure on an idle host, with ≥ 5 repetitions, before drawing scaling
conclusions.
