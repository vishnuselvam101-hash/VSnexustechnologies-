"""Identity: principals, password authentication, short-lived session tokens, revocation, replay protection
(VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §3).

* Secrets are verified against scrypt hashes (``hashlib.scrypt``; the cost is recorded with each hash).
* A token is ``vnxt1.<payload>.<mac>`` (URL-safe base64, no padding) with ``mac = HMAC-SHA256(K_token, "vnxt1." + payload)``.
  The payload binds the token to a principal, a session, a capability list and an expiry. Tokens are bearer secrets;
  their validity is re-checked on every request (expiry, revocation of the token, its session and its principal).
* A request carries a fresh nonce and a timestamp; a nonce seen again inside the replay window, or a timestamp outside
  the allowed skew, is a replay and is denied.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field

from .events import canonical

TOKEN_PREFIX = "vnxt1."
MAX_TOKEN_LEN = 4096
SCRYPT_MIN_N = 1 << 10


class AuthError(Exception):
    """Authentication or credential validation failed. ``reason`` is a stable machine-readable code."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    if not s or len(s) > MAX_TOKEN_LEN or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in s):
        raise AuthError("malformed_token", "bad base64")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except ValueError:          # binascii.Error: impossible length
        raise AuthError("malformed_token", "bad base64") from None


@dataclass(frozen=True)
class Principal:
    name: str
    roles: tuple = ()


def hash_secret(secret: str, *, n: int = 1 << 14, salt: bytes | None = None) -> dict:
    if n < SCRYPT_MIN_N or n & (n - 1):
        raise ValueError("scrypt n must be a power of two >= 1024")
    salt = salt if salt is not None else os.urandom(16)
    dk = hashlib.scrypt(secret.encode(), salt=salt, n=n, r=8, p=1, dklen=32)
    return {"alg": "scrypt", "n": n, "r": 8, "p": 1, "salt": salt.hex(), "hash": dk.hex()}


def check_secret(secret: str, rec: dict) -> bool:
    try:
        if rec.get("alg") != "scrypt" or rec["r"] != 8 or rec["p"] != 1 or rec["n"] < SCRYPT_MIN_N or rec["n"] > 1 << 20:
            return False
        dk = hashlib.scrypt(secret.encode(), salt=bytes.fromhex(rec["salt"]), n=rec["n"], r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), rec["hash"])
    except (KeyError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Claims:
    tid: str
    sub: str
    ses: str
    caps: tuple
    iat: float
    exp: float


@dataclass
class TokenAuthority:
    """Issues and validates tokens. ``revoked_*`` sets are the revocation state (persisted by the control plane)."""
    key: bytes
    clock: object
    ttl: float = 900.0
    revoked_tokens: set = field(default_factory=set)
    revoked_sessions: set = field(default_factory=set)
    revoked_principals: dict = field(default_factory=dict)    # principal -> time: tokens issued before it are dead
    counter: int = 0

    def issue(self, principal: Principal, session_id: str, caps: tuple | None = None, ttl: float | None = None) -> tuple[str, Claims]:
        if session_id in self.revoked_sessions:
            raise AuthError("session_revoked", session_id)
        now = self.clock()
        self.counter += 1
        tid = hashlib.sha256(self.key + b"tid" + self.counter.to_bytes(8, "big") + canonical([principal.name, session_id, now])).hexdigest()[:24]
        c = Claims(tid, principal.name, session_id, tuple(sorted(caps if caps is not None else principal.roles)), now,
                   now + (self.ttl if ttl is None else ttl))
        payload = _b64e(canonical({"tid": c.tid, "sub": c.sub, "ses": c.ses, "caps": list(c.caps), "iat": c.iat, "exp": c.exp}))
        mac = hmac.new(self.key, (TOKEN_PREFIX + payload).encode(), hashlib.sha256).digest()
        return f"{TOKEN_PREFIX}{payload}.{_b64e(mac)}", c

    def validate(self, token: str) -> Claims:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_LEN or not token.startswith(TOKEN_PREFIX):
            raise AuthError("malformed_token", "prefix or length")
        body = token[len(TOKEN_PREFIX):]
        if body.count(".") != 1:
            raise AuthError("malformed_token", "structure")
        payload, mac = body.split(".")
        want = hmac.new(self.key, (TOKEN_PREFIX + payload).encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _b64d(mac)):
            raise AuthError("bad_signature")
        try:
            d = json.loads(_b64d(payload))
            c = Claims(str(d["tid"]), str(d["sub"]), str(d["ses"]), tuple(str(x) for x in d["caps"]), float(d["iat"]), float(d["exp"]))
        except (KeyError, TypeError, ValueError) as e:
            raise AuthError("malformed_token", str(e))
        now = self.clock()
        if now >= c.exp:
            raise AuthError("expired", c.tid)
        if c.tid in self.revoked_tokens:
            raise AuthError("token_revoked", c.tid)
        if c.ses in self.revoked_sessions:
            raise AuthError("session_revoked", c.ses)
        if c.sub in self.revoked_principals and c.iat <= self.revoked_principals[c.sub]:
            raise AuthError("principal_revoked", c.sub)
        return c

    def revoke_token(self, tid: str) -> bool:
        new = tid not in self.revoked_tokens
        self.revoked_tokens.add(tid)
        return new

    def revoke_session(self, session_id: str) -> bool:
        new = session_id not in self.revoked_sessions
        self.revoked_sessions.add(session_id)
        return new

    def revoke_principal(self, name: str) -> bool:
        new = name not in self.revoked_principals
        self.revoked_principals[name] = max(self.clock(), self.revoked_principals.get(name, float("-inf")))
        return new


class ReplayGuard:
    """Rejects a request nonce seen before within ``window`` seconds, and timestamps more than ``skew`` from now.
    Memory is bounded (``capacity`` nonces, oldest evicted); an evicted nonce is still rejected through the skew bound
    as long as ``window >= 2 * skew``."""

    def __init__(self, clock, window: float = 600.0, skew: float = 120.0, capacity: int = 100_000):
        if window < 2 * skew:
            raise ValueError("replay window must be at least twice the clock skew")
        self.clock, self.window, self.skew, self.capacity = clock, window, skew, capacity
        self.seen: OrderedDict = OrderedDict()

    def check(self, nonce: str, ts: float) -> None:
        now = self.clock()
        if not isinstance(nonce, str) or not 16 <= len(nonce) <= 128:
            raise AuthError("malformed_nonce")
        if abs(now - ts) > self.skew:
            raise AuthError("stale_request", f"timestamp off by {now - ts:.0f}s")
        while self.seen and (next(iter(self.seen.values())) < now - self.window or len(self.seen) >= self.capacity):
            self.seen.popitem(last=False)
        if nonce in self.seen:
            raise AuthError("replay", nonce)
        self.seen[nonce] = now
