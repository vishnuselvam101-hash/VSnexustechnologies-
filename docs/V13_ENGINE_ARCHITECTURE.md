# VNX-DNA V13 engine architecture (streaming engine, disk-like volume)

Status: IMPLEMENTED on `build/v13-final` (the design below was written before the code on `build/v13-hybrid`, base
`build/v13` 9401721; A/B baseline V12 6bf7749). §8 records what shipped, what differs from the design and what was
measured; the numbers of this document's §3 table were design estimates and are superseded by
[V13_BENCHMARK_REPORT.md](V13_BENCHMARK_REPORT.md).
Every channel number in this repository is SIMULATED. The "disk" vocabulary below (throughput, latency, volume) describes
software codec rates on simulated read pools on this host. It is not a claim about physical DNA synthesis, storage or
sequencing throughput.

## 1. Goals and what is measured

1. **10x decode end to end** against V12 6bf7749 on the 4 MiB bench fixtures (indel 95300, clean 95100, subst 95200).
   Measured as wall time of `decode_reads` in one process, cold result cache.
2. **Streaming both ways.** Decode (reads -> bytes) and encode (bytes -> strands) run as continuous pipelines with
   bounded memory (peak RSS independent of the archive size), back-pressure between stages and a thread pool.
3. **A disk-like volume.** `write/append/flush/read(offset, length)/seek` on top of the codec, a hot-block cache, a
   range read that decodes only the extents it needs, a journal so an interrupted write is recoverable, and scrub.
4. Unchanged guarantees: the decoder never returns wrong data (inner RS + CRC per frame, outer code, container SHA-256
   and structural validation before publish); V4/V5/V12 archives keep decoding; on-disk and in-DNA formats unchanged
   (the volume is a new, versioned container of ordinary VNX archives); explicit failures stay explicit.

**Workers and threads (fair comparison).** `w` = the number of CPU threads a decode may use. V12 `w1` is one process
with one thread; V12 `w4` is four worker processes. The engine at `w1` runs IO, parsing and decoding on **one thread**
(no helper threads); at `w4` it runs four engine threads in one process. Pass 2 and the probe are Python in both.

## 2. Language architecture (chosen from scratch; replaces the per-kernel ctypes policy for the hot path)

| layer | language | why |
|---|---|---|
| streaming core (`native/rust/engine`, crate `vnx_engine`) | **Rust** (std only) | owns the whole pass-1 pipeline end to end: file IO, the FASTQ/FASTA scanner (untrusted input: bounds-checked slices, no manual pointer arithmetic), orientation, address scheduling, decode attempts, spill records, the thread pool. Threads with `std::thread::scope`, atomics and channels, no data races by construction; panics are caught at the C ABI. Builds offline with the toolchain on this host (rustc/cargo 1.75; no crates are vendored, so no third-party crates) |
| inner kernels reused inside the core | C11 (`v6/native/rs.c`, `v5/native/align.c`, `native/c/orient.c`) | already bit-exact with the Python references, fuzzed and sanitized, SIMD (AVX2/AVX-512). They are compiled **into** the Rust library by its `build.rs` (gcc, static), so the core calls them directly, with no ctypes hop per batch. Rewriting them in Rust would add risk for no speed |
| binding | C ABI + ctypes | one call per decode (`vnx_eng_pass1`); PyO3 would need crates that cannot be fetched offline under the repository's `--offline --locked` rule |
| orchestration, pass 2, formats, CLI/SDK/API | Python | unchanged specification; pass 2 (superblock, outer code, container validation) runs on the engine's spill files |
| references and the last fallback rung | Python/NumPy | the specification every native result is pinned to |

Trade-off: C++20 would also give one compiled core, but its standard library has no scoped threads with borrow-checked
sharing and its parsers would need the same manual bounds discipline as the C ones. Rust gives memory safety on the
untrusted-input side and safe parallelism. The rest of the work is in the existing C kernels either way.

## 3. The decode engine (pass 1 in one native call)

```
 file --IO--> blocks --scan--> records --parse/orient--> keys --schedule--> attempts --verify--> spill records
       (pread, buffer pool,      (newline SIMD scan,        (address      (exact pack | marker      (bucket files,
        read-ahead)               codes, orientation)        dedup)        projection | DP) -> RS+CRC  same format)
```

* **IO layer.** `pread` of 8 MiB blocks into a pool of reusable buffers, one block of read-ahead (the next block is
  fetched while the current one is decoded; at `w1` the same thread does both, sequentially). Records that cross a
  block boundary are carried over. Memory is bounded by the buffer pool plus one batch of records, whatever the file size.
* **Scanner.** The reference parser's rules (`dnaenc.reads.iter_reads`: 4-line FASTQ, `\r` stripping, blank header lines,
  `+` line, quality length, the A/C/G/T/N table with every other byte -> N), on SSE2 newline search. Any input outside
  the subset the engine reproduces exactly (FASTA wrapping rules aside, which are implemented; `.vxs`, gzip, overlong
  lines, malformed records) makes the engine refuse with `UNSUPPORTED_INPUT`, and the fallback path raises the reference
  error.
* **Per-read function.** For a read that is attempted, the engine computes exactly what V13 `recovery.pass1._process`
  computes for that read (the results of a read never depend on the other reads of its batch): orientation scan, the
  exact-length path (`vnx_or_pack_exact`, RS errors+erasures with the errors-only retry, CRC), the marker-anchored
  projection (`vnx_or_spec_project`), the banded DP (`vnx_align_batch_sel_lv`), the bounded reverse-complement retry
  (`vnx_or_rc_bound`), and the pending record with its two header readings. The test gate compares accepted/pending
  records of attempted reads with `_process` on the same reads.
* **Address scheduler (the 10x lever).** At coverage c, V12/V13 decode every copy of every strand. The engine reads
  each read's header bytes without correction (the `alt` reading of pass 1) and attempts a read only if its address
  is not verified yet. Within a batch the first read of an address is attempted. Further reads of that address wait,
  and are attempted only if every earlier attempt of the address failed. Reads whose header is unreadable (wrong
  version nibble or kind) are always attempted. The rounds are deterministic, so the same reads are attempted at any
  thread count. A read with a corrupted header can be skipped behind another address; its own address still has its
  other copies. If pass 2 then cannot reach SUCCESS, the fallback ladder decodes everything.
  *Protection trade-off, stated honestly:* V12 resolves duplicate verified copies by strict majority. The engine keeps
  one verified copy per address, so a frame that passes RS + CRC-32 wrongly (probability about 2^-32 per corrected
  frame) is no longer outvoted. It is still caught by the outer code and the container SHA-256, and a failed
  verification falls back to the full path. The decoder still never publishes unverified data.
* **Spill.** The engine writes the `acc{b}.bin` / `pend{b}.bin` bucket files in the existing record format (big-endian
  fields, bucket = group mod B), so pass 2 is the unchanged Python `_pass2`.
* **Balancer.** A pool of `w - 1` helper threads plus the calling thread. Every batch is split into chunks that the
  threads claim with an atomic counter (dynamic, work-stealing-like balancing; results are written by read index and
  merged in read order, so output does not depend on the thread count). The IO and scan stage runs ahead by at most
  two batches (back-pressure: a bounded queue). The batch size adapts to the attempt rate (more reads per batch when
  most are skipped), within a memory budget.

### Fallback ladder (automatic, recorded in `report["engine"]`)

1. `stream`: the Rust engine (pass 1) + Python pass 2.
2. `v13`: the build/v13 Python-orchestrated path (all reads, every kernel through ctypes).
3. `reference`: the same with every kernel on its Python reference (`VNXDNA_*_BACKEND=reference`).

Selection: `DecodeOptions.engine = "auto"` (default) tries `stream` when the engine library loads and the options are
inside its domain (default recovery configuration, FASTQ/FASTA input, no `select`, no stage counters or clustering).
Otherwise it uses `v13`. Any of these drops to the next rung: an engine refusal or internal error, a non-SUCCESS
pass 2, or a decode-class error. Configuration, key, output-exists and archive-mismatch errors propagate unchanged.
`"stream"` requires the engine (error if it is unavailable), `"v13"` selects the old path explicitly, and `"reference"`
forces rung 3. A retry with an escalating recovery budget (`recovery="adaptive"`: rung 2 with smart indel recovery
and soft decoding after a plain failure) and a wall-clock timeout with cancellation (`timeout_s`) sit on the same
ladder.

### Stage budget (design estimates for 4 MiB indel, about 700 k reads, about 140 k addresses; to be replaced by measurements)

| stage | V13 w1 (measured, s) | engine w1 target | engine w4 target |
|---|---|---|---|
| layout probe | 0.25 | 0.10 (vectorised sampling) | 0.10 |
| IO + scan (419 MB FASTQ) | in parse 0.33 | 0.08 | 0.03 |
| codes + orientation + header key, all reads | 0.37 + bookkeeping 0.5 | 0.12 | 0.04 |
| attempts (about 1.2 per address): pack/projection/DP/RS/CRC | 2.6 | 0.40 | 0.11 |
| spill write | in bookkeeping | 0.02 | 0.02 |
| pass 2 (about 140 k instead of 700 k verified records) | 0.46 | 0.15 | 0.15 (serial) |
| imports, output write, verify | 0.3 | 0.05 | 0.05 |
| **total** | **5.0** | **about 0.9 (10.4x vs 9.36)** | **about 0.5 (6x vs 2.99)** |

The w4 target is limited by the serial Python pass 2 and the probe (Amdahl): 10x at w4 (0.30 s) needs pass 2 below
0.1 s. That is listed as the next lever (native pass 2) if the measurements confirm it. The clean and substitution
fixtures have no DP work, so the attempt stage is smaller there.

## 4. Streaming encode

`encode_stream(source, sink)`: a byte source (file, stdin, iterator) is cut into extents of `extent_bytes`. Each extent
is packed into an ordinary VNX archive (unchanged format) and encoded to strands by the existing strand kernels in a
bounded producer/consumer pipeline (a reader thread, a pool of encoder workers, an ordered writer; at most 2 x workers
extents in flight). Strands go to the sink (FASTA file or stdout) in extent order. Peak memory = in-flight extents,
independent of the input size. A single archive larger than memory is not split: whole-archive encoding keeps the V12
path.

## 5. Volume (`vnxdna.volume`, opt-in, versioned container `vnx.volume/1`)

A volume is a directory:

```
  volume.json        format id vnx.volume/1, extent size, codec options, created_by
  journal.log        write-ahead log: one JSON line per record, each with its own CRC-32; append + fsync
  extents/e<id>-v<ver>.fasta   strands of extent <id> version <ver> (an ordinary VNX archive of that extent's bytes)
```

* `Volume.open(path, mode)`, `write(bytes)`, `append(bytes)`, `seek(offset)`, `tell()`, `read(length)` /
  `read_at(offset, length)`, `flush()`, `truncate()` is NOT offered (append-only extents; overwrite = new version),
  `close()`, `stat()`, `scrub()`.
* **Write path.** A write-back buffer holds the dirty tail extent. `flush()` encodes every dirty extent, writes
  `e<id>-v<ver>.fasta.tmp`, fsyncs it, renames it, fsyncs the directory, and only then appends + fsyncs the journal
  `commit` record (extent id, version, length, SHA-256 of the extent bytes, SHA-256 of the strand file). An interrupted
  flush leaves at most a temp file or an unreferenced extent file. On open, the journal is replayed up to the last
  record whose CRC verifies, and unreferenced or temp files are removed (crash-consistent).
* **Read path.** `read_at(offset, length)` maps the range to extents, serves hot extents from an LRU cache bounded in
  bytes, and decodes only the missing extents with the streaming engine (a range read touches only the strands of those
  extents). Each decoded extent is checked against the journal's per-extent SHA-256 before it is returned or cached.
* **Scrub.** `scrub()` decodes every committed extent from its strands and verifies both checksums. It reports per
  extent, and never repairs silently.
* **Result cache** (`vnxdna.cache`, off by default, never on in benchmarks): decoded containers keyed by engine
  version + commit, options and input identity (path, size, mtime_ns, inode, and a 64-bit content digest computed by the
  engine while it streams the file). Size-bounded LRU on disk; any key component that changes invalidates the entry;
  `vnxdna cache clear|stats`.
* **Metrics like a disk** (SIMULATED pools): sustained encode and decode MB/s, latency of `read_at` (cold and hot
  cache), peak RSS.

## 6. Observability and configuration

`report["engine"]`: rung, fallback reason, threads, per-stage seconds, reads scanned/attempted/skipped, MB/s, queue
depth high-water mark. `vnxdna native` and `native_status()` list the engine library (ABI, origin, SIMD of the reused
kernels). `vnxdna engine stats` and the volume's `stat()` report cache hit rate, extents and journal state. One
configuration surface: `DecodeOptions`/SDK keys, environment (`VNXDNA_ENGINE`, `VNXDNA_ENGINE_THREADS`,
`VNXDNA_ENGINE_LIB`, `VNXDNA_CACHE_DIR`) and CLI flags (`--engine`, `--threads`). Defaults are safe: engine auto with
fallback, cache off, threads = `workers`.

## 7. Test and evidence gates

* Byte-identical decode output of the engine vs 6bf7749 on the three bench fixtures (both w1 and w4).
* Equivalence of attempted-read records with `_process` and of the scanner with the reference parser, on randomized
  inputs (short fuzz in development, longer at the end); refusal tests (malformed inputs give the reference error
  through the ladder); fallback-rung tests (missing library, injected engine error, non-SUCCESS pass 2).
* Round-trip, crash-recovery (killed flush), scrub and cache-invalidation tests for the volume.
* Once at the end: the full suite (never below 4,209 tests, never skipping or weakening one), `cargo test`, ASan/UBSan
  of the engine library (Rust + the reused C), a fuzz run of the scanner, and the compact A/B bench (3 workloads x w1/w4
  x 3 reps), reported per workload with its honest tier (below Tier 1 / 2x / 5x / 10x).

## 8. Implementation status (2026-10-17, `build/v13-final`)

| design item | status | where |
|---|---|---|
| Rust streaming core, pass 1 in one native call, C kernels linked in, C ABI + ctypes | shipped (`vnx_eng_pass1`, `vnx_eng_scan_file`, ABI 1) | `src/vnxdna/native/rust/engine/`, `vnxdna.native.engine` (binding, plain parameters), `vnxdna.recovery.engine` (parameters from a layout and the options) |
| IO layer: mmap + read-ahead + release of consumed pages, buffered fallback | shipped (`io.rs`, `engine::scan_file`) | the resident set of a decode does not grow with the input (A/B RSS ratios in the benchmark report) |
| scanner = reference parser or refusal | shipped (`scan.rs`; randomized agreement test, crate unit tests) | `tests/v13/test_engine.py` |
| address scheduler (content fingerprints + raw header key, lossy tables, cheap-then-full rounds) | shipped (`sched.rs`, `engine.rs`) | deterministic across thread counts (test); `engine_dedup=False` attempts every read |
| balancer: persistent pool, atomic chunk claiming, results merged in read order | shipped (`pool.rs`, `par_chunks`) | threads = `engine_threads` or `workers` |
| fallback ladder stream -> v13 -> reference, timeout/cancel, request errors propagate | shipped (`pipeline.decode._decode_ladder`) | `report["engine"]` |
| adaptive retry with an escalating recovery budget | not as a separate rung: the ladder's `v13` rung runs the full V12/V13 recovery (smart/soft recovery stay opt-in as before) | — |
| streaming encode (bounded in-flight extents, ordered writer, stdin/stdout) | shipped | `vnxdna.pipeline.stream`, `vnx stream encode` |
| volume: write/append/seek/read/read_at/flush, hot-extent LRU cache, write-back, journal/WAL, crash-consistent flush, scrub, range reads touching only the needed strands | shipped | `vnxdna.volume`, `vnx volume` |
| result cache (keyed by software/commit/backends/engine library hash, input SHA-256 and size, options, key presence; bounded; off by default) | shipped at the SDK layer | `vnxdna.sdk.cache`, `sdk.decode(cache_dir=...)`, `vnx cache stats|clear`, `vnx decode --cache-dir` |
| observability: `report["engine"]` (rung, fallbacks, threads, per-stage seconds, reads scanned/attempted/skipped, table statistics), `vnxdna native` lists the engine, `vnx volume stat` (cache hit rate, journal, IO rates) | shipped | — |
| one configuration surface | shipped: `DecodeOptions.engine/engine_dedup/engine_threads`, SDK config keys, `VNXDNA_ENGINE`, `VNXDNA_ENGINE_THREADS`, `VNXDNA_ENGINE_BACKEND/_LIB`, `VNXDNA_CACHE_DIR/_BYTES`, CLI `--engine`, `--engine-dedup`, `--engine-threads`, `--cache-dir` | — |
| native pass 2 | not done (the next lever: pass 2 and the layout probe are the serial Python remainder; see the benchmark report's stage budget) | — |

Protection decision (§3, "one verified copy per address"): kept as the default, decided on measurement; the reasons
are in [V13_COMPLETION_REPORT.md](V13_COMPLETION_REPORT.md) and the lab decision log.
