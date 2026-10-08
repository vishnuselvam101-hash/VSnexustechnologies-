"""Future VNX-Q (quantum subsystem) security interface (ARCHITECTURAL ONLY; docs/VNX_SECURE_ARCHITECTURE.md §9).

No quantum hardware or quantum cryptography is used or claimed. A quantum processor does not make VNX secure. This
module names the controls a future QPU integration must provide; every method raises ``NotImplementedError``.
Classical post-quantum cryptography is tracked separately in :mod:`.crypto_agility` (status PLANNED).
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class QuantumSubsystemSecurity(ABC):
    @abstractmethod
    def authorize_qpu_job(self, principal: str, job_manifest: dict) -> bool:
        """Policy decision for a QPU workload (who, which circuit family, which data)."""

    @abstractmethod
    def isolate_qpu_job(self, job_id: str) -> None:
        """Run a job in its own control-plane partition; no shared classical state with other jobs."""

    @abstractmethod
    def attest_quantum_subsystem(self) -> dict:
        """Signed statement of control-firmware and calibration-software versions."""

    @abstractmethod
    def isolate_quantum_control(self, reason: str) -> None:
        """Cut the classical control path to the QPU (containment)."""


class NotAvailable(QuantumSubsystemSecurity):
    def authorize_qpu_job(self, principal, job_manifest):
        raise NotImplementedError("VNX-Q is architectural only")

    def isolate_qpu_job(self, job_id):
        raise NotImplementedError("VNX-Q is architectural only")

    def attest_quantum_subsystem(self):
        raise NotImplementedError("VNX-Q is architectural only")

    def isolate_quantum_control(self, reason):
        raise NotImplementedError("VNX-Q is architectural only")
