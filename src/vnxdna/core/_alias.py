"""Old module paths after the V6 Phase 2 moves (V6_ARCHITECTURE §7.2), and by-name module references.

:func:`alias_module` makes an old path the **same module object** as the moved module (``sys.modules``), so
``monkeypatch.setattr`` on either name affects the code that runs. A ``DeprecationWarning`` is emitted only when
``VNXDNA_WARN_LEGACY_IMPORTS=1`` (6.x keeps test output quiet; V6_ARCHITECTURE §7.3).

:func:`lazy_module` is a by-name reference to a module of a *higher* layer that is imported on first attribute access.
The native kernels use it for their bit-exact reference backends, so ``vnxdna.native`` does not import the layers that
use it (rule R1) while each kernel still falls back to its reference exactly as before.
"""
from __future__ import annotations

import importlib
import os
import sys
import warnings
from types import ModuleType

WARN_ENV = "VNXDNA_WARN_LEGACY_IMPORTS"


def alias_module(old: str, new: str) -> ModuleType:
    """Install the module ``new`` under the name ``old`` (call from the old module's body with ``__name__``)."""
    module = importlib.import_module(new)
    if os.environ.get(WARN_ENV) == "1":
        warnings.warn(f"{old} has moved to {new}; the old import path is a deprecated alias", DeprecationWarning, stacklevel=3)
    sys.modules[old] = module
    return module


class lazy_module:  # noqa: N801 - reads like a module
    """``lazy_module("vnxdna.x.y").attr`` imports ``vnxdna.x.y`` on first use and reads ``attr`` from it."""

    __slots__ = ("_name",)

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attr: str):
        return getattr(importlib.import_module(self._name), attr)

    def __repr__(self) -> str:
        return f"<lazy module {self._name}>"
