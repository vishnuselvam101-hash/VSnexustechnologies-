"""Transparent, additive risk score (VNX-Secure; docs/VNX_SECURE_SECURITY_MODEL.md §5).

    risk(subject) = Σ POINTS[finding.severity]   over the subject's findings in the last RISK_WINDOW seconds

grouped for explanation into the categories below. The points are chosen so that the level follows directly from the
findings, with no tuning constant hidden in between:

* one LOW finding (5) stays LOW; four LOW findings (20) reach MEDIUM;
* one MEDIUM finding (20) is MEDIUM; three (60) are HIGH; four (80) are CRITICAL;
* one HIGH finding (50) is HIGH; two (100) are CRITICAL;
* one CRITICAL finding (80, e.g. touching a deception resource) is CRITICAL on its own.

Thresholds: LOW < 20 ≤ MEDIUM < 50 ≤ HIGH < 80 ≤ CRITICAL.
"""
from __future__ import annotations

from dataclasses import dataclass

from .events import Finding

LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
POINTS = {"INFO": 0, "LOW": 5, "MEDIUM": 20, "HIGH": 50, "CRITICAL": 80}
THRESHOLDS = (("CRITICAL", 80), ("HIGH", 50), ("MEDIUM", 20), ("LOW", 0))
RISK_WINDOW = 900.0
CATEGORIES = ("authentication_anomalies", "authorization_violations", "integrity_failures", "rate_anomalies",
              "policy_violations", "input_anomalies", "deception_hits")


def level(score: int) -> str:
    for name, lo in THRESHOLDS:
        if score >= lo:
            return name
    return "LOW"


@dataclass(frozen=True)
class RiskAssessment:
    subject: str
    score: int
    level: str
    breakdown: dict         # category -> points
    findings: tuple         # (detector, severity, points, reason)

    def to_dict(self) -> dict:
        return {"subject": self.subject, "score": self.score, "level": self.level, "breakdown": self.breakdown,
                "findings": [list(f) for f in self.findings]}


def assess(subject: str, findings: list[Finding], now: float, window: float = RISK_WINDOW) -> RiskAssessment:
    live = [f for f in findings if f.subject == subject and now - f.timestamp <= window]
    breakdown = {c: 0 for c in CATEGORIES}
    for f in live:
        breakdown[f.category] += POINTS[f.severity]
    score = sum(breakdown.values())
    return RiskAssessment(subject, score, level(score), {k: v for k, v in breakdown.items() if v},
                          tuple((f.detector, f.severity, POINTS[f.severity], f.reason) for f in live))
