# Security

This page covers **V4** (`vnx`, VNX4 container) first. The **V3** security design and audit follow unchanged below
the divider.

## V4 cryptography

* **Primitives.** AES-256-GCM (chunks, file table, reference table), HKDF-SHA256 (key separation), HMAC-SHA256
  (manifest, keyed chunk IDs) and scrypt (passphrases), all from `cryptography`. No primitive is invented.
* **Order.** compress → encrypt → (outer code, DNA). Compressing first is required for any gain, because ciphertext
  does not compress. It leaks each chunk's compressed size (below).
* **Keys.** A 32-byte key file (raw / 64 hex / 44 base64; read bounded from regular files only), or a passphrase
  through `--passphrase-env VAR` (never on the command line) stretched with scrypt (N = 2¹⁵, r = 8, p = 1 by default,
  recorded in the manifest). **No key or passphrase is ever stored**; only a 16-byte HKDF-derived key check is kept,
  so a wrong key is detected before decryption (`VNXKeyError`, exit 4). Tested: the key bytes do not appear in the
  archive.
* **Nonces.** `domain ‖ index` under a key derived from a fresh 16-byte random salt per archive, so no (key, nonce)
  pair repeats. The associated data binds the archive ID, domain, index and count, so reordering, splicing or swapping
  chunks between archives fails authentication. For body chunks the count field is 0 (the writer streams, so the total
  is unknown while sealing; `v4/archive.py:265`): a dropped or appended chunk is caught by the chunk table, whose
  SHA-256 is in the HMAC-authenticated manifest, not by the AEAD.
* **Unencrypted archives** detect accidents (SHA-256 everywhere, Merkle root), not deliberate tampering: an attacker
  can rebuild every hash. The null-encryption mode provides **no confidentiality**.

### What an encrypted VNX4 archive still reveals

The chunk table is in clear, so stored sizes, the number of unique chunks, the compression flag and stored-chunk
hashes are visible. Deduplication shows which chunks of the archive are equal (keyed IDs prevent confirming guessed
content, but equality inside the archive is visible). The manifest reveals the chunk size, compression level,
total content size and number of files. Paths, file sizes, per-file hashes and chunk ordering per file are sealed.
The DNA layer reveals the archive tag (2 bytes of the random archive ID) and all coding parameters. There is no
padding.

## V4 parsing and resource safety (audit, 2026-10-03)

| area | risk | V4 behaviour | evidence |
|---|---|---|---|
| archive parsing | malformed or truncated containers | every section length cross-checked against the file size; canonical JSON only (no floats, no duplicate keys, ≤ 1 MiB); every table checked against SHA-256s in the manifest; manifest checked against the trailer (SHA-256 or HMAC) | `test_container_v4.py`, `test_fuzz_v4.py` (byte flips, truncation, insertion, semantic manifest mutations, random files) |
| path traversal | `../`, absolute paths, NUL, backslashes, control characters, symlink escapes | paths validated on write *and* read; extraction refuses symlinks in the output tree and paths resolving outside it | `test_unsafe_archive_paths_rejected`, `test_extract_refuses_symlink_escape` |
| decompression bombs | a chunk expanding far beyond its declared size | decompression bounded by the table's plaintext size (≤ 64 MiB) through a bounded stream reader. **Finding fixed:** `zstd.decompress(max_output_size=…)` is not a bound when the frame declares a content size | `test_decompression_bomb_bounded` |
| malicious metadata | counts, sizes and references inconsistent with the data | file sizes, chunk counts, reference ranges, contiguity, tiling and unreferenced chunks all validated; mismatches are format errors | `test_fuzz_counts_and_root_mutations` |
| malformed DNA / reads | binary data, gzip/BAM, huge lines, truncated FASTQ | bounded block reader (≤ 100,000 nt per read, line-length cap), explicit format errors, non-ACGTN → N | `test_fuzz_hostile_read_files`, `test_fuzz_read_file_parser` |
| forged strands | valid-CRC frames carrying wrong content | duplicate majority, outer decoding, then the container SHA-256 from the superblock; nothing is published unless it matches | `test_fuzz_corrupted_ecc_and_forged_frames` |
| resource exhaustion | unbounded memory or time | bounded batches and in-flight windows, disk spill in the decoder, read-count limit (`max_reads`), sweep size limit (points × trials ≤ 100,000), file count ≤ 10⁷ | code review; EXP-0012 memory measurements |
| unsafe deserialisation | pickle / eval | none: JSON (validated) and fixed binary layouts only. Worker processes receive plain arguments | code review |
| temporary files | races, leaks, permissions | `mkstemp` in the destination directory, mode 0600, fsync, atomic `os.replace`; removed on failure; decoder scratch directories removed in `finally` | `util.atomic_output`, `decoder.decode_reads` |
| permissions | archives readable by others | containers and extracted files are created 0600; modes are applied only with `--apply-metadata` | code review |
| cryptographic configuration | weak or absurd scrypt parameters | N must be a power of two ≤ 2²⁰, r ≤ 32, p ≤ 16; unknown KDF names are rejected | `crypto.scrypt_master` |

**Limits.** A malicious read file can make decoding fail or run slowly (denial of service). It cannot make V4
publish wrong bytes without a SHA-256 collision (plus HMAC/AES-GCM forgeries when encrypted). Python cannot reliably
erase keys from memory.

## Test-only keys in the repository (reviewed 2026-10-04)

The secret scan (gitleaks, full history) reports 5 `generic-api-key` findings. The founder and the security review confirmed on 2026-10-04 that none of them is a real secret:

| File : line | What it is | Why it is safe |
|---|---|---|
| `tests/fixtures/v0_1/generate_fixtures.py:20` (`TEST_ONLY_FERNET_KEY`) | Fernet key for the v0.1 compatibility fixtures | It's base64url of the text `vnx-dna-test-only-fixture-key!!!`. It's public by design, so the fixtures can be regenerated and decrypted in tests. It protects nothing. |
| `tests/fixtures/v0_1/fixtures.json:5` (`test_only_fernet_key`) | The same key, recorded in the fixture manifest | Same key as above. |
| `tests/fixtures/v2_0/SHA256SUMS.json:5` (`"key.hex": …`) | The SHA-256 **checksum** of the fixture file `key.hex` | Not a key at all (false positive). The fixture key in `key.hex` is a test-only key for the v2.0 compatibility archives. |

Each finding appears in two commits (bc0c3de, 137383f) or one (76337d8), which makes 5 history entries. Their gitleaks fingerprints are listed in `.gitleaksignore`, so CI and local scans stay quiet about these exact lines only. A new key in any other place, or a changed value on these lines, is still reported. `vnx-security-scan` still lists them as INFORMATIONAL ("reviewed test key") and never hides them.

**Rules for test keys:** name them `TEST_ONLY_*` or `test_only_*`; derive them from an obviously fake text; never reuse a test key anywhere outside `tests/`; record every new one in this table and in `.gitleaksignore`, with its review date.

## MEDIUM scan findings (triaged 2026-10-04)

`vnx-security-scan` (2026-10-04, at 71b9bc0) reported 10 MEDIUM findings: 9 dependency advisories (pip-audit, 5 distinct
IDs; the scan output lists 4 of them twice for the same installed version) and 1 cppcheck warning. None is reachable from VNX-DNA.
The pins `cryptography>=44,<47` and `pytest<9` stay until the founder decides to lift them; until then these findings
are expected in every scan.

| Finding | What it is | Why VNX-DNA is not affected |
|---|---|---|
| `cryptography` PYSEC-2026-3552 (CVE-2026-69247, fixed in 50.0.0) | Bleichenbacher oracle in PKCS#7 `EnvelopedData` decryption (`pkcs7_decrypt_*`) | VNX-DNA never uses PKCS#7, S/MIME or RSA. It uses only AES-GCM, HKDF, HMAC, scrypt and SHA-256 (V3/V4) and Fernet (v0.1 compatibility). |
| `cryptography` PYSEC-2026-3553 (CVE-2026-69249, fixed in 49.0.0) | Exponential path building on certificate chains with duplicate self-signed intermediates (DoS) | VNX-DNA does not verify X.509 certificates. |
| `cryptography` PYSEC-2026-3554 (CVE-2026-69248, fixed in 49.0.0) | Wildcard DNS SAN escapes a CA's `permittedSubtrees` | VNX-DNA does not verify X.509 certificates. |
| `cryptography` GHSA-537c-gmf6-5ccf (fixed in 48.0.1) | Wheels bundle OpenSSL with the issues of the [9 June 2026 advisory](https://openssl-library.org/news/secadv/20260609.txt) | Of its 19 issues, the cipher-mode ones are AES-OCB (CVE-2026-45445) and AES-GCM-SIV/AES-SIV (CVE-2026-45446). VNX-DNA uses plain AES-GCM. The rest are in PKCS#7, CMS, X.509, OCSP, ASN.1 certificate parsing, PKCS#12, CMP, CRMF, FFC-DH and QUIC, none of which VNX-DNA calls. |
| `pytest` PYSEC-2026-1845 (CVE-2025-71176, fixed in 9.0.3) | Predictable `/tmp/pytest-of-{user}` directories let another local user cause DoS or possibly escalate | Development only. It is never installed with the package. It matters only on a shared multi-user host. The lab and CI runners are single-user. |
| cppcheck `uninitvar` at `src/vnxdna/v5/native/align.c:322` (`Lv`) | "Uninitialized variable: Lv" | A false positive. `Lv` is a vector of `LANES` lanes, and the loop above writes every lane (`Lv[l] = Li` for `l = 0 … LANES-1`) before `dp_group` reads it. cppcheck does not track per-lane writes to GCC vector types. The C code is left unchanged, so the kernel stays bit-exact with its golden hashes. |

Re-check this table when the pins change or a new advisory names an AES-GCM, HKDF, HMAC, scrypt or Fernet code path.

## V6 security model and fuzzing (Phase 6, 2026-10-05)

The V6 threat model is [security/V6_SECURITY_MODEL.md](security/V6_SECURITY_MODEL.md), and the fuzz campaign is
[security/V6_FUZZ_REPORT.md](security/V6_FUZZ_REPORT.md). There is no CRITICAL or HIGH finding.

**Open findings:**

| ID | Severity | Finding |
|---|---|---|
| V6-SEC-01 | MEDIUM | A forged superblock can claim a huge container, and decode time grows with the claimed group count. |
| V6-SEC-02 | MEDIUM | A full `vnx decode` ignores a given key, so a downgraded (unencrypted) pool decodes to SUCCESS. Extract still refuses it. |
| V6-SEC-03 | MEDIUM | No expected-archive check, so substitution or rollback is possible. |
| V6-SEC-05 | LOW | The manifest shown without a key is unauthenticated. |
| V6-SEC-06 | LOW | The spec §2.3.3 report fields are missing. |
| V6-SEC-09 | LOW | Implicit native buffer sizes. |
| V6-SEC-10 | LOW | Extract has a TOCTOU window and a prefix containment check. |
| V6-SEC-11 | LOW | Deterministic clear IDs and 16-bit tags. |
| V6-SEC-13 | LOW | No AEAD index-binding test. |
| V6-SEC-15 | INFO | Passphrases are not Unicode-normalised. |
| V6-SEC-21 | INFO | The superblock's reserved bytes are not checked. |

**Accepted:**
- V6-SEC-07: the scrypt default awaits a founder decision.
- V6-SEC-08: library paths taken from the environment, and the RS test hook.
- V6-SEC-16, V6-SEC-17, V6-SEC-20.

**Waiting for a push:** V6-SEC-12, the CI jobs for gitleaks, pip-audit and the fuzz smoke.

**Fixed in `work/v6-security`, each with a regression test written first:**
- V6-SEC-04: type-confused manifest fields;
- V6-SEC-22: deeply nested manifest JSON;
- V6-SEC-23: unbounded RS table caches;
- V6-SEC-14: this page's AAD sentence.

---

# V3 security — unchanged

V3 keeps the V2 cryptographic design (no primitive or label changed) and fixes the weaknesses the V2 audit found in
how it was used: two AES-GCM nonce-reuse paths on resumed stores, an unauthenticated checkpoint epoch, and several
input paths that allowed unbounded allocation or silent acceptance. See [V3 changes](#v3-changes) and
[V3_AUDIT.md](V3_AUDIT.md).

## Modes

| mode | how | provides | does not provide |
|---|---|---|---|
| **encrypted (recommended)** | `vnx-dna keygen -o key.txt`, then `--key-file key.txt` (or `VNXDNA_KEY`) | confidentiality of content, name, exact size and plaintext hashes; authentication of the manifest, both index tables and every chunk; tamper and wrong-key detection | hiding the approximate size and per-chunk compressibility; protection if the key leaks |
| unencrypted | no key | corruption detection at every layer (SHA-256), deterministic archives | any protection against deliberate modification: unkeyed digests can be recomputed by an attacker |

Everything fails closed. Wrong key → `WRONG_KEY`/`AUTHENTICATION_FAILED` (exit 4). Tampering → exit 4 (encrypted) or
exit 1/3 (unencrypted). In every case nothing is written under the output name.

## Primitives (all from `cryptography` / OpenSSL; nothing invented)

* **Master key**: 32 bytes from `os.urandom` (`vnx-dna keygen`, file mode 0600). Never stored in archives, never
  derived from a password.
* **Key separation**: HKDF-SHA256 with a fresh random 16-byte salt per archive → AEAD key, manifest MAC key, 64-bit
  key-check value. Labels start with `VNX-DNA/5`, so V1 and V2 keys differ even for the same master key and salt.
* **Streaming authenticated encryption**: AES-256-GCM per chunk, nonce = `(epoch·256 + domain)(4) ‖ chunk index(8)`,
  associated data = `"VNX-DNA/5 aead" ‖ archive_id ‖ (epoch·256 + domain) ‖ index ‖ chunk_count`. This is the standard chunked-AEAD
  construction: memory stays bounded by one chunk, and each chunk is independently authenticated.

| attack on the stream | detected by |
|---|---|
| modified ciphertext | GCM tag |
| reordered chunks | index in the nonce and AD |
| duplicated chunk | index in the nonce and AD (a copy decrypts only at its own position) |
| missing chunk / truncated archive | chunk count in every chunk's AD and in the HMAC-authenticated manifest; the index must list exactly `chunk_count` chunks |
| chunk spliced from another archive | archive ID in the AD (and a different key via the salt) |

  All five are exercised in `tests/v2/test_container_v2.py`.
* **Nonce management**: every encrypted archive has a new random salt (so a new AEAD key) and a random archive ID.
  Within an archive each (epoch, domain, index) is used once. A **resumed** store keeps the salt and archive ID of its
  checkpoint (all chunks must share them), verifies the completed chunks instead of re-encrypting them, and seals
  every chunk it writes in a **new epoch** (1 byte per chunk in the HMAC-authenticated chunk index, at most 255
  resumes). Since V3 the new epoch is written to the checkpoint **before** anything is sealed with it. The checkpoint
  itself is HMAC-authenticated, so its epoch cannot be rolled back. The two finalisation records (sealed content and
  plaintext index) are sealed in the newest chunk epoch (optional manifest feature `final-seal-epoch-v3`, written only
  when that epoch is not 0). Together these ensure no (key, nonce) pair is used for two plaintexts, even across
  repeated interruptions and a changed input with an unchanged size and mtime. Tested:
  `tests/v3/test_container_security_v3.py` (two consecutive resumes, a re-finalisation, a forged checkpoint rollback)
  and `SIGKILL` + `--resume` (`tests/v2/test_container_v2.py`).
* **Authenticated metadata**: HMAC-SHA256 over the canonical manifest (without `seal`), verified in constant time
  before anything else is trusted. The manifest records the SHA-256 of the chunk index and of the sealed plaintext
  index, so the HMAC covers both tables. Name, size and object SHA-256 are sealed with AES-GCM (domain 1), per-chunk
  plaintext sizes and hashes with domain 2.
* **Downgrade protection**: `format_version` and `required_features` are inside the HMAC. V2 labels and AD differ from
  V1's, so format-5 ciphertext cannot be decrypted as format 4 or the other way round. An unknown required feature
  or a newer container version is refused (exit 6), never guessed.
* **Wrong-key detection**: the HKDF key-check value distinguishes a wrong key from a tampered manifest. It allows no
  more than the MAC already does, which is testing a candidate key.

ECC is applied after encryption. Decoding DNA never needs the key, and recovered ciphertext is authenticated before
decryption.

## What is encrypted, what is visible

| data | unencrypted archive | encrypted archive |
|---|---|---|
| file content | clear (compressed) | AES-256-GCM per chunk |
| file name, exact size, object SHA-256 | clear in the manifest | sealed (AES-GCM, domain 1) |
| per-chunk plaintext size and SHA-256 | clear (plaintext index) | sealed (domain 2) |
| chunk size and count (size to within one chunk) | visible | visible |
| per-chunk stored size and codec (compressibility) | visible | visible (use `--compression none` to hide compressibility) |
| stored SHA-256 per chunk, AEAD epoch per chunk | visible | visible |
| format, encoder version, archive ID, profile, `created_at` (if set) | visible | visible |
| compression, ECC, strand and constraint parameters | visible | visible |
| salt, key-check value | – | visible |
| in the DNA | the same data as the container, plus addresses (archive tag = first 4 bytes of the archive ID, ECC group, shard) in every strand | same; payloads are ciphertext |

There is no built-in padding; to hide the size, pad the input.

## Key handling

* Keys are 32 random bytes from `os.urandom`, written by `vnx-dna keygen` with file mode 0600, and passed with
  `--key-file` or the `VNXDNA_KEY` environment variable. There are no passwords and no password KDF.
* Keys are never stored in archives, checkpoints, reports, errors or logs. The only key-derived values stored are the
  HKDF salt, the 64-bit key-check value and HMAC tags.
* The fixture key in `tests/fixtures/v2_0/key.hex` and the test keys are SHA-256 hashes of public strings. They protect
  nothing and exist only so that tests are deterministic.

## V3 changes

| issue (audit id) | V2 behaviour | V3 |
|---|---|---|
| nonce reuse across two resumes (C1) | the new epoch reached the checkpoint only at the next periodic checkpoint; a second interrupted resume reused the first resume's nonces for changed plaintext (XOR of plaintexts and the GHASH key leak) | epoch persisted before sealing |
| nonce reuse at re-finalisation (C2) | sealed content and plaintext index always used epoch 0 | newest chunk epoch; `final-seal-epoch-v3` |
| checkpoint rollback (C2) | the checkpoint had only an unkeyed SHA-256 | HMAC-SHA256 under the archive MAC key |
| padded containers accepted (C3) | body length never compared with `stored_size`; `verify` PASS | rejected; `verify` also checks `stored_sha256` |
| forged metadata lengths from DNA (D2) | a 4 KB strand file could demand GBs of memory before authentication | bounded by the metadata strands present |
| unbounded line reads (D3) | whole lines read before any length check (430 MiB for a 190 MiB line) | lines cut while reading (47 MiB) |
| `--report` overwrote any file (B4) | including the command's own input, without `--force` | refused unless `--force`; never an input or output of the command |
| output clobbering race (S2) | an output created between the existence check and the rename was replaced | hard-link publish refuses it |
| report replaced the key file (release review H2) | `--report key.txt --force` (or `--report S.fasta.vxidx`) was accepted | key files and DNA indexes count as inputs/outputs |
| symlinks at fixed temporary names (release review M2) | reports, DNA indexes, cluster files and decoded containers were written through a planted `.<name>.partial` symlink | private `mkstemp` files |
| key files (release review L1, L7) | `keygen --force` wrote through symlinks and truncated the old key on failure; `-k /dev/zero` read without limit | atomic, symlink-safe `keygen`; bounded, regular-file key reads |

## Hardening

* **Malicious input**: strict canonical JSON (duplicate keys, floats, NaN rejected) and a strict schema. Manifest
  ≤ 1 MiB. Index lengths are checked against the file size. The chunk count is at most 76,695,844, which is what the
  trailer's 32-bit index length can address (the manifest schema's 2²⁸ is not reachable). All index invariants are
  checked vectorised before use. The body must be exactly the chunks the index describes. Every stored chunk is
  SHA-256-checked before decompression. Decompression is bounded by the authenticated plaintext size (no bombs).
  Read lines are cut at 100,002 bytes while reading, and reads at 100 kb. Metadata streams from DNA may not claim
  more metadata groups than the strands present. When a pool contains several archives' metadata, only one that
  decodes, or the one named by `--archive-tag`, is used. The fuzz tests mutate bytes, fields and index entries
  (with valid digests and the trailer resealed, to reach the inner layers).
* **Path safety**: output paths come only from the command line. The stored name is metadata (plain file name,
  control characters and separators rejected), never a path.
* **Temporary files**: created with `mkstemp` (unique name, `O_EXCL`, never through a symlink, mode 0600) in the
  output's directory (or `--temp-dir`), removed on failure, and never readable under the final name before
  verification. The only fixed names are a resumable store's `<output>.partial`, `.partial.ckpt` and `.partial.idx`;
  stale ones are removed and the files re-created exclusively, and an input that is one of them is refused. (Before
  the V3 release review, reports, DNA indexes, cluster files and decoded containers used fixed `.<name>.partial`
  names opened with `O_TRUNC`, so anyone able to write to the output directory could redirect the write through a
  planted symlink.) Every output VNX-DNA writes, including reports, DNA indexes and `keygen` keys, has mode 0600;
  restored files do not keep the original file's mode. Decoder,
  cluster and sequence work directories are private `mkdtemp` directories, removed on exit.
* **Checkpoints** carry their own SHA-256 and, when encrypted, an HMAC under the archive MAC key. `--resume` refuses
  a changed input (path, size, mtime, and every completed chunk's plaintext hash), changed options, a different key,
  a checkpoint whose HMAC fails, a VNX-DNA 2.0 checkpoint, or a corrupted partial file. Without a checkpoint an
  interrupted store removes its partial files. A full disk is reported as `OUTPUT_ERROR` (exit 8).
* **No** `pickle`, `eval`, `exec` or `shell=True`. Subprocesses: `git` (provenance), and `sha256sum`/`cmp` in the
  benchmark harness, all with argument lists.
* Keys never appear in reports, errors or logs.
* **Key files** are read only if they are regular files of at most 4 KiB (`-k /dev/zero` used to read until memory
  ran out), and a key file readable or writable by group or others is reported with a warning. `keygen` writes a
  private temporary file and publishes it: it never writes through a symlink and never truncates an existing key when
  the write fails.
* **Signals**: SIGTERM and SIGHUP clean up like Ctrl-C (partial outputs, temporary directories, worker processes), and
  worker processes exit when their parent dies, even by SIGKILL.

## Known limitations

* Unencrypted archives have integrity against accidents only.
* **Resume after a full rollback.** A resume chooses its AEAD epoch above every epoch recorded in the checkpoint and
  in the index sidecar, including sidecar entries written after the checkpoint (a replayed older checkpoint is
  therefore harmless; tested). If the partial file, its sidecar *and* its checkpoint are all rolled back together
  (for example by restoring a snapshot) after a later resume sealed chunks, and the input is then changed without
  changing its size or modification time, the next resume could reuse that later resume's nonces. Do not resume
  from restored snapshots: start over without `--resume`. Fixing this without trusted state would need a random
  per-run nonce component, which is a format change (listed in [ROADMAP.md](ROADMAP.md)).
* Python cannot reliably erase key material from memory.
* A lost key means a lost archive (no escrow).
* The simulated channel is not an adversary model. A malicious strand pool can make decoding *fail*, which is
  denial of service, but it cannot make it output wrong bytes without breaking SHA-256 or, with a key, AES-GCM/HMAC.

Report vulnerabilities privately to the repository owner.
