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

    def validate(self) -> ClusterConfig:
        ints = {"max_unplaced_reads": 0, "k": 4, "sketch_size": 1, "bucket_cap": 2, "min_shared_slots": 1, "max_candidate_pairs": 0,
                "band_slack": 0, "max_cluster_reads": 2, "max_refine_pairs": 0, "consensus_band": 1, "c_sub": 1,
                "c_indel": 1, "slack": 0, "min_votes": 1, "rounds": 0, "max_peels": 1, "max_trials": 1, "gmd_step": 1,
                "gmd_steps": 0, "medoid_members": 1}
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
        return self

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
