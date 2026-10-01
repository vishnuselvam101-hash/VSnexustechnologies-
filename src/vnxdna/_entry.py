"""Console entry point. Importing the CLI (NumPy, cryptography, pydantic, typer) takes a noticeable fraction of a
second; an interrupt (Ctrl-C, SIGTERM, SIGHUP) during that import ends with the normal message and exit 130 instead of a traceback."""
import signal
import sys


def _interrupt(signum, frame) -> None:
    raise KeyboardInterrupt  # only installed while the CLI is imported, before any worker process exists


def main() -> None:  # pragma: no cover - console entry point
    for name in ("SIGTERM", "SIGHUP"):  # until vnxdna.cli.main installs its own handler
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _interrupt)
    try:
        from .cli import main as cli_main
    except KeyboardInterrupt:
        sys.stderr.write("vnx-dna: interrupted\n")
        raise SystemExit(130)
    cli_main()
