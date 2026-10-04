"""V6 errors (subclasses of the V4 hierarchy, so existing handlers apply)."""
from __future__ import annotations

from ..v4.errors import VNXConfigurationError


class V6ConfigurationError(VNXConfigurationError):
    """Invalid V6 outer-code configuration."""
