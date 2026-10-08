"""V9 consensus candidates (docs/V9_PREREGISTRATION.md §5): name -> decoder cluster configuration.

``v8`` is the V8 production consensus (Phase 1 full-template consensus) and the baseline of every comparison. A
candidate's entry is frozen (its commit recorded) before its one EVAL evaluation; development uses DEV seeds only.
"""
from __future__ import annotations

CANDIDATES: dict[str, dict] = {
    "v8": {"consensus_template": "full"},
    # A: improved pairwise alignment — indel cost 4 in the consensus aligner (DEV proxy: the most frames of the c_indel /
    # c_sub / slack / band / rounds / vote_share sweep). Affine costs and an adaptive band were not built.
    "A": {"consensus_template": "full", "c_indel": 4},
    # E: hybrid indel-aware consensus — candidate A's move scoring plus template fill after the V8 GMD ladder fails
    "E": {"consensus_template": "full", "c_indel": 4, "fill": "template"},
}
# B (POA), C (quality-weighted) and D (homopolymer-aware) were not built in V9; they are not evaluated.


def cluster_config(name: str) -> dict:
    if name not in CANDIDATES:
        raise KeyError(f"unknown consensus candidate {name!r}; known: {sorted(CANDIDATES)}")
    return dict(CANDIDATES[name])
