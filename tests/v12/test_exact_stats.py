"""Float statistics in the decoder report must not depend on how batches, chunks or buckets are grouped.

The decoder merges per-batch counters (pass 1), per-chunk counters (deferred rounds, chunk size depends on the worker
count) and per-bucket counters (V12 parallel pass 2). Float addition is not associative, so a plain float sum such as
``false_accept_bound`` could differ in the last bits between worker counts; the report then differs across workers
although the output is identical. The summaries keep such sums as exact terms and the report converts them to float.
"""
from __future__ import annotations

import math
from collections import Counter
from fractions import Fraction

import numpy as np

from vnxdna.recovery.soft import decoder as sd
from vnxdna.sync.smart import recovery as rv

# binary64: a left fold gives 1.0, pairs give 1.0 + (1e-16 + 1e-16) == 1.0000000000000002 (grouping-sensitive)
TERMS = [1.0, 1e-16, 1e-16, 1e-16]


def _smart_outcome(fa: float) -> rv.ReadOutcome:
    o = rv.ReadOutcome()
    o.false_accept_bound = fa
    return o


def _merge(parts: list[dict]) -> Counter:
    total: Counter = Counter()
    for p in parts:
        total.update(p)
    return total


def _smart_summary(terms: list[float]) -> dict:
    outs = [_smart_outcome(t) for t in terms]
    plans = [None] * len(outs)
    return rv.summarize_outcomes(plans, outs, np.zeros((len(outs), 1), dtype=bool))


def test_smart_false_accept_bound_is_identical_for_every_grouping():
    whole = _merge([_smart_summary(TERMS)])
    pairs = _merge([_smart_summary(TERMS[i:i + 2]) for i in range(0, len(TERMS), 2)])
    singles = _merge([_smart_summary([t]) for t in TERMS])
    assert whole["false_accept_bound"] == pairs["false_accept_bound"] == singles["false_accept_bound"]
    assert rv.report_value(whole["false_accept_bound"]) == math.fsum(TERMS)
    assert isinstance(rv.report_value(whole["false_accept_bound"]), float)


def test_soft_float_sums_are_identical_for_every_grouping():
    def outcome(fa, ent):
        o = sd.SoftOutcome()
        o.false_accept_bound = fa
        o.entropy_bits = ent
        return o

    ents = [0.5, 4e-17, 4e-17, 4e-17]            # same shape around 0.5 (half ulp 5.55e-17)
    outs = [outcome(f, e) for f, e in zip(TERMS, ents)]
    whole = _merge([sd.summarize(outs)])
    singles = _merge([sd.summarize([o]) for o in outs])
    halves = _merge([sd.summarize(outs[:2]), sd.summarize(outs[2:])])
    for key, terms in (("false_accept_bound", TERMS), ("entropy_bits_sum", ents)):
        assert whole[key] == singles[key] == halves[key]
        assert rv.report_value(whole[key]) == math.fsum(terms)


def test_non_finite_bound_is_reported_as_none_and_absorbs_the_sum():
    o = sd.SoftOutcome()
    o.false_accept_bound = math.inf
    assert sd.summarize([o])["false_accept_bound"] is None
    assert rv.exact_term(math.inf) == math.inf and not isinstance(rv.exact_term(math.inf), Fraction)
    assert rv.exact_term(0.5) + math.inf == math.inf


def test_report_value_leaves_other_counters_alone():
    assert rv.report_value(3) == 3 and isinstance(rv.report_value(3), int)
    assert rv.report_value(None) is None
    assert rv.report_value(Fraction(1, 4)) == 0.25
