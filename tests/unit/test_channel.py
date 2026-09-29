"""The simulator is deterministic and reports events that actually happened."""
import pytest

from vnxdna.channel import ChannelConfig, simulate
from vnxdna.errors import ConfigurationError

POOL = ["ACGT" * 50 for _ in range(400)]


def test_zero_rates_are_identity():
    reads, report = simulate(POOL, ChannelConfig(seed=1))
    assert reads == POOL and report["reads_altered"] == 0 and report["strands_dropped"] == 0


def test_seeded_runs_are_reproducible_and_seeds_differ():
    cfg = ChannelConfig(seed=7, substitution_rate=0.01, insertion_rate=0.002, deletion_rate=0.002, dropout_rate=0.1, shuffle=True)
    a = simulate(POOL, cfg)
    assert a == simulate(POOL, cfg)
    assert a[0] != simulate(POOL, ChannelConfig(**{**cfg.to_dict(), "seed": 8}))[0]


def test_dropout_really_removes_strands():
    reads, r = simulate(POOL, ChannelConfig(seed=2, dropout_rate=0.2))
    assert r["strands_dropped"] == len(POOL) - len(reads) > 0
    assert abs(r["observed_dropout_rate"] - 0.2) < 0.06
    reads, r = simulate(POOL, ChannelConfig(seed=2, dropout_rate=0.2, exact_dropout=True))
    assert r["strands_dropped"] == 80 and len(reads) == 320


def test_event_log_matches_counts_and_positions():
    cfg = ChannelConfig(seed=3, substitution_rate=0.01, insertion_rate=0.003, deletion_rate=0.003, dropout_rate=0.05,
                        burst_rate=0.05, reverse_complement_rate=0.3)
    reads, r = simulate(POOL, cfg, record_events=True)
    events = r["events"]
    kinds = {k: sum(1 for e in events if e["type"] == k) for k in ("dropout", "substitution", "insertion", "deletion", "burst")}
    assert kinds["dropout"] == r["strands_dropped"] and kinds["insertion"] == r["insertions"]
    assert kinds["substitution"] == r["substitutions"] - (r["burst_bases"] if cfg.burst_kind == "substitution" else 0)
    burst_reads = {e["read"] for e in events if e["type"] == "burst"}
    checked = 0
    for e in events:
        if e["type"] == "substitution" and e["read"] not in burst_reads:  # bursts shift coordinates of deletions
            assert e["original"] != e["replacement"]
            assert POOL[e["source_strand"]][e["position"]] == e["original"]
            checked += 1
    assert checked > 100


def test_substitution_only_run_observed_rate_is_measured():
    reads, r = simulate(POOL, ChannelConfig(seed=4, substitution_rate=0.001))
    diffs = sum(a != b for x, y in zip(reads, POOL) for a, b in zip(x, y))
    assert diffs == r["substitutions"] and r["observed_substitution_rate"] == diffs / (len(POOL) * 200)


@pytest.mark.parametrize("bad", [dict(seed=-1), dict(substitution_rate=1.5), dict(coverage=0), dict(burst_kind="x")])
def test_invalid_channel_config(bad):
    with pytest.raises(ConfigurationError):
        ChannelConfig(**bad)
