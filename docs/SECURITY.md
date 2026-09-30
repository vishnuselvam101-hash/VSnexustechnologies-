# Security (V3)

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
* **Temporary files**: created with `mkstemp` or `O_CREAT|O_TRUNC` mode 0600 in the output's directory (or
  `--temp-dir`), removed on failure, and never readable under the final name before verification. Decoder,
  cluster and sequence work directories are private `mkdtemp` directories, removed on exit.
* **Checkpoints** carry their own SHA-256 and, when encrypted, an HMAC under the archive MAC key. `--resume` refuses
  a changed input (path, size, mtime, and every completed chunk's plaintext hash), changed options, a different key,
  a checkpoint whose HMAC fails, a VNX-DNA 2.0 checkpoint, or a corrupted partial file. Without a checkpoint an
  interrupted store removes its partial files. A full disk is reported as `OUTPUT_ERROR` (exit 8).
* **No** `pickle`, `eval`, `exec` or `shell=True`. Subprocesses: `git` (provenance), and `sha256sum`/`cmp` in the
  benchmark harness, all with argument lists.
* Keys never appear in reports, errors or logs.

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
