"""Moved to :mod:`vnxdna.commands.cli` (V6 Phase 2, M5); this old path is an alias of the same module object."""
from vnxdna.core._alias import alias_module

if __name__ == "__main__":
    from vnxdna.commands.cli import main

    raise SystemExit(main())
alias_module(__name__, "vnxdna.commands.cli")
