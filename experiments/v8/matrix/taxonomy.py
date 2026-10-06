"""V8.9 automated failure taxonomy.

Every lost data strand (first lost stage of the ``tests/nanopore`` funnel) and every non-EXACT decode is mapped to one of
the 12 V8 categories. An unknown stage or outcome raises ``UnclassifiedFailure`` (a V8 release blocker), so
``classified == total`` holds by construction for every result that is returned.
"""
from __future__ import annotations

from collections import Counter

CATEGORIES = ("read_generation", "read_loss", "insufficient_coverage", "alignment", "clustering", "indel_placement",
              "consensus", "substitution_correction", "rs_parity_budget", "archive_reconstruction", "integrity_verification",
              "configuration_input_error")


class UnclassifiedFailure(RuntimeError):
    pass


def strand_category(lost_at: str, reason: str, pool_lost: bool) -> str:
    """Category of one lost data strand from the funnel's first lost stage."""
    if lost_at == "observed":
        return "read_loss" if pool_lost else "insufficient_coverage"      # lost in the pool / zero reads drawn
    if lost_at in ("two_reads", "stored"):
        return "insufficient_coverage"                                   # < 2 reads: no consensus possible
    if lost_at in ("clustered", "oriented"):
        return "clustering"
    if lost_at == "candidate":
        return "alignment"                                               # < 2 reads inside the consensus band
    if lost_at == "valid_frame":
        return "consensus"                                               # consensus is not a valid frame (frame or address)
    if lost_at == "rs_recoverable":
        return "indel_placement" if reason.startswith("shifted") else "substitution_correction"
    raise UnclassifiedFailure(f"unknown funnel stage {lost_at!r} ({reason!r})")


def decode_category(decode: dict, strand_counts: Counter, rescued_by: dict, reads: int) -> tuple[str, str | None]:
    """(primary category, contributing strand category) of a non-EXACT decode."""
    oc, stage = decode.get("outcome"), decode.get("terminal_stage")
    if oc == "EXACT":
        raise ValueError("EXACT decodes are not failures")
    if reads == 0:
        return "read_generation", None
    if oc == "FALSE_SUCCESS":
        return "integrity_verification", None                           # wrong data accepted: the critical failure
    code = decode.get("error_code") or ""
    top = strand_counts.most_common(1)[0][0] if strand_counts else None
    if stage == "outer_ecc":                                             # an outer row has more missing symbols than parity
        contrib = next((k for k, ok in rescued_by.items() if ok), None) or top
        return "rs_parity_budget", contrib
    if stage == "superblock":                                            # the superblock could not be recovered
        return "archive_reconstruction", top
    if stage == "archive":
        if code == "CONTAINER_HASH_MISMATCH":
            return "integrity_verification", top                        # detected and refused (not a false success)
        return "archive_reconstruction", top
    if stage == "read_parsing":
        return "configuration_input_error", None
    raise UnclassifiedFailure(f"cannot classify outcome {oc!r} at stage {stage!r} (error {code!r})")


def classify(doc: dict, M: int, pool_lost: set, cluster_ran: bool = True) -> dict:
    """Taxonomy of one decode document (``nanofunnel`` strands + decode). ``M`` = outer parity per row. Without the cluster
    stage (a pass-1 decode) the strand funnel is not observed: strand losses are reported as None, never as losses."""
    if not cluster_ran:
        out: dict = {"lost_strands": None, "lost_by_category": None, "failing_rows": None, "deficit": None,
                     "note": "cluster stage not run (pass-1 decode): strand funnel not observed"}
        if doc["decode"]["outcome"] != "EXACT":
            primary, contrib = decode_category(doc["decode"], Counter(), {}, doc.get("reads", 1))
            out.update(primary=primary, contributing=contrib)
        else:
            out.update(primary=None, contributing=None)
        return out
    data = [r for r in doc.get("strands", []) if r["kind"] == "data"]
    n_row = Counter(r["group"] for r in data)
    miss_row = Counter(r["group"] for r in data if r["lost_at"] is not None)
    cat: Counter = Counter()
    cat_row: dict = {}
    for r in data:
        if r["lost_at"] is None:
            continue
        k = strand_category(r["lost_at"], r.get("reason", ""), r["strand"] in pool_lost)
        cat[k] += 1
        cat_row.setdefault(r["group"], Counter())[k] += 1
    rescued = {k: all(miss_row[g] - cat_row.get(g, Counter())[k] <= M for g in n_row) for k in CATEGORIES}
    out = {"lost_strands": sum(cat.values()), "lost_by_category": {k: cat[k] for k in CATEGORIES if cat[k]},
           "failing_rows": sum(1 for g in n_row if miss_row[g] > M),
           "deficit": sum(max(0, miss_row[g] - M) for g in n_row)}
    if sum(cat.values()) != sum(miss_row.values()):
        raise UnclassifiedFailure("strand accounting does not add up")
    if doc["decode"]["outcome"] != "EXACT":
        primary, contrib = decode_category(doc["decode"], cat, {k: v for k, v in rescued.items() if cat[k]}, doc.get("reads", 1))
        out.update(primary=primary, contributing=contrib)
    else:
        out.update(primary=None, contributing=None)
    return out
