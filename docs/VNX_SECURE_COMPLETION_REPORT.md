# VNX-Secure completion report

VNX-Secure was built on `build/vnx-secure`, which branches from the closed V8 state 8935217. It was merged into the V9
branch `work/v9-integrated` at 6c50160 and ships with V9. The V9 completion report covers it in §10 and gives the
combined verification in §11. This file records the VNX-Secure scope and evidence.

**Claim scope.** VNX-Secure is validated against the included simulated attack scenarios: synthetic attacks on a local,
in-memory control plane on one host. These results are not a real-world security guarantee. VNX-Secure protects access
to software archives and says nothing about physical DNA.

## What it is

`src/vnxdna/secure/` is an access-control and anomaly-response layer for archive access. It has these parts:

- signed, expiring, revocable session tokens;
- deny-by-default role/resource policies;
- 16 detectors;
- an additive risk score with idempotent containment (session containment, token revocation, login lock, rate limit,
  input quarantine, resource lock);
- a hash-chained audit log;
- an integrity baseline over archives and component files;
- a gated `SecureArchive` with sandboxed decoding limits;
- deception markers.

The design is in `docs/VNX_SECURE_ARCHITECTURE.md`, the threat model in `docs/VNX_SECURE_THREAT_MODEL.md`, the security
model in `docs/VNX_SECURE_SECURITY_MODEL.md`, and the limitations in `docs/VNX_SECURE_LIMITATIONS.md`.

The 16 detectors, as named in `detect.py`, are:

- `auth_failures`
- `authz_failures`
- `privilege_escalation`
- `malformed_input`
- `replay`
- `token_misuse`
- `invalid_tokens`
- `integrity_monitor`
- `tamper`
- `request_rate`
- `access_pattern`
- `config_change`
- `session_transition`
- `revoked_credential_use`
- `unexpected_key_usage`
- `deception`

## Evidence

All results are SIMULATED (`experiments/vnx-secure/results/`; method in `docs/VNX_SECURE_BENCHMARKS.md`).

| measure | result | file |
|---|---|---|
| included attack scenarios detected and contained | 10 / 10 | `simulation.json` |
| missed-attack rate / false-positive rate | 0 / 0 | `simulation.json` |
| benign requests: findings, denials | 270 requests: 0, 0 | `simulation.json` |
| median detection / recovery wall time | 4.2 ms / 12.1 ms | `simulation.json` |
| gated read overhead (chunk / file) | +0.92 ms / +0.81 ms | `simulation.json` |
| audit bytes per request | 419 | `simulation.json` |
| hypothesis fuzz examples, failures | 25,100, 0 | `fuzz-campaign.json` |
| tests | 99 in `tests/secure/` | — |
| full V9 suite with VNX-Secure merged | 3337 passed, 0 failed, 6 skipped | `experiments/v9/results/verification.json` |

The ten scenarios are:

- credential brute force
- unauthorized archive access
- privilege escalation
- malformed input
- replay
- token misuse
- integrity tampering
- abnormal request rate
- malicious archive metadata
- a compromised session that hits a deception marker

Fuzzing found one bug during development: the token parser let `binascii.Error` escape on impossible base64 lengths. It
was fixed in 3549f76, with a regression test.

## Limitations

- Every scenario was written by the developers. Attacks outside them are untested.
- The tests ran in a single process on one host, with a deterministic clock. The setup is not distributed and not
  multi-tenant, and there are no network-level controls.
- Wall times were measured while the V9 decoder evaluation was running (load1 3.67). Treat them as indicative only.
- No external security review has been done.
