# Security (V2)

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
  resumes). Some chunks may have been sealed and written after the last checkpoint and then cut off. When they are
  sealed again they get a different nonce, even if the input changed in between. So no (key, nonce) pair is ever
  used for two plaintexts (tested with `SIGKILL` + `--resume`).
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

## Visible in an encrypted archive

Format and encoder version, archive ID, profile, `created_at` if set, chunk size and count (so the size to within
one chunk), each chunk's stored size and codec (per-chunk compressibility), stored SHA-256 per chunk, compression
settings, ECC/strand/constraint parameters, salt and key-check value. **Hidden:** file name, exact size, plaintext
hashes, content. To hide compressibility use `--compression none`. To hide the size, pad the input (no built-in
padding).

## Hardening

* **Malicious input**: strict canonical JSON (duplicate keys, floats, NaN rejected) and a strict schema. Manifest
  ≤ 1 MiB. Index lengths are checked against the file size and a 2²⁸-chunk limit. All index invariants are checked
  vectorised before use. Every stored chunk is SHA-256-checked before decompression. Decompression is bounded by the
  authenticated plaintext size (no bombs). Reads are truncated at 100 kb. Metadata streams with implausible lengths
  are rejected. Pools containing metadata of several archives are refused. The fuzz tests mutate bytes, fields and
  index entries (with valid digests and trailer resealed, to reach the inner layers).
* **Path safety**: output paths come only from the command line. The stored name is metadata (plain file name,
  control characters and separators rejected), never a path.
* **Temporary files**: created with `mkstemp` or `O_CREAT|O_TRUNC` mode 0600 in the output's directory (or
  `--temp-dir`), removed on failure, and never readable under the final name before verification. Decoder,
  cluster and sequence work directories are private `mkdtemp` directories, removed on exit.
* **Checkpoints** carry their own SHA-256. `--resume` refuses a changed input (path, size, mtime, and every
  completed chunk's plaintext hash), changed options, a different key, or a corrupted partial file.
* **No** `pickle`, `eval`, `exec` or `shell=True`. Subprocesses: `git` (provenance), and `sha256sum`/`cmp` in the
  benchmark harness, all with argument lists.
* Keys never appear in reports, errors or logs.

## Known limitations

* Unencrypted archives have integrity against accidents only.
* Python cannot reliably erase key material from memory.
* A lost key means a lost archive (no escrow).
* The simulated channel is not an adversary model. A malicious strand pool can make decoding *fail*, which is
  denial of service, but it cannot make it output wrong bytes without breaking SHA-256 or, with a key, AES-GCM/HMAC.

Report vulnerabilities privately to the repository owner.
