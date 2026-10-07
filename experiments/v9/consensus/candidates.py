"""V9 consensus candidates (docs/V9_PREREGISTRATION.md §5): name -> decoder cluster configuration.

``v8`` is the V8 production consensus (Phase 1 full-template consensus) and the baseline of every comparison. A
candidate's entry is frozen (its commit recorded) before its one EVAL evaluation; development uses DEV seeds only.
"""
from __future__ import annotations

CANDIDATES: dict[str, dict] = {
    "v8": {"consensus_template": "full"},
}


def cluster_config(name: str) -> dict:
    if name not in CANDIDATES:
        raise KeyError(f"unknown consensus candidate {name!r}; known: {sorted(CANDIDATES)}")
    return dict(CANDIDATES[name])
