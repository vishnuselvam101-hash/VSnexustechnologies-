"""V6 adaptive recovery planner: one explicit place for the decoder's recovery decisions, budgets and provenance.

The decode cascade is unchanged in substance; what changes is that every escalation is decided, bounded and recorded
here instead of being implied by option flags scattered through the decoder::

    FAST     exact-length reads, markers stripped, inner RS + CRC            (pass 1, every read)
    SYNC     marker-template alignment, indels → erasures, inner RS + CRC     (pass 1, reads FAST did not verify)
    SMART    bounded local indel recovery (vnxdna.v5.indel)                   (deferred rounds S/A, opt-in)
    SOFT     bounded soft-information search (vnxdna.v5.soft)                 (deferred rounds S/A, opt-in)
    EXPENSIVE round B: every read not yet tried, incl. reads without a header (only while data is still missing)
    OUTER    row decode per group, then V6 stripe/column recovery              (pass 2)
    REJECT   nothing unverified is published; the container SHA-256 + structural validation decide SUCCESS

Signals used (all measured, none learned): the per-read path counts and confidence check of pass 1 (aligned but
unverified reads become *pending*), superblock decodability, groups decodable / incomplete, addresses still needed,
pending and unaddressed read counts, RS/outer decode status, and the remaining budget.

Budgets (:class:`RecoveryBudget`). ``None`` means unlimited, and the default budget is unlimited, so default
decoding is identical to V5/V6 without a planner. When a *work* budget (reads examined, smart/soft reads, outer
stripes) is exhausted the planner stops admitting that work, records why, and the decode continues with what has
been verified; the final integrity chain alone decides SUCCESS, so an exhausted budget can only turn SUCCESS into
PARTIAL/FAILURE, never the reverse. When a *hard* budget (wall time, peak RSS) is exceeded at a checkpoint, the
decode stops with :class:`VNXBudgetExceeded` and nothing is published.

Determinism: admission is by count in the decoder's fixed processing order, so a work-budgeted decode is
reproducible and independent of the worker count. A wall-time budget is inherently machine-dependent; when it fires
the report says so.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

from vnxdna.core.errors import VNXConfigurationError, VNXDecodeError
from vnxdna.core.util import peak_rss_bytes

STAGES = ("FAST", "SYNC", "SMART", "SOFT", "EXPENSIVE", "OUTER", "REJECT")


class VNXBudgetExceeded(VNXDecodeError):
    """A hard recovery budget (wall time or memory) was exceeded; the decode stopped and nothing was published."""

    stage = "budget"
    retryable = True  # a larger budget can succeed


@dataclass(frozen=True)
class RecoveryBudget:
    """Limits on expensive recovery. ``None`` = unlimited (the default for every field)."""

    max_reads_examined: int | None = None      # reads handed to smart/soft recovery, all deferred rounds together
    max_round_b_reads: int | None = None       # reads examined by round B (the EXPENSIVE sweep)
    max_outer_stripes: int | None = None       # V6 stripes attempted by column recovery in pass 2
    max_wall_seconds: float | None = None      # whole decode, checked at stage and batch boundaries (hard)
    max_rss_bytes: int | None = None           # parent-process peak RSS, checked at the same checkpoints (hard)

    def validate(self) -> RecoveryBudget:
        for name in ("max_reads_examined", "max_round_b_reads", "max_outer_stripes", "max_rss_bytes"):
            v = getattr(self, name)
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
                raise VNXConfigurationError(f"recovery budget {name} must be a non-negative integer or None")
        w = self.max_wall_seconds
        if w is not None and (not isinstance(w, (int, float)) or isinstance(w, bool) or not math.isfinite(w) or w <= 0):
            raise VNXConfigurationError("recovery budget max_wall_seconds must be a positive number or None")
        return self

    @property
    def unlimited(self) -> bool:
        return all(v is None for v in asdict(self).values())

    def to_dict(self) -> dict:
        return asdict(self)


class RecoveryPlanner:
    """Decides, bounds and records every recovery escalation of one decode."""

    def __init__(self, budget: RecoveryBudget | None = None, t0: float | None = None):
        self.budget = (budget or RecoveryBudget()).validate()
        self.t0 = time.perf_counter() if t0 is None else t0
        self.decisions: list[dict] = []
        self.spent = {"reads_examined": 0, "round_b_reads": 0, "outer_stripes": 0}
        self.exhausted: list[dict] = []
        self.checkpoints = 0

    # ---------------------------------------------------------------- hard limits
    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    def out_of_time(self) -> bool:
        w = self.budget.max_wall_seconds
        return w is not None and self.elapsed() > w

    def checkpoint(self, where: str) -> None:
        """Raise :class:`VNXBudgetExceeded` if the wall-time or memory budget is exceeded."""
        self.checkpoints += 1
        b = self.budget
        if b.max_wall_seconds is not None and self.elapsed() > b.max_wall_seconds:
            self._hard("max_wall_seconds", where, round(self.elapsed(), 3), b.max_wall_seconds)
        if b.max_rss_bytes is not None:
            rss = peak_rss_bytes()
            if rss > b.max_rss_bytes:
                self._hard("max_rss_bytes", where, rss, b.max_rss_bytes)

    def _hard(self, limit: str, where: str, used, allowed) -> None:
        self.exhausted.append({"limit": limit, "at": where, "used": used, "allowed": allowed, "effect": "decode stopped"})
        raise VNXBudgetExceeded(f"recovery budget {limit} exceeded at {where} ({used} > {allowed}); nothing published",
                                details={"recovery_plan": self.report()},
                                hint="raise the budget, or decode on a larger machine")

    # ---------------------------------------------------------------- work limits
    def _cap(self, counter: str, limit: str, n: int, where: str) -> int:
        allowed = getattr(self.budget, limit)
        if allowed is None:
            return n
        left = max(0, allowed - self.spent[counter])
        if n > left:
            self.exhausted.append({"limit": limit, "at": where, "requested": n, "admitted": left, "allowed": allowed,
                                   "effect": f"{n - left} reads not examined"})
            return left
        return n

    def admit_reads(self, rnd: str, n: int) -> int:
        """How many of ``n`` candidate reads a deferred round may examine (in the decoder's fixed order)."""
        n = self._cap("reads_examined", "max_reads_examined", n, f"round {rnd}")
        if rnd == "B":
            n = self._cap("round_b_reads", "max_round_b_reads", n, "round B")
            self.spent["round_b_reads"] += n
        self.spent["reads_examined"] += n
        return n

    def unexamined(self, rnd: str, n: int) -> None:
        """Return reads admitted but not examined (the wall-time limit cut a round short)."""
        self.spent["reads_examined"] -= n
        if rnd == "B":
            self.spent["round_b_reads"] -= n

    def admit_stripe(self, stripe: int) -> bool:
        allowed = self.budget.max_outer_stripes
        if allowed is not None and self.spent["outer_stripes"] >= allowed:
            if not any(e["limit"] == "max_outer_stripes" for e in self.exhausted):
                self.exhausted.append({"limit": "max_outer_stripes", "at": f"stripe {stripe}", "allowed": allowed,
                                       "effect": "remaining stripes not attempted; their failed rows stay failed"})
            return False
        self.spent["outer_stripes"] += 1
        return True

    # ---------------------------------------------------------------- provenance
    def decide(self, stage: str, run: bool, why: str, signals: dict | None = None) -> bool:
        """Record one escalation decision (``run`` = whether the stage is entered) and return ``run``."""
        self.decisions.append({"stage": stage, "run": bool(run), "why": why, "signals": dict(signals or {})})
        return run

    def outcome(self, stage: str, **result) -> None:
        """Attach the measured result (work spent, recovered) to the latest decision of ``stage``. Timings are kept out
        of the plan (they are in ``report["stage_seconds"]``), so the plan of a decode is reproducible."""
        for d in reversed(self.decisions):
            if d["stage"] == stage:
                d.setdefault("result", {}).update(result)
                return
        self.decisions.append({"stage": stage, "run": True, "why": "recorded", "signals": {}, "result": dict(result)})

    def report(self) -> dict:
        return {"budget": self.budget.to_dict(), "budget_unlimited": self.budget.unlimited, "spent": dict(self.spent),
                "budget_exhausted": list(self.exhausted), "decisions": list(self.decisions),
                "checkpoints": self.checkpoints}
