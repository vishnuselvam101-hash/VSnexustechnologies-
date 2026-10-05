"""Old path of the physical-record validator, kept working (V6 Phase 7 moved it to :mod:`vnxdna.physical.validate`).

    python experiments/v6/physical/validate.py check record.json
    python experiments/v6/physical/validate.py template [--out record.template.json]

Exit status of ``check``: 0 = VALID, 2 = INVALID, 3 = INCOMPLETE. The schemas live in ``src/vnxdna/physical/schemas``;
``experiments/v6/physical/schema`` is a link to that directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
if (_SRC / "vnxdna" / "physical").is_dir() and "vnxdna" not in sys.modules:
    sys.path.insert(0, str(_SRC))   # run as a script from a checkout: use this checkout's package, not an installed one
import vnxdna.physical.validate as _impl  # noqa: E402

from vnxdna.physical.validate import *  # noqa: E402,F401,F403  (the old module's public names)
from vnxdna.physical.validate import __all__, main  # noqa: E402,F401

HERE = Path(__file__).resolve().parent
SCHEMA_DIR = _impl.SCHEMA_DIR

if __name__ == "__main__":
    raise SystemExit(main())
