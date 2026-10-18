# V14 security model

Phase 4 specification (master directive §11; [V14_MASTER_PLAN.md](V14_MASTER_PLAN.md) P4). Implementation:
`src/vnxdna/native/rust/vnxd/src/{seal,authz,audit}.rs`, `src/vnxdna/device/{seal,worker}.py`. Failure handling is
in [V14_FAILURE_AND_RECOVERY_MODEL.md](V14_FAILURE_AND_RECOVERY_MODEL.md).

## 1. Assets, trust boundary, threats

| asset | where |
|---|---|
| extent data | host memory while served; the backend (image file, DNA simulator, later physical media) at rest |
| the sealing key | a key file read only by the sealing worker process |
| the volume journal (map, digests) | host-local directory of the service |
| management of the device | the management socket |
| the audit trail | host-local audit log |

Trusted: the host's kernel, the service user, the service's own directories (journal, staging, audit log, key file).
**Not trusted**: the backend and everything that handles stored objects outside the host (a storage provider, a
synthesis or sequencing vendor, a stolen disk image), and any local user who is not granted a management role.

| threat | control | section |
|---|---|---|
| a backend reads the data | extents sealed with AES-256-GCM before they leave the service | §3 |
| a backend alters, swaps, replays or truncates objects | authenticated sealing bound to device, extent and generation; SHA-256 of every extent in the journal checked at every read | §3 |
| the device is opened with the wrong key and mixes keys | the key check stored with the backend's binding marker, checked at open (UNAUTHORIZED) before anything is read or deleted | §3 |
| an unauthorised local user manages the device (shutdown, stats) | socket permissions + token roles; every denial audited | §4 |
| a local user or a partial edit hides what happened | hash-chained, fsynced, private (0600) audit log; the service user can only be caught by an external anchor of the head hash | §5 |
| data crosses between requests through reused buffers | VNX-RAM zeroes every buffer before reuse ([V14_RAM_AND_CACHE.md](V14_RAM_AND_CACHE.md) §1) | — |
| malformed protocol input crashes or misleads the service | strict parsers with typed errors ([V14_CONTRACTS.md](V14_CONTRACTS.md) §6), staging names never paths | §6 |

Open item for the founder: the `cryptography` pin (`>=44,<47`; the lab has 46.0.7) predates advisories fixed in
48.0.1 (one concerns the bundled OpenSSL; the others are X.509/PKCS7, which sealing does not use). Raising the pin
is a dependency decision recorded with the V11 crypto-pin question; it is not changed in P4.

Not in scope of V14 (stated, not claimed): protection against a compromised host or service user (they hold the key
and the plaintext); hiding extent sizes, write timing or which extents change (visible to the backend); key rotation
of an existing device (a new device is created and the data copied); hardware key storage (TPM/HSM).

## 2. Cipher policy

No new cipher and no cipher implementation in `vnxd`: sealing uses the primitives of the existing VNX-DNA security
layer (`cryptography`/OpenSSL AES-256-GCM, HKDF-SHA256), the same ones as the archive format. The Rust service never
holds the key: it calls a sealer through the `Sealer` trait; the production sealer is a pool of Python worker
processes. Decision recorded for the Rust/Python boundary: **sealing runs in the codec worker** (`vnx.codec-ipc/1`
operations `key_check`, `seal_extent`, `open_extent`; the codec operations join it in P6), not in `vnxd`.

## 3. Sealing at the extent boundary

A volume created with superblock flag `FLAG_SEALED` runs only over `SealedMedia` (and an unsealed volume never does;
both mismatches are INVALID_ARGUMENT at create and open). `SealedMedia` wraps the backend adapter: `put` seals,
`get` opens. Format `vnx.sealed-extent/1` (Python `vnxdna.device.seal`, little-endian):

```
offset  size  field
     0     4  "VNXS"
     4     2  version 1
     6     2  algorithm 1 = AES-256-GCM
     8    16  salt (random, new for every object)
    24     8  key check (first 8 bytes of HKDF(master, salt = device_id, info = "VNX-DNA/14 key check"))
    32     8  plaintext length n
    40     n  ciphertext
  40+n    16  GCM tag
```

* key = HKDF-SHA256(master, salt = the object's salt, info = "VNX-DNA/14 extent key" ‖ device_id ‖ extent ‖
  generation). Every object has its own key, so the fixed 96-bit nonce is never used twice under one key — even if a
  device were re-created with the same id and key, or a generation number repeated. Nonce safety does not depend on
  any counter the service keeps.
* associated data = the 40-byte header ‖ "VNX-DNA/14 sealed extent" ‖ device_id ‖ extent ‖ generation (u64
  big-endian): an object moved to another extent, generation or device, or an older generation replayed under the
  current one, fails authentication.
* `open` returns VERSION_MISMATCH for another format version and CORRUPTION for everything else that does not
  authenticate, a key-check mismatch in an object header included (a tampered header or another device's object is
  damage to this extent). It never returns bytes that failed the tag. The wrong key for a device is reported once, as
  UNAUTHORIZED, from the marker's key check at open. UNAUTHORIZED and CORRUPTION never count against the backend's
  health, so a backend cannot push the device into read-only mode by serving bad objects.
* the backend's binding marker is stored as marker ‖ 32-byte key check: opening a sealed device with another key, or
  an unsealed backend through the sealing adapter, is UNAUTHORIZED at `open`, before recovery deletes anything.
* the journal keeps the plaintext's SHA-256 per extent (the volume verifies every read against it). The journal is
  host-local, inside the trust boundary; it is not stored on the backend.

The worker (`python -m vnxdna.device.worker --key-file K --staging D`):
* opens the key file once without following symlinks and checks the opened file: regular, owned by the worker's
  user, no group or other access (UNAUTHORIZED otherwise), holding 64 hex or 44 base64 characters, i.e. a generated
  key (a raw 32-byte passphrase is refused: the key checks would let an attacker test passphrases offline at HKDF
  speed); reads the key once and never sends it;
* refuses a staging directory that is a symlink, not owned by its user, or open to group or others;
* opens files only inside the staging directory by validated names, never following symlinks, only regular files;
  checks every input against its announced SHA-256; creates outputs exclusively with mode 0600 and removes a partial
  one; answers every failure with a typed error (unexpected exceptions are INTERNAL).

`vnxd` (`WorkerSealer`) refuses the same unsafe staging directories, starts the workers with the staging directory
as their working directory (`python -m` imports from the working directory first, so the service's own working
directory is never used), names staging files with a per-process prefix, writes inputs with mode 0600, reads only
regular-file outputs, removes both files after every call, and at start removes any `seal-*` file a crashed process
left (files of processes that are still running are kept, so several sealers may share the directory). Errors a
worker reports about its own files or failures (a missing staging input, an existing output, INTERNAL) reach the
volume as BACKEND_UNAVAILABLE, never as data damage; only CORRUPTION, UNAUTHORIZED and VERSION_MISMATCH pass
through, and per-object key-check or version mismatches are CORRUPTION of that extent. Plaintext therefore exists on the host in the private staging directory for the duration of one call, or until
the next start after a crash; tmpfs is recommended so that it never reaches a disk. Worker output lines are bounded
(1 MiB), and objects larger than an extent plus the 56-byte overhead are refused before they are staged. A worker that exits, hangs
(timeout, default set by the service) or breaks the protocol is killed and replaced; its request fails with
BACKEND_UNAVAILABLE or TIMEOUT, which the fallback layer retries.

Overhead per extent: 56 bytes (header + tag) and two staging file transfers; P9 measures the throughput cost.

## 4. Authorisation of management operations

The management socket (P8) is a Unix socket with mode 0660 owned by the service user and its group: who may
connect is decided by the file system. Every session then authenticates:

| role | operations |
|---|---|
| (none) | `hello`, `authenticate` |
| `reader` | + `capacity`, `health`, `stats` |
| `admin` | + `shutdown` |

* the policy file lists `<role> <SHA-256 hex of a token>` lines; it must not be writable by group or others nor
  readable by others (refused with UNAUTHORIZED otherwise). Tokens are random secrets of at least 32 bytes (shorter
  tokens never authenticate); the policy holds only their digests;
* `authenticate` compares the token's digest with every entry in constant time;
* an operation the role does not allow is UNAUTHORIZED (EPERM) and audited (`mgmt_denied`); a failed authentication
  is audited (`mgmt_auth_failed`); after 3 failed authentications or 16 refused operations the session refuses
  everything (so one session cannot flood the audit log); granted authentications (`mgmt_auth_granted`) and every
  `shutdown` (`mgmt_shutdown`) are audited with the peer description. Limiting guesses across sessions (per peer) is
  the socket server's job (P8); tokens are 256-bit random values (generate them, e.g. `python -c "import secrets;
  print(secrets.token_urlsafe(32))"`), so guessing is hopeless anyway;
* the policy file is opened once, refused if it is a symlink, and its mode is checked on the opened file;
* the first request of a session must be `hello` (PROTOCOL otherwise); malformed requests get typed errors.

Peer credentials (SO_PEERCRED) are not read in V14: the crate has no `unsafe` code and stable Rust's standard library
does not expose them. The audit records the transport's peer description; P8 records the uid when available.

## 5. Audit log

Append-only JSON lines, created with mode 0600 (a symlink is refused), one event per line: canonical JSON of `{detail, event, prev, seq, ts_ms}`; `prev` = SHA-256
of the previous line (64 zeros for the first), `seq` = 1, 2, …; every event is fsynced. `open` verifies the whole
chain: an edited, removed, reordered or non-canonical line is CORRUPTION (the service refuses to start; the file is
left untouched); a torn last line (no newline) is cut; a failed append is cut back to the last complete line (or
poisons the log until it is reopened), so a fragment is never followed by another line. The chain is not keyed: it
detects partial edits by anyone who cannot rewrite the whole file; the service user can rewrite it consistently.
Removing the newest lines, or a whole consistent rewrite, cannot be detected from the file
alone: `AuditLog::head()` returns the event count and head hash, which the P8 service exports in `stats` so an
external copy can anchor them.

Events in V14: `backend_state` (health transitions), `retry_exhausted`, `mgmt_auth_granted`, `mgmt_auth_failed`,
`mgmt_denied`, `mgmt_shutdown`; P8 adds device start/stop and recovery reports. Events that cannot be written are
never dropped silently: the caller gets the error, or (inside the media layer) it is reported on stderr.

## 6. Untrusted input

Every byte string from outside the service is parsed by strict, bounded parsers that return typed errors:
records and journals ([V14_CONTRACTS.md](V14_CONTRACTS.md) §3, golden and bit-flip tests), protocol messages (§6
there: depth, size, integer range, duplicate keys, surrogates), sealed objects (§3 here), policy files, audit logs.
Staging names are validated names, never paths (`../etc/passwd` is PROTOCOL).

## 7. Tests (P4)

`cargo test` (vnxd): sealing over a test-only stand-in sealer (no plaintext on the backend; moved, replayed and
tampered objects are CORRUPTION; the wrong key UNAUTHORIZED at open with nothing collected; sealed/unsealed
mismatches refused), authorisation (every op per role, denials and failures audited, lock-out after 3 failures,
short tokens, strict and private policy files), audit (chain survives reopen; edit, removal, reordering, first-line
removal and non-canonical lines detected; torn last line cut). `tests/v14/test_device_seal.py`: the format against an
independent computation with the `cryptography` primitives, every alteration refused with its code, the worker
protocol (hello, key check, seal/open, SHA-256 mismatch, existing output, path traversal, codec ops refused,
malformed lines), key-file permissions, and `vnxd` end to end through the real worker (`cargo test --test
seal_worker -- --ignored`: a sealed volume of 8 extents, no plaintext on the backend, a moved object refused, the wrong
key refused at open, no staging file left, an unstartable worker BACKEND_UNAVAILABLE).
