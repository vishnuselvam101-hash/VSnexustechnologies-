# G-MEM: peak memory of encode and decode versus input size on noisy channels

**SIMULATED channel; wall time and peak RSS MEASURED on this host (shared; load recorded).** Software strands were run
through the shipped, unfitted channel models illumina-like, nanopore-like and deletion-heavy. No DNA was
synthesised, stored or sequenced.

- Scripts: `run.py` (driver), `measure.py` (fresh-process measurement, shared with `../g-scaling`), `memray.sh`
  (heap attribution). Results: `results/size-{1,4,16}MiB.json` (commit `a3e53cd`) and
  `results/size-64MiB-<model>.json` (run one model at a time because of disk space; commits `1cb7f08`, `36adade`
  and `358ab29`. The experiment code is the same in all of them: only the `--models` and incremental-save
  options were added in `6be3f65`). Every file records a clean tracked tree.
- Host: Intel Xeon Gold 6240, 8 logical CPUs, 31 GiB, Python 3.12.3. Native align, reads and rs (AVX2) kernels active.
- Method: every command runs in a fresh interpreter, which writes its **own `VmHWM`** at exit. This is the
  high-water mark of the address space created by exec, so the harness's memory is not included, unlike V6's `wait4`
  figures. `wait4` ru_maxrss is still recorded as `wait4_ru_maxrss_bytes`. Encode uses
  `vnx encode --workers 1 --compression none` and decode uses `vnx decode --workers 1 --events`. With one worker the
  whole decode runs in the measured process. RSS is sampled every 50 ms and each sample is assigned to the decode
  stage whose event closes its interval.
- Inputs: a random payload of S MiB (seed 7000+S) and simulate seed 71000+S. At the models' coverage of about 10 the
  reads file is 200-300 bytes per payload byte, e.g. 13.4 GB of FASTQ for 64 MiB of illumina-like.

## Peak RSS (VmHWM, MiB) and decode outcome

| input | encode | decode illumina-like | decode nanopore-like | decode deletion-heavy |
|---|---|---|---|---|
| 1 MiB | 57.9 | 110.0 (exact) | 110.5 (fails) | 170.5 (fails, PARTIAL) |
| 4 MiB | 60.3 | 116.4 (exact) | 107.2 (fails) | 194.6 (fails, PARTIAL) |
| 16 MiB | 63.5 | 115.8 (exact) | 116.2 (fails) | 186.7 (fails, PARTIAL) |
| 64 MiB | 63.2 | 118.2 (exact) | 122.0 (fails) | 186.5 (fails, PARTIAL) |
| reads at 64 MiB | — | 13.4 GB | 19.7 GB | 13.2 GB |
| decode wall s, 1/4/16/64 MiB | 0.7/1.9/6.8/26.3 (encode) | 3.6/12.6/48.9/195.0 | 12.6/48.5/210.9/800.3 | 15.1/54.1/235.4/975.7 |

"exact": the decoded container's SHA-256 equals the encoded one. "fails": exit 5, nothing published. The two
unfitted models fail at every size, as in V6: nanopore-like fails at the superblock after pass 1, and deletion-heavy
with `INSUFFICIENT_REDUNDANCY` after pass 2. Memory is still measured over the full decode effort. Wall times were
measured under a shared load (1-minute load average 2.5-12.4 at the start of the measured commands); they are linear
in the input to within that noise (illumina-like about 3 s per MiB).

## Bounded or growing?

**Bounded on every measured channel.** From 1 to 64 MiB of input (64x, reads files from 0.2 to 13-20 GB):

- **Encode** rises from 57.9 to 63.2-63.5 MiB, +5.6 MiB over the whole range and flat from 16 to 64 MiB. The heap
  peak at 16 MiB is 25.0 MB (memray, `results/memray-encode-16MiB.txt`; largest live allocations: constraint
  checking 3.8 MB, strand building 2.4 MB, marker insertion 1.6 MB). The rest of the RSS is the interpreter, NumPy,
  the cryptography library and the native kernels.
- **Decode** stays within 107-122 MiB on illumina-like and nanopore-like at every size. On deletion-heavy it stays
  within 170-195 MiB, with no trend from 4 to 64 MiB. The read parser streams 8 MiB blocks, pass 1 works in batches,
  and pass 1 writes fixed-width records to spill buckets on disk (about 200,000 reads per bucket); pass 2 loads one
  bucket at a time. The decoded container is not held in memory.

**Which stage dominates.** About 42-44 MiB is the interpreter before the command starts, and about 64-71 MiB is
reached once the libraries and the native kernels are loaded (stage `input`).
- illumina-like: pass 1 reaches 104-113 MiB, pass 2 (outer) adds 5-10 MiB. The peak is in pass 2 or in the output
  stage that follows (same value).
- nanopore-like: the peak is in pass 1 (107-122 MiB). The decode stops at the superblock.
- deletion-heavy: **pass 2 dominates**, at 171-195 MiB against 108-121 MiB in pass 1 (56-79 MiB above it). In memray
  (`results/memray-decode-deletion-heavy-1MiB.txt`, 1 MiB) the heap peak is 76.3 MB, of which 59.3 MB is live under
  the outer stage (`recovery/outer.py:run`): `consensus.snap_addresses` 33.2 MB (the one-byte-difference index over
  the bucket's missing addresses), `spill.load` 16.8 MB (one bucket's accepted and pending records) and consensus
  symbols 3.8 MB. Both scale with one bucket's pending reads, not with the pool size.

**Disk instead of memory.** The bound is bought with temporary disk. The spill peaked at 8.2 % of the reads file for
illumina-like at 64 MiB (1.10 GB) and at 9.3 % for nanopore-like at 4 MiB (`results/spill-observations.json`, polled
with `du`). The reads file itself must be a regular file: gzip and BAM are refused, and the bucket count is estimated
from the file size.

## Limits of this measurement

- One seed per cell and one run per cell. The memory figures are stable to a few MiB across sizes. Wall times are
  only indicative on this shared host.
- `--workers 1` only. With W workers each worker is a separate process of about 87-92 MiB (decode), so the total grows
  with W (see `../g-scaling`).
- The spill bucket count is capped by the open-file limit (up to 4096 buckets, i.e. about 800 M reads at 200,000 per
  bucket). Above that the bucket size, and so pass-2 memory, grows. This was not reached here: 64 MiB of illumina-like
  is 20,951,236 reads.
- Opt-in recovery modes (`--indel-recovery smart`, `--soft-decoding`, `--consensus-weighting quality`, `--retry-band`)
  were not measured. They keep raw reads in the spill records and can change these figures.
- MEASURED VmHWM is the resident peak, not the virtual size. Memory released back to the allocator but not to the OS
  still counts.

## For V8 (streaming)

1. Memory is already bounded per process. The open costs are about 65 MiB of fixed footprint before any work, about
   90 MiB per extra worker, and the pass-2 consensus index on recovery-heavy channels (56-79 MiB above pass 1 on
   deletion-heavy).
2. Streaming input: reads must currently be a seekable regular file of known size (bucket estimate), and the full
   read set is spilled before pass 2. A true streaming decode needs an input-size-free bucket plan (or adaptive
   re-bucketing) and an outer decode that can start on complete buckets before the input ends.
3. Disk: plan about 10 % of the read bytes for the spill on top of the reads themselves. For 64 MiB of
   nanopore-like that is about 2 GB of spill on top of a 20 GB FASTQ.
