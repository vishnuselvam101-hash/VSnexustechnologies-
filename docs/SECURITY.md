# Security

## Modes

| mode | how | provides | does not provide |
|---|---|---|---|
| **encrypted (recommended)** | `vnx-dna keygen -o key.txt`, then `--key-file key.txt` (or `VNXDNA_KEY`) on `store` | confidentiality of content, name, size and hashes; authentication of all metadata and data; tamper and wrong-key detection | anonymity; hiding the approximate size (see below); protection if the key leaks |
| unencrypted (research / deterministic) | no key | corruption detection (SHA-256 digests at every level) and deterministic output | **any** protection against deliberate modification. An attacker can rewrite the data and recompute every unkeyed digest consistently. |

The archive fails closed. A key-check mismatch is reported as *wrong key*, an HMAC or GCM failure as
*authentication failed* (exit 4), and nothing is written.

## Primitives (all from the maintained `cryptography` package / OpenSSL)

- **Master key:** 32 random bytes from `os.urandom` (`vnx-dna keygen`), given as base64url or hex. VNX-DNA never
  stores keys in archives and never derives keys from passwords.
- **Key separation:** HKDF-SHA256 with a fresh random 16-byte salt per archive derives three subkeys: `aead`
  (AES-256-GCM), `mac` (HMAC-SHA256 over the manifest) and `check` (a 64-bit key-check value).
- **Chunk encryption:** AES-256-GCM, 96-bit nonce = `domain(4) ‖ index(8)`, and associated data
  `"VNX-DNA/4 aead" ‖ archive_id ‖ domain ‖ index ‖ chunk_count`. The AD binds each ciphertext to its archive,
  position and count, so swapping, reordering, truncating or cross-archive splicing is detected.
- **Nonce uniqueness:** every encrypted archive gets a new random salt, and therefore a new AEAD key, plus a random
  archive ID. Within an archive, each (domain, index) pair is used once. The encoder never lets a caller choose the ID
  or salt for encrypted archives.
- **Sealed content:** name, size, SHA-256 and per-chunk plaintext hashes are sealed with AES-GCM (domain 1).
- **Manifest authentication:** HMAC-SHA256 over the canonical manifest minus the `seal`. It is verified in constant
  time before any content is used.
- **Key commitment / wrong-key reporting:** the key-check value (HKDF output) lets the decoder distinguish a wrong key
  from a tampered manifest. It reveals no more than the MAC already allows, which is testing a candidate key.

Error correction is applied **after** encryption. Decoding DNA therefore never needs the key, and the recovered
ciphertext is authenticated before decryption.

## What remains visible in an encrypted archive

Anyone holding the container or the DNA can read:
- the format and encoder version;
- the archive ID and `created_at` (if you set it);
- the chunk size and chunk count (so the plaintext size, to within one chunk);
- each chunk's stored size (which reveals per-chunk compressibility), and the stored SHA-256 of each chunk;
- the compression algorithm and level;
- the ECC, strand and constraint parameters;
- the salt and the key-check value.

Hidden: the file name, the exact size, the plaintext SHA-256 values, and the content itself.

If the size or compressibility must be hidden, pad the input and use `--compression none`. There is no built-in
padding.

## Hardening measures in the code

- Every parser has size limits: 64 MiB manifest, ≤ 2⁴⁰ container, 100 kb reads, 8 GiB read files and 4 GiB input.
  JSON is parsed strictly (duplicate keys, NaN and floats are rejected), and a pydantic strict schema forbids extra
  fields.
- Decompression is bounded by the authenticated plaintext size (no decompression bombs).
- Outputs are written atomically (a temp file in the same directory, then `os.replace`) and **only after** all
  verification. Existing files are never overwritten without `--force`.
- The stored file name is metadata only. `restore` always writes to the path the user gives, so a name like
  `../../x` cannot cause path traversal (such names are also rejected in the manifest).
- There is no `pickle`, `eval` or shell use. The only subprocess is `git rev-parse` / `git status` with an argument
  list, used for provenance.
- Key files written by `keygen` are created with mode 0600, and keys never appear in reports or errors.
- Test keys in `tests/` are derived from public strings and protect nothing.

## Known limitations

- Unencrypted archives have no authenticity. Use a key when tampering matters.
- Python cannot reliably erase key material from memory.
- A lost key makes an encrypted archive unrecoverable. There is no key escrow.
- The simulated DNA channel is not an adversary model. Physical DNA has its own security questions (contamination,
  forensic recovery) that are out of scope.

## Reporting

Report vulnerabilities privately to the repository owner rather than in a public issue.
