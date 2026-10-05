"""Small shared helpers: canonical JSON, atomic output, environment capture."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from vnxdna.core.errors import VNXFormatError, VNXOutputError

MAX_JSON_BYTES = 16 << 20


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, compact separators, ASCII only, no NaN. Floats are rejected (determinism)."""
    _reject_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _reject_floats(obj: Any) -> None:
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in canonical VNX4 JSON")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError("JSON keys must be strings")
            _reject_floats(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_floats(v)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise VNXFormatError(f"duplicate JSON key {k!r}")
        out[k] = v
    return out


def parse_canonical_json(data: bytes, what: str) -> dict:
    """Parse JSON that must be canonical (byte-identical to its re-serialisation)."""
    if len(data) > MAX_JSON_BYTES:
        raise VNXFormatError(f"{what} is too large ({len(data)} bytes)")
    try:
        obj = json.loads(data.decode("ascii"), object_pairs_hook=_no_duplicates,
                         parse_float=_float_refused, parse_constant=_float_refused)
    except VNXFormatError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise VNXFormatError(f"{what} is not valid canonical JSON: {error}") from None
    if not isinstance(obj, dict):
        raise VNXFormatError(f"{what} must be a JSON object")
    try:
        again = canonical_json(obj)
    except RecursionError:              # nesting that json.loads accepts can still exceed the limit here
        raise VNXFormatError(f"{what} is nested too deeply") from None
    if again != data:
        raise VNXFormatError(f"{what} is not in canonical form")
    return obj


def _float_refused(value: str):
    raise VNXFormatError(f"non-integer number {value!r} in canonical JSON")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


@contextmanager
def atomic_output(target: str | os.PathLike, *, overwrite: bool = False, mode: int = 0o600) -> Iterator[Path]:
    """Yield a temporary path next to ``target``; rename it into place only if the block succeeds."""
    target = Path(target)
    if target.exists() and not overwrite:
        raise VNXOutputError(f"output already exists: {target} (use --force to overwrite)")
    if target.is_dir():
        raise VNXOutputError(f"output is a directory: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="." + target.name + ".", suffix=".partial", dir=target.parent)
    except OSError as error:
        raise VNXOutputError(f"cannot write {target}: {error.strerror or error}") from None
    os.close(fd)
    os.chmod(tmp, mode)
    tmp_path = Path(tmp)
    try:
        yield tmp_path
        with open(tmp_path, "rb+") as f:
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def write_json(path: str | os.PathLike, obj: Any, *, overwrite: bool = True) -> None:
    with atomic_output(path, overwrite=overwrite, mode=0o644) as tmp:
        tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def git_commit(cwd: str | os.PathLike | None = None) -> str | None:
    try:
        here = cwd or Path(__file__).resolve().parent
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True, timeout=5)
        commit = out.stdout.strip()
        if out.returncode != 0 or not commit:
            return None
        # "-dirty" only when code that can change results differs from the commit (documentation edits do not count)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", ":/src", ":/pyproject.toml"],
                               cwd=here, capture_output=True, text=True, timeout=5).stdout.strip()
        return commit + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return None


def environment() -> dict[str, Any]:
    """Machine and software description recorded with every benchmark and experiment."""
    from importlib import metadata

    from vnxdna.core.version import __version__, FORMAT_VERSION
    cpu = "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        cpu = platform.processor() or "unknown"
    ram = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram = int(line.split()[1]) * 1024
    except OSError:
        pass
    packages = {}
    for name in ("numpy", "cryptography", "zstandard", "reedsolo", "pydantic", "typer"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vnx_version": __version__, "vnx4_format_version": FORMAT_VERSION,
        "git_commit": git_commit(),
        "cpu_model": cpu, "logical_cpus": os.cpu_count(), "ram_bytes": ram,
        "os": f"{platform.system()} {platform.release()}", "platform": platform.platform(),
        "python": platform.python_version(), "packages": packages,
    }


def peak_rss_bytes() -> int:
    """Peak RSS of this process (and waited-for children reported separately by callers)."""
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
