# VNX-Secure — Architecture

VNX-Secure is a defensive security control plane for VNX-DNA. It authenticates, authorizes, detects, contains, audits
and recovers. It never acts against another system: there is no scanning, exploitation or retaliation of any kind.

**VNX-Secure is not claimed to be mathematically or absolutely unhackable.** What it does and does not protect is listed
in [VNX_SECURE_LIMITATIONS.md](VNX_SECURE_LIMITATIONS.md).

Status labels used in all VNX-Secure documents:

| Label | Meaning |
|---|---|
| IMPLEMENTED | code exists in `src/vnxdna/secure` |
| TESTED | covered by `tests/secure` (unit, property/fuzz, integration) |
| SIMULATED | exercised by the local attack simulator with synthetic events only |
| ARCHITECTURAL | an interface or design only; no working mechanism |
| NOT VALIDATED | not tested against real attackers, real traffic or an external review |

## 1. Placement in the code base

`vnxdna.secure` is a new package in layer 6 of the V6 package structure (beside `providers` and `benchmark`). It
imports only `vnxdna.core` and `vnxdna.archive`. The `vnx` CLI reaches it through `vnxdna.sdk.security`, so rule R6
("commands import only sdk and core") still holds; `tests/architecture/test_layers.py` checks this. No existing codec,
archive or decoder module was modified: the archive gate calls the existing archive layer unchanged.

## 2. Components

```
            vnx security …  (CLI)   ·   vnxdna.sdk.security   ·   SecureArchive (gate)
                                   │
                    ┌──────────────▼──────────────────────────────┐
                    │  ControlPlane (control.py)                   │
                    │  identity · policy · state · integrity · audit│
                    └───┬──────────────┬───────────────┬──────────┘
                        ▼              ▼               ▼
                    Detection      Containment      Recovery
                    detect.py      contain.py       control.recover
                    risk.py        (isolation        state.py
                                    interface)
                        └──────────────┬───────────────┘
                                       ▼
                         VERIFIED (audit + integrity + policy)
```

| Component | Module | Status |
|---|---|---|
| Identity and authentication (scrypt secrets, HMAC tokens, revocation) | `identity.py` | IMPLEMENTED, TESTED |
| Replay protection (nonce + timestamp window) | `identity.ReplayGuard` | IMPLEMENTED, TESTED |
| Authorization (deny-by-default policy, explicit deny wins) | `policy.py` | IMPLEMENTED, TESTED, fuzzed |
| Security events and findings | `events.py` | IMPLEMENTED, TESTED |
| Deterministic detectors (16 rules) | `detect.py` | IMPLEMENTED, TESTED, fuzzed |
| Explainable risk score | `risk.py` | IMPLEMENTED, TESTED |
| Containment playbook (idempotent actions) | `contain.py`, `control._contain` | IMPLEMENTED, TESTED |
| Session isolation, credential revocation, rate limiting | `control.py` | IMPLEMENTED, TESTED |
| Network isolation interface | `contain.NetworkIsolator` | ARCHITECTURAL (only a recording stub) |
| Sandboxing of untrusted parsing (forked child, RLIMIT_AS/RLIMIT_CPU, timeout) | `sandbox.py`, `SecureArchive(sandbox=True)` | IMPLEMENTED, TESTED (resource/fault isolation, not a code-execution boundary) |
| Canary / deception resources | `deception.py` | IMPLEMENTED (names only), TESTED |
| HMAC-chained audit log | `audit.py` | IMPLEMENTED, TESTED, fuzzed |
| Evidence preservation (bundles, quarantine copies) | `control._preserve`, `_quarantine` | IMPLEMENTED, TESTED |
| Integrity snapshot | `integrity.py` | IMPLEMENTED, TESTED |
| Security state machine and recovery | `state.py`, `control.recover` | IMPLEMENTED, TESTED |
| Secure archive access (random access, verify, list, locate) | `gate.py` | IMPLEMENTED, TESTED, fuzzed |
| Crypto agility registry and AEAD interface | `crypto_agility.py` | IMPLEMENTED (registry); PQC PLANNED |
| VNX-RAM interface | `vnxram.py` | ARCHITECTURAL (+ in-process reference implementation) |
| VNX-Q interface | `vnxq.py` | ARCHITECTURAL |
| Attack simulator and security benchmark | `simulator.py` | IMPLEMENTED; results SIMULATED |
| CLI `vnx security …` | `commands/cli.py`, `sdk/security.py` | IMPLEMENTED, TESTED |

## 3. Request path (zero trust)

Every request carries `token`, `session`, `nonce`, `ts` and names a resource and an operation. `ControlPlane.authorize`
evaluates, in order, and denies at the first failure:

1. token: format, HMAC, expiry, revocation of the token, its session and its principal;
2. session binding: the token's session must equal the request's session;
3. replay: fresh nonce, timestamp within ±120 s;
4. rate: requests per session per minute (600 by default; 10 after a RATE_LIMIT action);
5. resource lock (set by containment);
6. capability: `admin`/`recover` need the `admin` capability, `write` needs `writer`;
7. policy, given the token's capabilities, the integrity state, the session's risk level and the system state.

Each decision, allow or deny, is an audited event. Earlier authentication is never assumed to hold: steps 1–7 run on
every request.

## 4. Archive gate and random access

`SecureArchive` wraps the existing archive layer (`vnxdna.archive.operations` and `container`). Random access is not
re-implemented. The existing layer already fails closed on a corrupted chunk body (stored SHA-256), wrong key
(AES-256-GCM tag), chunk-ID mismatch, malformed tables, truncated files and bad Merkle proofs. The gate adds:

- authorization before any byte is read;
- a quarantine check on the archive's SHA-256, so quarantined bytes are refused under any file name. The hash is cached
  per file identity and stat, so a large archive is hashed once, not per request; any change re-hashes it;
- a Merkle inclusion-proof check against the manifest root before `read_chunk` returns;
- a file-hash check before `read_file` returns;
- conversion of every archive-layer exception into an audited `SecurityFailure` (`malformed`, `integrity`,
  `key_mismatch`). Detection and containment then run on that event. No partial data is returned.

## 5. Persistence (`vnx security init`)

The VNX-Secure home is `$VNX_SECURE_HOME`, or `~/.vnx-secure` by default. Its directory mode is 0700 and it holds:

- `secure.key`: 32 random bytes, mode 0600;
- `policy.json` and `policy.good.json`;
- `principals.json`, which is MAC'd;
- `baseline.json`, which is MAC'd;
- `audit.log`;
- `state.json`, which is MAC'd and records the epoch, the state, containment, revocations and the audit anchor;
- `evidence/` and `quarantine/`, which hold mode-0400 copies.

Sub-keys are `HMAC-SHA256(master, "vnx-secure/<label>")`. The token key is rotated per epoch. A state or principals
file that does not authenticate is treated as hostile: the control plane starts in ISOLATED, or with no principals.

## 6. Deception

Decoy names live in the `deception:` namespace and are listed beside real resources. They hold no data, credentials or
secrets. The default policy denies them to every role, so any request for one raises a CRITICAL finding and the
session is contained. Nothing in the deception code opens a socket or touches the file system.

## 7. Recovery

See the security model, §7. Recovery never trusts the current state, and it returns to VERIFIED only after these
pass: the audit chain (or a new chain anchored to a preserved copy of the old one), the known-good policy MAC, a
credential rotation, the requested archive checks, and the integrity snapshot.

## 8. VNX-RAM (ARCHITECTURAL)

`vnxram.MemoryGuard` defines `secure_memory_region`, `authorize_memory_access`, `revoke_memory_access`,
`verify_memory_integrity` and `quarantine_memory_region`. `SoftwareMemoryGuard` is an in-process reference over byte
buffers with HMAC tags, used only to test the contract. No VNX-RAM hardware exists, and this protects nothing outside
the Python process.

## 9. VNX-Q (ARCHITECTURAL)

`vnxq.QuantumSubsystemSecurity` names the controls a future QPU integration would need: job authorization, job
isolation, subsystem attestation and control-path isolation. Every method raises `NotImplementedError`. No quantum
hardware or quantum cryptography is used, and none is claimed to make VNX secure. Post-quantum classical cryptography
is tracked as PLANNED in the crypto registry (security model, §9).
