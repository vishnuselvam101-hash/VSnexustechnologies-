"""VNX-Secure: a defensive security control plane for VNX-DNA (docs/VNX_SECURE_ARCHITECTURE.md).

Defensive only: it authenticates, authorizes, detects, contains, audits and recovers. It never acts against another
system. VNX-Secure is not claimed to be mathematically or absolutely unhackable; see docs/VNX_SECURE_LIMITATIONS.md.
"""
from .control import AccessDenied, ControlPlane, SecurityFailure
from .events import ManualClock, SecurityEvent

__all__ = ["AccessDenied", "ControlPlane", "ManualClock", "SecurityEvent", "SecurityFailure"]
