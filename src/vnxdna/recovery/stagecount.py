"""Per-stage decode counters (V7 protocol §8.1 failure taxonomy; opt-in, observability only).

``DecodeOptions(stage_counters=True)`` adds one block, ``report["stage_counters"]``, to the decode report (and to the
``details`` of a typed decode error). Every counter is computed from what the decoder itself sees: no ground truth is
used here. Questions that need ground truth (which strand a read came from, whether a header was corrupted or the read
was never placed, the first failing stage of a decode) are answered by the experiment harness
(``experiments/v7/a-diag``), which combines these counters with the simulator's truth.

Nothing here changes what the decoder decodes or publishes: with the option off no counter is computed and the report
is unchanged; with it on, the decoded bytes and every other report key are identical (tests/v7/test_stage_counters.py).

Stages, in the order of protocol §8.1 (``STAGES``):

* ``read_parsing``    reads parsed, reads containing N (non-ACGT), empty reads. Malformed or oversized records are
                      refused by the parser with a typed error (terminal stage ``read_parsing``).
* ``orientation``     orientation pre-pass flips, reverse-complement retries attempted / adopted, accepted reads read
                      as reverse complement.
* ``clustering``      ``{"applicable": 0}`` unless read clustering is on (``read_clustering="fallback"``, V7 item A):
                      then the unplaced-read store, sketch/candidate/verification/component counters, unassigned
                      reads and the fill-only merge (``recovery.cluster.stage``); its consensus counters are in
                      ``consensus`` with the prefix ``cluster_``.
* ``consensus``       pass-2 consensus per missing address: attempts with one / several reads, groups capped at
                      ``max_pending_per_address``, ambiguous columns (votes split below the threshold), empty columns
                      (no read has a base), frames whose erasures exceed the inner parity, recoveries.
* ``alignment``       per read: exact length, net drift within the band / within the retry window / beyond it, aligned,
                      and the reads whose first failure is alignment (``fail_drift_beyond_band``: net length drift
                      beyond the band; ``fail_path_beyond_band``: net drift inside the band but no path inside it).
                      Marker mismatches of aligned reads.
* ``indel_placement`` erased frame bytes of aligned reads; reads whose first failure is that the indel erasures
                      alone exceed the inner parity r (``fail_erasures_exceed_parity``).
* ``address``         pass 1: failed aligned reads kept as pending (header readable) or dropped (both header readings
                      have an invalid version nibble: wrong scrambler variant or corrupted header), headers with
                      erased bases; pass 2: pending records placed directly, snapped, or left unplaced, and consensus
                      frames that verified under another address.
* ``inner_ecc``       reads (pass 1) and consensus frames (pass 2) whose inner RS decoding failed (erasures within r).
* ``crc``             reads and consensus frames whose RS decoding succeeded but whose CRC-32 failed.
* ``coverage``        missing addresses (no verified pass-1 read) with 0, 1 or several placed pending reads.
* ``outer_ecc``       rows attempted / decoded / failed, symbols per row from pass 1 and from consensus; the first 100
                      failed rows with their symbol counts.
* ``superblock``      symbols needed and present, pending superblock records, consensus results, decoded or not.
* ``archive``         container SHA-256 checked / matched.

``terminal_stage`` is where the decoder stopped (its own view): ``None`` on SUCCESS, ``outer_ecc`` for unrecovered
groups, ``superblock`` for NO_SUPERBLOCK, ``archive`` for a SHA-256 mismatch, ``read_parsing`` for input errors.
Counters add up over read batches in any order, so they do not depend on the worker count.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

SCHEMA = "vnx.stage-counters/1"
STAGES = ("read_parsing", "orientation", "clustering", "consensus", "alignment", "indel_placement", "address",
          "inner_ecc", "crc", "coverage", "outer_ecc", "superblock", "archive")
MAX_FAILED_ROWS = 100


class StageCounters:
    """Counters keyed ``"<stage>.<name>"`` (a Counter, so batches and buckets simply add up)."""

    def __init__(self) -> None:
        self.c: Counter = Counter()
        self.failed_rows: dict[int, dict] = {}

    def add(self, stage: str, name: str, n: int = 1) -> None:
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        self.c[f"{stage}.{name}"] += int(n)

    def update(self, counts: dict) -> None:
        for k, v in counts.items():
            if k.split(".", 1)[0] not in STAGES:
                raise ValueError(f"unknown stage in {k!r}")
            self.c[k] += int(v)

    def failed_row(self, group: int, info: dict) -> None:
        if group not in self.failed_rows and len(self.failed_rows) < MAX_FAILED_ROWS:
            self.failed_rows[int(group)] = {k: int(v) for k, v in info.items()}

    def block(self, *, status: str | None = None, report: dict | None = None, error: BaseException | None = None) -> dict:
        stages: dict = {s: {} for s in STAGES}
        for k in sorted(self.c):
            s, name = k.split(".", 1)
            stages[s][name] = int(self.c[k])
        if not stages["clustering"]:          # V7 item A off (or a 6.0 decode): no clustering stage
            stages["clustering"] = {"applicable": 0}
        failed = sorted((report or {}).get("failed_groups") or [])
        rows = [dict(group=g, **self.failed_rows[g]) for g in failed if g in self.failed_rows]
        return {"schema": SCHEMA, "taxonomy": "V7_PROTOCOL section 8.1", "stages": stages,
                "outer_ecc_failed_rows": rows, "terminal_stage": terminal_stage(status, report, error)}


def terminal_stage(status: str | None, report: dict | None, error: BaseException | None) -> str | None:
    """Where the decoder stopped, from its own outcome (no ground truth)."""
    if error is not None:
        code = getattr(error, "code", None)
        stage = getattr(error, "stage", None)
        if code == "NO_SUPERBLOCK" or stage == "superblock":
            return "superblock"
        if code == "CONTAINER_HASH_MISMATCH" or stage == "integrity":
            return "archive"
        if stage in ("input", "format"):
            return "read_parsing"
        return f"other:{stage}"
    if status == "SUCCESS":
        return None
    if (report or {}).get("groups_failed"):
        return "outer_ecc"
    return "archive"


# ------------------------------------------------------------------------------------------------ pass 1 (per batch)
def pass1_counts(lay, lengths: np.ndarray, codes: np.ndarray, d: dict, acc: np.ndarray, path: np.ndarray,
                 cost: np.ndarray, band: int, retry_band: int, inf: int) -> tuple[Counter, np.ndarray]:
    """Counters of one pass-1 batch and a per-read code of the first stage at which each read failed (0 = accepted).

    ``d``: per-read arrays of the final attempt (``erased_bytes``, ``rs_ok``, ``crc_ok``, ``marker_mm``), the orientation
    arrays (``flip``, ``rc_tried``, ``rc_adopted``, ``rc_used``) and the address arrays (``pend``, ``pend_good``,
    ``header_erased``, ``reading``) filled by ``recovery.pass1``."""
    c: Counter = Counter()
    n = int(lengths.size)
    T = lay.strand_nt
    r = lay.inner_parity
    offs = np.concatenate([[0], np.cumsum(lengths)])
    has_n = np.zeros(n, dtype=bool)
    if codes.size:
        bad = np.flatnonzero(codes > 3)
        if bad.size:
            has_n[np.unique(np.searchsorted(offs, bad, side="right") - 1)] = True
    c["read_parsing.reads"] = n
    c["read_parsing.reads_with_n"] = int(has_n.sum())
    c["read_parsing.empty"] = int((lengths == 0).sum())
    c["orientation.prepass_flipped"] = int(d["flip"].sum())
    c["orientation.rc_retry_attempted"] = int(d["rc_tried"].sum())
    c["orientation.rc_retry_adopted"] = int(d["rc_adopted"].sum())
    c["orientation.accepted_reverse_complement"] = int((d["rc_used"] & acc).sum())
    drift = np.abs(lengths - T)
    wide = max(band, retry_band)
    aligned = cost < inf
    c["alignment.exact_length"] = int((drift == 0).sum())
    c["alignment.net_drift_within_band"] = int((drift <= band).sum())
    c["alignment.net_drift_in_retry_window"] = int(((drift > band) & (drift <= wide)).sum()) if retry_band else 0
    c["alignment.net_drift_beyond_band"] = int((drift > wide).sum())
    c["alignment.aligned"] = int(aligned.sum())
    c["alignment.marker_mismatches"] = int(d["marker_mm"][aligned].sum())
    c["alignment.aligned_with_marker_mismatch"] = int((d["marker_mm"][aligned] > 0).sum())
    eb = d["erased_bytes"]
    c["indel_placement.erased_bytes"] = int(eb[aligned].sum())
    c["indel_placement.aligned_erasures_within_parity"] = int((aligned & (eb <= r)).sum())
    c["indel_placement.aligned_erasures_exceed_parity"] = int((aligned & (eb > r)).sum())
    # first failing stage of every read (pipeline order); 0 = verified in pass 1
    fate = np.zeros(n, dtype=np.int8)
    rest = ~acc
    fate[rest & ~aligned & (drift > wide)] = 1
    fate[rest & ~aligned & (drift <= wide)] = 2
    al_f = rest & aligned
    fate[al_f & (eb > r)] = 3
    fate[al_f & (eb <= r) & ~d["rs_ok"]] = 4
    fate[al_f & (eb <= r) & d["rs_ok"] & ~d["crc_ok"]] = 5
    fate[al_f & (eb <= r) & d["rs_ok"] & d["crc_ok"]] = 6
    c["alignment.fail_drift_beyond_band"] = int((fate == 1).sum())
    c["alignment.fail_path_beyond_band"] = int((fate == 2).sum())
    c["indel_placement.fail_erasures_exceed_parity"] = int((fate == 3).sum())
    c["inner_ecc.fail_decode"] = int((fate == 4).sum())
    c["crc.fail_mismatch"] = int((fate == 5).sum())
    c["address.fail_invalid_version_or_kind"] = int((fate == 6).sum())
    for k, v in ((1, "fast"), (2, "sync"), (3, "smart"), (4, "soft")):
        c[f"inner_ecc.verified_{v}"] = int((acc & (path == k)).sum())
    c["inner_ecc.verified_after_rs_errata"] = int((acc & (d["errata"] > 0)).sum())
    c["address.pending_kept"] = int(d["pend_good"].sum())
    c["address.pending_dropped_header_unreadable"] = int(d["pend"].sum() - d["pend_good"].sum())
    c["address.pending_header_erased"] = int(d["header_erased"].sum())
    c["address.pending_projected_reading"] = int((d["reading"] == 1).sum())
    c["address.pending_raw_prefix_reading"] = int((d["reading"] == 2).sum())
    return Counter({k: v for k, v in c.items() if v}), fate


# ------------------------------------------------------------------------------------------------ pass 2 (consensus)
def consensus_counts(sc: StageCounters | None, *, superblock: bool, n_records: int, direct: int, snapped: int,
                     unplaced: int, missing: set | None, placed: Counter, groups: list, erased: list, P, cand_keys: list,
                     recovered: dict, threshold: float, cap: int, r: int) -> None:
    """Pass-2 consensus counters of one call. ``groups``: the vote input (m, frame_nt) of every attempt, in the order of
    ``cand_keys``; ``erased``: the byte erasures of each attempt's consensus frame; ``P``: its ``decode_frames`` result."""
    if sc is None:
        return

    def put(stage: str, name: str, n: int) -> None:
        if n:
            if superblock:
                sc.add("superblock", f"consensus_{name}" if stage == "consensus" else f"consensus_{stage}_{name}", n)
            else:
                sc.add(stage, name, n)

    put("address", "pass2_pending_records", n_records)
    put("address", "pass2_placed_direct", direct)
    put("address", "pass2_placed_snapped", snapped)
    put("address", "pass2_unplaced", unplaced)
    if missing is not None and not superblock:
        sc.add("coverage", "missing_addresses", len(missing))
        sc.add("coverage", "missing_no_placed_read", sum(1 for k in missing if placed.get(k, 0) == 0))
        sc.add("coverage", "missing_one_placed_read", sum(1 for k in missing if placed.get(k, 0) == 1))
        sc.add("coverage", "missing_several_placed_reads", sum(1 for k in missing if placed.get(k, 0) >= 2))
    put("consensus", "attempted", len(cand_keys))
    for key, g, eb, i in zip(cand_keys, groups, erased, range(len(cand_keys))):
        m = g.shape[0]
        put("consensus", "single_read" if m == 1 else "several_reads", 1)
        put("consensus", "capped", int(placed.get(key, 0) > cap))
        votes = np.stack([(g == b).sum(axis=0) for b in range(4)], axis=1)
        total = votes.sum(axis=1)
        share = np.where(total > 0, votes.max(axis=1) / np.maximum(total, 1), 0.0)
        put("consensus", "empty_columns", int((total == 0).sum()))
        put("consensus", "ambiguous_columns", int(((total > 0) & (share < threshold)).sum()))
        f = int(eb.sum())
        if key in recovered:
            put("consensus", "recovered", 1)
        elif f > r:
            put("consensus", "fail_erasures_exceed_parity", 1)
        elif not bool(P.rs_ok[i]):
            put("inner_ecc", "pass2_fail_decode", 1)
        elif not bool(P.crc_ok[i]):
            put("crc", "pass2_fail_mismatch", 1)
        else:
            put("address", "pass2_verified_other_address", 1)
