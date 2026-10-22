# Changelog

## 15.0.0

Branch `build/v15`, release candidate, not tagged. Governing scope: `docs/V15_FINALIZATION_PLAN.md` (LR-1..LR-6,
CC-1..CC-5, AP-1..AP-18, DA-1..DA-14) with the scope lock of `docs/V15_M0_DECISIONS.md`; the release notes of the parts
are `docs/V15_REFERENCE_DECODER.md`, `docs/V15_DIGITAL_TWIN.md` and `docs/V15_SECURITY_MONITOR.md`.

V15 is a software-and-simulation release. All channel results are SIMULATED; no DNA was synthesised, stored or
sequenced, and the evidence claims of this release are categories A-D only (software codec, software storage device,
electronic-backend persistence, simulated DNA channel) - nothing is E (physical synthesis) or F (physical sequencing).
The headline is the long-term readability of the software: a frozen format, a golden corpus with pinned digests and an
independent standard-library-only reference decoder, so that a party who has the spec and the strand files can recover
the data without this software. The decode speed improved, and both registered speed targets were missed; both are
stated plainly below.

The Python package version and `CITATION.cff` move to 15.0.0; the Rust crate versions (`vnxd`, `vnx_engine`, integrity)
are deliberately left unchanged, as in V14.

### Added: long-term readability (LR-1, LR-3)
- **A frozen format with a golden corpus.** `reference/golden/` holds four archives (plain-2bit, zlib-rotation3,
  codebook8-plain, encrypted-2bit) whose digests are pinned in `reference/golden/index.json`. Every archive still
  decodes to exactly the bytes it was made from, and `tests/v15/test_hardware_lane.py` fails if an artefact is
  altered, a recorded digest does not match, or the two decoders disagree.
- **An independent reference decoder.** `reference/vnx_ref_decoder.py` is a second implementation of the format-4 read
  path that imports nothing outside the Python standard library, so a future party can port it to another language
  without reading the production code. The version-compatibility contract and the compatibility rule are in
  `docs/V15_REFERENCE_DECODER.md`.
- **Hardware and OS design documents** (`hardware/h1-carrier`, `hardware/h3-fpga`, `hardware/h5-dna-io`). These are
  designs and specifications only: no board, no FPGA and no instrument exists, no bitstream was generated and no
  synthesis flow was run. The H3 decoder reproduces the software reference in simulation; it has never been through
  synthesis, placement, routing or timing.

### Added: the archive platform (AP-1, AP-6, AP-8, AP-14, AP-16, AP-17, DA-14)
- **AP-1 write-once (WORM) retention and snapshots** in the Rust volume (`src/vnxdna/native/rust/vnxd/src/volume.rs`).
  `set_retention` / `retention` set a deadline for one extent or for the whole volume; an active deadline can only be
  extended, never shortened, and every write, release and truncate path on a retained extent is refused with the typed
  error `RETAINED` (`ErrorCode::Retained`). A snapshot pins the mapping of a generation, so the live data may be
  rewritten while the snapshot still reads the old bytes (`src/vnxdna/native/rust/vnxd/tests/worm_snapshots.rs`).
- **AP-6 observability**: a Prometheus text exporter in `src/vnxdna/native/rust/vnxd/src/metrics.rs`. It renders 63
  metric names in the text exposition format 0.0.4 (one `# HELP` and one `# TYPE` line per name, integer values,
  escaped label values, sorted so that two scrapes of the same samples render to the same bytes and can be diffed).
  Every sample is derived from the structures the service already holds - the stats and health views, the backend
  health, the cache counters, the scrub report, the capacity view and the DNA simulator counters; the simulator
  counters are SIMULATED counts of the software model, not measurements of a medium. The alert rules of the plan are
  in this tree: `deploy/monitoring/vnxd-alerts.yml` holds 13 Prometheus rules, and `tests/v15/test_ap6_alert_rules.py`
  checks them against the metric names the exporter renders so the two cannot drift apart.
- **AP-8 synthesis-cost planner** (Python, `src/vnxdna/pipeline/plan.py`): the strands and bases an encode will
  produce, and the cost per vendor price profile that the operator supplies (`vnx.vendor-prices/1`, example in
  `docs/examples/vendor-prices.example.json`). No price is hard-coded, the arithmetic is exact (`Decimal`), and the
  planner's strand count is the encoder's own count (`tests/v15/test_ap8_cost_planner.py`,
  `tests/v15/test_ap8_prices.py`).
- **AP-14 partial retrieval**: `stream.read_range`, `sdk.stream_read_range` and `vnx read-range` decode only the
  extents a byte range overlaps and verify each extent's manifest SHA-256 before a byte of it is returned. The report
  names the extents the range overlaps and the strand bytes the request had to touch, and it does not depend on the
  number of workers (`tests/v15/test_ap14_partial_retrieval.py`).
- **AP-16 offline security monitor** (Python, `src/vnxdna/secure/monitor.py`): it checks the tree against a signed
  manifest, audits every access, applies anomaly rules and a configurable lockdown, and appends every finding to the
  tamper-evident hash-chained audit log. It needs no network (`docs/V15_SECURITY_MONITOR.md`,
  `tests/v15/test_ap16_monitor.py`). The signer is a placeholder HMAC-SHA256 until AP-4.
- **AP-17 offline-only check**: `tools/offline-check.sh` runs the suite inside a network namespace that has no route
  off the machine, and `tests/v15/test_ap17_offline.py` blocks the socket API at runtime and scans `src/vnxdna` for
  network-capable imports against the enforced part of `tests/v15/ap17_network_allowlist.txt`.
- **DA-14 drive digital twin** (Rust, SIMULATED): `src/vnxdna/native/rust/vnxd/src/twin.rs` models a whole drive as six
  pools (storage, RAM, IO-journal, metadata, security, bootstrap) with per-pool, per-replica fault injection, and
  checks one property: the device never answers a read with bytes other than the bytes of the last completed write of
  that extent (`docs/V15_DIGITAL_TWIN.md`).

### Added: the NBD device
- **A configurable bound on the space the device's store may hold** (`DeviceConfig.max_stored_bytes`, the `StoreSpace`
  tracker in `src/vnxdna/native/rust/vnxd/src/nbdserve.rs`): a write that needs a new extent beyond the bound is
  refused with `NO_SPACE`, which the host block layer sees as `ENOSPC`. Nothing already written changes, an overwrite
  of an extent that carries data needs no new space and still succeeds, and a device without the bound behaves as
  before (`src/vnxdna/native/rust/vnxd/tests/nbd_no_space.rs`).

### Fixed: the device, the parsers and the hardening sweep
- **The NBD attach announces a device only once the kernel has sized it.** The readiness rule requires the sysfs pid to
  be the server's own and the reported sector count to be the device's size in 512-byte sectors; the pid alone is not
  enough, because the kernel publishes the pid before it applies the capacity. If the device is not ready within 30 s
  the watcher logs `attach_not_ready`, detaches and the command fails (exit 1) instead of announcing a device that is
  not up as configured (`src/vnxdna/native/rust/vnxd/src/nbdsys.rs`, `src/vnxdna/native/rust/vnxd/src/nbdctl.rs`).
- **O-2: the ctypes wrappers bound their kernel arguments before they allocate or narrow them.** An out-of-range
  Python int was narrowed into the int32 the kernel takes without a word, and the sketch arrays were sized with the
  caller's `s` before the kernel had refused the value. The arguments are checked before anything is allocated now
  (`tests/v15/test_sec_cluster_bounds.py`).
- **O-3: the result-cache key race is closed.** The cache key is the SHA-256 of the read file and the decode then read
  the same file again, so a file written in between stored the new result under the key of the old content. The file is
  re-hashed after the decode and the result is cached only when it did not change
  (`tests/v15/test_j2_cache_race.py`).
- **O-4..O-9 (the V14 hardening rows) are closed** and each is held by a test: the CRC-32 row input has one shared
  validator on both backends (`tests/v15/test_j3_crc32_rows.py`), the alignment range check cannot wrap and a malformed
  geometry is refused (`tests/v15/test_j4_align_overflow.py`), the cluster kernel refuses an allocation size that does
  not fit `size_t` (`tests/v15/test_sec_cluster_alloc_guard.py`), an unusable thread count is a configuration error
  rather than a silent one (`tests/v15/test_j7_cluster_threads.py`), and a stream manifest extent index is
  type-checked before the order check (`tests/v15/test_j8_manifest_types.py`).
- **The security review findings F1..F8 are closed**: the control-plane files and the FASTA output are written through
  a private temporary file instead of a fixed name that a symlink could redirect
  (`tests/v15/test_sec_control_private_write.py`, `tests/v15/test_sec_reads_write_fasta.py`), the V1 read parser bounds
  the line length and the record count (`tests/v15/test_sec_reads_bounds.py`), the legacy V0.1 decoder caps the counts a
  manifest declares (`tests/v15/test_sec_legacy_caps.py`), and the policy's known-good MAC and the sealed volume's key
  check are compared in constant time (`tests/v15/test_sec_policy_mac.py`,
  `src/vnxdna/native/rust/vnxd/tests/seal_key_check.rs`).
- **The synthesis planner counts the strands the way the encoder counts them**, and the WORM superblock flag is known to
  both sides of the contract.

### Changed: decode speed (CC-1; SIMULATED)
- **The measured result against 14.0.0 is 1.236x on the clean 4 MiB workload and 1.042x on the indel 4 MiB workload.**
  Clean 4 MiB, 4 workers: 0.593 s for 14.0.0 and 0.480 s for this release, ratio 1.236 [1.216, 1.274] (n = 18 seeds).
  Indel 4 MiB, 1 worker: 3.109 s for 14.0.0 and 2.986 s for this release, ratio 1.042 [1.035, 1.046]. The secondary
  workloads: indel 16 MiB w4 1.056 [1.047, 1.144] and substitution 64 MiB w4 1.141 [1.133, 1.189] (n = 3).
  (`experiments/v15/cc1/final/primary-summary.json`, `experiments/v15/cc1/README.md`; protocol
  `experiments/v15/cc1/PREREG.md` with its two amendments.)
- **The output is byte-identical to 14.0.0 and there are 0 false successes.** The committed differential rows
  (`experiments/v15/cc1/diff/`) hold 374 rows against 14.0.0; every row is identical (output bytes and report) and no
  row is a false success. The golden decode digests and the probe-equivalence cases are in
  `tests/v15/test_cc1_golden.py` and `tests/v15/test_cc1_probe.py`.
- **The changes that were kept**: pass 2 no longer builds the unused known-address set, the engine tables use a
  fixed-distance prefetch for the V13-rule inserts with 2 MiB pages, and the layout probe keeps its sample flat,
  computes the frame heads on demand and shares the exact-length matrices. A third change (a scan-ahead on a helper
  thread) was measured and **reverted**: its lower bound did not clear the A/A band.
- **The CC-3 held-out evaluation is now committed** (`experiments/v15/cc3/results/`, 0 false successes over 271 rows,
  SIMULATED). The `--cluster-preset nanopore` preset and the `bucket_cap_floor` default 2 are the V14 baseline
  described in the 14.0.0 section below; they are not repeated here.

### Compatibility
- The archive, container, strand, stream (`vnx.stream/1`) and volume (`vnx.volume/1`) formats are unchanged from
  14.0.0. The V4 and V5 archives still decode, and the V13 volume fixture still opens, reads, scrubs and accepts new
  commits. Only the Python package version and `CITATION.cff` move to 15.0.0; the Rust crate versions are deliberately
  left unchanged.

### Missed targets and limitations (stated plainly)
- **Both registered speed targets are MISSED**: the clean 4 MiB workload reached 1.236x over 14.0.0 (2.774x over the v12
  tier denominator, target >= 3x) and the indel 4 MiB workload 1.042x (2.956x over v12, target >= 5x). The numbers are
  not rounded up.
- **No physical validation of any kind.** No DNA was synthesised, stored, amplified or sequenced; every channel result
  is SIMULATED and every read set in the corpus is software-produced. The hardware documents are designs.
- The external validation run of LR-5, the physical-preservation guidance of LR-4 and the
  multi-site replication of AP-2 are not in this tree.
- The strand retry rung still roughly doubles the time to report FAILURE on undecodable inputs.

## 14.0.0 (branch `build/v14`, release candidate, not tagged; docs/V14_IMPLEMENTATION_REPORT.md)

### Changed: read clustering (V15, CC-3; SIMULATED)
- **Opt-in `--cluster-preset nanopore`** (implies `--read-clustering fallback`): full-template consensus, template fill
  and outer-code list decoding of short rows. SIMULATED evaluation only (`experiments/v15/cc3/`).
- **`bucket_cap_floor` default 2 also changes the plain `--read-clustering fallback`**, not only the preset: over the
  candidate-pair budget the bucket cap is lowered and clustering continues (the stage stops only if no cap >= the
  floor fits). The cluster report adds `budget_limited` (true when a budget was hit without stopping).

All channel results are SIMULATED; no DNA was synthesised, stored or sequenced (evidence categories A-D only; nothing
is E or F). Archive, container, strand, stream (`vnx.stream/1`) and volume (`vnx.volume/1`) formats are unchanged from
13.0.0; a V13 volume fixture opens, reads, scrubs and accepts new commits. Only the Python package version and
CITATION.cff move to 14.0.0; the Rust crate versions (`vnxd`, `vnx_engine`, integrity) are deliberately left unchanged.
Governing scope: docs/V14_MASTER_DIRECTIVE_2026-10-18.md (P0-P13); benchmarks: docs/V14_BENCHMARK_REPORT.md.

### Added: the storage platform (P1-P8)
- **P1 contracts**: Rust crate `vnxd` and Python `vnxdna.device` (docs/V14_CONTRACTS.md).
- **P2 block volume + VNX-IO** in `vnxd`: journal, volume, I/O queue (docs/V14_VOLUME_AND_IO.md); mirrored journal,
  atomic multi-extent writes, no generation reuse.
- **P3 VNX-RAM + cache** (none / write-through / write-back) and the molecular-memory interface in Python
  (docs/V14_RAM_AND_CACHE.md, docs/V14_MOLECULAR_MEMORY.md).
- **P4 fallback + security**: fallback, fault, audit, authz and seal modules; Python seal worker
  (docs/V14_SECURITY_MODEL.md, docs/V14_FAILURE_AND_RECOVERY_MODEL.md).
- **P5 load balancer** (`balance.rs`, I/O queue reaper, measured defaults; docs/V14_LOAD_BALANCER.md).
- **P7 media adapters**: file image (electronic), DNA software simulator, seeded fault injection, simulated lab
  (docs/V14_MEDIA_ADAPTERS.md).
- **P8 Linux block device**: `vnxd nbd create|attach|serve` exports a volume as `/dev/nbdN` (docs/V14_DEVICE_INTERFACE.md).
  Measured on this host's disk, 64 MiB raw O_DIRECT, median MiB/s (`experiments/v14/device/p8-nbd/results.json`):
  cache none write 52.6 / read 306.3, write-through 47.4 / 926.4, write-back 58.3 / 874.1. These measure the host
  disk and page cache through the V14 stack, not any DNA medium.

### Changed: codec integration (P6 workstreams A-F; SIMULATED)
- **A, linear-time shuffled-pool decode** (`PoolDecoder`, one pass 1 partitioned by archive tag): v13/v14 pool decode
  3.3x / 6.6x / 13x / 25x at E = 4 / 8 / 16 / 32 extents (n = 2, n = 1 at E = 32), RSS ~100 MiB flat
  (`experiments/v14/bench/p6a-pool/`).
- **B, parallel per-extent pass 2 in the pool path**: 1.18x end to end at 4 workers on pools (E = 8: 1.184
  [1.138, 1.203]; E = 16: 1.184 [1.129, 1.211]); no gain on single archives (`experiments/v14/bench/p6b-profile/`).
- **C, recovery scheduler**: default `engine_scheduler="auto"` = the V13 rule plus one `strand` retry rung. `strand`
  missed its preregistered endpoint (0.70x on indel-w1, a negative result) and is kept as an option and retry rung;
  0 false skips in the campaign. Zero/repetitive payloads now decode on the engine without the v13 fallback
  (4 MiB w4 peak RSS 489.8 -> 114.2 MiB) (`experiments/v14/bench/p6c-scheduler/`, docs/V14_SCHEDULER.md).
- **D, volume reliability**: 23 fault-injection tests with reopen + scrub, three with a real SIGKILL
  (docs/V14_VOLUME_RELIABILITY.md).
- **E, channel benchmark suite**: 600 runs, 0 false successes, 0 wrong archives, 0 crashes
  (`experiments/v14/bench/p6e-channels/full/`).
- **F, configuration/SDK/CLI/security hardening** (docs/V14_HARDENING.md); finding O-1 (unkeyed journal CRC on
  encrypted volumes) is OPEN; the `cryptography` pin `>=44,<47` is unchanged pending a founder decision.

### Validation and portability (P9-P11)
- **P9 integrated validation**: 44-row E2E/fault-injection matrix, 43 pass, 1 skipped (R24-hw, needs Pi hardware);
  `tools/v14-e2e.sh` (docs/V14_PHASE9_REPORT.md, docs/V14_VALIDATION_PLAN.md).
- **P10 ARM64**: aarch64 cross-builds of the three crates and the C/C++ kernels, their tests under qemu-user, golden
  and equivalence tests through an aarch64 CPython (docs/V14_PHASE10_REPORT.md). Pi acceptance is INCOMPLETE (no
  hardware).
- **P11 hardware + boot design documents** (docs/V14_BOOT_ARCHITECTURE.md, docs/V14_EXTERNAL_CONTROLLER.md,
  docs/V14_PHYSICAL_DNA_DEVICE.md): designs only; no device exists. **P13** V15 plan: docs/V15_FINALIZATION_PLAN.md.

### Fixed: the native loader (V15)
- **A stale packaged extension no longer shadows a newer in-place build.** The loaders tried the extension `pip
  install` produced (`vnxdna[_vnx_<name><EXT_SUFFIX>]`) before the in-place development build
  (`python -m vnxdna.native.<name> build`), so a packaged extension built from older source won and the newer build
  was never exercised. The in-place build is compiled from the source in the tree and the extension from the source as
  it was at install time, so the newer of the two is the one that matches the tree: the in-place build now comes first
  when it is at least as new as the extension, the extension keeps priority when it is the newer of the two (the case
  right after an install), and an explicit `<PREFIX>_LIB` override is never reordered
  (`tests/v15/test_native_stale_extension.py`).

### Missed targets and limitations (stated plainly)
- Both primary speed targets are **MISSED**: 4 MiB indel w1 v12/v14 2.848 [2.627, 2.899] (target >= 5x); 4 MiB clean
  w4 2.209 [2.111, 2.326] (target >= 3x). On single-archive decode V14 equals V13; it is ~5 % slower than V13 on
  indel 16 MiB w4 (0.948 [0.944, 0.981]) and ~2 % slower on substitution 64 MiB w4 (0.977 [0.905, 0.981]).
- The strand retry rung roughly doubles the time to report FAILURE on undecodable inputs (e.g. 17.95 s vs 9.64 s).
- Not measured: logical-I/O overhead, isolated cache, electronic backend, simulator end-to-end timing (benchmark
  report §2/3/5/6). ext4 only, on this host. The DNA adapters in E2E rows M02/M04 use the TEST-ONLY `ToyCodec`.
  No clean-checkout reproducible-build evidence. No physical DNA validation of any kind.

## 13.0.0 (tag `v13.0.0`; docs/V13_COMPLETION_REPORT.md)

All channel results are SIMULATED; no DNA was synthesised, stored or sequenced. Archive, container and strand formats
are unchanged from 12.0.0 (no new format version); decode output is byte-identical to build/v12 (6bf7749) on every
benchmark seed. Two new, versioned containers of ordinary archives are added: the stream manifest (`vnx.stream/1`)
and the volume directory (`vnx.volume/1`, journal `vnx.volume-journal/1`). Benchmarks: docs/V13_BENCHMARK_REPORT.md;
plan and pre-declared workloads: docs/V13_PLAN.md; engine design and status: docs/V13_ENGINE_ARCHITECTURE.md.

### Added (V13 engine, streams, volume, cache)
- **Streaming decode engine** (`src/vnxdna/native/rust/engine`, crate `vnx_engine`, Rust std only, C ABI 1, kernel
  `engine`): pass 1 of a decode in one native call: memory-mapped IO with read-ahead and release of consumed pages,
  a FASTQ/FASTA scanner equal to the reference parser (or a refusal), the orientation pre-pass, an **address
  scheduler** (content fingerprints and raw header keys in fixed-size lossy tables; a read is attempted only while
  its address is not verified; cheap paths first, the DP only for what they do not verify; deterministic across
  thread counts), the decode attempts on the `rs`/`align`/`orient` C kernels linked into the library, the spill
  records of pass 2, and a persistent thread pool. Binding `vnxdna.native.engine` (plain parameters), parameters from
  a layout and the options in `vnxdna.recovery.engine`.
- **Fallback ladder** `stream -> v13 -> reference` (`pipeline.decode._decode_ladder`): any engine refusal, internal
  error or non-SUCCESS result falls back to the V13 Python-orchestrated path; request errors propagate unchanged;
  the wall-time budget cancels the engine at its next block or batch. `DecodeOptions.engine` = `auto` (default) |
  `stream` | `v13` | `reference`, `engine_dedup` (False attempts every read: pass-1 records equal the V13 path's),
  `engine_threads` (None = `workers`); environment defaults `VNXDNA_ENGINE`, `VNXDNA_ENGINE_THREADS`; kernel
  variables `VNXDNA_ENGINE_BACKEND`, `VNXDNA_ENGINE_LIB`; CLI `--engine`, `--engine-dedup/--no-engine-dedup`,
  `--engine-threads`; SDK config keys; `report["engine"]` (rung, fallbacks, threads, per-stage seconds, reads
  scanned/attempted/skipped, table statistics); `report["reads"]["skipped_redundant"]`.
- **Streaming encode and decode** (`vnxdna.pipeline.stream`; `vnx stream encode|decode`; `sdk.stream_encode`,
  `sdk.stream_decode`): a byte stream of any length (file, stdin, bytes, an iterator) cut into extents, each an
  ordinary archive (content-derived ID) encoded by the unchanged encoder, at most 2 x workers extents in flight; the
  `vnx.stream/1` manifest; decoding by strand byte range or from a whole (sequenced) pool by archive ID, every extent
  and the whole stream verified before the output is published.
- **Volume** (`vnxdna.volume`; `vnx volume init|write|read|stat|scrub`; `sdk.volume_*`): `write`/`append`/`seek`/
  `tell`/`read`/`read_at`/`flush`/`close`/`stat`/`scrub`; extents as one strand file per version; a byte-bounded LRU
  cache of decoded extents; a write-back buffer; a CRC-32-framed write-ahead journal with crash-consistent flush
  (strand file published and synced before its record; orphans removed on the next read-write open); range reads
  decode only the extents they touch; one writer at a time; encrypted volumes.
- **Decode result cache** (`vnxdna.sdk.cache`; `sdk.decode(cache_dir=...)`, `VNXDNA_CACHE_DIR`,
  `VNXDNA_CACHE_BYTES`; `vnx decode --cache-dir`, `vnx cache stats|clear`): off by default, never used by the
  benchmarks; keyed by software version, git commit, native backends and engine library hash, input SHA-256 and
  size, the output-changing options and key presence; bounded (LRU); cached containers re-verified by SHA-256.
- `vnxdna.native` lists the engine (`native_status`, `--require-native`, reports); `pip install` builds it with
  cargo; CI builds it strictly and runs the crate's unit tests; `benchmarks/v13/native_engine/sanitizers.sh`.
- `vnx.result/1`: kinds `stream-encode`, `stream-decode`, `volume-create|write|read|stat|scrub`, `cache-stats|clear`
  and the status `DAMAGED` (volume scrub); mirrored in the web contracts.
- `tests/v13/`: engine (scanner agreement, pass-1 equality without the scheduler, scheduler determinism, byte-identical
  decodes, ladder rungs and fallbacks, wall-time budget, configuration surface, CLI flags), stream, volume,
  integration (SDK envelopes, cache, CLI JSON).

### Added (V13 pass-1 levers of the Python path, from `build/v13`)
- **Aligner ABI 4** (`src/vnxdna/v5/native/align.c`): `vnx_align_batch_sel_lv` aligns any selected reads of a flat
  batch in place (per-read start and length arrays; the contiguous entry points are unchanged);
  `native.align.align_usable_flat`, `TemplateAligner.project_flat`.
- **Orientation kernel ABI 4** (`src/vnxdna/native/c/orient.c`): `vnx_or_rc_bound` (ABI 2), a lower bound on the
  band-limited marker-template alignment cost of a read taken as a reverse complement (reference
  `pass1._rc_bound_reference`); `vnx_or_spec_project` (ABI 3), the marker-anchored speculative projection of a read
  onto frame coordinates (reference `pass1._spec_project_reference`); `vnx_or_pack_exact` and
  `vnx_or_pack_projection` (ABI 4), the frame packing of the exact-length and projected reads (references
  `pass1._pack_exact_reference`, `pass1._pack_projection_reference`).
- `dnaenc.frame4.decode_frames_reference` (the V12 form of `decode_frames`); `pipeline.decode._prefetch`.
- `DecodeOptions.orientation_retry` = `bounded` (default) | `always`; CLI `--orientation-retry`; SDK config key;
  `report["reads"]["rc_retry_skipped"]`.
- `DecodeOptions.sync_speculation` = `markers` (default) | `off`; CLI `--sync-speculation`; SDK config key;
  `report["reads"]["speculative"]`; stage counter `inner_ecc.verified_speculative`.
- `dnaenc.mapping.nt_to_bytes_reference`, `recovery.consensus._row_ids`.
- V13 A/B benchmark harness `experiments/v13/bench/` (the V12 harness with labels v12/v13, BASE_TREE = build/v12 6bf7749).
- `tests/v13/`: flat pass-1 path, `nt_to_bytes`, row ids, bounded retry, speculative projection, `decode_frames`, frame packing, parser prefetch (see the completion report for the count).

### Changed
- **Flat pass-1 data path.** The default decode configuration never builds per-read view lists: `_process` → `_try` →
  `project_flat` → the ABI-4 kernel work on the batch buffers; the reverse-complement retry reverses its reads with one
  gather. Same results as the list path (pinned).
- **Speculative sync path.** A read of the sync path is first projected from the shifts of its marker blocks and
  accepted when the inner RS and CRC verify; only the reads that do not verify take the marker-template DP. The step
  can only add acceptances (reads whose DP alignment did not verify may now verify from the region-erased projection,
  so `pending` and the consensus counters fall); every read V12 accepted is still accepted with the same payload.
- **Bounded reverse-complement retry.** A failed read is retried as a reverse complement only when the cost bound of
  that alignment is below its forward cost (only a verified reverse-complement frame could otherwise adopt the retry;
  none does on the validation corpus). `--orientation-retry always` restores the V12 rule. Stage-counter goldens
  re-pinned: D1 changed only `orientation.rc_retry_attempted`; B1 changed the sync/speculative/pending split of the
  harsh indel pool (37 more verified reads, one more outer row decoded).
- `nt_to_bytes` packs four codes per uint32 multiply (4.4× on a pass-1 batch); `snap_addresses` builds its membership
  table with a lexsort partition instead of `np.unique(axis=0)`.
- `decode_frames` runs its keystream XOR and CRC once over the rows without flagged erasures and again only over the
  rows the inner code corrected (same fields as the V12 form).
- The flat pass-1 path packs frames in the native kernel (gather, marker mismatches, N/quality erasures, bytes and
  per-byte erasure flags in one pass); pass 1 parses the next two batches on a background thread.

### Fixed
- `native.heap`: the cached `malloc_trim` pointer is typed so the CI mypy job passes (two errors since 12.0.0's 521adb0).
- `tests/v3/test_release_review_v3.py::test_sigterm_cleans_up_like_ctrl_c`: a SIGTERM that lands after the output was
  published (an atomic rename after full verification) and before the command returned is a skip, not a failure
  (seen as a timing race in CI at delay 1.0).
- Layering: `vnxdna.native.engine` imported layer-3 modules (`tests/architecture` R1, package cycle); the binding now
  takes plain parameters and `vnxdna.recovery.engine` builds them.
- **Engine scheduler: a distinct strand was counted as a copy.** On a clean pool with one reading per strand encoded
  with stripes and column parity (distinct strands share 24-nt payload windows), one read was skipped as redundant
  and its symbol was recovered by the outer code (output still correct). Two causes, both fixed: a claim that
  verified address K only closed, although its members reading another address were meant to form a new claim (they
  now do); and the global screen skipped a read on a window match alone (now a read is skipped only when its readable
  header names a verified address and a window corroborates, or, with an unreadable header, when every usable window
  of at least two matches). Regression test on that pool; the A/B numbers of the benchmark report are from the fixed
  engine.
- Decode result cache: the key did not include the key material or `allow_unencrypted`, so a hit could serve a plain
  archive decoded earlier with another key, or bypass the V6-SEC-02 refusal; the key now carries a one-way fingerprint
  of the key or passphrase and the flag. `store()` hashed its own copy rather than the source and used a fixed
  temporary name: it now hashes the source, copies to a private temporary file and verifies the copy.
- Volume journal: a corrupt record in the middle of the journal was treated as a torn tail, which would have dropped
  every later commit and removed their strand files as orphans at the next read-write open; such a journal now refuses
  to open (`VNXIntegrityError`), and `scrub` reports `journal_torn_tail_bytes`. `Volume.open` releases its lock on a
  failed open; `Volume.create(overwrite=True)` replaces only a volume or an empty directory.
- `load_manifest` validates field types and ranges and that `total_bytes` equals the sum of the extent lengths (a
  malformed manifest was a raw `ValueError`); the pool path's archive-tag check runs before the first pool-path extent.
- `native.engine.run_pass1` checks the dtype and length of every parameter array against the engine's declarations
  (a shorter or differently typed array would have been read past its end).
- The engine's wall-time cancel raises the recovery planner's budget error with its `recovery_plan`, as the V13 path does.
- Tests that compare a decode with a V13-path-only option (stage counters, read clustering, retry band, observer
  progress events, parallel pass 2, aligner backends and levels) against a default decode compare on the V13 rung and
  check the default (engine) rung for the same status and bytes; the engine's stage timings are volatile keys there.
- `benchmarks/v13/native_engine/sanitizers.sh` built its sanitized library in place (`python -m vnxdna.native.engine
  build` ignores an output path), overwriting the production library; it now builds with cargo into its own target
  directory, and its canary accepts the UBSan object-size report as well as ASan's.

### Performance (SIMULATED; build/v12 6bf7749 = 1.0; docs/V13_BENCHMARK_REPORT.md, experiments/v13/bench/ab-final2)
- Median ratio of V12 seconds to V13 seconds at the final engine f7586b9, paired per seed, 95 % CI, outputs identical,
  every decode scenario faster beyond the A/A noise band (`baseline-aa`): 4 MiB indel decode **2.89× [2.73, 2.99]**
  at 1 worker, 2.11× at 2, 1.70× at 4, 1.37× at 8; reverse complement 50 % 1.76×; dropout 5 % 1.37×; clean 2.20×;
  substitution 2 % 1.48×; 64 KiB 1.55×; 16 MiB (4 workers) 1.72×; encode 0.92–1.01× (unchanged). Pre-declared tiers
  (docs/V13_PLAN.md): Tier 1 (≥ 2×) on indel-w1 and clean-w4, Tier 2 (≥ 5×) and **Tier 3 (10×) reached nowhere**;
  the stage budget is in the benchmark report §3.1. The pre-fix engine measured 5.23× at indel-w1 (`ab-final`,
  kept as a record); the difference is the copies the corrected scheduler no longer skips unverified.
- Decode peak RSS ~100 MiB at every worker count (V12: 104 MiB at 1 worker to 730 MiB at 8).
- Streaming codec and volume rates on this host (`experiments/v13/bench/stream-volume`, software codec rates, never
  physical DNA throughput): stream encode 2.6–2.9 MB/s (1 worker) / 7.7–9.2 MB/s (4); stream decode by range
  3.4–4.1 MB/s; volume write 3.1 MB/s, cold read 4.2–4.3 MB/s, cached read 1.57–1.75 GB/s; 4 KiB random read ~250 ms
  cold, ~0.02 ms hot. Known limitation: pool-mode stream decode is O(extents × pool) (docs/STREAMING.md).

## 12.0.0 (tag `v12.0.0`; performance release; docs/V12_COMPLETION_REPORT.md)

All channel results are SIMULATED; no DNA was synthesised, stored or sequenced. Archive, container and strand formats
are unchanged from v11.0.0 (no new format version); decode output is byte-identical to v11.0.0 on every benchmark seed.
Benchmarks: docs/V12_BENCHMARK_REPORT.md. The low-coverage and real-data robustness scope of the V12 brief
(notes V12-DESIGN-LOWCOV) is not part of this release.

### Added
- **Native orientation kernel** `vnxdna.native.orient` (`native/c/orient.c`, ABI 1): the pass-1 orientation
  pre-pass runs in C, bit-exact with the NumPy reference `pass1._orientation_reference` (equivalence on every layout,
  golden hashes, 125 405-read differential fuzz, gcc/clang ASan+UBSan). `VNXDNA_ORIENT_BACKEND` = auto | native |
  reference, `VNXDNA_ORIENT_LIB`; `python -m vnxdna.native.orient build`; reported by `python -m vnxdna.native`, the
  conformance `--backend` switch and the dispatch path report.
- **A/A and A/B benchmark harness** `experiments/v12/bench/` (paired per seed, alternating order, bootstrap CI,
  `compare_aa.py` against the A/A noise band) with the committed runs `baseline-aa`, `ab-pass2`, `ab-pass1`, `ab-orient`.
- `tests/v12/`: exact statistics, orientation, parallel pass 2, pass-1 gathers, native orientation kernel (+91 tests).

### Changed
- **Parallel pass 2.** Duplicate resolution and consensus per spill bucket run on the pass-1 worker pool; each bucket
  returns two arrays (addresses, payload matrix). Output identical for any worker count.
- **Shift-tolerant orientation pre-pass.** Marker blocks score their best agreement within ±2 nt, so one indel no
  longer flips a forward read (forward reads with indels flipped 4.9 % → 0.01 %, reverse-complement reads detected
  73 % → 99.6 % on the test fixtures). Reads whose forward agreement already settles the decision leave the scan early.
- **Pass-1 bookkeeping** without per-read Python loops (flat gathers for exact-length rows, raw headers, pending
  records).
- Stage-counter and read-clustering goldens re-pinned for the new pre-pass (status and container SHAs unchanged).

### Fixed
- `false_accept_bound` and `entropy_bits_sum` in the decode report differed in the last float bits between worker
  counts (latent since V11: float sums merged per batch/chunk/bucket). Smart/soft summaries now accumulate exact
  rational terms and convert once; `tests/v12/test_exact_stats.py` fails on the v11.0.0 code.

### Performance (SIMULATED, 4 MiB, 1 %/0.2 %/0.2 % indel channel, coverage 5, one 4C/8T host, v11.0.0 = 1.0)
- Decode: 1 worker 1.24x, 2 workers 1.26x, 4 workers 1.38x, 8 workers 1.41x; reverse-complement 1.34x, dropout 1.33x,
  substitution 1.18x, clean 1.12x, 64 KiB 1.09x, 16 MiB 1.49x; encode within the A/A noise band. Peak process-tree RSS
  within 2 % of v11.0.0 on every decode scenario. Pre-registered targets T1–T7 met (docs/V12_BENCHMARK_REPORT.md §3b,
  docs/V12_SESSION5_REPORT.md).
- Session 5: the pass-1 exact-length gather runs in 512-row chunks (one 20 MiB int64 index per batch stayed resident in
  every worker); pass-2 bucket consensus holds one filtered copy of the records and no key matrix; workers and the main
  process return freed heap pages to the OS between stages (`vnxdna.native.heap.trim_heap`, glibc `malloc_trim`);
  frame byte erasures are packed with one uint32 comparison instead of a length-4 axis reduction. Output unchanged;
  tests/v12/test_pass2_memory.py and tests/v12/test_pass1_fastpaths.py pin each form to the one it replaces (+67 tests).

## 11.0.0 (tag `v11.0.0`; production-oriented release; docs/V11_COMPLETION_REPORT.md)

All channel results are SIMULATED; no DNA was synthesised, stored or sequenced. Archive, container and strand formats
are unchanged from v10.0.0 (no new format version). Benchmarks: see docs/V11_BENCHMARK_REPORT.md.

### Added
- **SIMD levels in the native aligner (WP1, native ABI 3).** `align.c` runs its DP/traceback loop at 4 lanes
  (portable), 8 lanes (AVX2) or 16 lanes (AVX-512F/BW), chosen at run time; a level the CPU/OS cannot run is refused
  before any of its code executes. `VNXDNA_ALIGN_BACKEND` accepts `avx512|avx2|scalar`; `status()` reports
  `simd_level`. Output is byte-identical at every level (equivalence and V4/V5/V6 golden-read tests run per level). The
  loader accepts only ABI-3 libraries, so a stale prebuilt library falls back to the reference instead of loading.
- **Adaptive execution dispatcher (WP2, `vnxdna.native.dispatch`).** One capability probe, deterministic path choice,
  safe fallback to the reference implementation, and the selected path recorded in every encode and decode report
  (additive `path_selection` field). Opt-in `VNXDNA_DIFFERENTIAL` self-check compares native and reference output on a
  sample, quarantines a mismatching native kernel (reference fallback), or raises when native was requested explicitly.
- **Opt-in VNX-Secure gate (WP4)** (`vnxdna.core.guard`): `encode`, `decode` and `extract` in `vnxdna.sdk.api` accept
  `secure=SecureContext(...)`, and the metadata reads `inspect`, `verify`, `list_entries` and `locate` are gated as
  `list`/`verify`/`locate`. Every plaintext path is gated: the legacy `api`/`v2.api` restore, recover and extract,
  `v2.archive.restore_file` and `v2.archive.extract_range` (plus encode and decode), by
  `VNX_SECURE_HOME`/`VNX_SECURE_TOKEN`/`VNX_SECURE_SESSION` (env, so the CLIs are covered). `VNX_SECURE_REQUIRE=1` makes a
  missing context a denial, and a token or session without the full set is denied (`partial_context`). The resource is
  `archive:<archive_id>` (rename/copy cannot dodge a lock); read files are re-checked under the recovered archive id
  before any plaintext exists. Lock and revocation state written by another process is re-read before each decision. The
  gate uses the existing `ControlPlane.authorize`, fails closed (any error is `AccessDenied`), runs before any work and
  never puts a token in an error. Default behaviour is unchanged.

### Changed
- The manifest `encoder.version` / `extensions.vnx.software` value is now a frozen **format stamp**
  (`MANIFEST_ENCODER_VERSION = "6.0.0.dev0"`, spec §4.2), kept byte-identical so archive ids and golden fixtures do not
  move; it is not the software version. `vnxdna.v4.container.__version__` is the package version again (it had been an
  alias of the stamp); the stamp is `vnxdna.v4.container.MANIFEST_ENCODER_VERSION`.

- `vnx-dna --version` and `vnx --version` print the package version (tests keep both equal to `vnxdna._version`).

### Fixed
- **Memory and cleanup (WP3).** A stopped or interrupted decode releases its spill files and orphan map before the spill
  directory is removed; a stop inside read clustering no longer keeps the removed unplaced-read store memory-mapped. A
  memory regression test asserts that DNA-pipeline peak RSS does not grow with input size past the spill-bucket bound;
  measurements are in `experiments/v11/mem/` (SIMULATED). The 10 MB nanopore-like run is deferred (not part of V11).
- **V2 `recover` peak memory no longer steps up with input size.** Pass 2 (records → chunks) kept about five 13 MB
  spill blocks alive (a filtered copy of every block, the carried previous array, the concatenation and its
  big-endian copy); it now holds about two. The 64 MB recover peak drops from 155 MB to 105 MB, the same as 8 MB
  (SIMULATED, one host; present since v10.0.0; experiments/v11/mem/CI_RECOVER_PEAK_AND_SIGTERM.md). Output unchanged.
- `vnxdna.__version__` and `python -m vnxdna.native` reported `6.0.0.dev0`; now `11.0.0` (a test keeps the version equal
  to the CHANGELOG head). Stale "Unreleased" sections are folded under the releases they shipped in; no entry was removed.

### Security (local red-team review, job #90; docs/security/V11_SECURITY_REVIEW.md)
Nine findings fixed, each with a regression test that failed before the fix; no CRITICAL finding.
- HIGH V11-RT-05: `vnx conformance --vectors` could write vector files outside its temporary directory (absolute or
  `..` paths); both paths must now be relative archive paths that resolve inside it.
- MEDIUM V11-RT-01: a forged, CRC-valid superblock claiming ~10^8 groups exhausted memory in pass 2; refused up front
  (RESOURCE_LIMIT) when the reads cannot support the claim. MEDIUM V11-RT-04: the legacy v0.1 reader's zstd bound
  allocated the size the frame declared; now a stream bounded by the manifest size.
- LOW V11-RT-02 (extract containment is path-aware and re-checked before the rename; the V6-SEC-10 TOCTOU window is
  narrower, not closed), RT-03 (`sdk.archive` passes the VNX-Secure gate), RT-06 (conformance gzip reads capped at
  256 MiB), RT-07 (keys/passphrases kept out of reprs; warning on group/other-readable key files), RT-08 (relative
  `VNXDNA_*_LIB` overrides refused, so `dlopen` never searches), RT-09 (C++ outer-list wrappers check every buffer
  length before the call; no ABI change).

## 10.0.0 (tag `v10.0.0`; hybrid native architecture; docs/V10_COMPLETION_REPORT.md)

All channel results are SIMULATED; no DNA was synthesised, stored or sequenced. Formats are unchanged: v9.0.0 and
v10.0.0 write byte-identical FASTA and recover byte-identical containers (docs/ARCH_COMPARISON_REPORT.md).

### Added
- **Decoder preset F: outer-assisted recovery of short rows** (opt-in, EXPERIMENTAL; `ClusterConfig(outer_list="on")`,
  `recovery/outer_list.py`, C++ kernel `native/cpp/outerlist.cpp`, ABI 1). Failed consensus frames become candidates of
  missing outer symbols (header assignment, frame-level RANSAC, payload consistency); a per-column list decoder of the
  Cauchy RS outer code proposes the codeword; a symbol is accepted only after inner RS + CRC-32 at its own address, and
  SUCCESS still requires the container SHA-256. Pre-registered held-out evaluations on the nanopore-like D13-F1 channel
  (1 MiB, coverage 10): F 7/10, 15/20, 15/20 exact versus E 2/10, 1/20, 2/20; 0 false success in all three
  (`experiments/arch/outer_list_eval/`).
- **Native strand builder** (`native/c/strand.c`, strand ABI 1) for the encode path; **fused native polish kernel**
  (cluster ABI 5); **Rust integrity kernel** (`native/rust/integrity`, batched frame CRC-32, ABI 1, Rust >= 1.63);
  shared native loader (`native/_loader.py`). Every kernel has a bit-exact Python reference, golden hashes, randomized
  equivalence tests, ASan/UBSan builds and a libFuzzer harness.
- **TypeScript web layer** (`web/`): typed contracts and guards for `vnx.result/1`, `vnx.event/1`, `vnx.error/1`,
  event-log follower, progress reducer, CLI client and a static report dashboard.
- **CPU budget** (`core/concurrency.py`) that respects CPU affinity and cgroup quotas.
- V10 work carried into this release: read-clustering consensus batches run in the decoder's worker processes; windowed
  native polish trial costs; candidate-step memory bounded by the input; decode harness with per-process-group
  timeouts; shared statistics module for experiment runners.

### Changed
- Vectorised address snapping (4 MiB indel decode 17.6 -> 15.2 s, single run, SIMULATED).
- `conformance --backend reference` also forces the reference strand builder.

### Performance versus v9.0.0 (SIMULATED, one host, docs/ARCH_COMPARISON_REPORT.md)
- Encode 16 MiB: 1.15x (1 worker), 1.23x (4 workers). Decode 4 MiB: 1.02x-1.14x across clean, substitution, indel and
  low-coverage channels. Nanopore-like 1 MiB decode (preset E): 7.2x (about 1,017 s -> 140 s).

### Fixed
- Conformance strand backend; oracle crash when list decoding was off; list-decoding rounds stopped early when a
  round only found candidates; UBSan finding in the candidate step (qsort on NULL).

## 7.0.0 – 9.0.0 (formerly "Unreleased"; shipped in v7.0.0, v8.0.0 and v9.0.0)

### Added (V9, in development; docs/V9_PREREGISTRATION.md)

- **Native polish kernel** (`vnx_cl_edit_costs` in `native/c/cluster.c`, cluster ABI 2): the full-template consensus
  polish (`recovery/cluster/polish.edit_costs`) runs natively under `VNXDNA_CLUSTER_BACKEND=auto|native`, bit-identical
  to the NumPy reference `polish.edit_costs_reference` (golden hashes, randomized and hypothesis equivalence, sanitizers).
  It evaluates each read's own band plus one edge cell, which is exact (see the kernel comment). SIMULATED decodes:
  timings in `experiments/v9/consensus/results/`.
- **Consensus candidate E** (opt-in, EXPERIMENTAL): `ClusterConfig(consensus_template="full", c_indel=4,
  fill="template")`. After the erasure decode and the GMD ladder fail, E decodes the polished template's bases at
  undecided positions and erases the lowest-confidence bytes in `fill_trials` steps. Only the frame's own RS and CRC-32
  accept a result.
  - E is the winner of the pre-registered V9 selection: pooled ORE 0.575 vs 0.488 for V8, Holm-adjusted McNemar
    p = 5.2e-12, 0 false success, 0 false frames (SIMULATED; `experiments/v9/consensus/results/selection.json`).
  - The default `ClusterConfig` is unchanged.
- **Outer-row margins** (`stagecount.outer_ecc_margins`): the smallest verified-symbol margin over every attempted outer
  row, at three confidence levels. This signal drives the V9 adaptive computational coverage rule
  (`experiments/v9/coverage/`), which decides when a read pool already in hand is enough to decode. It does not control
  sequencing.
- **V9 reproduction** (`experiments/v9/reproduce.sh`, `verify_repro.py`, `docs/V9_REPRODUCTION.md`): a spot mode and a
  full mode, compared on deterministic fields only.
- **libFuzzer harness for the polish kernel** (`benchmarks/v7/native_cluster/edit_costs_fuzz.c`).
- **V9 completion report** (`docs/V9_COMPLETION_REPORT.md`) and **VNX-Secure completion report**
  (`docs/VNX_SECURE_COMPLETION_REPORT.md`).

### Added (VNX-Secure, merged into V9)

- **VNX-Secure** (`vnxdna.secure`, `vnx security …`): a defensive security control plane. It provides:
  - scrypt principals and HMAC session tokens with revocation and replay protection;
  - a deny-by-default policy;
  - 16 deterministic detectors with an explainable additive risk score;
  - idempotent containment (session containment, token revocation, login lock, rate limit, input quarantine, resource
    lock, workload-isolation interface, evidence bundles);
  - an HMAC-chained audit log with an anchor;
  - an authenticated integrity snapshot;
  - a recovery state machine that returns to VERIFIED only after verification;
  - a secure archive gate over the existing random access (Merkle-proof check, fail-closed, optional resource-limited
    process sandbox);
  - a crypto-agility registry (PQC PLANNED);
  - VNX-RAM and VNX-Q interfaces (ARCHITECTURAL);
  - a safe local attack simulator (10 synthetic scenarios, SIMULATED).

  See docs/VNX_SECURE_*.md. The codec and archive formats are unchanged. VNX-Secure is not claimed to be unhackable.

### Added (V7 Phase D, in development)

- **`vnx.channel-model/2`** extends `/1` (every `/1` model loads unchanged): provenance of fitted models (datasets with
  SHA-256, split, fitting commit and seed), per-parameter `ci95` and `basis`, and opt-in effects: insertion runs, 3-mer
  context and per-read rate heterogeneity (`read_heterogeneity`, gamma; honoured by the simulator, present only when set)
  are honoured; `correlation` and `asymmetry` are refused by every simulation path (docs/CHANNEL_MODEL.md).
- **Channel-model fitter** `vnxdna.simulation.fit` (V7 fitting plan 3.3/3.4): edlib alignment with leftmost indel
  normalisation, count estimators, bootstrap over references, simulation calibration, fit-quality metrics M1-M10, and a
  matched read-length window for length-selected datasets. Data split with a held-out access guard (`experiments/v7/split`).
- **Fitted models F** for CNR, D03 (guppy HAC and fast, forward and backward) and D02 (Twist, with a PhiX stage split),
  PUBLIC-DATA-DERIVED; validation reads SIMULATED. Two rounds; round 2 follows protocol amendment 2
  (docs/V7_PROTOCOL.md 5.5). Verdicts on DEV: every CNR and D03 model is INADEQUATE (D03 on M3, CNR on M2); D02 is
  ADEQUATE (marginal: M3 passes by 0.09 pp, about 1 Monte Carlo SE; DEV already looked at twice), with non-gating
  misfits, and must not be used for quality-dependent decoder decisions while M9 fails (Q11 bin: real 12.8 vs simulated
  3.0). Held-out validation not run (no PREREG yet). FIT/DEV requests are recorded in an access ledger. The D03
  forward models' substitution rate (min_run 2, confounded with the 3-mer context) is relabelled `estimated` without a
  refit; future fits fix the homopolymer substitution multiplier at 1 in that case. Register:
  docs/V7_CHANNEL_MODELS.md; details: experiments/v7/fit/README.md.
- The `/2` insertion-run step of the simulator works in row chunks: same reads and generator stream; peak memory stays
  close to that of the same model without insertion runs instead of about doubling (regression test
  `tests/simulation/test_sim_insertion_chunks.py`).

### Added

- **V7 item A reference (opt-in, EXPERIMENTAL): header-independent read clustering with fill-only merge.**
  `DecodeOptions(read_clustering="fallback")` / `vnx decode --read-clustering fallback`; the default `off` is the 6.0
  decode path byte for byte. Pass 1 keeps every unverified read in an unplaced-read store (budget 4,000,000 reads);
  a lazy stage D7c clusters them by k-mer sketches and banded edit distance, builds a per-cluster consensus with
  forward-backward certain calls (indels erase only the positions their placement affects), and accepts frames only
  after inner RS + CRC-32. Cluster frames fill only superblock symbols and data addresses the 6.0 path left
  unresolved (FC-9); the container SHA-256 still decides SUCCESS. Python/NumPy reference (native kernels: step A2);
  design `docs/V7_ARCHITECTURE.md` §5, deviations in the module docstrings; smoke run
  `experiments/v7/a1-smoke/README.md` (EXPERIMENTAL, SIMULATED, no efficacy claim). Formats unchanged.
- **V7 A2: native kernels for read clustering.** `src/vnxdna/native/c/cluster.c` (binding `vnxdna.native.cluster`,
  fourth optional extension `vnxdna._vnx_cluster` built by `pip install`, listed by `native_status()`) runs the
  sketch, candidate pairs, banded edit distance (Myers bit-vector), verification and forward-backward certain calls
  (run-time AVX2) bit-exactly as the NumPy reference, which stays the specification and the fallback
  (`VNXDNA_CLUSTER_BACKEND=auto|native|reference`). Evidence: golden hashes, >= 100,000 reads per kernel of randomized
  equivalence, hypothesis at the ctypes boundary, a whole-decode identity test (`tests/v7/test_native_cluster.py`);
  gcc/clang ASan+UBSan and clang UBSan-trap runs (`benchmarks/v7/native_cluster/sanitizers.sh`, in the CI sanitizers
  job). A1-SMOKE decode with clustering on, same 10 trials, identical outcomes: median 54.6 s -> 2.8 s
  (nanopore-like/cov10, EXPLICIT FAILURE 5/5 on both) and 35.8 s -> 2.6 s, 13.8x (deletion-heavy/cov10, EXACT 5/5) on
  the development host (MEASURED,
  `benchmarks/v7/native_cluster/results/a1smoke_bench.json`).

### Fixed

- **Random access with failed groups outside the selection (job #66).** On a V6 stripe archive the column pass decodes
  the whole stripe of an index group, and any unrecovered row of that stripe made `--select` refuse with "the archive
  index could not be decoded" although the index had decoded. Random access now refuses only when an index group or a
  group of the selected files is unrecovered; the error details list the other failed groups separately. Every
  selected file is still verified (chunk SHA-256, chunk ID, file SHA-256) before it is written
  (`tests/v6/test_random_access_partial.py`).
- `vnx locate` / `dna_location` with V6 outer-code options (stripes, column parity, interleaved order, adaptive plan)
  report the strand-file records the encoder writes for those options instead of refusing them; V4/V5 output is
  unchanged (job #62).
- **V2 consensus crash and mypy coverage (job #58).** A cluster that counts verified reads, none of which verifies again,
  made `consensus_file` raise AttributeError when the verified-read fallback was on; its consensus is now written as
  `invalid`, as without the fallback (`tests/v2/test_channel_cluster_consensus.py`). mypy now gates `vnxdna.v2`,
  `vnxdna.v4` and `vnxdna.ecc` (164 errors fixed, no new ignores).

## 6.0.0 (2026-10-05)

Tagged `v6.0.0` at 16b5811 (merge of PR #8). The tagged tree still reports the package version `6.0.0.dev0`: the
release did not set `_version.py` to `6.0.0`, so archives written by it name `6.0.0.dev0` as their writer. The tag is
not moved.

Consolidated entry for all V6 phases (development tree `6.0.0.dev0`). The
per-phase entries below it are kept as written during development. Formats are unchanged from 5.0.0 (VNX4 container,
frame 4, superblocks 1 and 2); defaults are unchanged except where stated. Every channel result is SIMULATED; time and
memory figures are MEASURED on a shared development host; the CNR statistics are PUBLIC-DATA-DERIVED. No DNA was
synthesised, stored or sequenced. Deferred work and the reason for each item: docs/V6_DEFERRED.md.

### Added

- **Specification and architecture (Phases 0, 1).** `docs/spec/VNX-DNA-SPEC-V6.md` (formats, nine version axes, stage
  graphs E0-E15 and D0-D14, conformance, failure contract, provider interface), `docs/V6_ARCHITECTURE.md`,
  `docs/V6_BASELINE_AUDIT.md`. Frame 6, superblock 3, primers and the wide address class are specified, not implemented.
- **Layered packages and the SDK (Phase 2).** `vnxdna.core`, `native`, `archive`, `codec`, `dnaenc`, `sync`, `recovery`,
  `pipeline`, `simulation`, `benchmark`, `sdk`, `conformance`, `commands`; layer rules checked by
  `tests/architecture`. `vnxdna.sdk` is the stable API with `vnx.result/1` envelopes; the `vnx` CLI is a thin layer
  over it. Stable error codes (`vnx.error/1`), JSON Schemas, `vnx.event/1` decode events, `vnx.version/1`, writer
  provenance in `extensions.vnx`, a frame-version probe with refusals (`FRAME_VERSION_UNSUPPORTED`, `LEGACY_FORMAT`,
  `LAYOUT_UNDETECTED`). Docs: docs/ARCHITECTURE.md (rewritten for V6; the V3 text is docs/V3_ARCHITECTURE.md).
- **Channel-model framework (Phase 3).** `vnx.channel-model/1`, staged simulator, Monte Carlo and sweeps, 14 shipped
  models (unfitted), `vnx channel models|show|convert|sweep`. EXP-SIM-1: 210/210 cells byte-identical to the previous
  simulator (experiments/v6/phase3/EXP-SIM-1).
- **Measurement-driven decoder options, all opt-in (Phase 4, job #80).** `--consensus-weighting quality`
  (pre-registered efficacy REJECT: 213/520 vs 211/520 exact) and `--retry-band R` (pre-registered: nanopore-like 0/60 vs
  0/60, high-indel models +0.0292 [+0.0074, +0.0590], KEEP OPT-IN). Failure taxonomy: 0 false SUCCESS in 1,450 decodes.
  Public nanopore (CNR, PUBLIC-DATA-DERIVED) per-read statistics: 2.16 / 1.66 / 1.95 % substitution / insertion / deletion
  (experiments/v6/phase4, experiments/v6/align-band).
- **Native packaging (Phase 5).** `pip install .` builds the aligner, read parser and inner Reed-Solomon kernels as
  optional extensions; `python -m vnxdna.native [--require-native]`, `vnx native`; the Docker build checks the kernels.
  Installed-path decode of a 4 MiB noisy input 10.8 s to 5.3 s (2.04x, MEASURED, one host and workload;
  benchmarks/v6/native_packaging/README.md). docs/NATIVE_KERNELS.md.
- **Security model and fuzzing (Phase 6).** docs/security/V6_SECURITY_MODEL.md (no CRITICAL or HIGH finding recorded),
  libFuzzer harnesses (read parser, Reed-Solomon, aligner) and ten Python fuzz targets with campaigns of 320-630 s each
  (docs/security/V6_FUZZ_REPORT.md), CI jobs for secrets, dependencies and a fuzz smoke test.
  Fixes with tests that failed first: V6-SEC-01 (`--max-container-bytes`), V6-SEC-02 (key checked on full decode),
  V6-SEC-03 (`--expect-archive-id`, `--expect-sha256`), and three fuzz-found robustness fixes (V6-SEC-04, -22, -23).
  Opt-in `content-v1` archive ID (`vnx archive --archive-id content`).
- **Interoperability and laboratory interface (Phase 7).** `vnxdna.providers` (`DNAWriter`, `DNAReader`, `DNAProvider`,
  `ReferenceSimulatorProvider` as the only provider), `vnx.export-package/1` and `vnx.import-package/1`, `vnxdna.physical`
  with the PUBLIC-DATA-DERIVED evidence class, exit code 10 (PROVIDER_ERROR). docs/INTEROPERABILITY.md,
  docs/LAB_INTERFACE.md, docs/DDSA_MAPPING.md (mapping table only; no Sector Zero/One output).
- **Conformance (Phase 8, part 1).** 226 vectors (132 positive, 94 negative; tests/conformance/index.json; 4 of them added with the `high-dropout` profile), a packaged
  22-vector subset, `vnx conformance [--backend native|reference]`, property tests.
- **Reproducibility (Phase 8, part 2).** `vnx.experiment/1` experiment manifests (`vnxdna.benchmark.manifest`: strict
  validator with typed errors, writer, JSON Schema) for two kinds, `channel-simulation` and `experiment`.
  `vnx channel simulate --manifest FILE`; `vnx experiment run` also writes `manifest.json`;
  `vnx experiment reproduce MANIFEST [--input F] [--workers N]` re-runs and exits 0 only if the result hash matches
  (1 if not). docs/CONFORMANCE.md covers the vectors, how to run and add them, and the manifest workflow. A committed
  manifest of one EXP-SIM-1 cell reproduces its recorded read-file SHA-256 (SIMULATED).
- **Benchmark lab B0 (Phase 9, first stage).** VNX-DNA as an external codec in `dt4dds-benchmark` beside DNA-RS, DNA
  Fountain and DNA-Aeon: 280 trials, not the published protocol (benchmarks/competitors/lab/README.md). 0 false SUCCESS
  in 157 VNX-DNA trials (SIMULATED); weaker than DNA-RS at 10 % dropout and at 1 % errors near 1 bit/nt (same README).
- **Opt-in redundancy profile `high-dropout` (job #79, b0-dropout).** Strand profile `v6-high-dropout`: 256 nt, 4 B
  inner parity, no markers, row code 160 + 48, at 0.998 bit/nt at 19 kB. It is opt-in with `--redundancy-profile high-dropout` and
  never the default. The format is unchanged. Selected by a pre-registered comparison (SIMULATED,
  benchmarks/competitors/lab/results/b0-dropout): 88/100 exact against 35/100 for B0's `s184`, and 10/10 in every cell
  with dropout up to 10 %. DNA-RS-medium recovered 100/100 on the same grid, and the new profile loses to it at 1 % errors
  with 20 % dropout. There were 0 false SUCCESS in 720 VNX trials. Conformance set: 4 vectors added for the new layout (226 in total).
- **Documentation (Phase 10).** docs/V6_DEFERRED.md, docs/research/V6_COMPETITIVE_RESEARCH.md,
  docs/research/V6_TECHNICAL_RESEARCH.md, docs/V6_IMPLEMENTATION_SUMMARY.md, docs/V6_COMPLETION_REPORT.md; README, docs/ARCHITECTURE.md, docs/BENCHMARKING.md, docs/CLI.md,
  docs/COMPATIBILITY.md and docs/STORAGE_FORMAT.md updated for 6.x.

### Changed

- Version is `6.0.0.dev0`; new archives carry `extensions.vnx`, so container bytes differ from 5.0.0 for the same input
  (`ArchiveOptions(writer_provenance=False)` writes the 5.x layout; spec section 2.3.1). The test that pinned the 5.0.0
  container SHA-256 was restated before the bump.
- `--report` files are the `vnx.decode-report/1` envelope with the 5.x fields at the top level; `--performance bogus` is
  exit 7 (was 70); `vnx encode` passes archive options through or refuses them.
- Every old module path (`vnxdna.v4.*`, `v5.*`, `v6.*`, ...) still imports.

### Fixed

- `--select` on stripe archives (job #56); `locate --dna-profile` on V6 outer-code layouts is refused with exit 7;
  archive-tag collisions are refused (`ARCHIVE_TAG_AMBIGUOUS`); random access snaps reads like a full decode; the decode
  outcome is independent of the spill bucket count.
- Peak-RSS method: the child's own `VmHWM` replaces `wait4` `ru_maxrss` (experiments/v6/align-band). The Phase 4 memory
  claim ("peak RSS unchanged", P4-EXP-04) is withdrawn; its time column stands (experiments/v6/phase4/README.md).

### Known limitations

- The simulated nanopore-like model decodes 0/20 at coverage 3, 5 and 10 (default and retry band). The measured causes
  (header address, whole-segment erasure) are V7 work.
- Not implemented: encode events; MSan and non-x86 builds; fuzz campaigns of one CPU-hour per target; open LOW findings in the security model. Open defects:
  `vnx locate` on V6 striped pools (job #62), `--select` when some failed groups lie outside the index (job #66).
- No physical validation of any kind.

## 6.0.0 development notes (formerly "Unreleased"; shipped in 6.0.0) — job #79: opt-in redundancy profile for heavy dropout (PERF-DROP)

Formats unchanged; default unchanged. Channel results are SIMULATED (benchmarks/competitors/lab/results/b0-dropout).

- **`high-dropout` redundancy profile** (`--redundancy-profile high-dropout`, `vnxdna.pipeline.profiles.dna_options`)
  over the new frame-4 strand profile `v6-high-dropout`: `Layout(46, 4, 0, 0)`, outer 160 + 48. The decoder detects it
  from read length. Its encoding is byte-identical to the lab profile `hd-l256-i4` (test).
- **b0-dropout** (pre-registered in 1946f3c before any trial ran): 19 kB, coverage 10, errors 0.5 % and 1 %, dropout
  0-20 %, 10 seeds per cell, 800 trials in the main grid.
  * Rule 3 accepted `hd-l256-i4`: 0.9984 bit/nt, 0 false SUCCESS, never below `s184` in any cell, Wilson-separated
    above it in 5 cells, and 10/10 at dropout up to 10 %.
  * Where it loses: DNA-RS-medium 100/100 against 88/100 pooled, and 3/10 against 10/10 at 1 % errors with 20 % dropout.
    Its strands are 78 % longer (256 against 144 nt) and decode peak RSS is 63 against 16 MiB.
  * Column parity did not help at this size: 86 against 87.
  * The 0.5 bit/nt secondary grid is reported only, because DNA-RS-low has only 14 of its 60 trials.
- `benchmarks/competitors/lab/dropout_report.py` writes the per-cell Wilson tables and applies the pre-registered rule.
  `seen_barocdes.json`, a runtime artefact of a third-party codec committed by mistake, is no longer tracked.

## 6.0.0 development notes (formerly "Unreleased"; shipped in 6.0.0) — job #80: opt-in retry band (ALIGN-BAND)

Formats unchanged; default unchanged. Channel results are SIMULATED (experiments/v6/align-band).

- **`DecodeOptions.retry_band`** (`--retry-band`, config `decode.retry_band`; 0 = off, the default): reads whose net
  drift exceeds `band` but not `retry_band` are aligned by a second, wider-band `TemplateAligner` (same DP, same native
  kernel, no C change) instead of being left unaligned. Reads inside the band are untouched, so default decodes are
  bit-identical. Pass 1 reports `retry_band_reads` only when the option is on.
- **AB-EXP-01** (pre-registered, 935 trials, 45 grid cells × 20 paired seeds): 0 false SUCCESS in 1,910 decodes; no
  harm in any cell (0 pairs where only the stock decoder succeeded); deletion-heavy coverage 10: 0/20 → 6/20; nanopore-like
  still 0/20 at every coverage (C2 REJECT) and +26 % peak RSS on deletion-heavy at 1 MiB (C5 REJECT): stays opt-in.
  The ground-truth funnel (AB-DIAG) puts the next nanopore-like bottlenecks at header-address recovery and
  whole-segment erasure. Native = reference on 102,400 fuzzed reads (AB-FUZZ).

## 6.0.0 development notes (formerly "Unreleased"; shipped in 6.0.0) — V6 Phase 3 (channel-model framework)

Plan: docs/V6_ARCHITECTURE.md §8 Phase 3; docs/CHANNEL_MODEL.md. Formats unchanged. Every channel result is SIMULATED.

- **`vnx.channel-model/1`**: staged channel models (synthesis → storage → amplification → sequencing) with independent
  parameters, per-position error profiles, 4×4 substitution matrices, deletion-run lengths, coverage fixed / Poisson /
  negative binomial / lognormal, `data_source` (SIMULATED, SYNTHETIC, LABORATORY, PHYSICAL_VALIDATION),
  `evidence_class` and provenance. The 14 Phase 1 models ship as /1 package data; `/0` files and V4 `ChannelConfig`
  JSON are read and converted exactly; unknown schema majors exit 6 `SCHEMA_UNSUPPORTED`.
- **`vnxdna.simulation`**: generic error models (substitution, insertion, deletion, dropout, coverage, quality,
  composite), the staged simulator, Monte Carlo trials and parameter sweeps; every run returns
  `vnx.simulation-metadata/1` (model, parameters, versions, seed, input/output SHA-256).
- **CLI/SDK**: `vnx channel simulate --model NAME[@VERSION] --seed N` (named models with loss and bursts; `--param`,
  `--metadata`), `vnx channel models | show | convert | sweep`; `sdk.simulate(model=...)`, `sdk.channel_*`.
- **Compatibility**: EXP-SIM-1 (experiments/v6/phase3/EXP-SIM-1): 210/210 cells (14 models × 3 strand files × 5 seeds)
  give byte-identical reads to the current simulator, as /0 and as /1. Old `ChannelConfig` JSON gives byte-identical
  output (tests/simulation/test_sim_compat.py).

## 6.0.0 development notes (formerly "Unreleased"; shipped in 6.0.0) — V6 Phase 2 (codec/API refactor, version fixes, P1 bugs)

Plan: docs/V6_ARCHITECTURE.md §7–§8; formats unchanged (goldens v4_0, v5_0, v6_0 decode bit-exactly). Channel results
are SIMULATED.

- **Layered packages** `vnxdna.core`, `native`, `archive`, `codec`, `dnaenc`, `sync`, `recovery`, `pipeline`,
  `simulation`, `benchmark`, `sdk`, `conformance`, `commands` (moves M1–M5). Every old module path (`vnxdna.v4.*`,
  `v5.*`, `v6.*`, `errors`, `provenance`, `native`, …) still imports: whole-module moves are aliases of the same module
  object, split modules (`v4.frame`, `v4.encoder`, `v4.decoder`, `v6.profiles`) are façades. `tests/architecture`
  checks the public paths and the layer rules (no rule violation, no import cycle).
- **`vnxdna.sdk`**, the stable Python API, returning `vnx.result/1` envelopes (software, spec, backends with ABI and
  library SHA-256, input/output SHA-256, formats, timings, resources). The `vnx` CLI (`vnxdna.commands`) is a thin layer
  over it; stdout keeps every 5.x field next to the envelope.
- **Stable error codes** (spec §10) in every error (`vnx.error/1`); exit code 10 reserved for provider errors.
- **Version axes**: `vnx version` emits `vnx.version/1`; new archives carry `extensions.vnx` (spec §2.3.1;
  `ArchiveOptions(writer_provenance=False)` writes the 5.x layout).
- **Probe and dispatch** (spec §3.10): unknown frame versions exit 6 `FRAME_VERSION_UNSUPPORTED` (not retryable), V1/V3
  pools exit 6 `LEGACY_FORMAT`, unrecognisable reads exit 3 `LAYOUT_UNDETECTED`; `vnx inspect` answers "can I read
  this?" for read files. EXP-PROBE-1: experiments/v6/phase2/EXP-PROBE-1.
- **JSON Schemas** in `vnxdna/core/schemas` (result, error, decode report, events, version, probe); `--events` lines are
  `vnx.event/1` with `seq`, `run_id`, `run_start`, `superblock` and the report hash on `command_end`.
- **Conformance runner** `vnx conformance` with a packaged vector subset (spec §6).
- Fixes: `--performance bogus` is exit 7 (was 70); `vnx encode` passes archive options through or refuses them;
  `locate --dna-profile` refuses V6 outer-code layouts (exit 7) and handles custom K/M; archive-tag collisions are
  refused as `ARCHIVE_TAG_AMBIGUOUS` (exit 3); random access snaps reads like a full decode. The decode outcome is
  tested to be independent of the spill bucket count; consensus internals are not (see tests/v6/test_spill_independence.py).
- `--report` files are now the `vnx.decode-report/1` envelope; the 5.x report fields stay at the top level.
- V6 Phase 4 (measurement-driven, SIMULATED): opt-in quality-weighted pass-2 consensus
  (`--consensus-weighting quality`; default `count` unchanged). Failure taxonomy, pre-registered comparison (efficacy
  REJECT, no default change), CNR IDS statistics (PUBLIC-DATA-DERIVED): experiments/v6/phase4.

## 6.0.0 development notes (formerly "Unreleased"; shipped in 6.0.0) — V6 Phase 1 (outer-code resilience)

Opt-in; without the new options encoding is byte-identical to 5.0.0. All results are SIMULATED. Details:
docs/V6_PHASE1_REPORT.md, format: docs/V6_OUTER_CODE.md.

- **Superblock version 2** with stripes of row groups plus column-parity groups (a product of two Cauchy RS codes),
  iterative row/column erasure decoding in pass 2, and per-archive strand-loss accounting (`report["outer_v6"]`).
- **Interleaved strand order** (position-major per stripe, superblock strands spread over the file) for burst loss.
- **Adaptive outer plan** (`outer_plan="adaptive"`, `--outer-plan adaptive`, `--redundancy-budget`) choosing row
  length, stripe depth and column parity under a redundancy budget from an analytic dropout bound.
- New `vnx encode` flags `--stripe-depth`, `--column-parity`, `--strand-order`, `--outer-plan`, `--redundancy-budget`
  and matching `dna.*` configuration keys.
- `jsonschema>=4.18,<5` added to the `[dev]` extra, so the physical-record schema cross-check runs instead of
  being skipped.

## 5.0.0 — 2026-10-04

VNX-DNA V5: adaptive and probabilistic decoding on top of the unchanged V4 format. All results are SIMULATED
(computational/software validation); nothing has been synthesised or sequenced. Details: docs/V5_COMPLETION_REPORT.md.

- **Phase 1 — baseline:** V4 frozen and profiled with provenance (benchmarks/v5/); golden projection hashes for the
  aligner; first bottleneck identified (marker-template alignment, 58 % of single-worker noisy decode).
- **Phase 2 — native alignment kernel:** C kernel via ctypes (`src/vnxdna/v5/native/align.c`, optional setuptools
  extension), bit-exact with the V4 NumPy reference (10/10 golden cases, 0 mismatches in 700,000 fuzzed reads,
  ASan/UBSan clean); backend auto-selection with `VNXDNA_ALIGN_BACKEND`. Aligner 9.6–11.3× faster.
- **Phase 3 — smart indel recovery:** opt-in `DecodeOptions(indel_recovery="smart")` / `--indel-recovery smart`;
  bounded local search and consensus realignment, fail-closed. Median erased nt per true indel 24 → 4.
- **Phase 4 — soft-decision inner decoding:** opt-in `soft_decoding=erasure|chase|auto` / `--soft-decoding`, with
  `--min-quality`; GMD/Chase candidates checked by the unchanged RS + CRC verifier. 0 false acceptances, 0 false SUCCESS.
- **Gate A/B:** Phase 4 results reproduced from the clean commit; deferred per-read recovery
  (`recovery_schedule="deferred"`, default; `eager` available): verified archives/hour 155 → 581 (V5-hard).
- The package version is now 5.0.0, so new archives record encoder version 5.0.0 and `vnx version` reports 5.0.0.
  The VNX4 container format (4.0) and strand frame version (4) are unchanged.

## 4.0.0 — 2026-10-03

VNX-DNA V4: a new format and package (`vnxdna.v4`, CLI `vnx`) on top of the unchanged V3 (format 5, `vnx-dna`).
All results are SIMULATED; nothing has been synthesised or sequenced. Details: docs/V4_COMPLETION_REPORT.md.

- **VNX4 archives:** multi-file/directory archives with deterministic order, content-addressed chunks and
  deduplication, RFC 6962 Merkle tree (`vnx verify --chunk`), AES-256-GCM with key files or scrypt passphrases,
  sealed file tables, random access (`vnx locate`, `vnx extract --file`), verify-before-publish extraction.
- **Strand frame v4** with in-strand synchronisation markers. A marker-template DP turns indels into erasures for
  the inner RS code. A superblock makes strand pools self-describing.
- **Decoder:** CRC-first fast path, orientation detection, sync path, consensus with soft posteriors, address
  snapping, duplicate resolution, outer decoding, a fail-closed publish rule, PARTIAL recovery of individually verified
  files (exit 9), selective decoding of single files from reads.
- **Channel simulator:** substitutions, insertions, deletions, dropout, fixed/Poisson/negative-binomial coverage,
  duplication, homopolymer- and GC-dependent effects, bursts, N calls, reverse complements, qualities. Seeded and
  worker-count independent.
- **Codecs:** comparative outer-code interface. Cauchy RS (V3 code, default) and an EXPERIMENTAL GF(2) fountain
  code. The inner RS gets fast table-gather kernels (bit-identical to V3, 2.5–5.2× faster).
- **Constraint engine** with JSON diagnostics (`vnx validate`), screening by scrambler variants.
- **Benchmarks, error sweeps, fair V3-vs-V4 comparison, 16 reproducible experiment directories**, generated results
  tables (docs/V4_RESULTS.md).
- **Security:** decompression bombs bounded (zstd `max_output_size` is not a bound when a frame declares its size),
  path/symlink safety, bounded parsers, fuzz tests for archives, manifests, frames, read files and configurations.
- V3 code, tests and formats are unchanged. The package version is now 4.0.0, so new V3-format archives record
  encoder version 4.0.0.

## 3.0.0 — 2026-10-01

### Release review (2026-10-01)
A final pre-release review ran the full suite, an independent end-to-end and corruption harness (42 round trips of
text, binary, random, empty, chunk-boundary and incompressible files; 300 container bit flips; truncations; exact-M
and M+1 strand losses; coverage-1 error channels), a 4 GB encrypted run, and an adversarial review of data-loss,
temporary-file and signal handling. Every finding below was reproduced, fixed, and has a regression test
(`tests/v3/test_release_review_v3.py`, `tests/v3/test_cli_v3.py`) that fails on the previous commit.
- **Data loss:** `pipeline --work-dir` overwrote existing intermediate-named files (e.g. the user's
  `photo.jpg.vxdna`) without `--force`, and `--cleanup` then deleted them. Existing intermediates are now refused
  unless `--force`/`--resume`, and cleanup deletes only files the run wrote.
- **Data loss:** `--report` could replace the key file (`store … -k key.txt --report key.txt --force`), making the new
  archive unrecoverable, or the DNA index. An output may now never be an input, not even with `--force` (this also
  stops `extract a.vxdna -o a.vxdna --force`, `store f -o f --force` and `restore a -o a --force`).
- **Data loss:** an input named `<output>.partial` was deleted by `store` and truncated by `decode`.
- **Signals:** SIGTERM/SIGHUP left partial files, temporary directories and orphaned worker processes (7 per
  command, ~90 MB each). They now clean up like Ctrl-C (exit 130), and workers exit when their parent dies. An
  interrupt no longer waits for the worker pool: a worker killed while writing its result could leave the pool's
  shutdown waiting forever (found while validating the fix: `recover` hung after SIGTERM in about 1 of 8 runs).
  Workers ignore Ctrl-C (each printed a traceback) and an inherited pending SIGTERM (a worker forked at that moment
  broke the pool: exit 70), and an interrupt during start-up ends quietly with exit 130.
  Checked with 300 randomly timed SIGTERM/SIGINT runs: every one exited 130 with no partial files, no temporary
  directories and no live workers.
- **Temporary files:** reports, DNA indexes, cluster files, decoded containers and checkpoints used fixed
  `.<name>.partial` names opened through symlinks; they now use private `mkstemp` files.
- `encode` of an empty unencrypted archive wrote its outputs and then exited 70 (summary formatting of `None`).
- `keygen` wrote through symlinks and truncated an existing key when the write failed; it is now atomic.
- `encode --no-index --force` left a stale DNA index that broke `extract`; it is removed.
- `extract` silently ignored conflicting selections (`--chunk 0 --length 5`, `--length` with `--end`); now exit 3.
- A missing input to `store`, `pipeline`, `simulate-errors` and `experiment run` exited 2 with a usage box instead of
  the documented exit 3.
- Key files must be regular files of at most 4 KiB (`-k /dev/zero` read without limit); loose permissions warn.
- `reads` printed Python reprs (`[{'length': …}]`, `mean None`); `pipeline --help` lost its optional stages to markup.
- Packaging: SPDX `license = "MIT"` (the table form stops building with setuptools releases after 2027-02-18), the
  wheel ships `LICENSE`, a `.dockerignore`, and the Docker example runs as the calling user.

Audit of V2 and an architectural upgrade on the same format. Computational only: no wet-lab validation. Archive
format 5 is kept: V2 archives are read, and V2 reads V3 archives, with one exception (see Compatibility). The full
report is [docs/V3_AUDIT.md](docs/V3_AUDIT.md).

### Security fixes
- **AES-GCM nonce reuse on resumed stores (two paths).**
  - A second interrupted resume could reuse the first resume's nonces, because the new AEAD epoch was persisted only
    at the next periodic checkpoint. The epoch is now written before anything is sealed with it.
  - The sealed content record and plaintext index were always sealed in epoch 0, so re-finalising a changed input
    reused their nonces. They now use the newest chunk epoch, declared by the optional feature `final-seal-epoch-v3`.
  - Checkpoints are now HMAC-authenticated (`vnx-store-checkpoint-2`).
  - A resume's epoch also exceeds every epoch in the index sidecar, so replaying an older, still-authentic
    checkpoint is harmless.
- Containers padded after the last chunk passed `verify`. The body length must now equal `stored_size`, and `verify`
  checks `stored_sha256`.
- DNA metadata headers are not authenticated until the manifest is rebuilt, yet their lengths could drive
  multi-gigabyte allocations from a few kilobytes of reads. They are now bounded by the metadata groups actually
  decodable.
- Whole read lines were loaded into memory before any check. Lines are now cut at 100,002 bytes while reading.
- `--report` overwrote any file, including the command's own input. Reports now require `--force` and may never be
  an input or output.
- An output created while a command ran could be silently replaced. Outputs are now published by hard link.

### Added
- **Vectorised Reed–Solomon decoder** (`vnxdna.ecc.rs_batch`): batched Berlekamp–Massey/Chien/Forney over NumPy,
  strictly bounded-distance, used by the decoder, clustering, consensus and indel repair.
- **ECC engine interface** (`vnxdna.ecc.engine`): outer and inner code protocols and a registry keyed by the code
  names the manifest declares.
- **Single-read burst resynchronisation** (`--burst-repair N`): one contiguous run of up to N lost or extra bases per
  read, recovered at coverage 1 with F hypotheses.
- **Burst errors in the channel simulator** (`--burst-rate`, `--burst-length`, `--burst-kind`).
- **`vnx-dna simulate-errors`**: seeded error-channel sweeps with recovery statistics (JSON, CSV, Markdown). It exits
  70 on any undetected corruption.
- **Multi-archive pools**: the only decodable archive is used, or `--archive-tag` picks one.
- `verify --force` (for `--report`), exit code 141 for a closed standard output, `ruff` lint in CI, V2 compatibility
  fixtures (`tests/fixtures/v2_0/`), the V3 research harness (`research/v3/`), and 195 new tests (194 in `tests/v3/`, 1 README test; 29 of them from the release review).
- Docs: ARCHITECTURE (V3), STORAGE_FORMAT, ENCODING, ERROR_MODEL, LIMITATIONS, REPRODUCIBILITY and V3_AUDIT. The V1
  documents were renamed V1_ARCHITECTURE and V1_FORMAT.

### Changed
- Single-read indel repair decodes its hypotheses in vectorised blocks: same results as V2, faster. It also repairs
  reads that contain `N`.
- Reads with IUPAC or other non-ACGTN symbols are decoded with those positions as erasures instead of being dropped.
- The simulator's shuffle buckets now depend on the pool, not the input file's byte size, so FASTA and VXS inputs
  give identical reads. This can change shuffled output relative to V2 for large pools and VXS inputs. Batches also
  shrink at very high coverage, which bounds memory. Ordinary channels (e.g. 276-nt strands up to coverage 14.8) keep
  V2's batches.
- `verify` on an encrypted archive without a key exits 4 when everything checkable without the key passed (was 1).
  `--file` without a key is now reported instead of being ignored silently.
- Disk full, file too large, quota and read-only file-system errors exit 8 (were 70). An interrupted store without a
  checkpoint removes its partial files.

### Fixed
- **Decoder**
  - Long junk reads aborted decoding of pools that had no error-free read (exit 7).
  - Metadata repaired by the outer code was reported as SUCCESS instead of RECOVERED.
  - A descriptor leak in parallel `verify`, and a truncated body raising instead of producing a FAIL report.
- **Store**
  - `store --compression none` always failed.
  - Non-UTF-8 file names crashed store (exit 70).
  - Profile names were validated only after the whole file was stored.
  - Chunk counts that the trailer cannot address crashed store after processing the whole input.
  - `info` printed raw JSON.
- **Simulator and read processing**
  - Truncation could copy bases from the next read.
  - Leaked `.partial` files.
  - Exit 70 for many bad paths and malformed cluster files.
  - Consensus accepted out-of-range parameters.
  - The pipeline leaked its temporary directory and deleted too little with `--cleanup all`.
  - Experiment ECC statistics ignored failed trials.
  - Observed error rates were diluted by duplicates.
  - The orphan cap made clustering depend on read order.
  - An explicit missing `--dna-index` was ignored.
  - Compressed output names were silently written as FASTA.
- **Tests**
  - `from conftest import …` made `pytest tests/v2 tests/unit` fail at collection.
- **Docs.** Claims corrected where the V2 docs were contradicted by code or results:
  - 10 GB at 10× (overstated about 1.7×);
  - consensus throughput (unmeasured);
  - trailer checks on restore;
  - indel repair cost;
  - geometry discovery sample size;
  - the fixed-coverage integer requirement;
  - Wilson intervals rendered as [1.000, 1.000].

### Compatibility
- Reads archive formats 5 and 4 and legacy V0.1. Writes format 5.
- V2 cannot read encrypted archives whose store was resumed by V3: they declare `final-seal-epoch-v3`, and V2 exits
  6. V3 does not resume V2 checkpoints.

## 2.0.0 — 2026-09-30

Streaming, scalable V2. Computational only: no wet-lab validation. V1 archives stay readable.

### Added
- **Archive format 5 / container file v2**: body first, then a canonical manifest, a binary chunk index (56 B per
  chunk) and a plaintext index (36 B per chunk, sealed when encrypted), and a trailer with a SHA-256 of the file.
  Written to `<output>.partial`, fsynced, renamed atomically.
- **Streaming store/restore/verify/extract** with bounded memory (≤ 2 × workers chunks in flight) and parallel
  compression/encryption/hashing. Per-chunk compression decision (zstd kept only if smaller).
- **Chunked AES-256-GCM** with the chunk count in the associated data (detects modified, reordered, duplicated,
  missing, truncated and spliced chunks). HKDF labels and AD carry `VNX-DNA/5` (downgrade protection).
- **Resumable store** (`--resume`): validated checkpoints; SIGKILL-tested; byte-identical to an uninterrupted run.
- **Frame format 5**: 32-bit stripe index and archive tag (V1 capped at 2²⁴ stripes). Vectorised CRC-32; linear
  scrambler screening (parity and 2bit mapping are XOR-linear); table-driven inner RS and outer Cauchy encoders.
  All byte-identical to the reference computations.
- **Chunk-parallel encoder** (process pool) writing FASTA or the new **packed VXS** strand file (2 bits per nt),
  plus a **DNA index** (`.vxidx`) for random access from DNA.
- **Two-pass decoder** with a disk spill and external bucket sort: memory independent of the pool size, any read
  order, any multiplicity. Geometry discovery falls back to inner-RS correction when no read is error-free.
  Quality-based erasures (`--quality-erasure-below`).
- **Sequencing simulator** (`sequence`, `simulate`): coverage models including log-normal abundance, synthesis and
  sequencing errors, duplication, truncation, N calls, junk and contamination reads, reverse complements, out-of-core
  shuffle, informative quality scores, FASTQ/FASTA/VXS output, event counts that actually happened.
- **Read processing**: `reads` (validation/filtering), `cluster` (address indexing + minimizer index), `consensus`
  (batched banded alignment, quality-weighted vote, `N` for ambiguity), single-read RS-assisted realignment for V2 frames.
- **Profiles**: compact, balanced, resilient, archival. **Tandem-repeat** constraint.
- **Random access** by byte range on containers and on DNA (via the DNA index).
- **`migrate`**: V1 → V2 with full verification before and after (optional key rotation).
- **Experiment engine** (`experiment run`, Monte Carlo, Wilson intervals, configuration/results JSON, CSV, summary)
  and research scripts that generate every table in the docs.
- **Benchmarks**: `benchmark generate` (reproducible 1 MB–10 GB test data), `benchmark scale` (the real CLI measured
  per stage: time, CPU, peak RAM of the process tree, swap, disk), `benchmark corruption`, `benchmark stages`.
- 1–10 GB acceptance runs, 1 GB corruption acceptance, and the V2 test suite (container, crypto, resume, DNA
  guarantees, channel, clustering, consensus, alignment, CLI, properties, fuzzing, bounded memory).
- Documentation: V2_ARCHITECTURE, V2_FORMAT, LARGE_FILES, STREAMING, SYNCHRONIZATION, CONSENSUS, EXPERIMENTS; ECC,
  CHANNEL_MODEL, RANDOM_ACCESS, BENCHMARKS, SECURITY, COMPATIBILITY, CLI and TESTING rewritten for V2.

### Changed
- The main CLI is V2 (writes format 5). The V1 CLI is unchanged and available as `vnx-dna v1 …`. Every reading
  command accepts V1 inputs and dispatches them to the unchanged V1 modules.
- `benchmark` is now a command group (`generate`, `scale`, `corruption`, `stages`, `v1`).

### Fixed during the release candidates (rc1 → rc4)
- A resumed store could re-seal chunks with a nonce it had already used; chunks now carry an AEAD epoch in the
  authenticated chunk index, so no (key, nonce) pair repeats.
- Clustering memory grew at high error rates; weak-read and orphan files are now streamed.
- Geometry discovery failed on pools where only a few percent of reads are correctable; `pipeline --resume` now
  validates a container before reusing it.
- Experiments counted a correct refusal (no read passed a frame CRC) as an internal error; it is now a detected
  failure.

### Compatibility
- Reads archive formats 5 and 4 and the legacy V0.1 formats. Writes format 5 (format 4 via `vnx-dna v1`).
- The V1 test suite still runs; the V1 CLI tests now invoke `vnx-dna v1`.

## 1.0.0 — 2026-09-29

First stable research-grade release. Computational only: no wet-lab validation.

### Fixed (V0.1 defects; see docs/V0.1_BASELINE.md)
- **Erasure code was not MDS.** V0.1 claimed any 4 of 12 shards recoverable (8+4), but 10 of 495 four-loss patterns,
  including {4,5,7,11}, could not be recovered. It is replaced by a systematic Cauchy Reed–Solomon code with a proof
  and exhaustive verification.
- `vnx-dna simulate` recursed infinitely. The simulator is rewritten and reports the events that actually happened.
- A corrupt first duplicate hid a valid copy. Every copy is now validated, and conflicts resolve by majority or become
  an erasure.
- `verify` printed the *stored* hash as the "recovered" hash. It now recomputes the SHA-256 of the recovered bytes.
- Malformed manifests raised `KeyError`/`TypeError`. They now raise structured errors with stable exit codes; the
  CLI used to exit 2 for every failure.
- With encryption, the manifest leaked the file name, size and plaintext SHA-256, and it was unauthenticated. These
  fields are now sealed, and the manifest is HMAC-authenticated.
- Strand addresses lived only in FASTA headers. They are now inside the DNA.
- Version labels disagreed (1.0.0 / 0.1.0). There is now one source, `src/vnxdna/_version.py`.

### Added
- The `.vxdna` self-describing container (archive format 4), a strict canonical manifest, and `required_features`.
- AES-256-GCM per chunk with HKDF-SHA256 subkeys, HMAC-SHA256 manifest authentication, and wrong-key detection.
- Two-level coding: per-strand CRC-32 + inner RS, and an outer Cauchy RS K+M with shortening. Metadata strands make a
  FASTA pool a complete archive.
- 2bit / rotation3 / codebook8 mappings, a constraint layer (GC, windowed GC, homopolymers, motifs) and scrambler
  screening that fails loudly when constraints are unsatisfiable.
- A seeded channel simulator (dropout, coverage, substitutions, indels, bursts, reverse complement, shuffle) with an
  event log.
- Experimental RS-assisted indel realignment (`--experimental-indel-repair`).
- Random access by chunk or byte range (`extract`).
- CLI: `store/pack, encode, simulate, decode, restore, recover, verify, info, extract, pipeline, keygen, benchmark,
  version, legacy`.
- Explicit read-only V0.1 compatibility (`vnx-dna legacy`).
- Test suite: unit, integration, property (Hypothesis), fuzz/adversarial, CLI, the clean-room acceptance test, and a
  README-executes test. Also a benchmark suite, an experiment runner and an extended fuzz campaign.

### Removed
- The V0.1 HTTP API and its web and plotting dependencies (fastapi, uvicorn, pandas, matplotlib). The V0.1 code,
  tests, docs and results are preserved under `research/legacy/`.

## 0.1 (baseline) — tag `v0.1-baseline` (cf7d1f5)

The R&D prototype. See docs/V0.1_BASELINE.md for its forensic record.
