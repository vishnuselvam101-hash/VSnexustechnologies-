# V14 Phase 6 workstream F: configuration, SDK, CLI and security hardening

Branch `work/v14-p6f` (from `build/v14` b69e70f). Brief: `docs/V14_BRIEF_2026-10-18.md` §9 and §11 steps 9–10.
Evidence category A (codec software) and B (software device: the volume). Every read used in tests is SIMULATED.

## 1. Audit scope

| Area | What was checked |
|---|---|
| Option consistency | `DecodeOptions` fields vs config `decode` keys vs `vnx decode` flags vs `VNXDNA_*` variables; `DNAOptions`/`ArchiveOptions` vs `dna`/`archive` keys; precedence explicit > config > profile > defaults |
| Option validation | Types and ranges of every scalar decode option (Python and NumPy ints, bools, floats, strings) |
| Environment | All `VNXDNA_*_BACKEND`, `*_LIB`, `*_THREADS`, `VNXDNA_ENGINE*`, `VNXDNA_CACHE_*` variables: invalid values and how they surface |
| Native boundaries | All 11 ctypes wrappers in `src/vnxdna/native/` and their C/C++/Rust entry points: argtypes/restype, dtype/contiguity/shape/length checks, integer narrowing, C-side re-checks; cluster.c under ASan |
| Result cache | Key construction (inputs, options, backends, key fingerprint, V6-SEC-02 flag), hit verification, store/evict, isolation |
| Manifests | Stream manifest (`*.stream.json`), volume `volume.json` + journal records, V4 container reader (88,337 random byte flips, no non-VNX exception) |
| Resource limits | Declared sizes trusted before allocation, decompression bombs, far offsets, extent ids, path traversal and symlinks on extract |
| Cancellation + cleanup | Work directories on exceptions and KeyboardInterrupt in stream encode/decode, volume open/write, `sdk.decode`/`encode` |
| Errors vs fallback | Raw Python exceptions (exit 70 INTERNAL_ERROR) vs typed `vnx.error/1`; silent fallbacks |
| Defaults | Optional features off unless proven |
| Web console | `web/` Node tests, `npm ci && npm test`, typecheck |
| Dependency | `cryptography` pin `>=44,<47` vs advisories |

Not in scope (other workstreams, being changed concurrently): decode pass 2 and the recovery scheduler internals. Not
re-run here: secret scan, fuzz campaigns, the full sanitizer matrix (only cluster.c was built with ASan for H-8).

## 2. Findings

Severity: CRITICAL / HIGH / MEDIUM / LOW / INFO. Reach: U = reachable from untrusted input or user configuration,
L = needs local write access to the volume/cache/input, I = internal callers only. No finding was suppressed.

| ID | Sev | Reach | Finding | Status | Test |
|---|---|---|---|---|---|
| H-1 | MEDIUM | U | `DecodeOptions.validate` did not check `batch_reads`, `max_pending_per_address`, `max_reads`, `archive_tag`, `reverse_complement` at all, nor the types of `workers`/`band`/`min_quality`/`consensus_threshold`. A config `"reverse_complement": "false"` was read as True; `batch_reads: 0`, a float worker count or an out-of-16-bit tag were accepted; strings gave raw `TypeError` (exit 70). Now: integers (Python or NumPy, no bools) in documented ranges, a real bool, `VNXConfigurationError` (exit 2) | FIXED | `test_h1_*` |
| H-2 | MEDIUM | U | `vnx decode --archive-tag zz` ended in `INTERNAL_ERROR` (exit 70, `int()` ValueError); a tag > 0xFFFF was only refused later as "not found". Now a `CONFIGURATION_ERROR` | FIXED | `test_h2_*` |
| H-3 | LOW | U | `decode_options_for` passed the performance profile's `workers` and `batch_reads` as overrides, so the config file's `decode.workers` / `decode.batch_reads` were silently ignored. Now explicit > config file > profile > defaults | FIXED | `test_h3_*` |
| H-4 | LOW | L | Result cache: a hit published its copy without hashing it (the module doc said "re-verified"), and an entry's `report.json` was not tied to its container. Now the published copy must hash as the entry (else `VNXIntegrityError`, nothing published) and a report naming another container makes the entry a miss | FIXED | `test_h4_*` |
| H-5 | MEDIUM | L | Volume journal: a journal whose commits skip an extent (CRC recomputed) made `volume_read` return extent n+1's bytes at extent n's offset with SUCCESS; a far extent id (2·10^6) made reads loop over empty extents (CPU); malformed records gave raw KeyError/TypeError (also from `volume_scrub`, which documents that it never raises); a `file` field with an absolute path was read outside the volume. Now every commit record is type/range-checked (`file` must be `e<extent>-v<version>.fasta|fastq`) and the extents must be 0..n-1 with only the last one short, at open (`VNXFormatError`). Writes zero-fill gaps and flush in ascending order, so every crash point satisfies this | FIXED | `test_h5_*` |
| H-6 | LOW | U | Stream manifest: `strands_format` was not checked (`"x/y"` became part of a work file path, raw FileNotFoundError; non-strings accepted); `strands_offset`/`strands_bytes` of 2^63 gave a raw ValueError from `seek`. Now `fasta`/`fastq` only and offset + bytes ≤ 2^62 (`VNXFormatError`) | FIXED | `test_h6_*` |
| H-7 | LOW | U | Work directories leaked: `encode_stream` of a missing source (mkdtemp ran before opening it), and `decode_stream` interrupted (KeyboardInterrupt) during the pool-decoder setup (the directory was made before the `try`) | FIXED | `test_h7_*` |
| H-8 | MEDIUM | I | `native.cluster.polish_deltas` checked only `read_start[0] == 0` and `read_start[-1] == n`; a non-monotonic `read_start` made the C kernel (which has no read count) read out of bounds (ASan heap-buffer-overflow at cluster.c:1555). The only caller builds it from a cumulative sum, so untrusted input cannot reach it. Now the wrapper requires it non-decreasing | FIXED | `test_h8_*` |
| H-9 | MEDIUM | U | A misspelled `VNXDNA_*_BACKEND` variable: aligner → `BrokenProcessPool` INTERNAL_ERROR (raised inside a worker), RS/reads → raw ValueError INTERNAL_ERROR, engine → silently swallowed (decode fell back to the V13 path). Now `vnxdna.native.check_backend_env()` runs in `DecodeOptions.validate` and `DNAOptions.resolve` → `CONFIGURATION_ERROR` before any work | FIXED | `test_h9_*` |
| H-10 | LOW | U | Web client: a failing `vnx` command writes its `vnx.error/1` object to stderr (stdout is empty), but `vnx()` parsed only stdout, so `VnxCommandError.vnxError` was always null for CLI errors although the client documents it carries it. The existing test accepted that. Now parsed from stderr when there is no envelope | FIXED | `web/test/client.test.ts` "the vnx.error/1 object the CLI writes to stderr reaches VnxCommandError.vnxError" |
| O-1 | LOW | L | Volume journal records carry an unkeyed CRC-32: for an encrypted volume, someone with write access can swap two commit records (or drop the newest, a rollback) and reads return reordered or older extents with SUCCESS. Needs a keyed MAC over journal records or the extent number/version bound into the sealed data: a format change, deferred (V15 format freeze) | OPEN | — |
| O-2 | LOW | I | ctypes `c_int32`/`c_int64` arguments and `np.ascontiguousarray(dtype=narrower)` silently wrap out-of-range Python values in `cluster.sketch/fb/edit_costs/trial_costs`, `strand.build_rows`, `outerlist.*`, `orient.*` (e.g. `sketch(..., w=2**32+4)` allocates a huge uninitialised result). C re-checks every length/offset, so no memory impact; internal callers only. Fix pattern exists (`engine._checked_arrays`) | OPEN | — |
| O-3 | LOW | L | Result cache key race: the read file is hashed for the key before the decode reads it again; a file changed in between stores the new result under the old content's key. Local race with write access to the input | OPEN | — |
| O-4 | INFO | I | `integrity.crc32_rows` native vs reference disagree for Python lists with ints > 255 (native wraps, reference raises); arrays agree | OPEN | — |
| O-5 | INFO | I | `align.align_usable_flat`: int64 `starts + lengths` overflow passes the Python domain check; C refuses it (NativeAlignmentError instead of the documented `None` fallback) | OPEN | — |
| O-6 | INFO | I | `align.align_usable*` do not compare geometry array sizes with `aligner.T`/`frame_nt` (over-read only with a malformed internal TemplateAligner) | OPEN | — |
| O-7 | INFO | I | cluster.c `verify`: `malloc(chunk * 8)` can wrap for chunk ≥ 2^61; safe today only because the `malloc(chunk)` beside it fails first | OPEN | — |
| O-8 | INFO | U | `VNXDNA_CLUSTER_THREADS=abc` silently means 1 thread (results do not depend on the thread count) | OPEN | — |
| O-9 | INFO | U | Stream manifest extent `index: true` is accepted for extent 1 (`True == 1`) | OPEN | — |
| O-10 | INFO | — | Environment, not code: the governed `vnxdna.slice` has `TasksMax=256` shared by every agent's run; with four agents on the host, thread/process creation failed with EAGAIN (`BlockingIOError: [Errno 11]`, slice `pids.events` max > 0). A full run inside the slice (a3c70f1, 21m46s) had 13 such failures (strand `nthreads=1000`, V12 parallel pass 2, the seal worker, two subprocess tests here); all 210 pass serially and the run outside the slice is clean (§5). Other agents' runs of b69e70f/c2ae79e show the same class of failures | ENVIRONMENT | — |
| O-11 | INFO | — | The lab venv carries stale `vnx-dna 5.0.0` distribution metadata (pip's resolver warns about it); every run here uses `PYTHONPATH=<worktree>/src` | OPEN | — |

Checked and clean: every ctypes symbol declares argtypes/restype, ABIs are checked before binding, library paths are
absolute; output buffers are sized in Python from validated shapes and the native side re-checks lengths/offsets
(reads.c, rs.c, orient.c, align.c, strand.c, outerlist.cpp, cluster.c, Rust integrity and engine); no native defect is
reachable from read files. Stream manifests: canonical JSON only, every extent field type/range-checked, extent
length and SHA-256 verified before writing, final hash checked before the output is published. V4 container reader:
section sizes must tile the file, manifest capped at 1 MiB, chunks capped at 64 MiB through a bounded reader, legacy
zstd bomb refused without memory growth; extraction refuses traversal and symlinks. Encryption downgrade (a key given
for an unencrypted volume or stream) is refused. `decode_reads`, `PoolDecoder`, `sdk.decode/encode`, `Volume.open`
clean up in `finally`/`except BaseException`.

Defaults: the result cache stays opt-in (`VNXDNA_CACHE_DIR` or `cache_dir=`); smart indel recovery, soft decoding,
read clustering, quality-weighted consensus, stage counters, the retry band and the differential self-check are off by
default. The streaming engine (`engine="auto"`) is on by default as decided in V13: it runs only inside its proven
domain and any refusal, error or non-SUCCESS falls back to the V13 path (recorded in `report["engine"]`).

Option surface (consistent after H-3): every config `decode` key is a `DecodeOptions` field; fields without a key are
structured objects (`layout` comes from `dna.layout`, budgets/configs from their own sections or the SDK) and
`max_reads`/`stage_counters` (SDK only). `consensus_threshold`, `reverse_complement`, `max_pending_per_address` and
`batch_reads` have no `vnx decode` flag (config file or SDK only).

## 3. Web console Node tests

Environment: Node v24.21.0, npm 11.19.0, registry https://registry.npmjs.org/ reachable. In `web/`:
`rm -rf node_modules && npm ci --ignore-scripts` (lockfile: `@types/node` 24.19.2, `typescript` 5.9.3; 0 vulnerabilities),
then `VNX_TEST_PYTHON=/root/vnx-dna-lab/.venv/bin/python npm test` and `npm run typecheck`.

- First run ever, before any change: with the default `python3` (system Python without the package's dependencies:
  `ModuleNotFoundError: No module named 'typer'`) 14 passed / 1 failed ("vnx native returns a validated envelope");
  with `VNX_TEST_PYTHON` set to the lab venv, 15 passed / 0 failed. Typecheck clean.
- After H-10 (one test added): 16 passed / 0 failed with `VNX_TEST_PYTHON` set; typecheck clean. With the bare
  system `python3` the two CLI-integration tests fail for the same environment reason (documented in `web/README.md`:
  the client tests need a Python that can import this repository's CLI).

## 4. `cryptography` pin (founder decision; the pin was NOT changed)

Current pin `cryptography>=44,<47` (pyproject.toml, requirements.txt); the lab has 46.0.7 (bundles OpenSSL 3.x).
VNX-DNA uses only `AESGCM`, `HKDF`, `Scrypt`, `hashes`, `Fernet`/`InvalidToken` and `InvalidTag`, through public APIs
(no X.509, PKCS#7/CMS, PKCS#12, QUIC, OCSP, OCB or SIV).

Advisories for 46.0.7 (pip-audit, 2026-10-18):

| Advisory | Fixed in | Touches VNX-DNA? |
|---|---|---|
| GHSA-537c-gmf6-5ccf: wheels bundle an OpenSSL affected by the 2026-06-09 OpenSSL advisory (CVE-2026-45447 PKCS7_verify UAF, High; CMS, QUIC, OCSP, AES-OCB `EVP_Cipher`, ASN.1, PKCS#12, AES-GCM-SIV/AES-SIV empty-message tags, ...) | 48.0.1 (OpenSSL 4.0.1) | Not through the APIs used (AES-GCM, HKDF, scrypt, SHA-256 are not named); the vulnerable code is in the same loaded library |
| CVE-2026-69248 / GHSA-m2h6-j472-rp4c: X.509 name-constraint wildcard | 49.0.0 | No (no X.509) |
| CVE-2026-69249 / GHSA-jwv3-5hgf-82ww: chain-building blowup | 49.0.0 | No |
| CVE-2026-69247 / GHSA-g6cj-pr64-35w5: PKCS#7 decrypt oracle | 50.0.0 | No (no PKCS#7) |

API changes 47.0–50.0 relevant to us: none breaking. 47.0.0 drops OpenSSL 1.1.x and LibreSSL < 4.1 for source builds,
removes binary curves, deprecates short TripleDES keys and the private HKDF extract (we use only `HKDF(...).derive`);
48.0.0 drops Python 3.8 (we require 3.12 in practice); 49.0.0 drops x86_64 macOS and 32-bit Windows wheels and
removes long-deprecated cipher locations; 50.0.0 deprecates FFDH. Test impact, measured: the 656 crypto-relevant tests
(`tests/unit`, `tests/v6` SEC-01..03, `tests/v11` red team, `tests/v14/test_device_seal.py`, container v2/v3/v4,
`tests/compat`, `tests/conformance`, `tests/secure`, `tests/sdk`, `tests/v13/test_volume.py`) pass unchanged on
46.0.7, 48.0.1 and 50.0.2 (656 passed each; no cryptography deprecation warnings), and golden/compatibility vectors
(AES-GCM containers of earlier versions) decode identically.

Recommendation: raise the pin to `cryptography>=48.0.1,<51` (tested ceiling 50.0.2) in `pyproject.toml` and
`requirements.txt` together, with no code change. Reason: fresh installs would otherwise resolve to 46.0.x and ship an
OpenSSL with a High-severity advisory in the process, even though no VNX-DNA code path calls the affected functions.
Raising the floor further to 50.0.0 buys only X.509/PKCS#7 fixes VNX-DNA does not use; keep it at 48.0.1 unless a
deployment also uses `cryptography` for TLS/X.509 in the same environment. Risk: platforms losing wheels in 49.0
(x86_64 macOS, 32-bit Windows) would need a source build or a `<49` cap there; Linux x86_64/aarch64 (the lab, the Pi
target) are unaffected.

## 5. Full suite

Commit a3c70f1, alone on the host, `systemd-run --scope -p MemoryMax=12G -p CPUQuota=600% -p TasksMax=4096`, 6 xdist
workers, lab venv on PATH (as `tools/vnx-lab full`): **4,666 passed / 4 skipped / 0 failed** in 411 s (gate before this
work: 4,584 / 4 / 0; +82 tests in `tests/v14/test_hardening_f.py`). Lab log:
`results/full-suite-a3c70f1-scope-path-20261018-1935.txt`.

## 6. Reproduction

```
cd <worktree>/web && rm -rf node_modules && npm ci --ignore-scripts && VNX_TEST_PYTHON=<venv>/bin/python npm test
PYTHONPATH=<worktree>/src python -m pytest tests/v14/test_hardening_f.py
pip install --target <dir> cryptography==48.0.1 && PYTHONPATH=<dir>:<worktree>/src python -m pytest <crypto test set above>
```
