"""Decode stage D7c ``cluster`` and the fill-only merge (V7_ARCHITECTURE §3, §5.4, FC-9; opt-in).

The stage runs **lazily**, at most once per decode: only when the 6.0 path cannot decode the superblock (D8 raises
NO_SUPERBLOCK) or a data row has too few 6.0 symbols to decode (the predicate of ``recovery.schedule._group_decodable``).
A pool that 6.0 decodes therefore never pays for clustering beyond the unplaced-read store of pass 1.

Run: unplaced reads → sketches → candidate pairs → verified edges → components → refinement → consensus per cluster
(in cluster-ID order) → verified frames (inner RS + CRC-32, I-5), resolved by strict majority of identical copies and
written to ``cacc{b}.bin`` (bucket = *verified* group mod B), never to the 6.0 ``acc`` files.

Fill-only (FC-9): cluster symbols are added only (a) to the superblock decode, and only after the 6.0 symbols alone
raised NO_SUPERBLOCK, at superblock symbols the 6.0 path did not resolve; (b) to data rows the 6.0 symbols cannot decode,
at addresses the 6.0 path did not resolve. A cluster symbol at an address 6.0 resolved is never used; it is counted as
a confirmation (same payload) or a conflict (different payload). The container SHA-256 still decides SUCCESS (FC-1).
"""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

import numpy as np

from vnxdna.dnaenc.layout import KIND_DATA, KIND_SUPER, Layout
from vnxdna.recovery.cluster import ClusterConfig
from vnxdna.recovery.cluster.consensus import cluster_consensus
from vnxdna.recovery.cluster.editdist import revcomp
from vnxdna.recovery.cluster.graph import cluster_reads
from vnxdna.recovery.cluster.sketch import sketch_reads
from vnxdna.recovery.cluster.store import UnplacedStore
from vnxdna.recovery.consensus import resolve_duplicates

SCHEMA = "vnx.read-clustering/1"
#: clusters handed to the consensus step at once (bounds memory; the result does not depend on it)
CONSENSUS_BATCH = 256


def cluster_frames(reads: list, lay: Layout, cfg: ClusterConfig, marker_mismatch: int = 4, counts: Counter | None = None,
                   cons: Counter | None = None, checkpoint=None, raw: np.ndarray | None = None) -> tuple[list[dict], dict | None]:
    """Reads (any order and orientation) → verified cluster frames, in cluster-ID order, and the work budget that
    stopped the stage (None if none did). Every frame passed inner RS + CRC-32; nothing here trusts a read's header.
    ``raw``: the reads as a zero-padded (n, W) matrix when the caller already has one (the store's memory map)."""
    counts = Counter() if counts is None else counts
    cons = Counter() if cons is None else cons
    n = len(reads)
    lens = np.fromiter((r.size for r in reads), dtype=np.int64, count=n)
    if raw is None:
        raw = np.full((n, max(1, int(lens.max(initial=1)))), 4, dtype=np.uint8)
        for i, r in enumerate(reads):
            raw[i, : r.size] = r
    hashes, orient = sketch_reads(raw, lens, cfg.k, cfg.sketch_size)
    cl = cluster_reads(reads, hashes, orient, cfg)
    counts.update(cl.counts)
    if cl.budget is not None and cl.budget["limit"] == "max_candidate_pairs":
        return [], cl.budget
    clusters = []
    for cid in np.unique(cl.labels[cl.labels >= 0]).tolist():
        mem = np.flatnonzero(cl.labels == cid)
        if mem.size > cfg.max_cluster_reads:
            counts["cluster_reads_capped"] += int(mem.size - cfg.max_cluster_reads)
            mem = mem[: cfg.max_cluster_reads]
        clusters.append((cid, [revcomp(reads[i]) if cl.orient[i] else reads[i] for i in mem.tolist()]))
    frames: list[dict] = []
    for c0 in range(0, len(clusters), CONSENSUS_BATCH):
        if checkpoint is not None:
            checkpoint()
        frames += cluster_consensus(lay, clusters[c0:c0 + CONSENSUS_BATCH], cfg, marker_mismatch, cons)
    counts["clusters_decoded"] = len({f["cluster"] for f in frames})
    return frames, cl.budget


class ClusterStage:
    """One decode's clustering stage: the unplaced store (pass 1), the lazy run, the cluster frames and the fill."""

    def __init__(self, workdir: Path, lay: Layout, cfg: ClusterConfig, buckets: int, acc_dtype: np.dtype,
                 marker_mismatch: int = 4, stage_counters=None, planner=None):
        self.dir = Path(workdir)
        self.lay = lay
        self.cfg = cfg
        self.B = int(buckets)
        self.acc_dtype = acc_dtype
        self.marker_mismatch = int(marker_mismatch)
        self.sc = stage_counters
        self.planner = planner
        self.store = UnplacedStore(self.dir, lay.strand_nt, cfg.max_unplaced_reads)
        self.counts: Counter = Counter()          # clustering-stage counters (stage "clustering")
        self.cons: Counter = Counter()            # consensus counters (stage "consensus", prefixed cluster_)
        self.fill: Counter = Counter()
        self.status = "not_run"
        self.trigger: str | None = None
        self.budget: list[dict] = []
        self.seconds = 0.0
        self.frames = 0

    # ------------------------------------------------------------------------------------------------ pass 1
    def write(self, res: dict) -> None:
        rec = res.get("unpl")
        if rec is not None:
            self.store.write(rec)

    def close_pass1(self) -> None:
        self.store.close()
        self.counts.update(self.store.counts)
        if self.store.exceeded:
            self.budget.append({"limit": "max_unplaced_reads", "allowed": self.cfg.max_unplaced_reads,
                                "used": self.store.counts["unplaced_offered"],
                                "effect": "clustering stage not run; the decode continues on the 6.0 path"})
        self._publish_counters()

    # ------------------------------------------------------------------------------------------------ the stage
    @property
    def ran(self) -> bool:
        return self.status != "not_run"

    def ensure_run(self, trigger: str) -> None:
        if self.ran:
            return
        self.trigger = trigger
        t = time.perf_counter()
        try:
            self._run()
        finally:
            self.seconds = time.perf_counter() - t
            self._publish_counters()

    def _run(self) -> None:
        if self.store.exceeded:
            self.status = "budget_exceeded"
            if self.planner is not None:
                self.planner.decide("CLUSTER", False, "read clustering not run: the unplaced-read budget was exceeded",
                                    {"max_unplaced_reads": self.cfg.max_unplaced_reads})
            return
        if self.planner is not None:
            self.planner.checkpoint("read clustering")
            self.planner.decide("CLUSTER", True, f"read clustering (fill-only): triggered by {self.trigger}",
                                {"unplaced_reads": self.store.stored})
        recs = self.store.load()
        n = len(recs)
        lens = np.asarray(recs["len"], dtype=np.int64) if n else np.zeros(0, dtype=np.int64)
        reads = [np.array(recs["raw"][i, : lens[i]], dtype=np.uint8) for i in range(n)]
        check = (lambda: self.planner.checkpoint("read clustering consensus")) if self.planner is not None else None
        frames, budget = cluster_frames(reads, self.lay, self.cfg, self.marker_mismatch, self.counts, self.cons, check,
                                        raw=recs["raw"] if n else None)
        if budget is not None:
            self.budget.append(budget)
            if budget["limit"] == "max_candidate_pairs":
                self.status = "budget_exceeded"
                return
        self._write_frames(frames)
        self.frames = len(frames)
        self.status = "ran"
        if self.planner is not None:
            self.planner.outcome("CLUSTER", clusters=int(self.counts.get("clusters", 0)), frames_verified=len(frames))

    def _write_frames(self, frames: list[dict]) -> None:
        files = {}
        try:
            for b in range(self.B):
                files[b] = open(self.dir / f"cacc{b}.bin", "wb")
            if not frames:
                return
            rec = np.zeros(len(frames), dtype=self.acc_dtype)
            rec["kind"] = [f["kind"] for f in frames]
            rec["tag"] = [f["tag"] for f in frames]
            rec["group"] = [f["group"] for f in frames]
            rec["symbol"] = [f["symbol"] for f in frames]
            rec["payload"] = np.stack([f["payload"] for f in frames])
            kinds = Counter(int(f["kind"]) for f in frames)
            self.counts["frames_data"] += kinds.get(KIND_DATA, 0)
            self.counts["frames_superblock"] += kinds.get(KIND_SUPER, 0)
            bucket = rec["group"].astype(np.int64) % self.B
            for b in np.unique(bucket).tolist():
                files[b].write(rec[bucket == b].tobytes())
        finally:
            for f in files.values():
                f.close()

    def load(self, b: int) -> np.ndarray:
        path = self.dir / f"cacc{b}.bin"
        if not self.ran or not path.exists():
            return np.zeros(0, dtype=self.acc_dtype)
        return np.fromfile(path, dtype=self.acc_dtype)

    # ------------------------------------------------------------------------------------------------ fill-only merge
    def superblock_records(self) -> np.ndarray:
        acc = self.load(0)
        return acc[(acc["kind"] == KIND_SUPER) & (acc["group"] == 0)]

    def fill_superblock(self, symbols: dict, values: dict, ks_ms: int) -> int:
        """Add cluster superblock symbols at (tag, symbol) keys the 6.0 path left unresolved. ``values`` (tag → symbol
        → distinct verified payloads) gains the filled values, so the two-archive check of spec §3.10 step 7 sees them."""
        acc = self.superblock_records()
        acc = acc[acc["symbol"] < ks_ms]
        csym, conflicts = resolve_duplicates(acc)
        self.fill["superblock_cluster_conflicts"] += conflicts
        filled = 0
        for key in sorted(csym):
            v = csym[key]
            if key in symbols:
                self.fill["superblock_confirmations" if np.array_equal(symbols[key], v) else "superblock_conflicts"] += 1
                continue
            symbols[key] = v
            vs = values.setdefault(int(key[1]), {}).setdefault(int(key[3]), [])
            if not any(np.array_equal(x, v) for x in vs):
                vs.append(np.array(v))
            filled += 1
        self.fill["superblock_symbols"] += filled
        self._publish_counters()
        return filled

    def fill_rows(self, b: int, symbols: dict, groups: list, tag: int, total: int, codec, row_k, P: int) -> dict:
        """Add cluster data symbols of bucket ``b`` for rows in ``groups`` that the 6.0 symbols cannot decode, at
        addresses 6.0 left unresolved. Runs the stage (lazily) only when such a row exists. Returns group → filled."""
        from vnxdna.recovery.schedule import _group_decodable
        by_group: dict[int, dict] = {}
        for (kd, tg, g, s), v in symbols.items():
            if kd == KIND_DATA and tg == tag:
                by_group.setdefault(g, {})[s] = v
        short = {g for g in groups if not _group_decodable(codec, by_group.get(g, {}), row_k(g), P, g)}
        if not short:
            return {}
        self.ensure_run("rows")
        acc = self.load(b)
        acc = acc[(acc["kind"] == KIND_DATA) & (acc["tag"] == tag) & (acc["group"] < total)]
        csym, conflicts = resolve_duplicates(acc)
        self.fill["data_cluster_conflicts"] += conflicts
        filled: Counter = Counter()
        for key in sorted(csym):
            v = csym[key]
            if key in symbols:
                self.fill["data_confirmations" if np.array_equal(symbols[key], v) else "data_conflicts"] += 1
                continue
            if key[2] in short:
                symbols[key] = v
                filled[key[2]] += 1
            else:
                self.fill["data_unused_row_decodable"] += 1
        self.fill["data_symbols"] += sum(filled.values())
        self.fill["rows_filled"] += len(filled)
        self.fill["rows_short"] += len(short)
        self._publish_counters()
        return dict(filled)

    # ------------------------------------------------------------------------------------------------ reporting
    def _publish_counters(self) -> None:
        """Mirror into the V7 stage counters (absolute values; idempotent)."""
        sc = self.sc
        if sc is None:
            return
        for k in [k for k in sc.c if k.startswith("clustering.") or k.startswith("consensus.cluster_")]:
            del sc.c[k]
        sc.add("clustering", "applicable", 1)
        sc.add("clustering", "stage_run", int(self.status == "ran"))
        for k, v in sorted(self.counts.items()):
            if v:
                sc.add("clustering", k, v)
        for k, v in sorted(self.fill.items()):
            if v:
                sc.add("clustering", f"fill_{k}", v)
        for k, v in sorted(self.cons.items()):
            if v:
                sc.add("consensus", k, v)

    def report(self) -> dict:
        conflicts = self.fill.get("superblock_conflicts", 0) + self.fill.get("data_conflicts", 0)
        out = {"schema": SCHEMA, "mode": "fallback", "evidence": "fill-only (FC-9); SUCCESS still requires the "
               "container SHA-256", "config": self.cfg.to_dict(), "status": self.status, "trigger": self.trigger,
               "store": {k: int(self.counts.get(k, 0)) for k in ("unplaced_offered", "unplaced_stored",
                                                                  "unplaced_too_short", "unplaced_too_long",
                                                                  "unplaced_over_budget")},
               "clustering": {k: int(v) for k, v in sorted(self.counts.items()) if not k.startswith("unplaced_")},
               "consensus": {k[len("cluster_"):]: int(v) for k, v in sorted(self.cons.items())},
               "frames_verified": self.frames, "fill": {k: int(v) for k, v in sorted(self.fill.items())},
               "budget_exceeded": list(self.budget)}
        if conflicts:
            out["hint"] = "cluster/V6 conflict: a verified cluster frame disagrees with a 6.0 symbol at the same address"
        return out
