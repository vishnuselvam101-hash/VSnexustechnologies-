"""Probe: is ReedSolomonShards MDS? Every K-subset of the n generator rows must be invertible."""
import itertools, random, sys
from vnxdna.ecc.reed_solomon import ReedSolomonShards, _invert
from vnxdna.core.errors import ECCRecoveryError
def singular_subsets(K, M, limit=None, rng=None):
    c = ReedSolomonShards(K, M); n = K+M; bad = 0; total = 0; example=None
    combos = itertools.combinations(range(n), K) if limit is None else (tuple(sorted(rng.sample(range(n), K))) for _ in range(limit))
    for rows in combos:
        total += 1
        try: _invert([c.generator[r] for r in rows])
        except ECCRecoveryError:
            bad += 1; example = example or sorted(set(range(n))-set(rows))
    return bad, total, example
rng = random.Random(1)
for K, M in [(4,2),(8,4),(10,4),(16,4),(16,8),(20,10),(32,8),(32,16),(64,16),(64,64)]:
    from math import comb
    exhaustive = comb(K+M, K) <= 200_000
    bad, total, ex = singular_subsets(K, M, None if exhaustive else 3000, rng)
    print(f"K={K:2d} M={M:2d} {'exhaustive' if exhaustive else 'sampled   '} patterns={total:6d} singular={bad:5d} example_erased={ex}")
