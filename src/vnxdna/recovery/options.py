"""Decode options and the decode result (formerly in ``vnxdna.v4.decoder``; V6 Phase 2, M4)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import Layout
from vnxdna.sync.template import SyncCosts

#: V6-SEC-01: the largest container a superblock may claim before the decoder refuses it (RESOURCE_LIMIT, exit 3). A
#: forged, CRC-valid superblock chooses ``container_size`` freely (u64, up to 2^32 groups) and pass 2's work grows with
#: it. 4 GiB leaves room for the 1 GiB inputs of EXP-0012 (and their container overhead); larger archives pass a
#: larger ``max_container_bytes`` (``vnx decode --max-container-bytes``).
DEFAULT_MAX_CONTAINER_BYTES = 4 << 30


ENGINES = ("auto", "stream", "v13", "reference")
#: V13 configuration surface (docs/V13_ENGINE_ARCHITECTURE.md §6): the environment sets the *defaults* of the engine
#: options; an explicit option or CLI flag always wins.
ENV_ENGINE, ENV_ENGINE_THREADS = "VNXDNA_ENGINE", "VNXDNA_ENGINE_THREADS"


def _integer(name: str, value, lo: int, hi: int) -> int:
    """``value`` as an int in ``lo..hi`` (Python or NumPy integers; bools, floats and strings are refused)."""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)) or not lo <= int(value) <= hi:
        raise VNXConfigurationError(f"{name} must be an integer in {lo}..{hi}")
    return int(value)


def _env_engine() -> str:
    return (os.environ.get(ENV_ENGINE) or "auto").strip().lower()


def _env_engine_threads() -> int | None:
    v = os.environ.get(ENV_ENGINE_THREADS)
    if v is None or not v.strip():
        return None
    try:
        return int(v)
    except ValueError:
        return -1                           # reported by validate() as a configuration error


@dataclass
class DecodeOptions:
    profile: str | None = None          # None = detect from read lengths
    layout: Layout | None = None
    workers: int = 1
    batch_reads: int = 8192
    band: int = 6
    # V6 (opt-in, job #80): 0 = off. Reads whose net drift |len − strand_nt| exceeds ``band`` but not ``retry_band`` are
    # aligned once more with this wider band instead of being left unaligned. Reads inside ``band`` are unaffected.
    retry_band: int = 0
    min_quality: int = 0                # bases below this Phred score become erasures (soft-information hook)
    reverse_complement: bool = True
    # V13 D1: "bounded" (default) skips the reverse-complement retry of a failed read when a lower bound on its
    # reverse-complement alignment cost already reaches its forward cost (only a verified reverse-complement frame
    # could then adopt the retry; report["reads"]["rc_retry_skipped"] counts them); "always" retries every failed
    # read as V12 did. Only the default recovery configuration (no smart/soft recovery, no quality-weighted
    # consensus) applies the bound.
    orientation_retry: str = "bounded"
    # V13 B1: "markers" (default) projects a read whose length differs from the strand length (or whose exact-length
    # frame failed) from its marker-block shifts first and accepts it when the inner RS and CRC verify; the
    # marker-template DP runs only for the reads that do not verify. "off" aligns every such read by the DP (the V12
    # path). Verified frames are accepted either way; the speculative path can only add acceptances
    # (report["reads"]["speculative"] counts them). Only the default recovery configuration (no smart/soft recovery,
    # no quality-weighted consensus) uses it.
    sync_speculation: str = "markers"
    consensus_threshold: float = 0.6    # minimum posterior for a consensus base; below → erasure
    # V6 Phase 4 (opt-in): how pass-2 consensus weighs the pending reads of one address. "count" = the V4 vote (one
    # vote per read base, the default); "quality" = each projected base weighted by its own Phred quality
    # (recovery.consensus.consensus_quality_weighted; a Phred-interpreted score, not a calibrated probability). Groups
    # whose reads carry no qualities (FASTA) fall back to "count". Verification is unchanged.
    consensus_weighting: str = "count"
    max_pending_per_address: int = 64
    archive_tag: int | None = None
    sync_costs: SyncCosts = field(default_factory=SyncCosts)
    max_reads: int = 2_000_000_000
    # V5 Phase 3: "segment" = V4 (an indel erases its whole segment); "smart" = bounded local indel recovery
    # (vnxdna.v5.indel) for reads the V4 path cannot decode, plus consensus realignment in pass 2. Opt-in.
    indel_recovery: str = "segment"
    indel_config: object = None         # vnxdna.v5.indel.recovery.IndelRecoveryConfig (None = defaults)
    # V5 Phase 4: "off" (default) | "erasure" (GMD) | "chase" | "auto" — bounded soft-information search for reads
    # every hard path failed, and soft consensus in pass 2. Opt-in; verification is unchanged.
    soft_decoding: str = "off"
    soft_config: object = None          # vnxdna.v5.soft.decoder.SoftDecodeConfig (None = defaults for the mode)
    # V5: when the per-read smart/soft recovery runs. "deferred" = after the cheap pass, only for reads that can still
    # contribute to a group that is not yet decodable (see _deferred_recovery); "eager" = inside pass 1 for every read
    # the V4 paths fail (the Phase 3/4 behaviour). Irrelevant unless smart indel recovery or soft decoding is on.
    recovery_schedule: str = "deferred"
    # V6: limits on expensive recovery (vnxdna.v6.recovery.RecoveryBudget; None = unlimited, the V5 behaviour). Every
    # escalation is decided and recorded by vnxdna.v6.recovery.RecoveryPlanner → report["recovery_plan"].
    recovery_budget: object = None
    # V6-SEC-01: refuse a superblock claiming a larger container (checked before pass 2 sizes or walks anything)
    max_container_bytes: int = DEFAULT_MAX_CONTAINER_BYTES
    # V6-SEC-03: the archive the caller expects (hex). Checked against the superblock before pass 2 and against the
    # recovered manifest before SUCCESS; anything else is ARCHIVE_MISMATCH (exit 1), nothing published. A given
    # archive ID also selects its archive tag in a pool holding several archives (unless archive_tag is set).
    expect_archive_id: str | None = None        # 16-byte archive ID (manifest/superblock), 32 hex characters
    expect_sha256: str | None = None            # SHA-256 of the whole container file, 64 hex characters
    # V7 (opt-in, observability only): per-stage counters of the protocol §8.1 failure taxonomy in
    # report["stage_counters"] (and in the details of a typed decode error); see vnxdna.recovery.stagecount. Never
    # changes what is decoded or published.
    stage_counters: bool = False
    # V7 item A (opt-in): "off" (default, the 6.0 path byte for byte) | "fallback" = header-independent read clustering
    # and per-cluster consensus whose verified frames only FILL superblock symbols and data addresses the 6.0 path left
    # unresolved (FC-9); see vnxdna.recovery.cluster. cluster_config: a ClusterConfig (None = defaults).
    read_clustering: str = "off"
    cluster_config: object = None
    # V13 engine (docs/V13_ENGINE_ARCHITECTURE.md): "auto" (default) = the streaming engine (Rust core) when it is built
    # and the options are inside its domain, else the V13 path; any engine refusal, internal error or non-SUCCESS
    # result falls back to the V13 path (report["engine"] records the rung that ran and why). "stream" = the engine or
    # an error; "v13" = the V13 Python-orchestrated path; "reference" = the V13 path with every kernel on its Python
    # reference. The default comes from VNXDNA_ENGINE (unset = "auto").
    engine: str = field(default_factory=_env_engine)
    # V13 engine: attempt only the reads whose address is not verified yet (address scheduler); False attempts every
    # read (pass-1 results then equal the V13 path's, record for record)
    engine_dedup: bool = True
    # V13 engine: threads of the engine's pool (IO/scan stay on the calling thread); None = ``workers``. The default
    # comes from VNXDNA_ENGINE_THREADS (unset = None).
    engine_threads: int | None = field(default_factory=_env_engine_threads)

    @property
    def threads(self) -> int:
        """Threads the streaming engine runs with: ``engine_threads`` when set, else ``workers``."""
        return self.workers if self.engine_threads is None else self.engine_threads

    def validate(self) -> None:
        if not isinstance(self.stage_counters, bool):
            raise VNXConfigurationError("stage_counters must be True or False")
        # V14 F (H-1): types and ranges first, so a JSON string or a float never reaches a comparison, a slice or a
        # native call (a raw TypeError, or the string "false" read as True)
        from vnxdna.native import check_backend_env
        check_backend_env()                     # V14 F (H-9)
        if not isinstance(self.reverse_complement, (bool, np.bool_)):
            raise VNXConfigurationError("reverse_complement must be True or False")
        for name, lo, hi in (("workers", 1, 256), ("band", 1, 64), ("retry_band", 0, 64), ("min_quality", 0, 93),
                             ("batch_reads", 1, 1 << 24), ("max_pending_per_address", 1, 1 << 16),
                             ("max_reads", 1, 1 << 62)):
            _integer(name, getattr(self, name), lo, hi)
        if self.archive_tag is not None:
            _integer("archive_tag", self.archive_tag, 0, 0xFFFF)
        if isinstance(self.consensus_threshold, (bool, str)) or not isinstance(self.consensus_threshold, (int, float, np.number)):
            raise VNXConfigurationError("consensus_threshold must be a number in [0.25, 1]")
        from vnxdna.recovery.cluster import validate_mode
        self.cluster_config = validate_mode(self.read_clustering, self.cluster_config)
        if not 1 <= self.workers <= 256:
            raise VNXConfigurationError("workers must be in 1..256")
        if self.orientation_retry not in ("bounded", "always"):
            raise VNXConfigurationError("orientation_retry must be 'bounded' or 'always'")
        if self.engine not in ENGINES:
            raise VNXConfigurationError(f"engine must be one of {', '.join(ENGINES)} (option or {ENV_ENGINE})")
        if not isinstance(self.engine_dedup, bool):
            raise VNXConfigurationError("engine_dedup must be True or False")
        if self.engine_threads is not None and (not isinstance(self.engine_threads, int) or isinstance(self.engine_threads, bool)
                                                or not 1 <= self.engine_threads <= 256):
            raise VNXConfigurationError(f"engine_threads must be None or in 1..256 (option or {ENV_ENGINE_THREADS})")
        if self.sync_speculation not in ("markers", "off"):
            raise VNXConfigurationError("sync_speculation must be 'markers' or 'off'")
        if not 1 <= self.band <= 64:
            raise VNXConfigurationError("band must be in 1..64")
        if (not isinstance(self.retry_band, int) or isinstance(self.retry_band, bool)
                or not (self.retry_band == 0 or self.band < self.retry_band <= 64)):
            raise VNXConfigurationError("retry_band must be 0 (off) or in band+1..64")
        if not 0.25 <= self.consensus_threshold <= 1.0:
            raise VNXConfigurationError("consensus_threshold must be in [0.25, 1]")
        if not 0 <= self.min_quality <= 93:
            raise VNXConfigurationError("min_quality must be in 0..93")
        if (not isinstance(self.max_container_bytes, int) or isinstance(self.max_container_bytes, bool)
                or self.max_container_bytes < 1):
            raise VNXConfigurationError("max_container_bytes must be a positive integer (bytes)")
        from vnxdna.core.util import expected_hex
        self.expect_archive_id = expected_hex(self.expect_archive_id, 16, "expect_archive_id")
        self.expect_sha256 = expected_hex(self.expect_sha256, 32, "expect_sha256")
        if self.indel_recovery not in ("segment", "smart"):
            raise VNXConfigurationError("indel_recovery must be 'segment' (V4) or 'smart' (V5)")
        if self.indel_recovery == "smart":
            from vnxdna.sync.smart.recovery import IndelRecoveryConfig
            if self.indel_config is None:
                self.indel_config = IndelRecoveryConfig()
            elif not isinstance(self.indel_config, IndelRecoveryConfig):
                raise VNXConfigurationError("indel_config must be an IndelRecoveryConfig")
            self.indel_config.validate()
        if self.consensus_weighting not in ("count", "quality"):
            raise VNXConfigurationError("consensus_weighting must be 'count' (V4 vote) or 'quality'")
        if self.recovery_schedule not in ("deferred", "eager"):
            raise VNXConfigurationError("recovery_schedule must be 'deferred' or 'eager'")
        if self.soft_decoding not in ("off", "erasure", "chase", "auto"):
            raise VNXConfigurationError("soft_decoding must be off, erasure, chase or auto")
        if self.soft_decoding != "off":
            from dataclasses import replace
            from vnxdna.recovery.soft.decoder import SoftDecodeConfig
            if self.soft_config is None:
                self.soft_config = SoftDecodeConfig(mode=self.soft_decoding)
            elif not isinstance(self.soft_config, SoftDecodeConfig):
                raise VNXConfigurationError("soft_config must be a SoftDecodeConfig")
            else:
                self.soft_config = replace(self.soft_config, mode=self.soft_decoding)
            self.soft_config.validate()
        from vnxdna.recovery.planner import RecoveryBudget
        if self.recovery_budget is None:
            self.recovery_budget = RecoveryBudget()
        elif not isinstance(self.recovery_budget, RecoveryBudget):
            raise VNXConfigurationError("recovery_budget must be a RecoveryBudget")
        self.recovery_budget.validate()


@dataclass
class DecodeResult:
    status: str                    # SUCCESS | PARTIAL | FAILURE
    report: dict
    output: str | None = None
