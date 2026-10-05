"""Layer 8 (V6_ARCHITECTURE §2): the ``vnx`` command-line interface. It imports only :mod:`vnxdna.sdk` and
:mod:`vnxdna.core` (rule R6). Entry point: ``vnx = vnxdna.commands:main``."""
from vnxdna.commands.cli import app, main

__all__ = ["app", "main"]
