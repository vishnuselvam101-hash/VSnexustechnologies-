# V15 Security Monitor (AP-16)

**Status:** Implemented  
**Location:** `src/vnxdna/secure/monitor.py`  
**Tests:** `tests/v15/test_ap16_monitor.py`

---

## Overview

The AP-16 offline security monitor provides always-on integrity checking, access auditing, anomaly detection, and configurable lockdown for VNX-DNA storage nodes. It operates with **no network dependencies** — all verification is local.

The monitor is built on the tamper-evident, HMAC-chained `AuditLog` (VNX-Secure §6) and uses a placeholder `HmacSigner` (HMAC-SHA256) for manifest signing until AP-4 delivers real signatures.

---

## Components

### `HmacSigner` (placeholder until AP-4)

```python
class HmacSigner:
    def __init__(self, key: bytes)          # key must be >= 32 bytes
    def sign(self, data: bytes) -> bytes    # HMAC-SHA256
    def verify(self, data: bytes, sig: bytes) -> bool  # constant-time compare
```

- Uses `hmac.compare_digest` for constant-time verification.
- Key length enforced at construction (>= 32 bytes).
- Clearly documented as a placeholder; real signatures drop in via the `Signer` protocol.

### `Manifest`

```python
@dataclass(frozen=True)
class Manifest:
    root: str                      # monitored directory (absolute path)
    files: dict[str, str]          # relative path -> sha256 hex
    created: float                 # Unix timestamp

    def canonical(self) -> bytes   # canonical JSON (sorted keys, no whitespace)
```

Covers the entire directory tree under `root`. Built by `build_manifest()`.

### `build_manifest(root, signer, *, now) -> tuple[Manifest, bytes]`

Walks `root`, computes SHA-256 of every file, returns a signed `(Manifest, signature)` pair.

### `Finding`

```python
@dataclass(frozen=True)
class Finding:
    kind: str          # "modified" | "missing" | "added" | "bad-signature" | "auth-failures" | "read-burst" | "audit-broken"
    path: str | None   # affected file (None for system-wide findings)
    detail: dict       # context (hashes, counts, limits, etc.)
```

Every finding is immediately appended to the `AuditLog` as a `SecurityEvent`.

### `Policy`

```python
@dataclass(frozen=True)
class Policy:
    lockdown_on: frozenset[str] = frozenset({"bad-signature", "audit-broken", "modified", "missing", "added"})
    max_auth_failures: int = 5              # per actor per window
    window_s: float = 60.0                  # sliding window for auth failures and read bursts
    max_read_bytes: int = 1 << 30           # 1 GiB per window
```

- `lockdown_on` controls which finding kinds trigger sticky lockdown.
- Default includes all integrity violations plus audit-chain breaks.
- Anomaly findings (`auth-failures`, `read-burst`) are logged but **do not** lockdown by default; add them to `lockdown_on` if desired.

### `Monitor`

```python
class Monitor:
    def __init__(
        self,
        root: str | os.PathLike,
        manifest: Manifest,
        signature: bytes,
        signer: Signer,
        audit: AuditLog,
        policy: Policy | None = None,
        clock: Callable[[], float] = SYSTEM_CLOCK,
    ): ...

    def check_integrity(self) -> list[Finding]
    def record_access(self, actor: str, op: str, path: str, nbytes: int, ok: bool) -> list[Finding]
    def tick(self) -> list[Finding]

    @property
    def locked(self) -> bool
    def unlock(self, admin_token_ok: bool) -> None
    def lockdown_actions(self) -> list[str]
```

#### `check_integrity()`

1. Verifies the manifest signature. If invalid → `bad-signature` finding + lockdown.
2. For each file in the manifest:
   - Missing → `missing` finding + lockdown.
   - Hash mismatch → `modified` finding + lockdown.
3. Scans the directory for files not in the manifest → `added` finding + lockdown.

All findings are logged to the audit chain.

#### `record_access(actor, op, path, nbytes, ok)`

- **Always** appends a `SecurityEvent` (kind="request") to the audit log.
- If `ok=False`: tracks auth failures per actor in a sliding window (`window_s`). Exceeding `max_auth_failures` → `auth-failures` finding.
- If `ok=True` and `op` is a read-like operation: tracks bytes read in a sliding window. Exceeding `max_read_bytes` → `read-burst` finding.

#### `tick()`

One complete monitoring round:

1. `check_integrity()`
2. Verify the audit chain via `audit.verify()`. If `status != "VERIFIED"` → `audit-broken` finding + lockdown.

Returns all findings from the round.

#### `locked` (property)

Sticky: once `True`, stays `True` until `unlock(admin_token_ok=True)`.

#### `unlock(admin_token_ok: bool)`

Only unlocks if `admin_token_ok` is `True`. Designed for operator-controlled recovery.

#### `lockdown_actions() -> list[str]`

Returns active lockdown actions. Currently `["read-only"]` when locked. Key wipe is **simulated only** (tests only) and off by default.

---

## Usage Example

```python
from vnxdna.secure.monitor import HmacSigner, build_manifest, Monitor, Policy, AuditLog
from vnxdna.secure.events import ManualClock
from pathlib import Path

# Keys (in production: load from HSM / sealed storage)
SIGN_KEY = b"s" * 32
AUDIT_KEY = b"a" * 32

root = Path("/var/vnx/data")
audit = AuditLog(AUDIT_KEY, Path("/var/vnx/audit.log"))

signer = HmacSigner(SIGN_KEY)
manifest, sig = build_manifest(root, signer, now=1_800_000_000.0)

monitor = Monitor(
    root, manifest, sig, signer, audit,
    policy=Policy(),
    clock=ManualClock(1_800_000_000.0),  # or SYSTEM_CLOCK
)

# On every access:
findings = monitor.record_access("alice", "read", "archive.vnx", 4096, ok=True)

# Periodic monitoring round (e.g., from a cron job or daemon loop):
findings = monitor.tick()

if monitor.locked:
    print("LOCKDOWN:", monitor.lockdown_actions())
    # Operator investigates, then:
    # monitor.unlock(admin_token_ok=verify_admin_token(token))
```

---

## Tamper Detection Guarantees

| Tamper Type | Finding Kind | Lockdown (Default Policy) |
|-------------|--------------|---------------------------|
| File content changed | `modified` | ✅ |
| File deleted | `missing` | ✅ |
| New file added | `added` | ✅ |
| Manifest edited | `bad-signature` | ✅ |
| Manifest signature forged | `bad-signature` | ✅ |
| Audit line edited | `audit-broken` | ✅ |
| Audit line inserted | `audit-broken` | ✅ |
| Audit line deleted | `audit-broken` | ✅ |
| Audit lines swapped | `audit-broken` | ✅ |
| 6+ auth failures in 60s | `auth-failures` | ❌ (configurable) |
| Read burst > 1 GiB in 60s | `read-burst` | ❌ (configurable) |

All findings are **immutably recorded** in the HMAC-chained audit log. An attacker with filesystem access can delete the entire log, but cannot forge or selectively tamper with it without detection.

---

## Offline Guarantee

- **No network calls** — the monitor never opens sockets, makes HTTP requests, or depends on external services.
- Tests enforce this by monkeypatching `socket.socket` and `socket.create_connection` to raise `RuntimeError`.
- All time is injected via a `clock` callable; tests use `ManualClock` for determinism.

---

## Integration with VNX-Secure

- Reuses `vnxdna.secure.audit.AuditLog` — no new chain format.
- Logs `SecurityEvent` from `vnxdna.secure.events` with `kind="finding"` (findings) and `kind="request"` (access records).
- Compatible with `ControlPlane` and `Simulator` for red-team exercises.

---

## Testing

Run the full AP-16 test suite:

```bash
PYTHONPATH=src python -m pytest -q -n 8 tests/v15/test_ap16_monitor.py tests/secure/
```

### Test Coverage

- **Integrity tamper campaign** (9 tests): modify, delete, add, manifest edit, signature edit, audit edit/insert/delete/swap — all detected, logged, and trigger lockdown.
- **Anomaly rules** (5 tests): auth-failure window, read-burst window, lockdown policy control.
- **Normal-use campaign** (1 test): 1000 normal accesses + 20 ticks → zero findings, no lockdown.
- **Lockdown mechanics** (2 tests): sticky lockdown, unlock with admin token, lockdown actions.
- **Unit tests** (7 tests): `HmacSigner`, `build_manifest`, `Policy`, audit chain verification.

All tests pass with network disabled and deterministic clocks.

---

## Security Notes

1. **HmacSigner is a placeholder** — do not use in production without AP-4 signatures. The key is symmetric; anyone with the key can forge manifests. Plan: replace with Ed25519/ECDSA in AP-4.
2. **Key management** — the signing key and audit key must be protected (HSM, TPM, sealed storage). This module does not manage keys.
3. **Log deletion** — an attacker with root can delete the audit log file. The monitor detects missing logs on restart (via anchor mismatch) but cannot prevent deletion. Mitigation: replicate audit logs to a write-once store.
4. **Time source** — the `clock` callable must be monotonic and trustworthy. In production, use a hardware monotonic clock or TPM time.
5. **Lockdown is advisory** — `lockdown_actions()` returns `["read-only"]`; enforcement is the caller's responsibility (e.g., `ControlPlane` checks `monitor.locked` before allowing writes).

---

## Related Documents

- `docs/VNX_SECURE_SECURITY_MODEL.md` — threat model, audit chain design
- `src/vnxdna/secure/audit.py` — HMAC-chained audit log implementation
- `src/vnxdna/secure/events.py` — `SecurityEvent`, `Finding`, `ManualClock`
- `tests/v15/test_ap16_monitor.py` — acceptance test suite