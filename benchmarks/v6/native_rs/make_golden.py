"""Generate the golden inner-RS vectors (tests/v6/native/native_rs_golden.json) for the native decoder tests.

Every vector is a received word (hex), its flagged erasure positions, and the expected (corrected word, ok, errata)
computed by the V3 reference ``vnxdna.ecc.rs_batch.decode_batch``; the generator asserts that the default
``vnxdna.v4.rs_fast`` gives the identical triple. Codewords are encoded with ``reedsolo`` (fcr 0, prim 0x11D,
generator 2). Within the correction bound (2e + f <= nsym) the generator also requires ``reedsolo.decode`` to return
the transmitted codeword, the same as the reference. Beyond the bound a bounded-distance decoder may fail or
miscorrect, and decoders legitimately differ, so reedsolo's outcome there is recorded but not asserted.

usage: python benchmarks/v6/native_rs/make_golden.py [--out tests/v6/native/native_rs_golden.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import reedsolo

from vnxdna.ecc import gf256, rs_batch
from vnxdna.v4 import rs_fast

REPO = Path(__file__).resolve().parents[3]
PARAMS = [(70, 16), (70, 12), (70, 20), (255, 32), (255, 254), (255, 1), (30, 6), (40, 7), (128, 64), (3, 2), (2, 1)]
SEED = 20261004


def codec(nsym: int) -> reedsolo.RSCodec:
    return reedsolo.RSCodec(nsym, nsize=255, fcr=0, prim=gf256.PRIMITIVE_POLY, generator=gf256.GENERATOR)


def cases_for(n: int, nsym: int, rng: np.random.Generator):
    """(label, transmitted, received, erasure positions, errors e, erasures f)."""
    k = n - nsym
    rs = codec(nsym)

    def word():
        return np.frombuffer(bytes(rs.encode(rng.integers(0, 256, k, dtype=np.uint8).tobytes())), dtype=np.uint8).copy()

    def damage(e: int, f: int, label: str):
        c = word()
        pos = rng.permutation(n)
        epos, fpos = np.sort(pos[:e]), np.sort(pos[e:e + f])
        r = c.copy()
        r[epos] ^= rng.integers(1, 256, e, dtype=np.uint8)
        # an erased symbol holds an arbitrary value, sometimes the right one
        r[fpos] = rng.integers(0, 256, f, dtype=np.uint8)
        return label, c, r, fpos.tolist(), e, f

    t = nsym // 2
    yield damage(0, 0, "clean")
    if nsym <= n - 1:
        for e in sorted({1, max(1, t // 2), t} - {0}):
            if e <= t:
                yield damage(e, 0, f"errors e={e}")
        for f in sorted({1, nsym // 2, nsym} - {0}):
            yield damage(0, f, f"erasures f={f}")
        for e in range(1, t + 1, max(1, t // 3)):
            f = nsym - 2 * e
            if f > 0:
                yield damage(e, f, f"mixed e={e} f={f}")
        # just beyond the bound, well beyond it, and more erasures than parity
        if nsym >= 1 and t + 1 + 0 <= n:
            yield damage(t + 1, 0, f"beyond: errors e={t + 1}")
        if 2 * t + 1 <= n:
            yield damage(min(n, nsym), 0, f"beyond: errors e={min(n, nsym)}")
        if nsym + 1 <= n:
            yield damage(0, nsym + 1, f"beyond: erasures f={nsym + 1}")
        if nsym >= 2:
            yield damage(1, nsym - 1, f"beyond: mixed e=1 f={nsym - 1}")
    # garbage: uniformly random words, with and without random erasure flags
    for g in range(3):
        r = rng.integers(0, 256, n, dtype=np.uint8)
        f = int(rng.integers(0, nsym + 1)) if g else 0
        fpos = np.sort(rng.permutation(n)[:f]).tolist()
        yield f"garbage {g}", None, r, fpos, None, f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "tests" / "v6" / "native" / "native_rs_golden.json"))
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)
    vectors = []
    rs_checked = rs_beyond = rs_beyond_agree = 0
    for n, nsym in PARAMS:
        for label, sent, recv, fpos, e, f in cases_for(n, nsym, rng):
            er = np.zeros((1, n), dtype=bool)
            er[0, fpos] = True
            ref = rs_batch.decode_batch(recv[None, :], nsym, er)
            fast = rs_fast.decode_batch(recv[None, :], nsym, er)
            for a, b in zip(ref, fast):
                assert np.array_equal(a, b), (n, nsym, label)
            out, ok, errata = ref[0][0], bool(ref[1][0]), int(ref[2][0])
            try:
                got = bytes(codec(nsym).decode(bytearray(recv.tobytes()), erase_pos=fpos or None)[1])
                rs_ok = True
            except reedsolo.ReedSolomonError:
                got, rs_ok = None, False
            within = e is not None and 2 * e + f <= nsym
            if within:
                assert ok and out.tobytes() == sent.tobytes(), (n, nsym, label)
                assert rs_ok and got == sent.tobytes(), (n, nsym, label, "reedsolo")
                rs_checked += 1
            else:
                rs_beyond += 1
                rs_beyond_agree += (rs_ok == ok) and (not ok or got == out.tobytes())
            vectors.append({"n": n, "nsym": nsym, "label": label, "received": recv.tobytes().hex(), "erasures": fpos,
                            "corrected": out.tobytes().hex(), "ok": ok, "errata": errata, "within_bound": within,
                            "reedsolo_checked": within})
    digest = hashlib.sha256(json.dumps([[v["corrected"], v["ok"], v["errata"]] for v in vectors]).encode()).hexdigest()
    payload = {"description": "Golden inner-RS decoding vectors: expected outputs from vnxdna.ecc.rs_batch (== vnxdna.v4.rs_fast); "
                              "within the bound also equal to reedsolo's decoding. Generated by benchmarks/v6/native_rs/make_golden.py.",
               "field": "GF(2^8)/0x11D, generator 2, fcr 0", "seed": SEED, "params": PARAMS,
               "reedsolo_version": getattr(reedsolo, "__version__", None), "vectors": len(vectors),
               "reedsolo_cross_checked_within_bound": rs_checked,
               "beyond_bound": rs_beyond, "beyond_bound_reedsolo_same_outcome": rs_beyond_agree,
               "expected_sha256": digest, "cases": vectors}
    Path(args.out).write_text(json.dumps(payload, indent=1) + "\n")
    print(f"{len(vectors)} vectors, {rs_checked} cross-checked with reedsolo, beyond bound {rs_beyond} "
          f"(reedsolo same outcome {rs_beyond_agree}); expected sha256 {digest}")


if __name__ == "__main__":
    main()
