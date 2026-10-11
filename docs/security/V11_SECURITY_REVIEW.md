# V11 security review and hybrid-boundary hardening (job #90)

Branch `work/v11-redteam`, based on `build/v11` at `7a9d0b7`; reviewed 2026-10-11. This is a local defensive review of
our own code: no network service, no external system. Every number below comes from a file in
[`v11-redteam/`](v11-redteam/) or from a committed test. All test data is SYNTHETIC; no DNA was synthesised, stored
or sequenced.

Severity scale (vnx-security skill): CRITICAL exploitable now with data or key exposure; HIGH exploitable with effort,
or a memory-safety bug; MEDIUM defence-in-depth or a reachable dependency CVE; LOW hardening; INFO notes.

## 1. Findings

Each fixed finding has a regression test that was written first and failed before the fix (the pre-fix pytest
results are listed in `v11-redteam/pre-fix-measurements.txt`). No on-disk or in-DNA
format changed and no native ABI version changed.

| ID | Severity | Finding | Status | Test |
|---|---|---|---|---|
| V11-RT-05 | HIGH | `vnx conformance --vectors DIR`: the `container.build` operation wrote vector inputs to `Path(tmp) / params["root"] / file["path"]`. An absolute or `..` path in a vector file wrote attacker-chosen bytes anywhere the user can write. Before the fix, the test wrote `/escaped.txt`; `root = ".."` archived `/tmp`. The precondition is running an untrusted vector set. | fixed `9f1df61`: both paths must be archive paths (relative, no `.`/`..`) and must resolve inside the temporary directory | `tests/v11/test_redteam_conformance_paths.py` |
| V11-RT-01 | MEDIUM | Phase 0 audit D10, job #61. A forged, CRC-valid superblock under `max_container_bytes` (4 GiB) with K = 1 claims 107,374,183 groups. Pass 2 builds one missing address per claimed symbol, so 51 reads caused a MemoryError after 25.7–37.0 s at 5.2 GB RSS. | fixed `ccd6ccb`: the superblock stage refuses (RESOURCE_LIMIT, exit 3) a claim that the reads cannot fully decode (fewer spilled records than data symbols) and whose addresses exceed 8 × records + 2^20, before any group is walked | `tests/v11/test_redteam_superblock_evidence.py` |
| V11-RT-04 | MEDIUM | The legacy v0.1 / RD-1 reader (`vnx-dna` legacy restore) bounded zstd with `decompress(max_output_size=...)`. That call allocates the size the frame header declares: a 262 KB file declaring 8 GiB raised MemoryError even with `max_output_size = 2^20`. | fixed `4e61f0f`: a stream reader bounded by the manifest's declared size; any other length is an IntegrityError | `tests/v11/test_redteam_legacy_decompress_bound.py` |
| V11-RT-02 | LOW | Extract containment used a string prefix test (`/out2` passes for `/out`). A directory replaced by a symlink to a same-prefix sibling after the component check (V6-SEC-10 TOCTOU) wrote outside the output directory. | fixed `fa1674f`: path-aware `is_relative_to`, checked again on the temporary file's directory just before the rename. The TOCTOU window is narrower but not closed (V6-SEC-10 stays LOW). | `tests/v11/test_redteam_extract_paths.py` (adds symlink component, symlink target, dangling link and symlinked output-directory cases) |
| V11-RT-03 | LOW | `sdk.archive` / `vnx archive` did not pass the opt-in VNX-Secure gate that `sdk.encode` applies. With `VNX_SECURE_REQUIRE=1`, or under a guest context, it still wrote the container. | fixed `0d83fda`: gated as `encode` (policy `write`) | `tests/v11/test_redteam_secure_gate_archive.py` |
| V11-RT-06 | LOW | Conformance gzip-stored reads were decompressed without a bound. | fixed `9f1df61`: capped at 256 MiB | `tests/v11/test_redteam_conformance_paths.py` |
| V11-RT-07 | LOW | `repr(ArchiveOptions)` and `repr(TokenAuthority)` contained the key or passphrase. No current path prints these reprs: Typer 0.27 does not show locals and results serialise options without secrets. `vnx` accepted a key file readable by group or others without saying so; `vnx-dna` warns. | fixed `12de934`: `repr=False` on the secret fields, plus a warning (logging) on group/other-accessible key files. Wrong-key and passphrase errors in CLI JSON were checked to contain no secret. | `tests/v11/test_redteam_secret_hygiene.py` |
| V11-RT-08 | LOW | A relative `VNXDNA_*_LIB` override was checked with `is_file()` against the current directory but given to `dlopen`. `dlopen` resolves a name without a slash through the library search path, so the file checked and the file loaded could differ. | fixed `4ae2122`: `_loader.open_library` refuses relative paths in all five loaders; the package libraries still load, or the reference runs | `tests/v11/test_redteam_native_loader.py` |
| V11-RT-09 | LOW | The C++ outer-list entry points (`vnx_ol_decode_row`, `vnx_ol_ransac`) read `known` (n bytes), `sym` (n × P), `mul` (65536) and `inv` (256) without being given their lengths. The Python wrappers accepted shorter arrays, which the kernel then read past their end. | fixed `c48921e`: every size is checked in the wrapper before the call (no ABI change) | `tests/v11/test_redteam_outerlist_buffers.py` |
| V11-RT-10 | INFO | scrypt costs compared with current guidance (the OWASP Password Storage Cheat Sheet recommends at least N = 2^17, r = 8, p = 1). Archive passphrases use N = 2^15, r = 8, p = 1 (32 MiB). VNX-Secure principals use N = 2^14 by default (cost recorded per hash). `hashlib.scrypt`'s default `maxmem` (32 MiB) makes N ≥ 2^15 fail, so raising the principal cost also needs a `maxmem` argument. | deferred: the archive default is a format decision (V6-SEC-07, founder) | — |
| V11-RT-11 | INFO | Conformance vector *inputs* may name files outside the vector directory. This is by design: the packaged vectors read `../../../fixtures/...`. The reads are SHA-256 checked when the vector records a hash, never written outside the temporary directory, and only decoded or hashed. | accepted (documented) | — |
| V11-RT-12 | INFO | `extract` does not check the trailer's whole-file SHA-256 (`vnx verify` does). Every byte that reaches the output is authenticated through the MAC → manifest → table hashes → chunk and file SHA-256 chain. The byte-flip sweep confirms this: the 32 flips that left the output unchanged are all in that digest. | accepted | `tests/v11/test_redteam_tamper_never_success.py` |
| V11-RT-13 | INFO | The library layers below the SDK (`vnxdna.archive.operations.extract`, `vnxdna.pipeline.decode.decode_reads`) are not gated. The gate is enforced at the SDK, CLI and legacy API entry points. An in-process Python caller can read the files directly anyway, so gating internals would add no protection, and it would double-count rate limits. | accepted (documented) | `tests/v11/test_secure_gate.py` (existing) |

No CRITICAL finding.

## 2. Checks that held

| Check | Evidence |
|---|---|
| Wrong key (two keys), a passphrase on a key archive, no key: refused; no SUCCESS | `test_redteam_tamper_never_success.py`; sweep in `v11-redteam/pre-fix-measurements.txt` |
| Single-bit flips in the header, body, chunk table (Merkle leaves), file table, refs, manifest and trailer (MAC, sizes, magic): never wrong data | 1834 flips: 1802 refused, 32 identical output (the whole-file digest), 0 wrong (`v11-redteam/byte_flip_sweep.py`); regression test samples every section |
| Truncation at every 97th offset (sweep) and every 251st offset (test): never extracted | same |
| Container major/minor version change and header flags: refused (UNSUPPORTED); an unencrypted forgery given to a key holder is refused (KEY_FOR_UNENCRYPTED) | `test_header_version_change_is_refused`, `test_downgrade_to_an_unencrypted_forgery_is_refused_with_a_key` |
| Container and V2 decompression is bounded by the recorded plaintext size (stream reader; plaintext ≤ chunk size) | `archive/compression.py`, `container/reader.py`, `v2/archive.py`; atheris `py-bomb` 63,927 runs, `py-container` 252,625 runs, 0 crashes |
| Superblock fields are fully validated (V6-SEC-01 size cap, group-count consistency); forged candidates are moved past | existing `tests/v6/test_security_*`, plus V11-RT-01 |
| VNX-Secure gate on SDK encode/decode/extract/inspect/list/verify/locate, the V1/V2 legacy APIs, and the CLI (via SDK) | existing `tests/v11/test_secure_gate.py` and V11-RT-03 |
| No key or passphrase in error messages or CLI error JSON; Typer shows no locals (0.27.2 default `pretty_exceptions_show_locals=False`) | `test_redteam_secret_hygiene.py`; `v11-redteam/environment-checks.txt` |
| Native libraries load only from the package directory (absolute `_HERE`) or an explicit absolute override; no RPATH/RUNPATH in any of the 7 built libraries; ABI checked before any other symbol is bound; fall back to the reference (`auto`) or raise (`native`) | `v11-redteam/environment-checks.txt` (`readelf -d`: NEEDED only), `test_redteam_native_loader.py`, existing loader tests |
| Native buffer sizes: RS (`_prepare` shape checks), strand (shape checks), cluster (capacities passed and checked in C), outer-list `assign` (all sizes from the arrays), reads (streaming state with explicit sizes) | wrapper review; existing C argument-check tests in `tests/v6/native`, `tests/v7`, `tests/arch`, `tests/v9` |
| Dependencies: pip-audit finds 5 distinct advisories in 2 packages, exactly the SECURITY.md triage (cryptography PYSEC-2026-3552/3553/3554 and GHSA-537c-gmf6-5ccf; pytest PYSEC-2026-1845). V11 adds no PKCS#7, X.509 or RSA use: `secure/` uses `hashlib.scrypt`, HMAC and SHA-256 | `v11-redteam/pip-audit.json` |

## 3. Native boundary (C, C++, Rust via ctypes)

* libFuzzer + ASan + UBSan harnesses for the RS decoder (`fuzz/native/rs_fuzz.c`) and the aligner
  (`fuzz/native/align_fuzz.c`) already existed in this tree (`adf30b6`). The Phase 0 gap was the job #10 entry of the
  fuzzing skill table. The align harness did not reach the V11 WP1 SIMD entry points. It now checks
  `vnx_align_batch_lv` at 8 and 16 lanes against the 4-lane path, and checks that an unsupported level is refused
  (`a7772da`); edge coverage rose from 299 to 545.
* Campaigns (60–90 s smoke, clang 18; `v11-redteam/fuzz-campaigns.txt`): align 44,059 + 36,134 runs, rs 16,341,
  reads 628,117; 0 crashes.
* Sanitizer scripts run with `PY=/root/vnx-dna-lab/.venv/bin/python` (an absolute path, required in a worktree):
  align (gcc and clang ASan+UBSan, clang UBSan-trap; 304 tests per build; stress fuzz 60 rounds × 15,000 reads per
  SIMD level, 0 mismatches), rs (119 tests per build; 11,232 comparisons over scalar/AVX2/AVX-512, 0 mismatches),
  outer-list (103 tests per build; libFuzzer 248,797 runs). ASan canaries fired. Overall: PASS
  (`v11-redteam/sanitizers-*.txt`).
* Not run in this review: valgrind, MSan (see `V7_MSAN.md`), cluster/strand/integrity sanitizer scripts (unchanged
  code), campaigns longer than 90 s (the pre-release budget is 1 h per target).

## 4. Verification

* Focused runs per fix, listed with each commit. Full suite: `vnx-task verify v11-redteam`; the pytest summary line
  is quoted in the job #90 log.
* Before this branch, `7a9d0b7` already failed 3 tests (2 in `tests/architecture/test_layers.py`, 1 in
  `tests/conformance/test_vectors.py::test_vnx_conformance_is_conformant_on_each_backend[native]`), reproduced on a
  clean export of that commit. `build/v11` fixed them later (`e021683`, `c5f6424`). This branch neither merges nor
  rebases, so they stay in its verify run. A suite run of `build/v11` with this branch's diff applied is reported in
  the job log too.

## 5. Open items

* V6-SEC-10 (LOW): the extract TOCTOU window is narrower but not closed. Closing it needs `openat`-style directory
  file descriptors, which is not a format change. Proposed as a follow-up.
* V11-RT-10 (INFO): the scrypt defaults, for a founder decision.
* Longer native campaigns (1 h per target) before the V11 release.
