# VNX-DNA V6 fuzz report (Phase 6, 2026-10-05)

Campaign on branch `work/v6-security`. Every number below is from `fuzz/campaigns/2026-10-05.json`, which was extracted
from the campaign logs (`fuzz/out/<target>/campaign-*.log`, not committed). The threat model is in
[V6_SECURITY_MODEL.md](V6_SECURITY_MODEL.md).

## 1. Harnesses

| Target | Harness | Engine | What it asserts (true invariants only) |
|---|---|---|---|
| `reads` | `fuzz/native/reads_fuzz.c` | libFuzzer + ASan + UBSan | `reads.c` driven like the `native_reads.py` feed loop, with chunk sizes from 1 to 256 bytes. Consumed bytes, record counts and used bytes stay within capacity. Return codes are in the documented set. NEED_MORE never appears on the final chunk. Codes are 0..4 and flags are 0/1. `info[4]` and `ctx[40]` are sized exactly as the Python caller sizes them. |
| `rs` | `fuzz/native/rs_fuzz.c` | libFuzzer + ASan + UBSan | `vnx_rs_decode_batch` against the harness's own GF(256)/0x11D oracle. A word reported ok is a codeword, and its changed unflagged positions u satisfy 2u + f ≤ nsym. A coded word with 2e + f ≤ nsym is recovered exactly. Failed words are unchanged. Invalid arguments give EINVAL. Scalar, AVX2 and AVX-512 (when the CPU has them), in-place decoding, and a NULL versus all-zero erasure mask agree byte for byte. |
| `align` | `fuzz/native/align_fuzz.c` | libFuzzer + ASan + UBSan | `vnx_align_batch` with geometries shaped like `Layout.template`, or arbitrary ones, and output buffers sized exactly as `native_alignment.py` allocates them. Return codes are in {0, −1..−5}. Outputs are in range, and failed reads are fully erased. A second call is identical. A read gives the same result alone and inside a batch. The `_path` and `_profiled` entry points equal the plain one. |
| `py-container` | `tests/fuzz/harnesses.py::fuzz_container` | atheris | Arbitrary `.vnx` bytes are opened (no key, key, key plus `allow_unencrypted`), verified and extracted. Only `VNXDNAError` is raised. VERIFIED implies the input is byte-identical to a seed. A successful extract yields exactly the seed files. |
| `py-manifest` | `fuzz_manifest` | atheris | Structure-aware manifest mutations: type confusion, deletions, insertions, boundary values, raw bytes. Each mutated manifest sits behind a **valid** trailer digest, or a valid HMAC under the TEST-ONLY key, so every semantic check is reached. Only `VNXDNAError` is raised, and an accepted archive extracts to exactly the seed files. |
| `py-superblock` | `fuzz_superblock` | atheris | `Superblock.unpack` v1/v2, with the CRC optionally recomputed. Only `VNXDNAError` is raised. An accepted superblock re-packs to its own bytes (except the 2 reserved bytes, V6-SEC-21), and its derived geometry never raises another error type. |
| `py-frame` | `fuzz_frame` | atheris | `decode_frames` on raw frames and on valid frames with chosen corruption and erasures. An accepted clean frame matches its fields byte for byte. An RS-corrected frame lies within r symbols of the codeword its fields re-encode to. A frame with ≤ r/2 byte errors is recovered with the original payload. |
| `py-reads` | `fuzz_reads` | atheris | FASTA/FASTQ/plain files with fuzzed block size, record cap, batch size, `max_reads` and read chunk sizes. The V4 reference and the V6 native parser give identical batches and an identical exception (class, message, stage), and the reference raises only `VNXDNAError`. |
| `py-vxs` | `fuzz_vxs` | atheris | VXS files, either raw or with a consistent header and trailer around fuzzed records, read through `v4.reads.iter_reads` → `v2.strandio`. Only `VNXDNAError` is raised. Lengths equal `strand_nt`, and codes are ≤ 4. |
| `py-rs` | `fuzz_rs` | atheris | Differential: `ecc.rs_batch` (V3 reference), `v4.rs_fast`, and every native SIMD level agree exactly. Within 2e + f ≤ r the codeword is recovered, and every ok word has zero syndromes. |
| `py-align` | `fuzz_align` | atheris | Differential: the native aligner and the V4 NumPy reference agree on every `Projection` field. Reads are strands with edits or raw bytes 0..255, with optional qualities. |
| `py-bomb` | `fuzz_bomb` | atheris | `v4.archive.bounded_zstd` and V3 `container.compression` (zstd, zlib) on fuzzed streams. These include real frames of up to about 4 MiB of zeros, with and without a declared content size. The output is exactly the recorded size, or the call raises `IntegrityError`. |
| `py-decode` | `fuzz_decode` | atheris | End to end: the FASTA of the plain seed archive with strands dropped, reordered or duplicated, byte edits, and truncation, or raw bytes, through `v4.decoder.decode_reads`. Only `VNXDNAError` is raised. SUCCESS implies the output container is byte-identical to the original. |

Each Python target is also run in the normal suite by `tests/fuzz/test_fuzz_smoke.py` (corpus replay, random bytes,
and mutated seeds with derandomised hypothesis; about 10 s). Each native target is run by
`tests/fuzz/test_native_fuzz_smoke.py` (ASan+UBSan build and a bounded replay; about 10 s; skipped without clang's
libFuzzer). Long campaigns: `fuzz/run.sh <target> <seconds>`. Seeds: `fuzz/corpus/<target>/`, regenerated for the
Python targets by `python -m fuzz.make_seeds`. Regression inputs: `fuzz/regressions/<target>/`.

## 2. Campaign (2026-10-05)

Shared 8-CPU lab host (load average 7–13). Each campaign ran in a 1-CPU, 4 GiB systemd slice, with at most four at a
time. The native harnesses were built with clang 18.1.3 using `-fsanitize=fuzzer,address,undefined
-fno-sanitize-recover=undefined`. Python campaigns ran under atheris; the final `py-align` run used restarted 60 s
slices (§4.3).

| Target | Seconds | Executions | Exec/s | Final cov / ft | Corpus | Crashes | Timeouts | OOMs | Result |
|---|---|---|---|---|---|---|---|---|---|
| reads (native) | 630 | 4,804,208 | 7,613 | 199 / 1,253 | 1,028 | 0 | 0 | 0 | clean |
| rs (native) | 630 | 83,799 | 132 | 358 / 2,034 | 511 | 0 | 0 | 0 | clean |
| align (native) | 630 | 407,372 | 645 | 293 / 1,308 | 652 | 0 | 0 | 0 | clean |
| py-container | 320 | 1,183,000 | 3,685 | 324 / 802 | 59 | 0 | 0 | 0 | clean |
| py-manifest, run 1 | 320 | 57,758 | 589 | 290 / 751 | 79 | 1 | 0 | 0 | **invalid**: harness bug (§4.1) |
| py-manifest, run 2 | 320 | 192,813 | 600 | 295 / 763 | 87 | 0 | 0 | 0 | clean |
| py-superblock | 320 | 22,517,470 | 70,147 | 82 / 90 | 26 | 0 | 0 | 0 | clean |
| py-frame | 320 | 30,806 | 95 | 78 / 180 | 31 | 0 | 0 | 0 | clean |
| py-reads | 320 | 254,639 | 793 | 166 / 635 | 151 | 0 | 0 | 0 | clean |
| py-vxs | 320 | 971,407 | 3,026 | 131 / 491 | 128 | 0 | 0 | 0 | clean |
| py-rs, run 1 | 320 | 16,939 | 211 | 133 / 327 | 52 | 0 | 0 | 1 | **V6-FUZZ-03** (§3) |
| py-rs, run 2 (after the fix) | 320 | 71,346 | 222 | 137 / 336 | 52 | 0 | 0 | 0 | clean |
| py-align, runs 1 and 2 | 320 + 320 | 6,662 + 9,066 | 58, 50 | 97 / 253 | 50 | 0 | 0 | 2 | engine memory growth, triaged (§4.3) |
| py-align, run 3 (60 s slices) | 330 | 19,076 | 58 | 97 / 253 | 44 | 0 | 0 | 0 | clean |
| py-bomb | 320 | 203,656 | 634 | 24 / 27 | 12 | 0 | 0 | 0 | clean |
| py-decode | 320 | 45,200 | 140 | 867 / 1,529 | 93 | 0 | 0 | 0 | clean |

"cov" counts coverage counters, not lines. For atheris it counts only the instrumented `vnxdna` modules. For `py-bomb`
the zstd/zlib C code is not instrumented, which is why the count is low. Exec/s for the native targets is below the
1k exec/s smoke criterion of the fuzzing playbook for `rs` and `align`. Each `rs` input runs 4 to 5 batch decodes plus
the oracle, and most of the time is libFuzzer's comparison tracing in the kernel's own GF loops. Coverage had
plateaued in both.

**Not met yet:** V6_ARCHITECTURE §8 Phase 6 asks for ≥ 1 CPU-hour per harness. This campaign ran 10.5 minutes per
native harness and 5.3 minutes per Python harness, the time box of this task. Run the 1-hour campaigns before the V6
release: `fuzz/run.sh <target> 3600` for each target, one or two at a time.

## 3. Bugs found and fixed

All three are robustness and availability bugs. None of them was a memory-safety or wrong-data bug. Each fix has a
regression test, written and seen failing before the fix.

| ID | Found by | Root cause | Impact | Fix (commit) | Test |
|---|---|---|---|---|---|
| V6-FUZZ-01 | `py-manifest` (first replay of the target) | `open_container` indexed `tables.*`, `encryption.salt` and `encryption.key_check` without type checks (pre-fix `container.py:310`, `:325-330`; `crypto.py:133`) | A manifest behind a valid digest (unencrypted: anyone can write one, FC-8) or a valid HMAC raised `TypeError`/`KeyError`/`ValueError` → CLI exit 70 instead of a format error. LOW | `validate_manifest` type-checks the tables (object, integer size, hex SHA-256), the salt and the key check (1036eda) | `tests/fuzz/test_fuzz_regressions.py::test_type_confused_tables_are_format_errors` (16 cases), `::test_malformed_salt_and_key_check_are_format_errors` (3) |
| V6-FUZZ-02 | triage of the `py-manifest` run-1 crash (§4.1) | `parse_canonical_json` ran the canonical re-serialisation outside its `try`. Nesting that `json.loads` accepts (depth about 1,500) overflowed the recursion limit in `canonical_json` | `RecursionError` before the MAC check, for any archive, with no key needed → exit 70. LOW | The re-serialisation is wrapped and raises `VNXFormatError` (7ce9dcb) | `::test_deeply_nested_manifest_is_a_format_error` (3 depths) |
| V6-FUZZ-03 | `py-rs` run 1 (OOM at 2 GiB after 16,939 runs) | `InnerRS.parity` (`codecs.py`) and `rs_fast._tables` cached one GF(256) table set per geometry, up to about 8 MiB each, without bound | Memory grows in any long-lived process that sees many (n, r) geometries (future API/service; a forged superblock chooses the inner parity). LOW | Both caches hold at most 16 geometries and are cleared when full (761154d) | `::test_rs_table_caches_are_bounded`; `py-rs` run 2 is clean |

Reproducers: `fuzz/regressions/py-manifest/v6-fuzz-01-chunk-table-string`, `…/v6-fuzz-02-deep-nesting`,
`fuzz/regressions/py-rs/v6-fuzz-03-cache-growth-oom`. They are replayed by `test_corpus_replay`.

## 4. Triaged, no code bug

### 4.1 py-manifest run 1: harness bug (run invalid)
`RecursionError` in `canonical_json`, called by the harness. `_mutate` inserted palette values without copying them, so
a later mutation could place a dict inside itself, giving a cyclic object. The harness was fixed in ba13f74. The input
is kept as `fuzz/regressions/py-manifest/harness-invalid-shared-value-cycle` and now passes. Checking whether a
*file* can cause deep recursion led to V6-FUZZ-02.

### 4.2 py-rs run 1: OOM
Root cause V6-FUZZ-03. The 2-byte input alone uses 55 MB. Replaying 600 random-geometry inputs without the engine filled the cache
with 580 geometries and reached about 1.6 GB RSS.

### 4.3 py-align runs 1 and 2: OOM from engine memory growth
RSS grew by about 0.3–0.4 MB per execution under atheris (2,054 MB after 6,662 runs; 3,806 MB after 9,066 runs). It
did not grow outside the engine. The crash input alone ran fine, and 3,000 mutated corpus inputs replayed without
atheris gave no RSS growth, with tracemalloc showing no growth in `vnxdna`. The growth is attributed to atheris'
comparison and coverage instrumentation of this NumPy-heavy path (V4 `TemplateAligner` reference). This is a false
positive for the code under test. `fuzz/run.sh` now restarts the Python engine every `PY_SLICE` seconds (default 60,
fc9ca19). Run 3 is clean, with a peak RSS of 1,365 MB inside a 60 s slice. The input is kept as
`fuzz/regressions/py-align/engine-oom-not-reproducible`.

## 5. Native-safety observations (no bug)

- `align.c:304-313`, `:192-199`: read base codes are not range-checked at the ABI. Bytes above 3 count as mismatches
  and are projected as N. This is safe, and it was fuzzed with bytes 0..255.
- `align.c:28`: the int32 DP with INF = 2²⁸. Inside the native domain (cost ≤ 65,536, T ≤ 8,192, band ≤ 64) the worst
  case, about 1.34·10⁹, stays below 2³¹. This is an analytic argument: UBSan does not instrument GCC vector arithmetic,
  and the harness uses small costs so that traceback is reachable.
- Implicit buffer contracts not covered by an ABI number: the aligner outputs (`native_alignment.py:223-238`) and the
  read parser's `info[4]`/`ctx[40]` (`reads.c:28`, `native_reads.py:245-246`). The harnesses allocate exactly these
  sizes, so ASan would catch an overrun (V6-SEC-09).
- `rs.c:452`: `vnx_rs_restrict_levels` is an exported test hook that is not thread-safe and changes global dispatch
  (V6-SEC-08). `rs.c:477-487` validates its arguments, and its one allocation per call is bounded at about 130 KB
  (`rs.c:209-210`).

## 6. Not covered

- Phase 7 export/import manifests and the version probe (spec §3.10). Neither exists yet.
- MSan and non-x86 builds.
- The CLI as a whole. The decode path is covered through `decode_reads`; the CLI error mapping is covered by the
  existing tests.
- Campaigns of ≥ 1 CPU-hour per target (§2).
