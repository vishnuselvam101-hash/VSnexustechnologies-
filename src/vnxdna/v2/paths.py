"""Up-front checks of output and temporary paths (VNX-DNA 3).

Commands used to discover an unusable output path (a directory, a path below
a regular file, an existing file without ``--force``) or a missing
``--temp-dir`` only when they finally wrote their result, often after all the
work, and then failed with an internal error. These checks run first and
raise the documented OUTPUT_ERROR (exit 8).
"""
from __future__ import annotations

import os
from pathlib import Path

from ..errors import OutputError


def check_output_file(path: str | os.PathLike, *, overwrite: bool, what: str = "output") -> Path:
    """Validate a file to be created: not a directory, not existing without ``overwrite``, parent creatable."""
    p = Path(path)
    if p.is_dir():
        raise OutputError(f"{what} is a directory: {p}")
    if p.exists() and not overwrite:
        raise OutputError(f"{what} already exists: {p} (use --force to overwrite)")
    parent = p.parent if str(p.parent) else Path(".")
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OutputError(f"cannot create the directory of {what} {p}: {error.strerror or error}") from None
    if not parent.is_dir():
        raise OutputError(f"cannot write {what} {p}: {parent} is not a directory")
    return p


def check_output_dir(path: str | os.PathLike, *, what: str = "output directory") -> Path:
    """Validate a directory to be used or created for outputs."""
    p = Path(path)
    if p.exists() and not p.is_dir():
        raise OutputError(f"{what} is not a directory: {p}")
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OutputError(f"cannot create {what} {p}: {error.strerror or error}") from None
    return p


def check_temp_dir(temp_dir: str | os.PathLike | None) -> None:
    """``--temp-dir`` must be an existing directory (it is not created implicitly)."""
    if temp_dir is not None and not Path(temp_dir).is_dir():
        raise OutputError(f"temporary directory does not exist or is not a directory: {temp_dir}")
