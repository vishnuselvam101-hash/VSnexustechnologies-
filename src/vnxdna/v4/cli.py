"""Moved to :mod:`vnxdna.commands.cli` (V6 Phase 2, M5); this old path is an alias of the same module object."""
from typing import TYPE_CHECKING

from vnxdna.core._alias import alias_module

if TYPE_CHECKING:  # the runtime alias is invisible to type checkers: expose the moved module's names to them
    from vnxdna.commands.cli import *  # noqa: F401, F403

if __name__ == "__main__":
    from vnxdna.commands.cli import main

    main()  # returns None, as before: exit status 0 unless the CLI raised SystemExit itself
    raise SystemExit
alias_module(__name__, "vnxdna.commands.cli")
