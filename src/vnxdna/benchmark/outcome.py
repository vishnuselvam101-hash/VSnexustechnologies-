"""Decode outcome classification for experiments (V7 protocol §6; normative there, implemented here exactly).

Each decode is classified by comparing what the decoder *claims* with ground truth (SHA-256 of the original bytes):

==================  ===========================================================================================
EXACT               status SUCCESS, exit 0, output SHA-256 equal to the input
FALSE_SUCCESS       the decoder publishes bytes as correct (status SUCCESS with exit 0, a ``--select`` result, or
                    any byte range reported as verified in a PARTIAL report) and any of those bytes differ from the
                    input
PARTIAL             status PARTIAL; every byte reported as verified is correct; the share of verified bytes is
                    recorded
EXPLICIT_FAILURE    status FAILURE or non-zero exit with a typed error, no bytes published as correct
CRASH               uncaught exception, signal, sanitizer report or timeout; counted as a failure and filed as a
                    defect
==================  ===========================================================================================

Precedence: CRASH, then FALSE_SUCCESS (any published byte wrong outranks everything the decoder says about itself),
then EXACT, PARTIAL, EXPLICIT_FAILURE. A claim that fits none of the rows (for example SUCCESS with a non-zero exit, SUCCESS
that publishes nothing, or FAILURE that publishes verified bytes) is a decoder defect and is classified CRASH with the
reason ``inconsistent claim``: protocol §6 counts it as a failure and files it, and it can never be read as a success.

A published item is ``name → SHA-256`` of bytes the decoder presented as correct: ``"container"`` for the output of a
full decode, the archive path of each selected file for ``--select``, the archive path of each file extracted from a
PARTIAL decode. ``truth`` maps the same names to the SHA-256 of the original bytes. A published name absent from
``truth`` is wrong by definition (the decoder invented it).
"""
from __future__ import annotations

from dataclasses import dataclass, field

EXACT = "EXACT"
FALSE_SUCCESS = "FALSE_SUCCESS"
PARTIAL = "PARTIAL"
EXPLICIT_FAILURE = "EXPLICIT_FAILURE"
CRASH = "CRASH"
OUTCOMES = (EXACT, FALSE_SUCCESS, PARTIAL, EXPLICIT_FAILURE, CRASH)
STATUSES = ("SUCCESS", "PARTIAL", "FAILURE")


@dataclass
class DecodeClaim:
    """What one decode claimed. ``status``: the decoder's status (None when it raised); ``exit_code``: 0 for a
    returned SUCCESS, the CLI exit code otherwise (None if unknown); ``typed_error``: code of a typed VNX error;
    ``crash``: description of an uncaught exception, signal, sanitizer report or timeout; ``published``: name →
    SHA-256 of every byte range presented as correct; ``selective``: a ``--select`` decode (no container is published);
    ``verified_bytes`` / ``total_bytes``: the PARTIAL share."""

    status: str | None = None
    exit_code: int | None = None
    typed_error: str | None = None
    crash: str | None = None
    published: dict = field(default_factory=dict)
    selective: bool = False
    verified_bytes: int = 0
    total_bytes: int = 0


def classify_outcome(claim: DecodeClaim, truth: dict) -> dict:
    """``{"outcome", "reason", "verified_share", "wrong"}`` for one decode (protocol §6)."""
    if claim.status is not None and claim.status not in STATUSES:
        raise ValueError(f"unknown decoder status {claim.status!r}")

    def out(outcome: str, reason: str, share: float | None = None, wrong: list | None = None) -> dict:
        return {"outcome": outcome, "reason": reason, "verified_share": share, "wrong": sorted(wrong or [])}

    if claim.crash:
        return out(CRASH, claim.crash)
    wrong = [name for name, sha in claim.published.items() if truth.get(name) != sha]
    if wrong:
        return out(FALSE_SUCCESS, "published bytes differ from the input", wrong=wrong)
    if claim.status is None and claim.typed_error is None:
        raise ValueError("a claim needs a status, a typed error or a crash")
    if claim.status == "SUCCESS":
        if claim.exit_code != 0:
            return out(CRASH, f"inconsistent claim: SUCCESS with exit code {claim.exit_code}")
        if claim.selective:
            if not claim.published:
                return out(CRASH, "inconsistent claim: selective SUCCESS published nothing")
            return out(EXACT, "every selected file equals the input")
        if "container" not in claim.published:
            return out(CRASH, "inconsistent claim: SUCCESS published no container")
        return out(EXACT, "output SHA-256 equals the input")
    if claim.status == "PARTIAL":
        share = claim.verified_bytes / claim.total_bytes if claim.total_bytes else 0.0
        return out(PARTIAL, "every verified byte is correct", round(share, 6))
    # FAILURE status, or a typed error (status None)
    if claim.published:
        return out(CRASH, "inconsistent claim: a failed decode published bytes as correct")
    if claim.status is None and claim.exit_code == 0:
        return out(CRASH, "inconsistent claim: typed error with exit code 0")
    return out(EXPLICIT_FAILURE, claim.typed_error or "status FAILURE")


#: CLI exit codes of a returned (not raised) decode result (``vnxdna.commands.cli``: 0, 9 PARTIAL, 5 otherwise)
EXIT_OF_STATUS = {"SUCCESS": 0, "PARTIAL": 9, "FAILURE": 5}


def _sha256_file(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def decode_claim(call, output=None, *, partial_dir=None, select_dir=None, selective: bool = False,
                 total_bytes: int = 0) -> tuple:
    """Run ``call()`` (an in-process decode returning a ``DecodeResult`` or raising) and build its :class:`DecodeClaim`
    from what it actually published: the ``output`` file if it exists afterwards (whatever the status), every file under
    ``select_dir`` (selective) or ``partial_dir`` (PARTIAL). A typed ``VNXError`` is a typed error with its exit code;
    any other exception is a crash. Returns ``(claim, result or None, error or None)``."""
    from pathlib import Path
    from vnxdna.core.errors import VNXError

    if output is not None and Path(output).exists():
        raise ValueError(f"{output} exists before the decode: a stale file could be mistaken for published output")
    res = err = None
    claim = DecodeClaim(selective=selective, total_bytes=int(total_bytes))
    try:
        res = call()
    except VNXError as error:
        err = error
        claim.typed_error = getattr(error, "code", type(error).__name__)
        claim.exit_code = int(getattr(error, "exit_code", 70))
    except Exception as error:  # noqa: BLE001 - any other exception is a CRASH by definition
        err = error
        claim.crash = f"uncaught {type(error).__name__}: {str(error)[:300]}"
    if res is not None:
        claim.status = res.status
        claim.exit_code = EXIT_OF_STATUS.get(res.status)
    if output is not None and Path(output).exists():
        claim.published["container"] = _sha256_file(output)
    for root in (select_dir, partial_dir):
        if root is not None and Path(root).is_dir():
            for p in sorted(Path(root).rglob("*")):
                if p.is_file():
                    claim.published[p.relative_to(root).as_posix()] = _sha256_file(p)
                    claim.verified_bytes += p.stat().st_size
    return claim, res, err
