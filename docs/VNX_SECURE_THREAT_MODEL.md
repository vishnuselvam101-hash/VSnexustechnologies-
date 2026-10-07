# VNX-Secure — Threat Model

**Scope:** VNX-DNA as it exists at `build/vnx-secure`. This covers:

- the archive container (VNX4: AES-256-GCM, Merkle tree, chunk table);
- random access;
- the `vnx` CLI and SDK;
- the VNX-Secure control plane, which is a library and CLI;
- the VNX-Secure home directory.

VNX-DNA has no network service yet; the V9–V11 API/service will need its own review.

**VNX-Secure is not claimed to be mathematically or absolutely unhackable.**

## 1. Assets

| Asset | Where | Why it matters |
|---|---|---|
| Archive plaintext | `.vnx` containers (encrypted or not) | confidentiality |
| Archive integrity | chunk hashes, Merkle root, trailer digest | a wrong decode must never pass as success |
| Archive keys / passphrases | key files, environment | decrypt everything in an archive |
| VNX-Secure master key | `secure.key` (0600) | forges tokens, audit MACs, state and baselines |
| Principal secrets | `principals.json` (scrypt hashes) | log in as that principal |
| Session tokens | clients | bearer access until expiry/revocation |
| Audit chain | `audit.log` + anchor in `state.json` | after-the-fact accountability |
| Policy | `policy.json` / `policy.good.json` | who may do what |
| Code | installed `vnxdna` package | everything above runs in it |

## 2. Trust boundaries

1. **Caller ↔ control plane.** Every request is untrusted until steps 1–7 of the architecture, §3, pass.
2. **Archive bytes ↔ parser.** Archive contents are untrusted input. The archive layer validates the structure; the
   gate converts every failure into an explicit, audited failure. `sandbox=True` parses in a resource-limited child.
3. **VNX-Secure home ↔ other local users.** File modes are 0700/0600, and MACs authenticate state, principals and
   baselines.
4. **Process ↔ host.** VNX-Secure trusts the interpreter, the OS and the hardware. It cannot defend against a host it
   runs on that is already compromised.

## 3. Attack surfaces

The attack surfaces are:

- the `ControlPlane.login` and `authorize` inputs (tokens, nonces, timestamps, names);
- archive files given to the gate (any bytes);
- the policy JSON;
- the VNX-Secure home files;
- the audit log;
- the CLI arguments and environment;
- third-party dependencies (`cryptography`, `numpy`, `zstandard`, `reedsolo`, `pydantic`, `typer`);
- the build and release pipeline.

## 4. Attackers

Each entry gives the attack surface, the preconditions, then how VNX-Secure detects, contains and recovers, and the
residual risk.

### External attacker (no credentials)

- **Attack surface:** login and request APIs.
- **Preconditions:** can call the API.
- **Detection:**
  - `auth_failures` (5 in 300 s);
  - `authz_failures`;
  - `request_rate`;
  - `replay`.
- **Containment:**
  - login lock (900 s);
  - session containment;
  - rate limit.
- **Recovery:** `vnx security recover`.
- **Residual risk:**
  - online guessing stays possible at a reduced rate;
  - the lockout can be abused to deny service to a named user (DoS).

### Compromised credential (stolen secret or token)

- **Attack surface:** requests carrying a valid token.
- **Preconditions:** the attacker has the token or the secret.
- **Detection:**
  - `token_misuse` (the session does not match);
  - `session_transition`;
  - `deception` hits;
  - enumeration (`access_pattern`);
  - rate.
- **Containment:**
  - session contained;
  - token, session and principal tokens revoked;
  - the workload isolated (recorded).
- **Recovery:** recovery rotates the token key; the principal's secret must be reset by an operator.
- **Residual risk:**
  - a stolen token used *inside its own session*, within policy and below the thresholds, is indistinguishable from
    the user until it expires (15 min by default).

### Malicious archive / input

- **Attack surface:** archive bytes.
- **Preconditions:** can supply a file.
- **Detection:**
  - `malformed_input` (MEDIUM, then HIGH at 3);
  - `tamper` (HIGH, then CRITICAL at 2);
  - `unexpected_key_usage`.
- **Containment:**
  - the input is quarantined by SHA-256 (refused under any name);
  - the resource is locked;
  - the session is contained;
  - the sandbox bounds memory and CPU.
- **Recovery:** recovery re-verifies the listed archives; a failing archive stays locked.
- **Residual risk:**
  - a parser bug that executes code is **not** contained by the sandbox (same user, same file system);
  - mitigated only by the existing fuzzing of the archive and native parsers.

### Insider with limited privileges

- **Attack surface:** requests within their role.
- **Preconditions:** a valid reader or writer account.
- **Detection:**
  - `privilege_escalation` (capability);
  - `authz_failures`;
  - `deception`;
  - `access_pattern`.
- **Containment:**
  - contain;
  - revoke;
  - lock login on CRITICAL.
- **Recovery:** recovery.
- **Residual risk:**
  - reading data that the role allows is not an anomaly;
  - data exfiltration within policy and below the rate thresholds is not detected.

### Compromised application (code calling VNX-DNA)

- **Attack surface:** the SDK, or direct file access.
- **Preconditions:** runs as a user with file access and the archive key.
- **Detection:** none if it bypasses the gate.
- **Containment:** none if it bypasses the gate.
- **Recovery:** key rotation and re-encryption are outside VNX-Secure.
- **Residual risk:** **the gate only protects access that goes through it.** Anyone holding the archive key and the
  file reads it directly.

### Compromised dependency

- **Attack surface:** imported packages.
- **Preconditions:** a malicious release is installed.
- **Detection:** the integrity snapshot detects a modified `vnxdna` file only; dependencies outside the baseline roots
  are not covered.
- **Containment:** none automatic.
- **Recovery:** reinstall from pinned, reviewed versions and re-baseline.
- **Residual risk:** **HIGH**; there is no dependency pinning by hash in this build.

### Host-level compromise (root, or the same user)

- **Attack surface:** everything.
- **Preconditions:** a shell on the host.
- **Detection:** the integrity snapshot and the audit MACs detect naive tampering only.
- **Containment:** none.
- **Recovery:** rebuild the host from trusted media.
- **Residual risk:**
  - **not defended**: the attacker can read `secure.key`, forge MACs and rewrite logs and state consistently;
  - an off-host anchor (future work) is required.

### Supply-chain compromise (build / release)

- **Attack surface:** the source repository and the build.
- **Preconditions:** commit or CI access.
- **Detection:** review and CI; no signed releases.
- **Containment:** none.
- **Recovery:** revert and re-release.
- **Residual risk:** **HIGH**; signed manifests (ML-DSA/Ed25519) are PLANNED, not implemented.

### Physical attacker

- **Attack surface:** disks and machines.
- **Preconditions:** physical access.
- **Detection:** none.
- **Containment:** none.
- **Recovery:** none from VNX-Secure.
- **Residual risk:**
  - encrypted archives stay confidential while their key is not on the same disk;
  - the VNX-Secure home is not encrypted at rest;
  - for DNA media, physical custody is outside the software.

## 5. Assumptions

These assumptions are untested:

1. The OS enforces file modes, and `os.urandom` is a CSPRNG.
2. `cryptography`/OpenSSL implement AES-GCM and scrypt correctly. Python's `hmac`/`hashlib` are correct.
3. Clients keep tokens secret. Clocks are within ±120 s.
4. Requests reach VNX-Secure through the gate. A caller with the key and the file can bypass it.
5. A single control-plane process owns a home. Concurrent processes on one home are not coordinated.
