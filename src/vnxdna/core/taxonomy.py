"""Structured VNX-DNA error hierarchy and the stable CLI exit-code contract.

Every failure the library can anticipate is raised as a subclass of
:class:`VNXDNAError`. Each class carries a stable ``exit_code`` and a
``category`` string used in JSON reports. Any other exception escaping the
public API is a bug, and the CLI reports it as ``INTERNAL_ERROR`` (70).

====  ========================  =================================================
code  category                  meaning
====  ========================  =================================================
0     SUCCESS                   completed; outputs verified where applicable
1     VERIFICATION_FAILED       recovered bytes do not match the recorded digest
2     USAGE_ERROR               command-line usage error (argument parser)
3     INVALID_INPUT             missing/malformed/corrupt input, metadata or DNA
4     AUTHENTICATION_FAILED     tampering detected, wrong key or missing key
5     INSUFFICIENT_REDUNDANCY   more strands lost/invalid than the ECC can recover
6     UNSUPPORTED_FORMAT        unknown format/version/feature, or legacy input
7     CONFIGURATION_ERROR       invalid or unsatisfiable parameters
8     OUTPUT_ERROR              output cannot be written (or already exists)
70    INTERNAL_ERROR            bug: unexpected exception
====  ========================  =================================================

A decode that needed repair (inner-code corrections, erasure recovery,
duplicate conflicts) still exits 0; its report says ``"status": "RECOVERED"``.
"""
from __future__ import annotations

from typing import Any

EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_INTERNAL = 70


class VNXDNAError(Exception):
    """Base class for anticipated VNX-DNA failures."""

    exit_code = EXIT_INTERNAL
    category = "INTERNAL_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"status": "FAILED", "error": self.category, "exit_code": self.exit_code, "message": str(self), "details": self.details}


class IntegrityError(VNXDNAError):
    """Recovered data does not match its recorded digest; the output is rejected."""

    exit_code = 1
    category = "VERIFICATION_FAILED"


class InvalidInputError(VNXDNAError):
    """Input file, container or read file is missing, malformed or corrupt."""

    exit_code = 3
    category = "INVALID_INPUT"


class MetadataError(InvalidInputError):
    """A manifest or other metadata structure is malformed or inconsistent."""


class InvalidDNAError(InvalidInputError):
    """A DNA sequence contains invalid symbols or cannot be demapped."""


class UnrecoverableCorruptionError(InvalidInputError):
    """No usable archive structure could be identified in the reads."""

    category = "UNRECOVERABLE_CORRUPTION"


class AuthenticationError(VNXDNAError):
    """Authenticated metadata or ciphertext failed verification (tampering or wrong key)."""

    exit_code = 4
    category = "AUTHENTICATION_FAILED"


class WrongKeyError(AuthenticationError):
    """The supplied key does not match the key the archive was sealed with."""


class KeyRequiredError(AuthenticationError):
    """The archive is encrypted and no key was supplied."""


class InsufficientRedundancyError(VNXDNAError):
    """Too many strands/shards are missing or invalid for the configured redundancy."""

    exit_code = 5
    category = "INSUFFICIENT_REDUNDANCY"


class UnsupportedFormatError(VNXDNAError):
    """Unknown format version or required feature, or an input that needs another decoder."""

    exit_code = 6
    category = "UNSUPPORTED_FORMAT"


class ConfigurationError(VNXDNAError):
    """Requested encoding parameters are invalid or cannot be satisfied."""

    exit_code = 7
    category = "CONFIGURATION_ERROR"


class ConstraintError(ConfigurationError):
    """DNA constraints could not be satisfied for at least one strand."""


class OutputError(VNXDNAError):
    """The requested output location cannot be written."""

    exit_code = 8
    category = "OUTPUT_ERROR"


EXIT_CODES = {
    0: "SUCCESS", 1: "VERIFICATION_FAILED", 2: "USAGE_ERROR", 3: "INVALID_INPUT", 4: "AUTHENTICATION_FAILED",
    5: "INSUFFICIENT_REDUNDANCY", 6: "UNSUPPORTED_FORMAT", 7: "CONFIGURATION_ERROR", 8: "OUTPUT_ERROR", 70: "INTERNAL_ERROR",
}
