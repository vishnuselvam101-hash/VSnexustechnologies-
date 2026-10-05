"""Atomic publication of finished files (formerly in ``vnxdna.v2.container``; moved verbatim in V6 Phase 2, M3)."""
from __future__ import annotations

import os
from pathlib import Path

from vnxdna.core.taxonomy import OutputError


def publish(tmp: Path, target: Path, *, overwrite: bool) -> None:
    """Rename a finished temporary file into place.

    Without ``overwrite`` the file is hard-linked (fails if ``target`` appeared
    meanwhile) instead of renamed, so a file created after the up-front
    existence check is never replaced silently. File systems without hard
    links fall back to the rename.
    """
    if overwrite:
        os.replace(tmp, target)
        return
    try:
        os.link(tmp, target)
    except FileExistsError:
        raise OutputError(f"output already exists: {target} (created while this command ran; use --force to overwrite)") from None
    except OSError:
        # hard links unsupported here (EPERM, EXDEV, ENOTSUP, ENOSYS, EINVAL, EACCES on some FUSE/SMB mounts ...):
        # fall back to a checked rename (a file created in the instant between the check and the rename is replaced)
        if target.exists():
            raise OutputError(f"output already exists: {target} (created while this command ran; use --force to overwrite)") from None
        os.replace(tmp, target)
        return
    os.unlink(tmp)


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
