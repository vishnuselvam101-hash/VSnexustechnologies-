"""Atomic publication of finished files (formerly in ``vnxdna.v2.container``; moved verbatim in V6 Phase 2, M3)."""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from vnxdna.core.taxonomy import OutputError


@contextmanager
def private_output(target: str | os.PathLike, *, mode: int = 0o600, suffix: str = ".partial") -> Iterator[IO[bytes]]:
    """Yield a private temporary file next to ``target``; publish it over ``target`` when the block ends.

    The temporary file is created with ``tempfile.mkstemp``: a unique name, ``O_CREAT|O_EXCL`` and mode 0600. A fixed
    name such as ``<name>.tmp`` can be planted as a symlink by anyone who may write in the directory, and the write
    would then go through it (and would keep the permissions of the planted file). The publication is a rename over
    ``target``: it replaces the name that is there (a symlink included) instead of writing through it, so the
    published path is a regular file.
    """
    t = Path(target)
    parent = t.parent if str(t.parent) else Path(".")
    fd, name = tempfile.mkstemp(prefix="." + t.name + ".", suffix=suffix, dir=parent)
    tmp = Path(name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, t)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
