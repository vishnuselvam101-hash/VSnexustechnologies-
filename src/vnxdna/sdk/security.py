"""SDK façade for VNX-Secure (layer 7 over :mod:`vnxdna.secure`, so the ``vnx security`` commands keep rule R6)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from vnxdna.secure import simulator
from vnxdna.secure.audit import verify_file
from vnxdna.secure.control import ControlPlane, subkey
from vnxdna.secure.crypto_agility import registry
from vnxdna.secure.policy import Policy

__all__ = ["home", "init", "open_home", "status", "audit_verify", "audit_tail", "integrity", "sessions", "policy_show",
           "policy_check", "simulate", "recover", "crypto_registry"]


def home(path: str | os.PathLike | None = None) -> Path:
    return Path(path or os.environ.get("VNX_SECURE_HOME") or Path.home() / ".vnx-secure")


def init(path=None, *, admin: tuple[str, str] | None = None, roots: list | None = None) -> dict:
    cp = ControlPlane.init(home(path), admin=admin, integrity_roots=roots)
    return cp.status()


def open_home(path=None) -> ControlPlane:
    h = home(path)
    if not (h / "secure.key").exists():
        raise FileNotFoundError(f"{h} is not a VNX-Secure home; run `vnx security init` first")
    return ControlPlane(h)


def status(path=None) -> dict:
    return open_home(path).status()


def audit_verify(path=None) -> dict:
    cp = open_home(path)
    h = home(path)
    st = json.loads((h / "state.json").read_text())["body"] if (h / "state.json").exists() else {}
    anchor = tuple(st["audit_anchor"]) if st.get("audit_anchor") else None
    v = verify_file(h / "audit.log", subkey(cp.master, "audit"), anchor)
    return {**v.to_dict(), "anchor": list(anchor) if anchor else None, "trusted_by_control_plane": cp.audit_trusted}


def audit_tail(path=None, n: int = 20) -> list:
    return open_home(path).audit.events()[-n:]


def integrity(path=None) -> dict:
    return open_home(path).check_integrity()


def sessions(path=None) -> dict:
    s = open_home(path).status()
    return {"containment": s["containment"], "revoked": s["revoked"], "epoch": s["epoch"]}


def policy_show(path=None) -> dict:
    return open_home(path).policy.to_dict()


def policy_check(file: str | os.PathLike) -> dict:
    p = Policy.from_json(Path(file).read_bytes())
    return {"valid": True, "rules": len(p.rules)}


def simulate(quick: bool = False) -> dict:
    return simulator.run(with_benign=not quick, with_overhead=not quick)


def recover(path=None, archives: list | None = None, key: bytes | None = None) -> dict:
    return open_home(path).recover(archives=archives, key=key)


def crypto_registry() -> list:
    return registry()
