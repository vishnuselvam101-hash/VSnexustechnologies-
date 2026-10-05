"""atheris driver for the Python fuzz targets: ``python -m fuzz.atheris_run <target> [libFuzzer flags] [corpus dirs]``.

Run through ``fuzz/run.sh <target> <seconds>`` (PYTHONPATH = src:tests). atheris is optional; without it this exits 2.
"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    target = sys.argv[1]
    try:
        import atheris
    except ImportError:
        print("atheris is not installed (pip install atheris)", file=sys.stderr)
        return 2
    with atheris.instrument_imports(include=["vnxdna"]):
        import vnxdna.container.compression  # noqa: F401 - instrumented before first use
        import vnxdna.v2.strandio  # noqa: F401
        import vnxdna.v4.archive  # noqa: F401
        import vnxdna.v4.container  # noqa: F401
        import vnxdna.v4.decoder  # noqa: F401
        import vnxdna.v4.encoder  # noqa: F401
        import vnxdna.v4.frame  # noqa: F401
        import vnxdna.v4.reads  # noqa: F401
        import vnxdna.v4.sync  # noqa: F401
        import vnxdna.v6.native_reads  # noqa: F401
        import vnxdna.v6.native_rs  # noqa: F401
    from fuzz import harnesses
    if target not in harnesses.TARGETS:
        print(f"unknown target {target!r}; targets: {sorted(harnesses.TARGETS)}", file=sys.stderr)
        return 2
    fn = harnesses.TARGETS[target]
    harnesses.seed_archives()          # build fixtures once, outside the timed loop
    if target == "py-decode":
        harnesses._decode_seed()
    atheris.Setup([sys.argv[0]] + sys.argv[2:], fn)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":
    sys.exit(main())
