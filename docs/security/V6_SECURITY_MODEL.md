# VNX-DNA V6 security model

Scope: the `vnx` CLI and the `vnxdna.v4` / `vnxdna.v6` / `vnxdna.v5` code that it runs (VNX4 container, DNA encoder and
decoder, V6 outer code, native kernels), at branch `work/v6-security` (base `build/v6-sprint`, 3e4d681). The V1–V3
`vnx-dna` commands keep the design and audit in [`docs/SECURITY.md`](../SECURITY.md) ("V3 security — unchanged"). They
are mentioned here only where V6 code reaches them.

This document follows V6_ARCHITECTURE §8 Phase 6 and spec §11 (`docs/spec/VNX-DNA-SPEC-V6.md:858-866`). Every claim
cites code or a test at this revision. Anything not verified is marked **(not verified)**. Severities:

- **CRITICAL**: exploitable now; data or key exposure.
- **HIGH**: exploitable with effort, or a memory-safety bug.
- **MEDIUM**: defence-in-depth, a reachable DoS, or a reachable dependency CVE.
- **LOW**: hardening.
- **INFO**: notes.

## 1. Findings summary

No CRITICAL or HIGH finding. Not fixed: 3 MEDIUM (V6-SEC-01, -02, -03), 9 LOW (open, accepted, or awaiting a push) and 7 INFO. Fixed in this branch: 3 LOW (V6-SEC-04, -22, -23) and 1 INFO (V6-SEC-14, documentation).

| ID | Sev. | Finding | Status | Recommended action |
|---|---|---|---|---|
| V6-SEC-01 | MEDIUM | A forged superblock (12 strands, CRC-valid) can claim any `container_size` up to the u32 `group_count` limit. The decoder sparse-allocates that size and walks every group, so the time to FAILURE grows linearly with the claimed group count (up to 2³² groups). A local reproduction confirmed the effect; its timing is not a committed measurement. Larger claims end in `OverflowError`/`OSError` (exit 70). | open | Bound `container_size` by the evidence: groups that no read can address cannot exist. Also add an absolute cap or a `--max-container-bytes` option. |
| V6-SEC-02 | MEDIUM | A full `vnx decode -o OUT` ignores `--key-file`/`--passphrase-env`. It does not check the manifest MAC and does not refuse an unencrypted (downgraded) pool. Reproduced: plain pool + key gives exit 0, SUCCESS. | open | When a key is given, call `open_container(work, key=…, require_key=True, allow_unencrypted=…)` before SUCCESS. |
| V6-SEC-03 | MEDIUM | Archive substitution or rollback. The decoder has no notion of an *expected* archive. A clear pool can be replaced by any self-consistent pool (FC-8). An encrypted pool can be replaced by an older or different archive under the same key, and extract accepts it. | open (design) | Add `--expect-archive-id` / `--expect-sha256` to decode and extract, and print the archive ID and container SHA-256 for the key holder's catalogue. |
| V6-SEC-04 | LOW | `open_container` raised `TypeError`/`KeyError`/`ValueError` (not `VNXError`) on hash-consistent manifests with type-confused fields. The CLI mapped these to exit 70 INTERNAL_ERROR. No wrong data. Found by the `py-manifest` fuzz target (V6-FUZZ-01). | fixed in this branch (1036eda; `tests/fuzz/test_fuzz_regressions.py`, 19 cases, failed before the fix) | §5.4 lists every hole. |
| V6-SEC-05 | LOW | For an encrypted archive opened without a key, the manifest is checked against nothing (no MAC, and the trailer digest is skipped). `inspect`/`verify` print unauthenticated metadata; `VERIFIED_STORED` means self-consistent only. | open | State it in the output (`"authenticated": false`) and in the docs. |
| V6-SEC-06 | LOW | Spec §2.3.3 requires decode results of encrypted archives to carry `"encrypted": true, "content_verified": false`. The decoder does not emit them. | open | Implement in Phase 2/7 (report fields). |
| V6-SEC-07 | LOW | Default scrypt cost N = 2¹⁵, r = 8, p = 1 (≈ 32 MiB). This is below the OWASP Password Storage Cheat Sheet minimum (N = 2¹⁷, r = 8, p = 1). The OWASP figure was not re-checked today. | accepted until a founder decision | Founder decides the default; the caps already allow 2¹⁷. |
| V6-SEC-08 | LOW | Native libraries are loaded from env-var paths (`VNXDNA_NATIVE_LIB`, `VNXDNA_READS_LIB`, `VNXDNA_RS_LIB`) and in-place `.so` files by name, checked by ABI integer only. The exported test hook `vnx_rs_restrict_levels` mutates global dispatch. | accepted (local trust boundary) | Record the backend and a library hash in provenance; hide the hook behind a test build flag. |
| V6-SEC-09 | LOW | Native ABIs with implicit buffer sizes: align outputs (`n*frame_nt`, `n*T`), reads `info[4]` / `ctx[40]`. They are correct today, and the ABI number does not cover them. | open | Pass the capacities, or bump the ABI with a size check. |
| V6-SEC-10 | LOW | `extract`: the symlink checks and the write are not atomic (TOCTOU). The containment check is a string-prefix test (`/out` also matches `/out2`). | open | Use `os.path.commonpath`, plus `O_NOFOLLOW`/`openat`-style creation. |
| V6-SEC-11 | LOW | Unencrypted archive IDs are deterministic from options, paths and sizes, not content. Two different clear archives with the same file names and sizes get the same ID and tag. Random IDs have 16-bit tags (birthday ≈ 300 archives per pool). | open (`content-v1` deferred; pool rule Phase 7) | Spec §2.3.2 `content-v1`; refuse tag collisions (`ARCHIVE_TAG_COLLISION`, Phase 7). |
| V6-SEC-12 | LOW | CI runs no secret scan, dependency audit or fuzz smoke (`.github/workflows/ci.yml`), while `docs/SECURITY.md:62` says "CI … stay quiet". | addressed in this branch (needs a push) | Push the CI addition after the founder's go. |
| V6-SEC-13 | LOW | No test swaps or reorders *encrypted* chunks under a consistent (re-hashed) chunk table, so the AEAD index binding has no direct test. The MAC'd manifest catches it first today. | open | Add a test that rebuilds the table and needs the AEAD to fail. |
| V6-SEC-14 | INFO | `docs/SECURITY.md:18` says the AAD binds "count". For chunks the count is 0 (`archive.py:265`, `archive.py:346`). `VNX4_FORMAT.md` states this correctly; chunk truncation is caught by the MAC'd manifest and chunk table. | fixed in this branch (bc6faa0, `docs/SECURITY.md`) | none |
| V6-SEC-15 | INFO | Passphrases are UTF-8 encoded without Unicode normalisation (`crypto.py:111`): NFC and NFD spellings give different keys (availability, not secrecy). | open | Document, or apply NFC with a format flag. |
| V6-SEC-16 | INFO | Secret lifetime: keys and passphrases are immutable Python `bytes`/`str` and are never zeroised. A passphrase passed through the environment is readable from `/proc/<pid>/environ` by the same user. | accepted | Already documented (`docs/SECURITY.md:49`, `:234`). |
| V6-SEC-17 | INFO | The key check is verified before the manifest MAC. A tampered `key_check` is reported as "wrong key" (exit 4), not as tampering. The clear key check gives an offline guess verifier, which is no stronger than the AEAD tags already allow. | accepted | none |
| V6-SEC-18 | INFO | Metadata visible without a key (spec §11): chunk table, sizes, counts, tag, coding parameters. Frame-6 tags, primers and Sector Zero/One are not implemented. | documented | Extend `docs/SECURITY.md` when they land (V7/V9). |
| V6-SEC-19 | INFO | Provider compromise: no provider code exists (`vnxdna.providers` is Phase 7). | n/a today | Threat noted in §4.14 for the Phase 7 design. |
| V6-SEC-21 | INFO | `Superblock.unpack` does not check the two reserved bytes before the CRC (`encoder.py:149` writes zeros; `:154-196` never reads bytes 90-91). A superblock with non-zero reserved bytes is accepted and re-packs differently. Not a wrong-data path: the CRC still covers them and every field is validated. Confirmed by the `py-superblock` fuzz target, whose invariant masks exactly these bytes. | open | Require zeros in a future superblock version, or document them as reserved-ignored. |
| V6-SEC-22 | LOW | A manifest nested about 1,500 levels deep made `parse_canonical_json` raise `RecursionError` (canonical re-serialisation outside the `try`, `util.py:61` before the fix). This happened before the MAC check, for any archive, with no key. Exit 70, no wrong data. Found while triaging a `py-manifest` crash (V6-FUZZ-02). | fixed in this branch (7ce9dcb; `test_fuzz_regressions.py::test_deeply_nested_manifest_is_a_format_error`) | none |
| V6-SEC-23 | LOW | The inner-RS GF(256) table caches (`v4/codecs.py` `InnerRS.parity`, `v4/rs_fast.py` `_tables`) grew without bound, up to about 8 MiB per geometry. A long-lived process seeing many layouts grows without limit, and a forged superblock chooses the inner parity. Found by `py-rs`, which hit the 2 GiB OOM limit (V6-FUZZ-03). | fixed in this branch (761154d: at most 16 geometries; `::test_rs_table_caches_are_bounded`) | none |
| V6-SEC-20 | INFO | Dependency advisories: the 2026-10-04 triage (`docs/SECURITY.md` MEDIUM table: `cryptography` ×4, `pytest` ×1) still applies, and none is reachable. The 2026-10-05 scan lists only the cppcheck false positive (`align.c:322`) as MEDIUM. | accepted (pins: founder decision) | Re-check when pins change. |

## 2. Assets, actors, trust boundaries

| Asset | Where |
|---|---|
| Content of archived files | container body (`v4/container.py:6`); DNA strands |
| Metadata: paths, sizes, hashes | file table (sealed when encrypted, `archive.py:298`) |
| Keys | key file (32 B, `crypto.py:52-81`), passphrase via env (`cli.py:92-101`), derived keys (`crypto.py:118-130`) |
| Correctness of recovered bytes | the "never wrong data" rule: container SHA-256 in the superblock (`decoder.py:1454`), chunk and file hashes (`archive.py:343-357`, `:538`) |
| Decoder host availability | CPU, RAM and disk during `vnx decode` |

| Actor | Capability |
|---|---|
| A1 pool / read-file writer | writes arbitrary reads: corrupt, forged, or adversarial FASTA/FASTQ/VXS |
| A2 container writer | supplies an arbitrary `.vnx` file (download, shared storage) |
| A3 storage observer | reads strands, containers and manifests without the key |
| A4 local user | sets the environment and races on the output directory |
| A5 lab / provider (Phase 7) | synthesises, stores and sequences the DNA (can substitute, drop or replay) |

Trust boundary: every byte from reads, containers, manifests and superblocks is untrusted. The key file and environment
are trusted, apart from the bounded reading (`crypto.py:58-65`).

## 3. Cryptography review (`src/vnxdna/v4/crypto.py`)

| Item | Code | Assessment |
|---|---|---|
| Primitives | `cryptography`: AESGCM, HKDF-SHA256, Scrypt, stdlib HMAC-SHA256 (`crypto.py:33-37`, `:163-167`) | No invented primitive. |
| Master key | key file of 32 raw bytes, 64 hex or 44 base64 characters; regular file only; ≤ 1024 B read (`crypto.py:52-81`). `generate_key_file` uses `os.urandom(32)`, `O_EXCL`, mode 0600 (`:84-88`) | Sound. |
| Passphrase | only through `--passphrase-env VAR`; an empty or unset value is refused (`cli.py:96-99`, `crypto.py:109-110`); scrypt with the salt (`:111`); UTF-8 without normalisation (V6-SEC-15) | Sound apart from V6-SEC-07 and V6-SEC-15. |
| scrypt caps | N a power of two ≤ 2²⁰, r ≤ 32, p ≤ 16, 128·r·N ≤ 1 GiB, N·r·p ≤ 2²⁵, ints only, not bool (`crypto.py:91-101`). Enforced at manifest validation (`container.py:399-402`) and again before derivation (`crypto.py:105-107`) | Bounded cost per open. Tests: `test_security_v6.py::test_scrypt_parameters_above_caps_rejected_before_derivation`, `::test_scrypt_parameters_within_caps_accepted`. |
| Salt / RNG | fresh `os.urandom(16)` salt and `os.urandom(16)` archive ID per encrypted archive (`archive.py:201-202`). The `salt=` / `archive_id=` overrides of `build_archive` (`archive.py:185`) are not passed by any `src/` caller (checked with grep) | Sound. The override is a foot-gun: a fixed salt with the same key twice is GCM nonce reuse. Keep it test-only (LOW hardening, folded into V6-SEC-13's test work). |
| HKDF | `HKDF(SHA256, salt=archive salt, info="VNX4 <label>")` for `aead key`, `mac key`, `chunk id key`, `key check` (16 B) (`crypto.py:114-130`) | Key separation by label; per-archive keys through the salt. |
| Nonces | `domain(4) ‖ index(8)` = 96 bits (`crypto.py:137-138`). Domains: chunk 0, file table 1, refs 2 (`:43-45`). Each (domain, index) is used once per archive: chunks by index (`archive.py:265`), tables at index 0 (`:298-299`). The per-archive key from the random salt makes (key, nonce) unique | No reuse within an archive; across archives the keys differ (barring a 128-bit salt collision). The chunk size ≤ 64 MiB (`container.py:39`) is far below the GCM per-message limit. |
| AAD | `"VNX4 aead\0" ‖ archive_id ‖ domain ‖ index ‖ count` (`crypto.py:141-142`); count = 0 for chunks (`archive.py:265`, `:346`), 1 for tables | Binds position and archive. The count for chunks is not bound (V6-SEC-14); truncation is caught by the MAC'd manifest. |
| Manifest MAC | HMAC-SHA256(mac key, `"VNX4 manifest\0" ‖ manifest`) (`crypto.py:163-164`), compared with `hmac.compare_digest` (`container.py:320`, `:356-358`) | Sound. Tables are bound through their SHA-256 in the MAC'd manifest (`container.py:325-330`). |
| Key check | `key_check.hex()` compared with `compare_digest` (`crypto.py:132-134`), before the MAC (`container.py:318-321`) | INFO V6-SEC-17. A non-ASCII `key_check` string raises `TypeError` (V6-SEC-04). |
| Keyed chunk IDs | HMAC(chunk id key, plaintext) (`crypto.py:166-167`); public IDs are `SHA-256("VNX4 chunk\0" ‖ pt)` (`:170-172`) | Keyed IDs stop an observer from confirming guessed content; equality *within* an archive (dedup) stays visible (`docs/SECURITY.md:23-30`). |
| Comparisons with `!=` | `container.py:325-337` (table SHA-256s, Merkle root), `archive.py:343`, `:356` | Not a timing issue. Both operands are public (attacker-known table and hash values). For keyed chunk IDs the plaintext has already passed AES-GCM authentication. |
| Order of operations | compress → encrypt (`archive.py` pipeline docstring, lines 3-6); on read: stored SHA-256 → AEAD open → bounded zstd → size → chunk ID (`archive.py:343-357`) | Authenticate before decompress. Compressed sizes leak (documented). |
| Error leakage | `InvalidTag` → `VNXIntegrityError` with a generic message, `from None` (`crypto.py:159-161`). CLI errors are printed as JSON; internal errors as `"<Type>: <message>"` (`cli.py:80-88`) | No key material in messages (checked in `crypto.py` / `container.py` / `archive.py` messages). Reports and events are not exhaustively audited for passphrase echo **(not verified)**; `_keys` keeps the passphrase local (`cli.py:92-101`). |
| Secret lifetime | `ArchiveKeys` is a frozen dataclass of `bytes` (`crypto.py:118-123`) | Cannot be zeroised in Python (V6-SEC-16). |
| Downgrade (container) | a key or passphrase given for a clear archive is refused unless `allow_unencrypted` (`container.py:302-304`) | Tests: `test_security_v6.py::test_api_refuses_key_for_unencrypted_archive`, `::test_cli_refuses_key_for_unencrypted_archive`, `::test_partial_decode_with_key_refuses_unencrypted`. **Gap:** the full decode path (V6-SEC-02). |
| KDF confusion | key file vs passphrase archives are distinguished by `enc["kdf"]` (`container.py:312-316`) | Sound. |

V3 (`src/vnxdna/container/crypto.py`) and V2 (`src/vnxdna/v2/crypto.py`) use the same construction with their own labels
(`container/crypto.py:75-125`). They are reachable only from `vnx-dna` / `vnxdna` (`pyproject.toml:27-28`), not from
`vnx`. Their audit is V3_AUDIT.md.

## 4. Threats

Each subsection gives the entry points, the mitigations (with their tests), the gaps and the severity.

### 4.1 Malicious or corrupted container (A2)
- **Entry:** `open_container` (`container.py:283-353`), `read_header_trailer` (`:249-280`), `read_chunk` (`archive.py:327-358`),
  `verify_container` (`:368-438`), `extract` (`:507-545`).
- **Mitigations:**
  - magic, version and flags checks (`container.py:259-270`);
  - section sizes must sum to the file size (`:276-277`);
  - manifest size ≤ 1 MiB and nonzero (`:278`);
  - canonical JSON only: no floats, no duplicate keys, re-serialisation must be byte-identical (`util.py:48-63`);
  - semantic manifest checks (`container.py:375-416`);
  - table SHA-256s (`:325-330`);
  - chunk table tiling and size rules (`:419-443`);
  - file and ref consistency (`:446-471`);
  - Merkle root (`:335-337`);
  - per-chunk stored SHA-256, AEAD and chunk ID (`archive.py:343-357`);
  - whole-file trailer digest in `verify` only (`archive.py:400-411`).
- **Tests:**
  - `tests/v4/test_container_v4.py`: `::test_body_corruption_detected`, `::test_every_table_and_manifest_byte_is_protected`, `::test_truncated_and_garbage_inputs`, `::test_unsupported_version_and_flags`, `::test_reordered_file_table_rejected`;
  - `tests/v4/test_fuzz_v4.py`: `::test_fuzz_byte_flips`, `::test_fuzz_truncation_insertion_deletion`, `::test_fuzz_random_files_as_containers`, `::test_fuzz_semantic_manifest_mutations`, `::test_fuzz_counts_and_root_mutations`, `::test_fuzz_noncanonical_and_hostile_json`;
  - `tests/adversarial/test_fuzz.py::test_mutated_containers_never_yield_wrong_data`.
- **Gaps:** V6-SEC-04 (type confusion → non-VNX exceptions). `open_container` does not check the trailer digest; that is by design, since chunks are checked lazily and `verify` checks the digest. INFO.
- **Severity:** LOW (no wrong-data path found).

### 4.2 Malformed strands and forged frames (A1)
- **Entry:** `decode_frames` (`v4/frame.py`), the pass-1/pass-2 decoder (`v4/decoder.py`), duplicate majority (`decoder.py:529-557`).
- **Mitigations:**
  - inner RS plus CRC per frame;
  - a strict majority of identical verified copies, with ties dropped (`decoder.py:529`);
  - the outer code;
  - the final container SHA-256 against the superblock before anything is published (`decoder.py:1447-1460`).
- **Tests:**
  - `test_fuzz_v4.py`: `::test_fuzz_frames_never_accept_wrong_payload`, `::test_fuzz_corrupted_ecc_and_forged_frames`, `::test_fuzz_random_dna_and_shuffled_valid_reads`;
  - `tests/adversarial/test_fuzz.py::test_corrupted_reads_at_every_level_never_yield_wrong_data`;
  - `tests/v5/test_indel_false_success.py`.
- **Gaps:** valid-CRC forgeries can cause FAILURE (availability), not wrong output (FC-8 limits this to accidents for clear archives, §6.1).
- **Severity:** INFO.

### 4.3 Forged superblock (A1)
- **Entry:** `Superblock.unpack` (`v4/encoder.py:154-196`); candidate selection (`decoder.py:748-788`).
- **Mitigations:**
  - magic and CRC-32 (`encoder.py:158`);
  - every field validated so that a CRC-valid forgery is a format error (`:175-195`);
  - a forged or unsupported candidate does not abort the decode (`decoder.py:766-770`);
  - tag and layout must match (`:772`);
  - several candidates require `--archive-tag` (`:781-784`).
- **Tests:**
  - `test_security_v6.py`: `::test_superblock_k_zero_is_a_format_error`, `::test_forged_superblock_candidate_does_not_abort_decode`;
  - `test_fuzz_v4.py::test_fuzz_superblock_bytes`.
- **Gap V6-SEC-01:**
  - `container_size` is a u64 checked only for consistency with `group_count` (u32) and `index_offset` (`encoder.py:183`).
  - The decoder then runs `f.truncate(sb.container_size)` (`decoder.py:1319`) and iterates every group.
  - Reproduced locally on 2026-10-05 (not a committed measurement): 12 forged superblock strands (v4-balanced) claiming a container of 10⁹ bytes made a decode run for tens of seconds before FAILURE. The cost is linear in the group count, and the u32 limit allows 2³² groups.
  - Claims above the filesystem limit raise `OSError(EFBIG)`; claims ≥ 2⁶³ raise `OverflowError`. Both end in exit 70.
  - The V6 recovery budgets (`v6/recovery.py:55-56`, `--max-wall-seconds`, `--max-rss-mb`) are opt-in and off by default. Whether they fire inside this loop is **(not verified)**.
- **Severity:** MEDIUM.

### 4.4 Read-file parser attacks (A1)
- **Entry:**
  - `v4/reads.py:101-176` (Python FASTA/FASTQ/plain);
  - `v6/native_reads.py` plus `v6/native/reads.c` (native; backend `VNXDNA_READS_BACKEND`);
  - VXS through `v2/strandio.py:280-314` (`reads.py:104-107`).
- **Mitigations:**
  - records ≤ `MAX_READ_NT` = 100,000 (`reads.py:24`, `:128-166`);
  - line cap (`:76-77`);
  - `max_reads` (`:179-181`);
  - gzip/BAM refused (`:62`);
  - VXS header consistency and size checks (`strandio.py:288-303`);
  - the native kernel never allocates and checks capacities (INVENTORY §3.2).
- **Tests:**
  - `test_fuzz_v4.py`: `::test_fuzz_read_file_parser`, `::test_fuzz_hostile_read_files`;
  - `tests/adversarial/test_fuzz.py`: `::test_read_parser_and_scanner_accept_arbitrary_text`, `::test_junk_inputs_give_structured_errors`;
  - `tests/v6/native/test_native_reads_fuzz.py` (3 tests), `test_native_reads.py`: `::test_malformed_inputs`, `::test_c_abi_rejects_bad_arguments`, `::test_c_abi_position_overflow_is_rejected`;
  - `test_native_reads_memory.py::test_bounded_rss_on_200mb_fastq`;
  - the libFuzzer reads harness: 0 crashes in previous campaigns (`/root/vnx-dna-ai/logs/fuzz.log`).
- **Gaps:** V6-SEC-09 (implicit `info[4]`/`ctx[40]`, `native_reads.py:245-246` vs `reads.c:29`).
- **Severity:** LOW.

### 4.5 Malicious metadata (A2)
- **Mitigations:**
  - path validation on read: no absolute paths, `..`, `.`, empty components, NUL, backslash or control characters; ≤ 4096 B (`container.py:90-102`, applied in `parse_file_table` `:121`);
  - strictly sorted, unique paths (`:122-124`);
  - type and size rules (`:127-132`);
  - refs inside the table (`:345-349`);
  - contiguous refs, full-size non-final chunks, totals, no unreferenced chunks (`:446-471`).
- **Tests:**
  - `test_container_v4.py::test_unsafe_archive_paths_rejected`;
  - `test_fuzz_v4.py::test_fuzz_counts_and_root_mutations`.
- **Gap:** V6-SEC-04.
- **Severity:** LOW.

### 4.6 Resource exhaustion
- **Bounded:**
  - manifest ≤ 1 MiB (`container.py:38`, `:278`);
  - chunk plaintext ≤ 64 MiB (`:39`, `:433`);
  - files ≤ 10⁷ (`:42`, `:407`);
  - key file ≤ 1 KiB;
  - scrypt caps;
  - reads as in §4.4;
  - the chunk table and Merkle work are linear in the file size.
- **Unbounded:**
  - the superblock-claimed container (V6-SEC-01);
  - `max_reads` defaults to 2·10⁹ (`decoder.py:68`);
  - wall time and RSS are bounded only when the opt-in budgets are given.
- **Severity:** MEDIUM (through V6-SEC-01).

### 4.7 Decompression bombs
- **Mitigation:**
  - bounded `stream_reader.read(plain + 1)` (`container/compression.py:44-49`) through `bounded_zstd` (`archive.py:313-324`);
  - the size must equal the table's `plain` (`archive.py:350-353`), which is ≤ chunk_size ≤ 64 MiB;
  - the stored size must be ≤ plain + tag (`container.py:436-437`);
  - AEAD is checked before decompression when encrypted.
- **Test:** `test_container_v4.py::test_decompression_bomb_bounded`.
- **Severity:** INFO.

### 4.8 Invalid lengths and integer overflow
- **Python:** `int` is unbounded. Table fields are unpacked as unsigned (`container.py:52-53`, `:272`) and validated: lengths sum exactly to the file size (`:276`), offsets tile the body (`:423`). `parse_file_table` checks each record's length before slicing (`:110-115`). `Superblock.unpack` checks `k ≥ 1` before dividing by `k·p` (`encoder.py:177-183`), and `Layout.validate` bounds `p` to 1..200 (`v4/frame.py:62-64`).
- **C:**
  - RS: `n_words ≤ PTRDIFF_MAX/256` (`rs.c:479`), `1 ≤ n ≤ 255`, `nsym < n` (`:474`), overlap checks (`:481-484`);
  - reads: a position-overflow test exists (`test_native_reads.py::test_c_abi_position_overflow_is_rejected`);
  - align: size-overflow checks (INVENTORY §3.1, `align.c:296-297`).
- **Severity:** INFO.

### 4.9 Memory corruption (native kernels)
- **Kernels:** `v5/native/align.c` (ABI 2), `v6/native/reads.c` (ABI 1), `v6/native/rs.c` (ABI 1); all loaded by ctypes (INVENTORY §3).
- **Mitigations:**
  - the caller owns the buffers; capacities are checked (reads, RS);
  - ABI version checks: `test_native_reads.py::test_abi_mismatch_is_rejected`, `test_native_rs.py::test_abi_mismatch_is_not_loaded`, `tests/v5/test_native_alignment.py::test_abi_mismatch_is_rejected`;
  - reference differential tests: `tests/v6/native/test_native_rs_fuzz.py::test_seeded_differential_fuzz`, `tests/v5/test_native_alignment_equivalence.py::test_fuzz_random`;
  - previous ASan/UBSan runs (`results/v6-audit/05a-05c`).
- **Gaps:**
  - V6-SEC-09 (implicit sizes);
  - V6-SEC-08 (environment-selected libraries, global test hook `rs.c:74`, `:452`);
  - no MSan and no non-x86 builds (INVENTORY §3.4.8).
- Phase 6 adds RS and aligner harnesses (see "Fuzzing evidence").
- **Severity:** LOW (no memory-safety bug known).

### 4.10 Key misuse
- **Mitigations:**
  - wrong key → `VNXKeyError` (exit 4) before decryption (`crypto.py:132-134`);
  - a key-file archive refuses a passphrase and vice versa (`container.py:312-316`);
  - no key or passphrase is stored (`test_container_v4.py::test_no_plaintext_key_in_archive`);
  - an encrypted archive without a key cannot be listed or extracted (`test_container_v4.py::test_encrypted_round_trip_and_wrong_key`).
- **Gaps:** V6-SEC-02 (the key is silently ignored by a full decode).
- **Severity:** MEDIUM (through V6-SEC-02).

### 4.11 Authentication failures
- AEAD failure → `VNXIntegrityError` (`crypto.py:156-161`); MAC failure (`container.py:320-321`).
- A file is renamed into place only after its SHA-256 verifies (`archive.py:531-539`, `util.py:82-105`).
- Test: `test_container_v4.py::test_body_corruption_detected` (nothing published).
- **Gap:** V6-SEC-13 (no direct test of the AEAD index binding under a re-hashed table).
- **Severity:** LOW.

### 4.12 Downgrade
- Container: refused (§3).
- DNA decode: V6-SEC-02.
- Superblock version: an unsupported version gives `VNXUnsupportedVersionError` but other candidates still count (`decoder.py:768-770`). A newer-version forgery cannot mask a valid candidate.
- Format minor or required features: refused (`container.py:267-268`, `:383-385`).
- **Severity:** MEDIUM (through V6-SEC-02).

### 4.13 Archive substitution and rollback
- **Clear archives (FC-8, spec `:688-690`):** anyone who can write the pool can build a consistent forgery with matching superblock SHA-256, Merkle root and trailer, and decode reports SUCCESS.
- **Encrypted archives:** an attacker without the key cannot forge content. They can:
  - replace the pool with a clear archive (caught at extract with a key; missed by a full decode, V6-SEC-02);
  - replay an *older or different* archive made with the same key. Nothing detects this: the archive ID binds chunks to their archive but is not checked against an expectation.
- **Gap:** V6-SEC-03.
- **Severity:** MEDIUM.

### 4.14 Provider compromise (A5)
- No provider, export or import code exists (spec §9, `docs/spec/VNX-DNA-SPEC-V6.md:725`; Phase 7).
- What a compromised lab can already do: substitution, rollback, dropout, or mixing pools (§4.3, §4.13). Encrypted archives give confidentiality and content authenticity against it; nothing gives freshness or completeness beyond the decoder's own SHA-256 checks.
- The Phase 7 package parsers are fuzz targets with a 1 MiB JSON limit (spec §11, `:863-864`).
- **Severity:** INFO.

### 4.15 Paths and output files (A4)
- **Extract:**
  - `_safe_target` refuses symlinked components and targets (`archive.py:491-504`);
  - containment check (`:529`);
  - atomic, 0600 output (`util.py:82-105`).
  - Tests: `test_container_v4.py::test_extract_refuses_symlink_escape`, `::test_extract_does_not_overwrite_without_force`.
  - **Gap V6-SEC-10:** the checks and the write are separate syscalls. `startswith(str(root))` is a prefix test; it is reachable only together with the race, because `rel` is validated first.
- **Events and report files:**
  - `O_NOFOLLOW`, `S_ISREG`, 0600 (`v6/observe.py:59-75`);
  - must not alias an input or output, including hardlinks (`cli.py:360-387`).
  - Tests: `test_security_v6.py::test_events_refuses_symlink`, `::test_events_refuses_an_input_or_output_file`, `::test_events_refuses_non_regular_file`, `::test_report_refuses_symlink_and_existing_without_force`, `::test_report_refuses_an_input_file`, `::test_report_new_file_mode_0600`.
- **Severity:** LOW.

### 4.16 Supply chain and CI
- Pins: `cryptography>=44,<47`, `zstandard>=0.23,<1`, `numpy>=2.0,<3` (`pyproject.toml:15-17`).
- Advisories: V6-SEC-20.
- CI: V6-SEC-12.
- Test keys: reviewed, and `.gitleaksignore` lists exactly 5 fingerprints (`docs/SECURITY.md:52-64`).
- Bandit LOW findings (`/root/VNX-Vault/Builds/security-2026-10-05.md`):
  - `subprocess` calls use argument lists (`git`, compilers);
  - the `assert`s in `v6/decode.py:39`, `:48` and `container/reader.py:153` are type-narrowing only and do not guard untrusted input.

## 5. Specification items

### 5.1 FC-8: no authenticity for clear archives
FC-8 (`docs/spec/VNX-DNA-SPEC-V6.md:688-690`) holds in the code:
- The clear-archive manifest is protected by an unkeyed SHA-256 in the trailer (`container.py:322-323`).
- The container is protected by the superblock SHA-256, behind a CRC-32 (`encoder.py:158`, `decoder.py:1454`).
- No secret is involved, so these checks stop accidents, not adversaries. `docs/SECURITY.md:19-20` says the same.

Users must not treat SUCCESS on a clear archive as proof of origin (V6-SEC-03).

### 5.2 Metadata leakage: tags, primers, Sector Zero/One (spec §11)
- **Visible today without a key:**
  - archive tag = archive ID bytes 0-1 in every strand header (`decoder.py:772`);
  - all coding parameters and `container_size`, `index_offset` and group count in the superblock (`encoder.py:145-149`);
  - the container SHA-256;
  - in the container: the chunk table (stored and plain sizes, codec, stored SHA-256, chunk IDs) and the manifest (chunk size, compression, counts, content size, file count).
- **Sealed:** paths, file sizes, per-file hashes, ref order (`docs/SECURITY.md:23-30`).
- **Clear archives:** the deterministic ID (`archive.py:216-222`) is derived from the options plus path, type, size, mode and mtime of every entry. Truncated to 16 bytes, it is a fingerprint of the *file list*, so equal listings are linkable across pools. This goes beyond what spec §11 says about `content-v1`.
- **Not implemented:** frame-6 pool tags, primer IDs and sequences, Sector Zero/One, export/import manifests (V7/V9; `docs/spec/VNX-DNA-SPEC-V6.md:860-862`). `docs/SECURITY.md` must be extended when they land.

### 5.3 Encrypted SUCCESS semantics
For a full decode (`decoder.py:1447-1470`), SUCCESS means:
1. Every group was recovered.
2. The SHA-256 of the reconstructed container equals the superblock value (`:1454`).
3. `open_container(work)` with **no key** passes (`:1460`). For an encrypted archive this checks the manifest against nothing (no MAC, no trailer digest; `container.py:305-323`), then the chunk table against the manifest and the Merkle root.

SUCCESS therefore means "these are the bytes the superblock describes", not "this is authentic content":
- Authenticity (MAC, AEAD) is established only when files are extracted with the key (`--extract` or `vnx extract`), and it does not include freshness (V6-SEC-03).
- The key passed to a full decode is not used (V6-SEC-02).
- The spec requires `"encrypted": true, "content_verified": false` in such results (`docs/spec/VNX-DNA-SPEC-V6.md:124-127`); this is not implemented (V6-SEC-06).

Selective and partial decodes do use the key and the downgrade refusal (`decoder.py:1492`, `:1523`, `:1537`).

### 5.4 V6-SEC-04 details (type-confused manifests)

These were reproduced with re-wrapped manifests whose trailer SHA-256 is valid (`test_fuzz_v4.py::_rebuild_with_manifest` pattern):

| Field | Line | Exception |
|---|---|---|
| `tables.chunk_table` not a dict (e.g. a string) | `container.py:325` | `TypeError` |
| `tables.chunk_table` missing `entries` (likewise `sha256`, and `bytes`/`sha256` of `file_table`/`refs`) | `container.py:325-330` | `KeyError` |
| `encryption.salt` 32 characters but not hex, with a key | `container.py:310` | `ValueError` |
| `encryption.key_check` with non-ASCII characters, with a key | `crypto.py:133` | `TypeError` |

The CLI mapped these to exit 70 (`cli.py:80-88`). Fixed: `validate_manifest` now requires each table to be an object with an integer size and a lower-case hex SHA-256, and the salt and key check to be lower-case hex (`container.py:368-370`, `:397-398`, `:410-412`).

More holes found by reading, to be confirmed by the fuzzer:
- `tables.*.entries` / `bytes` of a non-int type compare unequal and give a `VNXIntegrityError`, which is fine.
- `integrity.merkle_root` must be a `str` (`container.py:414`), so it is fine.
- `chunking.chunk_size`, `counts.*` and the scrypt parameters are int-checked.
- `extensions` is checked only to be a dict (`container.py:416`). No `src/` reader consumes its content at this revision (grep), so this is fine for now. A Phase 2 `extensions.vnx` reader must validate it.

## Fuzzing evidence

Full results: [V6_FUZZ_REPORT.md](V6_FUZZ_REPORT.md); raw numbers: `fuzz/campaigns/2026-10-05.json`.

- **Harnesses.**
  - Three libFuzzer + ASan + UBSan harnesses: the read parser, the RS decoder (own GF(256) oracle plus a SIMD-level
    differential) and the aligner.
  - Ten Python targets (atheris; hypothesis smoke in the default suite): container, structure-aware manifest
    (valid digest or HMAC), superblock v1/v2, frame decode, reads (native vs reference), VXS, RS (three
    implementations plus every SIMD level), aligner (native vs reference), decompression bombs, and end-to-end decode.
- **Campaign of 2026-10-05.** 10.5 minutes per native target and 5.3 minutes per Python target. No memory-safety
  finding. No wrong-data path: no mutated archive verified, no accepted archive extracted other bytes, and no decode
  reported SUCCESS with a different container.
- **Bugs fixed, each with a regression test written first:**
  - V6-SEC-04 (V6-FUZZ-01): type-confused manifest fields;
  - V6-SEC-22 (V6-FUZZ-02): deep nesting;
  - V6-SEC-23 (V6-FUZZ-03): unbounded RS table caches.
- **Triaged:** one harness bug (run invalid) and two engine-memory OOMs in `py-align`, which do not reproduce
  without atheris.
- **Not yet met:** the ≥ 1 CPU-hour per harness acceptance (V6_ARCHITECTURE §8 Phase 6).
