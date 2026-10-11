"""V7 item A (opt-in): header-independent read clustering, per-cluster consensus and fill-only merge.

Decode stage D7c ``cluster`` (V7_ARCHITECTURE §3, §5). The NumPy code here is the reference; the sketch, candidate,
distance, verification and forward-backward steps run in the native kernel ``vnxdna.native.cluster`` when it is
available (``VNXDNA_CLUSTER_BACKEND``), with identical results.

* :mod:`.store`      the unplaced-read store written in pass 1 (§5.1)
* :mod:`.sketch`     canonical k-mer MinHash sketches (§5.2 step 1)
* :mod:`.editdist`   banded unit-cost edit distance, vectorised over read pairs (§5.2 step 3)
* :mod:`.graph`      candidate pairs, verification, union-find components, refinement (§5.2 steps 2-5)
* :mod:`.consensus`  forward-backward certain calls, vote, decode-and-peel (§5.3)
* :mod:`.stage`      the stage itself: runs lazily, writes verified cluster frames, fill-only merge helpers (§5.4)

Enabled by ``DecodeOptions(read_clustering="fallback")`` (CLI ``--read-clustering fallback``); the default ``"off"``
leaves the 6.0 decode path byte for byte unchanged. Cluster frames never decide SUCCESS: every frame passes inner RS +
CRC-32 (I-5), may only fill superblock symbols and data addresses the 6.0 path left unresolved (FC-9), and the container
SHA-256 still decides publication (FC-1).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from vnxdna.core.errors import VNXConfigurationError

READ_CLUSTERING_MODES = ("off", "fallback")


CONSENSUS_TEMPLATES = ("wildcard", "full")
FILL_MODES = ("none", "template")
OUTER_LIST_MODES = ("off", "on")


@dataclass(frozen=True)
class ClusterConfig:
    """Parameters of the clustering and consensus stage (V7_ARCHITECTURE §5.2, §5.3; defaults as stated there)."""

    # §5.1 unplaced-read store and its budget (protocol §11: 4,000,000 reads, stop and report when exceeded)
    max_unplaced_reads: int = 4_000_000
    # §5.2 sketch and candidates
    k: int = 12
    sketch_size: int = 32
    bucket_cap: int = 256
    min_shared_slots: int = 2                   # candidate pair = reads sharing at least this many retained buckets
    max_candidate_pairs: int = 50_000_000       # work budget of the candidate step (stop and report)
    # V10: the budget scales with the unplaced reads, up to a ceiling: budget(n) = max(max_candidate_pairs,
    # min(max_candidate_pairs_ceiling, max_candidate_pairs_per_read * n)); 0 per read = the absolute budget only
    max_candidate_pairs_per_read: int = 200
    max_candidate_pairs_ceiling: int = 1_000_000_000
    candidate_chunk_pairs: int = 1 << 25        # slot pairs held in memory at once (8 B each); results do not depend on it
    # V12: over the budget, lower the bucket cap (largest buckets first: the least specific k-mers) down to this floor
    # instead of stopping; the stage stops only if no cap >= the floor fits. The budget, not the cap, bounds the work,
    # and every cluster frame is still verified (inner RS + CRC-32, container SHA-256), so a lower cap can only add
    # verified frames compared with stopping: default 2 (any cap). At 10 MB D13-F1 (SIMULATED, ~10 reads per strand)
    # the slot pairs come from buckets of <= 64 reads, so a floor of 64 still stopped. A floor >= bucket_cap disables it.
    bucket_cap_floor: int = 2
    # §5.2 verification: accept an edge iff distance <= theta * max(La, Lb); band |dL| + band_slack
    theta: float = 0.30
    band_slack: int = 32
    # §5.2 refinement of oversized components (V6 max_pending_per_address)
    max_cluster_reads: int = 64
    max_refine_pairs: int = 2_000_000           # work budget of the refinement step per decode (stop and report)
    # §5.3 consensus
    consensus_band: int = 32                    # per-cluster band B = min(consensus_band, max |len - T| + 4)
    c_sub: int = 3                              # known-frame mismatch cost (markers cost SyncCosts.marker_mismatch)
    c_indel: int = 6
    slack: int = 0                              # delta: optimal-path slack of the certain-call criterion
    vote_share: float = 0.6
    min_votes: int = 2
    rounds: int = 3
    max_peels: int = 3
    max_trials: int = 8                         # inner RS + CRC trials per cluster per peel
    gmd_step: int = 2
    gmd_steps: int = 4
    peel_theta: float = 0.15                    # a member is peeled when its distance to the verified strand <= this * T
    medoid_members: int = 16
    # §5.3 alignment template of the consensus rounds (EXPERIMENTAL, opt-in): "wildcard" (default, the reference path)
    # aligns to decided bases and leaves undecided frame positions as cost-0 wildcards; "full" fills every frame position
    # that has certain calls with its plurality base, so indels are placed against base values, not wildcards
    consensus_template: str = "wildcard"
    # V9 candidate E (EXPERIMENTAL, opt-in; needs consensus_template "full" and a layout with markers): after the
    # erasure decode and the GMD ladder fail, "template" decodes the polished template's bases at undecided positions
    # (instead of erasing them) and erases the lowest-confidence bytes, 0 ... inner parity, in ``fill_trials`` steps
    fill: str = "none"
    fill_trials: int = 5
    # Hybrid architecture (EXPERIMENTAL, opt-in): outer-assisted inner decoding of rows that stay short after the fill.
    # Failed consensus frames are tried at every missing address within ``list_max_header_distance`` header bytes,
    # with the header forced to that address and payload bytes repaired/erased from the outer code's column proposals
    # (C++ kernel vnxdna.native.outerlist); a symbol is added only when the repaired frame passes inner RS + CRC-32 at
    # that address, exactly as every other frame. Rows that reach k symbols then decode as usual; ``list_rounds``
    # iterations. The container SHA-256 still decides SUCCESS.
    outer_list: str = "off"
    max_list_candidates: int = 1_000_000        # failed frames kept (bounded memory)
    list_variants: int = 64                     # scrambler variants tried when assigning proposal candidates
    list_max_header_distance: int = 5           # bytes 0..9 (variant + header) a hypothesis may differ in
    list_min_extra: int = 1
    list_margin: int = 1
    list_max_subsets: int = 20_000              # f-subsets of candidate positions per column (deterministic)
    list_max_hypotheses: int = 2_000_000        # (frame, address) trials per round
    list_rounds: int = 4
    list_repair_bytes: int = 3                  # payload columns a tail-verified repair may change
    list_max_repairs: int = 4096                # repair variants per candidate
    list_min_tail_matches: int = 8              # of the 20 CRC-32 + inner parity bytes (random: ~7e-15 per trial)
    list_min_consistent: int = 12               # payload columns agreeing with a proposal to assign a frame by payload
    list_check_symbols: int = 1                 # verified symbols beyond k that the decoded row must reproduce
    list_ransac_hypotheses: int = 64            # frame-level RANSAC: hypotheses per row (0 = off)
    list_ransac_header_distance: int = 4        # header bytes a hypothesis frame may differ in
    list_ransac_tau: int = 8                    # agreeing payload columns of a supporter (random: ~1e-11 for 40 B)
    list_ransac_max_tuples: int = 50_000
    list_ransac_scan_variants: int = 8          # scrambler variants per failed frame scanned against the RANSAC codeword
    list_min_frame_match_percent: int = 50      # or: % of a failed frame's bytes matching a predicted missing frame

    def validate(self) -> ClusterConfig:
        ints = {"max_unplaced_reads": 0, "k": 4, "sketch_size": 1, "bucket_cap": 2, "min_shared_slots": 1, "max_candidate_pairs": 0,
                "max_candidate_pairs_per_read": 0, "max_candidate_pairs_ceiling": 0, "candidate_chunk_pairs": 1,
                "bucket_cap_floor": 0,
                "band_slack": 0, "max_cluster_reads": 2, "max_refine_pairs": 0, "consensus_band": 1, "c_sub": 1,
                "c_indel": 1, "slack": 0, "min_votes": 1, "rounds": 0, "max_peels": 1, "max_trials": 1, "gmd_step": 1,
                "gmd_steps": 0, "medoid_members": 1, "fill_trials": 1, "max_list_candidates": 0, "list_variants": 1,
                "list_max_header_distance": 0, "list_min_extra": 0, "list_margin": 1, "list_max_subsets": 1,
                "list_max_hypotheses": 0, "list_rounds": 1, "list_repair_bytes": 0, "list_max_repairs": 1,
                "list_min_tail_matches": 1, "list_min_consistent": 1, "list_check_symbols": 1,
                "list_ransac_hypotheses": 0, "list_ransac_header_distance": 0, "list_ransac_tau": 1,
                "list_ransac_max_tuples": 1, "list_ransac_scan_variants": 0,
                "list_min_frame_match_percent": 1}
        for name, lo in ints.items():
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < lo:
                raise VNXConfigurationError(f"cluster config {name} must be an integer >= {lo}")
        if self.k > 31:
            raise VNXConfigurationError("cluster config k must be at most 31 (2 bits per base in 64 bits)")
        if self.sketch_size > 256:
            raise VNXConfigurationError("cluster config sketch_size must be at most 256")
        if self.consensus_band > 64:
            raise VNXConfigurationError("cluster config consensus_band must be at most 64")
        for name in ("theta", "peel_theta"):
            v = getattr(self, name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 < v < 1.0:
                raise VNXConfigurationError(f"cluster config {name} must be in (0, 1)")
        if not isinstance(self.vote_share, (int, float)) or not 0.5 <= self.vote_share <= 1.0:
            raise VNXConfigurationError("cluster config vote_share must be in [0.5, 1]")
        if self.consensus_template not in CONSENSUS_TEMPLATES:
            raise VNXConfigurationError("cluster config consensus_template must be 'wildcard' or 'full'")
        if self.fill not in FILL_MODES:
            raise VNXConfigurationError("cluster config fill must be 'none' or 'template'")
        if self.outer_list not in OUTER_LIST_MODES:
            raise VNXConfigurationError("cluster config outer_list must be 'off' or 'on'")
        if self.list_min_frame_match_percent > 100:
            raise VNXConfigurationError("cluster config list_min_frame_match_percent must be at most 100")
        if self.list_variants > 256 or self.list_max_header_distance > 10 or self.list_max_subsets > 1 << 32:
            raise VNXConfigurationError("cluster config list_variants <= 256, list_max_header_distance <= 10, "
                                        "list_max_subsets <= 2^32")
        return self

    def candidate_budget(self, n_reads: int) -> int:
        """Slot-pair budget of the candidate step for ``n_reads`` unplaced reads (never below the absolute budget)."""
        scaled = min(self.max_candidate_pairs_ceiling, self.max_candidate_pairs_per_read * max(0, int(n_reads)))
        return max(self.max_candidate_pairs, scaled)

    def to_dict(self) -> dict:
        return asdict(self)


def validate_mode(mode, config) -> ClusterConfig | None:
    """Check ``DecodeOptions.read_clustering`` / ``cluster_config``; returns the effective config (None when off)."""
    if mode not in READ_CLUSTERING_MODES:
        raise VNXConfigurationError("read_clustering must be 'off' (default) or 'fallback'")
    if config is not None and not isinstance(config, ClusterConfig):
        raise VNXConfigurationError("cluster_config must be a ClusterConfig")
    if mode == "off":
        return config.validate() if config is not None else None
    return (config or ClusterConfig()).validate()
