"""V13 S4: the pass-1 batch prefetch thread yields exactly the parser's items, in order, re-raises the parser's
exception at its position in the stream, and stops the parser when the consumer stops early."""
from __future__ import annotations

import threading
import time

import pytest

from vnxdna.pipeline.decode import _prefetch


def test_items_and_order_are_those_of_the_generator():
    assert list(_prefetch(iter(range(100)))) == list(range(100))
    assert list(_prefetch(iter([]))) == []
    assert list(_prefetch(iter([None, (1, 2), "x"]), depth=1)) == [None, (1, 2), "x"]


def test_exception_is_raised_after_the_items_parsed_before_it():
    def gen():
        yield 1
        yield 2
        raise ValueError("malformed record")

    out = []
    with pytest.raises(ValueError, match="malformed record"):
        for x in _prefetch(gen()):
            out.append(x)
    assert out == [1, 2]


def test_consumer_stopping_early_stops_the_parser():
    produced = []
    finished = threading.Event()

    def gen():
        try:
            for i in range(10_000):
                produced.append(i)
                yield i
        finally:
            finished.set()

    it = _prefetch(gen(), depth=2)
    assert next(it) == 0 and next(it) == 1
    it.close()
    assert finished.wait(5)
    assert len(produced) <= 2 + 2 + 1          # consumed + queue depth + one item in flight, never the whole stream
    assert all(not t.is_alive() for t in threading.enumerate() if t.name == "vnx-reads-prefetch")


def test_parser_runs_ahead_of_a_slow_consumer():
    seen = []

    def gen():
        for i in range(6):
            seen.append(time.perf_counter())
            yield i

    out = []
    for x in _prefetch(gen(), depth=2):
        time.sleep(0.02)
        out.append(x)
    assert out == list(range(6))
    assert seen[2] - seen[0] < 0.02            # the first items were parsed before the consumer finished the first
