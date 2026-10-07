# VNX-Secure — Security Model

**VNX-Secure is not claimed to be mathematically or absolutely unhackable.** This document states exactly what each
mechanism does and the parameters it uses. Every number below is a constant in `src/vnxdna/secure`.

## 1. Fail-closed rules

| Situation | Behaviour |
|---|---|
| Unauthorized access | DENY (audited `deny`, code `policy` / `capability`) |
| Invalid or expired credential | DENY (`bad_signature`, `malformed_token`, `expired`, `*_revoked`) |
| Integrity mismatch (archive) | FAIL (`SecurityFailure`, no data returned) |
| Integrity mismatch (components / policy / audit) | integrity state ≠ VERIFIED ⇒ reads denied; CRITICAL ⇒ ISOLATED |
| Unknown security state | treated as ISOLATED; policy denies with "require re-authentication" |
| Corrupted security log | ALERT, chain not trusted, not extended, system ISOLATED until recovery |
| State / principals file MAC mismatch | ISOLATED / no principal can log in |
| Unreadable policy | empty (deny-all) policy, ISOLATED |

## 2. Events

`SecurityEvent` fields:

| Field | Content |
|---|---|
| `event_id` | `ev-<epoch>-<seq>` |
| `timestamp` | |
| `session_id` | |
| `principal` | |
| `resource` | |
| `operation` | |
| `result` | `allow` / `deny` / `error` / `observed` |
| `severity` | |
| `detector` | |
| `evidence` | a dict: `code`, `tid`, `error`, `input_sha256`, ... |
| `response` | |
| `integrity_state` | |
| `kind` | `auth` / `request` / `integrity` / `config` / `finding` / `containment` / `evidence` / `recovery` / `admin` |

Events have a canonical JSON form: sorted keys, no whitespace, ASCII.

## 3. Identity

- **Secrets:** scrypt, N = 2^14, r = 8, p = 1, 16-byte salt, 32-byte output.
  - A login for an unknown principal is checked against a dummy hash, so it takes about as long as a known one.
- **Tokens:** `vnxt1.<payload>.<HMAC-SHA256>`, with time to live (TTL) 900 s.
  - The payload holds `tid`, `sub`, `ses`, `caps`, `iat` and `exp`.
  - Validation checks the MAC (constant-time compare), the expiry, and revocation by token, by session and by
    principal (tokens issued at or before the revocation time).
- **Replay protection:**
  - the nonce must be 16–128 characters;
  - |now − ts| ≤ 120 s;
  - a nonce is remembered for 600 s, up to 100,000 nonces.
  - Because the window is at least twice the skew, a nonce evicted early is still refused by the timestamp check.
  - The nonce memory is in-process: a restart forgets it, and only the timestamp check then remains.

## 4. Authorization

- Every request needs an `allow` rule that matches its role, resource (`fnmatch`) and operation. No `deny` rule may
  match; explicit deny wins.
- The integrity state must be in the rule's `integrity` list (default: VERIFIED), and the session's risk level must
  be ≤ the rule's `max_risk`.
- The system state limits operations before any rule is considered:

| State | Operations |
|---|---|
| TRUSTED / VERIFIED | all |
| DEGRADED | everything except `write` |
| SUSPICIOUS | `list`, `verify`, `admin`, `recover` |
| ISOLATED | `admin`, `recover` |
| INTEGRITY_CHECK / RECOVERY | `recover` |

The default policy has four rules:

- `deny-deception`: every role, `deception:*`;
- `reader`: `list` / `verify` / `locate` / `read` / `extract` on `archive:*`, integrity VERIFIED, risk ≤ MEDIUM;
- `writer`: adds `write`, risk ≤ LOW;
- `admin`: `admin` / `recover` / `verify` / `list` on `*`, any integrity, risk ≤ HIGH.

## 5. Detection and risk

Detection is deterministic: the same events in the same order give the same findings. Tests check this on recorded
events (`test_detection_is_deterministic_on_replay`) and by fuzzing (5,000 event streams). Windows use event
timestamps.

| Detector | Rule | Severity |
|---|---|---|
| `auth_failures` | ≥ 5 failed logins for one principal in 300 s | HIGH |
| `revoked_credential_use` | a revoked token, session or principal is presented | HIGH |
| `replay` | nonce reused, or timestamp outside the skew | HIGH |
| `token_misuse` | token presented for another session | HIGH |
| `session_transition` | a session id used by a second principal | HIGH |
| `integrity_monitor` | integrity check not VERIFIED | CRITICAL |
| `config_change` | unexpected configuration change event | HIGH |
| `request_rate` | > 120 requests per session in 60 s | MEDIUM |
| `access_pattern` | > 50 distinct resources per session in 300 s | MEDIUM |
| `deception` | any request for `deception:*` | CRITICAL |
| `privilege_escalation` | operation beyond the token's capabilities | HIGH |
| `authz_failures` | ≥ 5 policy or capability denials per session in 300 s | HIGH |
| `unexpected_key_usage` | archive opened with a key it was not sealed with | HIGH |
| `malformed_input` | malformed archive or input (HIGH from the third in 300 s) | MEDIUM / HIGH |
| `tamper` | archive integrity failure (CRITICAL from the second in 300 s) | HIGH / CRITICAL |

Window detectors fire at most once per window per key. The risk score is

```
risk(subject) = Σ points[severity]   over the subject's findings in the last 900 s
```

with LOW = 5, MEDIUM = 20, HIGH = 50 and CRITICAL = 80 points. Levels are:

- LOW: under 20;
- MEDIUM: 20 to 49;
- HIGH: 50 to 79;
- CRITICAL: 80 and above.

With these values:

- one HIGH finding gives HIGH;
- one CRITICAL finding gives CRITICAL;
- two HIGH findings, or four MEDIUM findings, give CRITICAL.

Each assessment lists its findings and a per-category breakdown. The categories are authentication anomalies,
authorization violations, integrity failures, rate anomalies, policy violations, input anomalies and deception hits.

## 6. Containment

The playbook maps the subject's risk level to actions:

| Level | Actions |
|---|---|
| LOW | none |
| MEDIUM | RATE_LIMIT (10 per minute), PRESERVE_EVIDENCE |
| HIGH | the MEDIUM actions, plus REQUIRE_REAUTHENTICATION (revokes the principal's existing tokens), CONTAIN_SESSION (revokes the session), REVOKE_TOKEN, QUARANTINE_INPUT |
| CRITICAL | the HIGH actions, plus REVOKE_PRINCIPAL_TOKENS + LOCK_PRINCIPAL_LOGIN (900 s), LOCK_RESOURCE, ISOLATE_WORKLOAD; the system moves to DEGRADED |

Two further rules:

- `auth_failures` always locks the principal's login for 900 s.
- `integrity_monitor` and `config_change` trigger ENTER_RECOVERY: the system goes to ISOLATED, and evidence is
  preserved.

Every action is recorded as an audited `containment` event that names:

- the finding and the risk arithmetic (`why`);
- the triggering event ids (`evidence`);
- the playbook rule (`policy`).

Actions are idempotent: re-applying a finding changes nothing (`changed=false`), as `test_containment_is_idempotent`
checks.

Evidence bundles contain:

- the finding;
- the risk assessment;
- the triggering event;
- the cited events;
- the audit head and count;
- the state.

They are stored canonically and named by SHA-256, with mode 0400 on disk. Their hash is itself written to the audit
chain. Quarantine keeps a mode-0400 copy (up to 64 MiB) and refuses those bytes under any name.

Containment acts only on VNX's own state. ISOLATE_WORKLOAD calls a `NetworkIsolator`; this build ships only a
recording stub and changes no network configuration.

## 7. State machine and recovery

```
TRUSTED → DEGRADED → SUSPICIOUS → ISOLATED → INTEGRITY_CHECK → VERIFIED
                                                  ↕
                                               RECOVERY
```

- Escalation is always allowed. Reaching VERIFIED is possible only from INTEGRITY_CHECK.
- A severe event during a check or recovery aborts it and returns to ISOLATED.
- An unknown state loads as ISOLATED.

`recover()` runs these steps:

1. Enter INTEGRITY_CHECK.
2. Verify the audit chain against its anchor.
   - If it does not verify, move it to `evidence/` and start a new chain that records the old head.
3. Compare the policy with the known-good MAC.
   - Restore `policy.good.json` if it differs.
   - If the good copy itself does not authenticate, recovery fails.
4. Rotate the token key (epoch + 1), so every existing token is invalid. Clear the replay and rate state.
5. Verify each archive passed in; one that fails stays locked.
6. Run the integrity snapshot (components, policy, audit).
   - Only if it is VERIFIED and no new severe event happened, clear the session containment (quarantine and failed
     locks are kept) and move to VERIFIED.
   - Otherwise go RECOVERY → ISOLATED, with the reason ("reinstall from a trusted source and re-baseline").

## 8. Integrity snapshot

The baseline is {path: SHA-256} over the installed `vnxdna` package's `.py`, `.c`, `.h`, `.so` and `.json` files, plus
the policy, authenticated with HMAC-SHA256. Each file has one status:

- VERIFIED: the bytes match;
- MODIFIED: the bytes differ, or the file is missing;
- UNKNOWN: a new file;
- FAILED: unreadable, or the baseline itself does not authenticate.

The overall status is the worst status of any file. Policy drift or audit failure lowers it as well.

What this verifies: the listed files have the recorded bytes, as seen by this process. It does not verify the
interpreter, libraries, kernel, firmware or memory, nor the checking process itself. A hash match does not prove the
host is uncompromised.

## 9. Cryptography

Run `vnx security crypto` to print the registry.

| Algorithm | Purpose | Status | Source |
|---|---|---|---|
| AES-256-GCM | archive encryption | IMPLEMENTED (existing) | cryptography / OpenSSL |
| HKDF-SHA256 | archive sub-keys | IMPLEMENTED (existing) | cryptography |
| scrypt | passphrase keys, principal secrets | IMPLEMENTED | OpenSSL via cryptography / hashlib |
| HMAC-SHA256 | audit chain, tokens, state, baselines | IMPLEMENTED (new) | Python `hmac` |
| SHA-256 | digests, Merkle tree, snapshot | IMPLEMENTED | Python `hashlib` |
| ML-KEM-768 (FIPS 203) | future key encapsulation | PLANNED | none in this build |
| ML-DSA-65 (FIPS 204) | future signed manifests / releases | PLANNED | none in this build |

No new algorithm is invented. The archive records its AEAD by name (`manifest.encryption.algorithm`), and
`crypto_agility.aead(name)` resolves a suite or raises for a PLANNED or unknown one. A post-quantum suite can be added
under a new name without changing the archive layout.

Migration path:

1. Add hybrid ML-KEM/X25519 key wrapping for multi-party keys and ML-DSA/Ed25519 signatures on manifests and
   releases, once a reviewed implementation is in the dependency set.
2. Keep AES-256-GCM and SHA-256, which are generally assessed as retaining adequate margins against known quantum
   algorithms at these sizes.
