"""VNX-DNA V4 error taxonomy.

Every anticipated V4 failure is a :class:`VNXError`. The classes also inherit
from the V3 hierarchy (:mod:`vnxdna.errors`), so the stable exit-code contract
(1 verification, 3 invalid input, 4 authentication, 5 insufficient redundancy,
6 unsupported format, 7 configuration, 8 output) is unchanged, and code that
catches V3 errors keeps working.

Each error says *what* failed (``message``), *where* (``stage``), *why*
(``details``), and whether a retry can help (``retryable``): a retry with more
reads/coverage can help a decode failure, it cannot help a malformed archive.

Since V6 every error also carries a stable ``code`` (spec §10): a class default, refined at the raise site with
``code=...`` (e.g. ``FRAME_VERSION_UNSUPPORTED``, ``NO_SUPERBLOCK``). :meth:`VNXError.to_dict` is the ``vnx.error/1``
object (``schema``, ``code``, ``category``, ``exit_code``, ``retryable``, ``stage``, ``message``, ``hint``, ``details``)
plus the 5.x keys (``status``, ``error`` = category, ``error_class``), kept for the 6.x deprecation period.
"""
from __future__ import annotations

from typing import Any

from vnxdna.core import taxonomy as v3


ERROR_SCHEMA = "vnx.error/1"

#: stable codes (spec §10) -> (category, exit code, retryable). Only ``code`` is stable; ``error_class`` is not.
CODES: dict[str, tuple[str, int, bool]] = {
    "CONTAINER_VERSION_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6, False),
    "FEATURE_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6, False),
    "LEGACY_FORMAT": ("UNSUPPORTED_FORMAT", 6, False),
    "FRAME_VERSION_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6, False),
    "SUPERBLOCK_VERSION_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6, False),
    "SCHEMA_UNSUPPORTED": ("UNSUPPORTED_FORMAT", 6, False),
    "FORMAT_ERROR": ("INVALID_INPUT", 3, False),
    "LAYOUT_UNDETECTED": ("INVALID_INPUT", 3, False),
    "ARCHIVE_TAG_AMBIGUOUS": ("INVALID_INPUT", 3, False),
    "ARCHIVE_TAG_NOT_FOUND": ("INVALID_INPUT", 3, False),
    "MULTIPLE_ARCHIVES": ("INVALID_INPUT", 3, False),
    "ADDRESS_ERROR": ("INVALID_INPUT", 3, False),
    "RESOURCE_LIMIT": ("INVALID_INPUT", 3, False),
    "NO_SUPERBLOCK": ("INSUFFICIENT_REDUNDANCY", 5, True),
    "INSUFFICIENT_REDUNDANCY": ("INSUFFICIENT_REDUNDANCY", 5, True),
    "BUDGET_EXCEEDED": ("INSUFFICIENT_REDUNDANCY", 5, True),
    "CONTAINER_HASH_MISMATCH": ("VERIFICATION_FAILED", 1, False),
    "INTEGRITY_ERROR": ("VERIFICATION_FAILED", 1, False),
    "WRONG_KEY": ("AUTHENTICATION_FAILED", 4, False),
    "KEY_FOR_UNENCRYPTED": ("AUTHENTICATION_FAILED", 4, False),
    "CONSTRAINT_ERROR": ("CONFIGURATION_ERROR", 7, False),
    "CONFIGURATION_ERROR": ("CONFIGURATION_ERROR", 7, False),
    "VENDOR_MAX_LENGTH": ("CONFIGURATION_ERROR", 7, False),
    "ARCHIVE_TAG_COLLISION": ("CONFIGURATION_ERROR", 7, False),
    "OUTPUT_ERROR": ("OUTPUT_ERROR", 8, False),
    "PROVIDER_ERROR": ("PROVIDER_ERROR", 10, False),       # exit 10 (V6 decision 3): VNXProviderError, vnxdna.providers
    "INTERNAL_ERROR": ("INTERNAL_ERROR", 70, False),
}


class VNXError(v3.VNXDNAError):
    """Base class of anticipated V4 failures."""

    stage = "unknown"
    retryable = False
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, *, stage: str | None = None, retryable: bool | None = None,
                 details: dict[str, Any] | None = None, hint: str | None = None, code: str | None = None):
        super().__init__(message, details=details)
        if stage is not None:
            self.stage = stage
        if retryable is not None:
            self.retryable = retryable
        if code is not None:
            if code not in CODES:
                raise ValueError(f"unknown error code {code!r}")
            self.code = code
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        out = super().to_dict()
        out.update({"error_class": type(self).__name__, "stage": self.stage, "retryable": self.retryable})
        if self.hint:
            out["hint"] = self.hint
        out.update({"schema": ERROR_SCHEMA, "code": self.code, "category": self.category})
        out.setdefault("hint", None)
        return out


class VNXFormatError(VNXError, v3.InvalidInputError):
    """Malformed, truncated or inconsistent archive, table, frame or read file."""

    code = "FORMAT_ERROR"

    stage = "format"


class VNXUnsupportedVersionError(VNXError, v3.UnsupportedFormatError):
    """Unknown magic, format version, flag or required feature."""

    code = "FEATURE_UNSUPPORTED"

    stage = "format"


class VNXIntegrityError(VNXError, v3.IntegrityError):
    """Recovered or stored bytes do not match their recorded digest; nothing is published."""

    code = "INTEGRITY_ERROR"

    stage = "integrity"


class VNXKeyError(VNXError, v3.WrongKeyError):
    """Missing or wrong key / passphrase, or failed authentication."""

    code = "WRONG_KEY"

    stage = "crypto"


class VNXDecodeError(VNXError, v3.InsufficientRedundancyError):
    """The reads do not carry enough information to reconstruct the data (beyond the codes' capability)."""

    code = "INSUFFICIENT_REDUNDANCY"

    stage = "decode"
    retryable = True  # more reads / higher coverage can succeed


class VNXAddressError(VNXError, v3.InvalidDNAError):
    """A strand address is invalid, inconsistent with the archive or cannot be recovered."""

    code = "ADDRESS_ERROR"

    stage = "address"


class VNXConstraintError(VNXError, v3.ConstraintError):
    """A biological sequence constraint cannot be satisfied or is violated."""

    code = "CONSTRAINT_ERROR"

    stage = "constraints"


class VNXConfigurationError(VNXError, v3.ConfigurationError):
    """Invalid or unsatisfiable configuration."""

    code = "CONFIGURATION_ERROR"

    stage = "configuration"


class VNXResourceError(VNXError, v3.InvalidInputError):
    """An input would exceed a configured resource limit (size, count, memory, path depth)."""

    code = "RESOURCE_LIMIT"

    stage = "resources"


class VNXOutputError(VNXError, v3.OutputError):
    """An output cannot be written (exists, unsafe path, I/O error)."""

    code = "OUTPUT_ERROR"

    stage = "output"


class VNXProviderError(VNXError):
    """A DNA provider (synthesis, storage, retrieval or sequencing service, or the reference simulator standing in for
    one) failed: exit 10 (spec §9.2, §10; founder decision 3). Never mapped to INSUFFICIENT_REDUNDANCY: a failed
    retrieval says nothing about the archive's redundancy. Whether a retry can help is provider-defined."""

    exit_code = 10
    category = "PROVIDER_ERROR"
    code = "PROVIDER_ERROR"

    stage = "provider"


class V6ConfigurationError(VNXConfigurationError):
    """Invalid V6 outer-code configuration (formerly ``vnxdna.v6.errors``)."""


def error_json(error: BaseException) -> dict:
    """``vnx.error/1`` (plus the 5.x keys) for any exception; unexpected ones are ``INTERNAL_ERROR`` (exit 70)."""
    if isinstance(error, VNXError):
        return error.to_dict()
    if isinstance(error, v3.VNXDNAError):          # V1–V3 classes: the category is the code
        out = error.to_dict()
        code = error.category if error.category in CODES else {"UNRECOVERABLE_CORRUPTION": "FORMAT_ERROR"}.get(
            error.category, "INTERNAL_ERROR")
        out.update(schema=ERROR_SCHEMA, code=code, category=error.category, error_class=type(error).__name__,
                   stage=getattr(error, "stage", "unknown"), retryable=bool(getattr(error, "retryable", False)), hint=None)
        return out
    return {"schema": ERROR_SCHEMA, "status": "FAILED", "error": "INTERNAL_ERROR", "code": "INTERNAL_ERROR",
            "category": "INTERNAL_ERROR", "exit_code": 70, "retryable": False, "stage": "unknown",
            "message": f"{type(error).__name__}: {error}", "hint": None, "details": {}, "error_class": type(error).__name__}
