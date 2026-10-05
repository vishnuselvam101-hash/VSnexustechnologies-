"""Moved to :mod:`vnxdna.dnaenc.strandio` (V6 Phase 2, M3); this old path is an alias of the same module object."""
from typing import TYPE_CHECKING

from vnxdna.core._alias import alias_module

if TYPE_CHECKING:  # the runtime alias is invisible to type checkers: expose the moved module's names to them
    from vnxdna.dnaenc.strandio import *  # noqa: F401, F403

alias_module(__name__, "vnxdna.dnaenc.strandio")
