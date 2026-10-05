"""Fast fuzz smoke tests (seconds): every Python fuzz target over its seed and regression corpora, plus a short
hypothesis run on random bytes and on mutated seeds. The long coverage-guided campaigns are ``fuzz/run.sh <target>
<seconds>`` (atheris); results in docs/security/V6_FUZZ_REPORT.md.
"""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from .harnesses import TARGETS, corpus_files

SLOW = {"py-decode": 40, "py-container": 60, "py-manifest": 60, "py-align": 60, "py-rs": 40}
SMOKE = settings(deadline=None, derandomize=True, database=None, suppress_health_check=list(HealthCheck))


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_corpus_replay(target):
    files = corpus_files(target)
    assert files, f"no seed corpus for {target} (python -m fuzz.make_seeds)"
    for p in files:
        TARGETS[target](p.read_bytes())


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_random_bytes(target):
    @settings(SMOKE, max_examples=SLOW.get(target, 150))
    @given(st.binary(max_size=2048))
    def run(data):
        TARGETS[target](data)
    run()


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_mutated_seeds(target):
    seeds = [p.read_bytes() for p in corpus_files(target)]

    @settings(SMOKE, max_examples=SLOW.get(target, 150))
    @given(st.sampled_from(seeds), st.lists(st.tuples(st.integers(0, 1 << 20), st.integers(0, 255), st.integers(0, 3)),
                                             max_size=6), st.integers(0, 1 << 20))
    def run(seed, edits, cut):
        data = bytearray(seed)
        for pos, val, op in edits:
            if not data:
                break
            i = pos % len(data)
            if op == 0:
                data[i] = val
            elif op == 1:
                data.insert(i, val)
            elif op == 2:
                del data[i]
            else:
                data[i] ^= 1 << (val % 8)
        if cut % 4 == 0:
            data = data[: cut % (len(data) + 1)]
        TARGETS[target](bytes(data))
    run()
