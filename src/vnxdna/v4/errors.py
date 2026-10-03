"""VNX-DNA V4 error taxonomy.

Every anticipated V4 failure is a :class:`VNXError`. The classes also inherit
from the V3 hierarchy (:mod:`vnxdna.errors`), so the stable exit-code contract
(1 verification, 3 invalid input, 4 authentication, 5 insufficient redundancy,
6 unsupported format, 7 configuration, 8 output) is unchanged, and code that
catches V3 errors keeps working.

Each error says *what* failed (``message``), *where* (``stage``), *why*
(``details``), and whether a retry can help (``retryable``): a retry with more
reads/coverage can help a decode failure, it cannot help a malformed archive.
"""
from __future__ import annotations

from typing import Any

from .. import errors as v3


class VNXError(v3.VNXDNAError):
    """Base class of anticipated V4 failures."""

    stage = "unknown"
    retryable = False

    def __init__(self, message: str, *, stage: str | None = None, retryable: bool | None = None,
                 details: dict[str, Any] | None = None, hint: str | None = None):
        super().__init__(message, details=details)
        if stage is not None:
            self.stage = stage
        if retryable is not None:
            self.retryable = retryable
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out.update({"error_class": type(self).__name__, "stage": self.stage, "retryable": self.retryable})
        if self.hint:
            out["hint"] = self.hint
        return out


class VNXFormatError(VNXError, v3.InvalidInputError):
    """Malformed, truncated or inconsistent archive, table, frame or read file."""

    stage = "format"


class VNXUnsupportedVersionError(VNXError, v3.UnsupportedFormatError):
    """Unknown magic, format version, flag or required feature."""

    stage = "format"


class VNXIntegrityError(VNXError, v3.IntegrityError):
    """Recovered or stored bytes do not match their recorded digest; nothing is published."""

    stage = "integrity"


class VNXKeyError(VNXError, v3.WrongKeyError):
    """Missing or wrong key / passphrase, or failed authentication."""

    stage = "crypto"


class VNXDecodeError(VNXError, v3.InsufficientRedundancyError):
    """The reads do not carry enough information to reconstruct the data (beyond the codes' capability)."""

    stage = "decode"
    retryable = True  # more reads / higher coverage can succeed


class VNXAddressError(VNXError, v3.InvalidDNAError):
    """A strand address is invalid, inconsistent with the archive or cannot be recovered."""

    stage = "address"


class VNXConstraintError(VNXError, v3.ConstraintError):
    """A biological sequence constraint cannot be satisfied or is violated."""

    stage = "constraints"


class VNXConfigurationError(VNXError, v3.ConfigurationError):
    """Invalid or unsatisfiable configuration."""

    stage = "configuration"


class VNXResourceError(VNXError, v3.InvalidInputError):
    """An input would exceed a configured resource limit (size, count, memory, path depth)."""

    stage = "resources"


class VNXOutputError(VNXError, v3.OutputError):
    """An output cannot be written (exists, unsafe path, I/O error)."""

    stage = "output"
