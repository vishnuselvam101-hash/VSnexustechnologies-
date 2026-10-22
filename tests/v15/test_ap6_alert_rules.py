"""AP-6: the Prometheus alert rules of ``vnxd`` only use metrics the service can emit.

The service exposes its metrics in the Prometheus text exposition format (0.0.4) on the management socket
(``src/vnxdna/native/rust/vnxd/src/metrics.rs`` builds the exposition, ``vnxd-alerts.yml`` turns the samples
into alerts). A rule that names a metric the service never emits is a rule that can never fire, and a rule that
misses a metric the service does emit is a hole in the coverage: both are silent failures, so the two files are
checked against each other here.

The list of the names comes from the Rust module itself: the ``DEFS`` table and the name literals of the
converters are read out of the source, so a new metric (or a new rule) cannot drift away from the other side
without this test failing.

SYNTHETIC SOFTWARE TEST.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

#: the repository root (tests/v15/<this file>)
ROOT = Path(__file__).resolve().parents[2]
#: the alert rules of the service
RULES = ROOT / "deploy" / "monitoring" / "vnxd-alerts.yml"
#: the monitoring notes
README = ROOT / "deploy" / "monitoring" / "README.md"
#: the module that builds the exposition
METRICS_RS = ROOT / "src" / "vnxdna" / "native" / "rust" / "vnxd" / "src" / "metrics.rs"

#: every alert the rule file must hold
EXPECTED_ALERTS = {
    "VnxdHealthDegraded",
    "VnxdHealthFailed",
    "VnxdReadOnly",
    "VnxdBackendUnavailable",
    "VnxdBackendDegraded",
    "VnxdScrubFailures",
    "VnxdDamagedExtents",
    "VnxdIntegrityFailures",
    "VnxdCacheWritebackFailures",
    "VnxdCacheHitRateLow",
    "VnxdIoTimeouts",
    "VnxdDnaSimDecodeFailures",
    "VnxdScrubStale",
}

#: a metric name of the service
METRIC_RE = re.compile(r"vnxd_[a-z0-9_]+")
#: the name of a metric in the ``DEFS`` table of the Rust module
DEF_RE = re.compile(r'Def\s*\{\s*name:\s*"(vnxd_[a-z0-9_]+)"')


# --------------------------------------------------------------------------------------------------------------
# the rules


def _load_rules(text: str) -> list[dict]:
    """The rules of the file, as a list of ``{alert, expr, for, severity, summary, description}``.

    PyYAML is used when it is installed; otherwise the few fields the test needs are read out of the text, so
    the test still runs on a machine without PyYAML.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is installed in the lab environment
        return _parse_rules(text)
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict), "the rule file is not a mapping"
    groups = doc.get("groups")
    assert isinstance(groups, list) and groups, "the rule file holds no group"
    out: list[dict] = []
    for group in groups:
        assert isinstance(group, dict), "a group is not a mapping"
        assert isinstance(group.get("name"), str) and group["name"], "a group has no name"
        rules = group.get("rules")
        assert isinstance(rules, list) and rules, f"the group {group['name']} holds no rule"
        for rule in rules:
            assert isinstance(rule, dict), "a rule is not a mapping"
            labels = rule.get("labels") or {}
            annotations = rule.get("annotations") or {}
            out.append(
                {
                    "alert": rule.get("alert"),
                    "expr": rule.get("expr"),
                    "for": rule.get("for"),
                    "severity": labels.get("severity"),
                    "summary": annotations.get("summary"),
                    "description": annotations.get("description"),
                }
            )
    return out


def _parse_rules(text: str) -> list[dict]:
    """The rules of the file, read out of the text (the fallback when PyYAML is missing)."""
    rules: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- alert:"):
            current = {"alert": stripped.split(":", 1)[1].strip(), "expr": None, "for": None, "severity": None,
                       "summary": None, "description": None}
            rules.append(current)
            continue
        if current is None or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip().strip("'\"")
        if key == "expr":
            current["expr"] = value
        elif key == "for":
            current["for"] = value
        elif key == "severity":
            current["severity"] = value
        elif key in ("summary", "description"):
            current[key] = value
    return rules


@pytest.fixture(scope="module")
def rules() -> list[dict]:
    assert RULES.is_file(), f"the rule file is missing: {RULES}"
    return _load_rules(RULES.read_text())


# --------------------------------------------------------------------------------------------------------------
# the metrics the service can emit


@pytest.fixture(scope="module")
def metric_names() -> set[str]:
    """The names the Rust module can emit: the ``DEFS`` table and the name literals of the converters."""
    assert METRICS_RS.is_file(), f"the metrics module is missing: {METRICS_RS}"
    src = METRICS_RS.read_text()
    names = set(DEF_RE.findall(src))
    names |= set(re.findall(r'"(vnxd_[a-z0-9_]+)"', src))
    assert names, "no metric name was found in the Rust module"
    return names


@pytest.fixture(scope="module")
def def_names() -> set[str]:
    """The names of the ``DEFS`` table of the Rust module (the metrics with a help text and a type)."""
    src = METRICS_RS.read_text()
    names = set(DEF_RE.findall(src))
    assert len(names) > 20, f"the DEFS table of the Rust module looks empty ({len(names)} names)"
    return names


# --------------------------------------------------------------------------------------------------------------
# the tests


def test_the_rule_file_is_valid_yaml_with_the_expected_alerts(rules: list[dict]) -> None:
    names = [r["alert"] for r in rules]
    assert all(isinstance(n, str) and n for n in names), f"a rule has no name: {names}"
    assert len(names) == len(set(names)), f"an alert is defined twice: {names}"
    assert set(names) == EXPECTED_ALERTS, f"the alerts are {sorted(set(names))}, expected {sorted(EXPECTED_ALERTS)}"


def test_every_rule_has_an_expression_a_severity_and_annotations(rules: list[dict]) -> None:
    for rule in rules:
        assert isinstance(rule["expr"], str) and rule["expr"].strip(), f"{rule['alert']} has no expression"
        assert rule["severity"] in ("critical", "warning", "info"), f"{rule['alert']} has the severity {rule['severity']!r}"
        assert rule["summary"], f"{rule['alert']} has no summary"
        assert rule["description"], f"{rule['alert']} has no description"


def test_every_metric_of_the_rules_is_one_the_service_emits(rules: list[dict], metric_names: set[str]) -> None:
    used: set[str] = set()
    for rule in rules:
        names = set(METRIC_RE.findall(rule["expr"]))
        assert names, f"{rule['alert']} uses no metric at all"
        unknown = names - metric_names
        assert not unknown, f"{rule['alert']} uses the metrics {sorted(unknown)}, which the service never emits"
        used |= names
    # the rules cover the metrics the operator has to see
    assert "vnxd_health_state" in used
    assert "vnxd_backend_state" in used
    assert "vnxd_read_only" in used
    assert "vnxd_damaged_extents" in used
    assert "vnxd_scrub_failed_extents" in used
    assert "vnxd_volume_integrity_failures_total" in used


def test_every_metric_of_the_rules_is_defined_in_the_rust_module(rules: list[dict], def_names: set[str]) -> None:
    """A name the module only builds by hand (a name of the stats view) is not a name of the DEFS table."""
    for rule in rules:
        unknown = set(METRIC_RE.findall(rule["expr"])) - def_names
        assert not unknown, f"{rule['alert']} uses the metrics {sorted(unknown)}, which the DEFS table does not hold"


def test_every_metric_name_follows_the_prefix_convention(rules: list[dict]) -> None:
    for rule in rules:
        for name in METRIC_RE.findall(rule["expr"]):
            assert name.startswith("vnxd_"), f"{name} does not follow the vnxd_ convention"
            assert name == name.lower(), f"{name} is not lower case"
            assert not name.endswith("_"), f"{name} ends with an underscore"


def _rule(rules: list[dict], name: str) -> dict:
    for rule in rules:
        if rule["alert"] == name:
            return rule
    raise AssertionError(f"the rule {name} is missing")


def test_the_health_rules_watch_the_state_of_the_device(rules: list[dict]) -> None:
    degraded = _rule(rules, "VnxdHealthDegraded")
    assert 'vnxd_health_state{state="degraded"}' in degraded["expr"]
    assert degraded["for"] == "5m", "a device that is degraded for a moment is not an incident"
    failed = _rule(rules, "VnxdHealthFailed")
    assert 'vnxd_health_state{state="failed"}' in failed["expr"]
    assert failed["for"] is None, "a failed device is an incident at once"
    assert failed["severity"] == "critical"
    read_only = _rule(rules, "VnxdReadOnly")
    assert "vnxd_read_only" in read_only["expr"]


def test_the_backend_rules_watch_the_state_of_the_backend(rules: list[dict]) -> None:
    unavailable = _rule(rules, "VnxdBackendUnavailable")
    assert 'vnxd_backend_state{state="unavailable"}' in unavailable["expr"]
    assert unavailable["severity"] == "critical"
    degraded = _rule(rules, "VnxdBackendDegraded")
    assert 'vnxd_backend_state{state="degraded"}' in degraded["expr"]


def test_the_cache_rule_measures_the_hit_rate(rules: list[dict]) -> None:
    rule = _rule(rules, "VnxdCacheHitRateLow")
    expr = rule["expr"]
    assert "rate(vnxd_cache_hits_total" in expr
    assert "rate(vnxd_cache_misses_total" in expr
    assert "< 0.5" in expr
    assert rule["for"] == "30m"
    assert rule["severity"] == "info", "a low hit rate is an observation, not an incident"


def test_the_integrity_rule_counts_the_failures_of_a_window(rules: list[dict]) -> None:
    expr = _rule(rules, "VnxdIntegrityFailures")["expr"]
    assert "increase(vnxd_volume_integrity_failures_total[10m])" in expr
    assert "> 0" in expr


def test_the_scrub_rules_watch_the_failures_and_the_age_of_the_scrub(rules: list[dict]) -> None:
    failures = _rule(rules, "VnxdScrubFailures")
    assert "vnxd_scrub_failed_extents > 0" in failures["expr"]
    stale = _rule(rules, "VnxdScrubStale")
    assert "vnxd_scrub_last_run_unix_ms" in stale["expr"]
    # 7 days in milliseconds
    assert "604800000" in stale["expr"], "the scrub is watched for 7 days"


def test_the_io_and_simulator_rules_watch_their_counters(rules: list[dict]) -> None:
    assert "vnxd_io_timed_out_total" in _rule(rules, "VnxdIoTimeouts")["expr"]
    assert "vnxd_decode_failures_total" in _rule(rules, "VnxdDnaSimDecodeFailures")["expr"]


def test_the_notes_say_how_the_metrics_are_scraped() -> None:
    text = README.read_text()
    assert "metrics" in text
    assert "vnx.mgmt" in text
    assert "Unix socket" in text or "Unix socket" in text
    # the DNA simulator is simulated, never synthesised DNA
    assert "SIMULATED" in text
    # every metric of the rules is named in the notes
    src = METRICS_RS.read_text()
    names = set(DEF_RE.findall(src))
    rules = _load_rules(RULES.read_text())
    for rule in rules:
        for name in METRIC_RE.findall(rule["expr"]):
            if name in names:
                assert name in text, f"{name} is not named in the monitoring notes"
