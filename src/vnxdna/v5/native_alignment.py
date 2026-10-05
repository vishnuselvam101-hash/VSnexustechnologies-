"""Moved to :mod:`vnxdna.native.align` (V6 Phase 2, M1); this old path is an alias of the same module object."""
from vnxdna.core._alias import alias_module

if __name__ == "__main__":
    from vnxdna.native.align import main

    raise SystemExit(main())
alias_module(__name__, "vnxdna.native.align")
