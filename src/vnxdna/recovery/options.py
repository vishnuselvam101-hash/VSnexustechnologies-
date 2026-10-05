"""Decode options and the decode result (formerly in ``vnxdna.v4.decoder``; V6 Phase 2, M4)."""
from __future__ import annotations

from dataclasses import dataclass, field

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.dnaenc.layout import Layout
from vnxdna.sync.template import SyncCosts


@dataclass
class DecodeOptions:
    profile: str | None = None          # None = detect from read lengths
    layout: Layout | None = None
    workers: int = 1
    batch_reads: int = 8192
    band: int = 6
    min_quality: int = 0                # bases below this Phred score become erasures (soft-information hook)
    reverse_complement: bool = True
    consensus_threshold: float = 0.6    # minimum posterior for a consensus base; below → erasure
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

    def validate(self) -> None:
        if not 1 <= self.workers <= 256:
            raise VNXConfigurationError("workers must be in 1..256")
        if not 1 <= self.band <= 64:
            raise VNXConfigurationError("band must be in 1..64")
        if not 0.25 <= self.consensus_threshold <= 1.0:
            raise VNXConfigurationError("consensus_threshold must be in [0.25, 1]")
        if not 0 <= self.min_quality <= 93:
            raise VNXConfigurationError("min_quality must be in 0..93")
        if self.indel_recovery not in ("segment", "smart"):
            raise VNXConfigurationError("indel_recovery must be 'segment' (V4) or 'smart' (V5)")
        if self.indel_recovery == "smart":
            from vnxdna.sync.smart.recovery import IndelRecoveryConfig
            if self.indel_config is None:
                self.indel_config = IndelRecoveryConfig()
            elif not isinstance(self.indel_config, IndelRecoveryConfig):
                raise VNXConfigurationError("indel_config must be an IndelRecoveryConfig")
            self.indel_config.validate()
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
