"""Insertion-run step of the per-base kernel in row chunks (V7 review, simulator memory): the chunked step must give the
same bytes and consume the auxiliary generator exactly as the dense step it replaced (frozen below as
_dense_per_base_errors, the kernel at 562bf52), and its peak memory must stay close to that of the same model without
insertion runs (SIMULATED, software tests)."""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from vnxdna.simulation import errormodels as em, model as cm, registry
from vnxdna.simulation.engine import Simulator
from vnxdna.simulation.errormodels import (MAX_INSERTION_RUN, DeletionModel, InsertionModel, SubstitutionModel,
                                           cumulative_matrix, sample_targets)

ROOT = Path(__file__).resolve().parents[2]
#: SHA-256 of one simulated batch (256 random 150-nt strands, seed 17, batch 0) per model, computed with the dense kernel
#: at 562bf52 (before the chunked insertion-run step): fitted round-2 models and every shipped model
GOLDEN = {
    "burst-loss@1.0.0": "891a7ff8989a363be5dc5049608d4263f1ad9d60b638bb0524563678dfd17bc4",
    "clean@1.0.0": "09d17d222f752b8169048cbaf1ba8ff85b77b3c615aeb00e3e2a617eb5438f56",
    "deletion-heavy@1.0.0": "3b305f84bdeba515db8da2ee83e7233d54bb9110939d22c789dc1cde018ef001",
    "dropout-10@1.0.0": "298c29a13b02751fb2c8cbe5bea1bb9d6ee17707a90b2007fe3fe7a2687e065a",
    "dropout-20@1.0.0": "298c29a13b02751fb2c8cbe5bea1bb9d6ee17707a90b2007fe3fe7a2687e065a",
    "dropout-5@1.0.0": "298c29a13b02751fb2c8cbe5bea1bb9d6ee17707a90b2007fe3fe7a2687e065a",
    "experiments/v7/fit-cnr/a2/models/cnr-ont-fit.json": "094774203f91ec5d2996a00fe078468dec7f40e9aec778888b85f0f2ba618b92",
    "experiments/v7/fit-cnr/a2/models/cnr-ont-p4tie-fit.json": "92bda9b2c4448662d2ae6fa296db0028df9c6099a747ddc68b9ce45e2493cfd6",
    "experiments/v7/fit-d02/a2/models/illumina-iseq-twist-fit.json": "c53cf8af4d1d0ce72ce93734ce2dadbbfd97894afa94a022d3a5ae8f23eb18af",
    "experiments/v7/fit-d03/a2/models/ont-guppy-fast-pass-bwd-fit.json": "bf9127bcc01b8db18eec5b13bdb78fa29a7c2c01927b8bc0c1a76aad1ddcf3f8",
    "experiments/v7/fit-d03/a2/models/ont-guppy-fast-pass-fwd-fit.json": "344140ea91c1cd6bad1d5134ef0b82ac04944528a60a6a7cd8093805d4133c49",
    "experiments/v7/fit-d03/a2/models/ont-guppy-hac-pass-bwd-fit.json": "d537ad1a7a0adedfc1ad4fc964235707d3a1f6a588d3244a2039ead38bc33c47",
    "experiments/v7/fit-d03/a2/models/ont-guppy-hac-pass-fwd-fit.json": "7c764b1c97104141fc5167389706b438b3cfa80934ee1573b39f345024f6da20",
    "illumina-like@1.0.0": "e968431d64ab9a4623dc42cdd15f74537596196f15b4dd97cb8a86639dee1e41",
    "insertion-heavy@1.0.0": "5cb08306e70d04ce186f1f0bdd2aced2bf04233e4a23c691389727bb39b3852f",
    "mixed-harsh@1.0.0": "c311d4f73dffbc3cf2dccfdcb3656bd038ef501b24e0b088cb7f4c213947591e",
    "mixed-mild@1.0.0": "fbb7298bb8d9ca554ad762152d4ebf961c0d0019a1da143beb23129335e88fa8",
    "nanopore-like@1.0.0": "ad6b9fb013713d9c0860bba3b24151057957357e10c3cfa92c3a1d0b62007d64",
    "quality-degradation@1.0.0": "925e4341235968cb882374284d61f67cca4c8f1a85964e074641301a091d65f0",
    "substitution-heavy@1.0.0": "16460c80c021a89c037bbff313c7fb052771f07ad65651c45a680053114a5f4c",
    "uneven-coverage@1.0.0": "741aad7f15403b987478f753bf5849df0d27399fcc6eae9c98ec68a7959bbe57",
}


def _batch_digest(m, n=256, L=150, seed=17):
    rnd = random.Random(5)
    codes = np.array([[rnd.randrange(4) for _ in range(L)] for _ in range(n)], dtype=np.uint8)
    res = Simulator(m.stages).simulate_batch(codes, seed, 0)
    h = hashlib.sha256()
    for k in sorted(res):
        v = res[k]
        if isinstance(v, np.ndarray):
            h.update(k.encode())
            h.update(np.ascontiguousarray(v).tobytes())
    return h.hexdigest()


def _load(key):
    if key.endswith(".json"):
        return cm.from_doc(json.loads((ROOT / key).read_text()))[0]
    return registry.load_model(key)


def test_golden_covers_every_round_2_model_and_every_shipped_model():
    fitted = {str(p.relative_to(ROOT)) for p in ROOT.glob("experiments/v7/fit-*/a2/models/*.json")}
    shipped = {f"{n}@{v}" for n, v in registry.available()}
    assert set(GOLDEN) == fitted | shipped and len(shipped) == 14


@pytest.mark.parametrize("chunk", [em.CHUNK_ELEMENTS, 997])
@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_models_simulate_byte_identically_to_the_dense_kernel(key, chunk, monkeypatch):
    monkeypatch.setattr(em, "CHUNK_ELEMENTS", chunk)
    assert _batch_digest(_load(key)) == GOLDEN[key]


@pytest.mark.parametrize("weights", [None, (0.4, 0.1, 0.2, 0.3)])
@pytest.mark.parametrize("run_mean", [1.2, 4.0])
@pytest.mark.parametrize("ragged", [False, True])
@pytest.mark.parametrize("chunk", [1, 50, 4096, 1 << 21])
def test_chunked_step_equals_the_dense_step_and_leaves_the_same_generator_state(weights, run_mean, ragged, chunk, monkeypatch):
    monkeypatch.setattr(em, "CHUNK_ELEMENTS", chunk)
    rng0 = np.random.default_rng(11)
    m, w = 37, 61
    base = rng0.integers(0, 4, (m, w)).astype(np.uint8)
    lengths = rng0.integers(20, w + 1, m).astype(np.int64) if ragged else None
    sub = em.SubstitutionModel(0.02, matrix=((0, .5, .3, .2), (.2, 0, .5, .3), (.3, .2, 0, .5), (.5, .3, .2, 0)))
    ins = em.InsertionModel(0.05, base_weights=weights, run_distribution="geometric", run_mean=run_mean)
    dele = em.DeletionModel(0.03, run_distribution="geometric", run_mean=1.5)
    rates = em.rate_arrays(base, lengths, sub, ins, dele)
    outs, states = [], []
    for fn in (_dense_per_base_errors, em.per_base_errors):
        rng, aux = np.random.default_rng(5), np.random.default_rng(6)
        outs.append(fn(base, lengths, rates, rng, aux, sub=sub, ins=ins, dele=dele))
        states.append((rng.bit_generator.state, aux.bit_generator.state))
    a, b = outs
    assert set(a) == set(b)
    for k in a:
        if isinstance(a[k], np.ndarray):
            assert a[k].dtype == b[k].dtype and np.array_equal(a[k], b[k]), k
        else:
            assert a[k] == b[k], k
    assert states[0] == states[1]
    assert ins.clustered and a["insertions"] > 0


def test_insertion_run_step_peak_memory_stays_near_the_model_without_runs():
    """VmHWM of 2,048 strands x coverage 40 with a fitted model that has insertion runs and base weights, against the same
    model with single-base insertions (the dense step needed about 2.3 times as much; the chunked one about 1.05)."""
    script = textwrap.dedent('''
        import json, random, sys
        from vnxdna.simulation import model as cm
        from vnxdna.simulation.fit.simulate import simulate_clusters
        doc = json.load(open(sys.argv[1]))
        if sys.argv[2] == "single":
            doc["stages"]["sequencing"]["insertion"]["run_length"] = {"distribution": "single", "mean": 1.0}
            doc.pop("parameters", None)
        m = cm.from_doc(doc)[0]
        rnd = random.Random(1)
        refs = [bytes(rnd.choice(b"ACGT") for _ in range(150)) for _ in range(2048)]
        simulate_clusters(m, refs, 40, 3)
        print([ln.split()[1] for ln in open("/proc/self/status") if ln.startswith("VmHWM")][0])
    ''')
    model = str(ROOT / "experiments/v7/fit-cnr/a2/models/cnr-ont-fit.json")
    doc = json.loads(Path(model).read_text())
    assert doc["stages"]["sequencing"]["insertion"]["run_length"]["distribution"] == "geometric"
    assert doc["stages"]["sequencing"]["insertion"]["base_weights"] is not None
    if not Path("/proc/self/status").exists():
        pytest.skip("VmHWM needs /proc")
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}
    kb = {}
    for mode in ("single", "runs"):
        r = subprocess.run([sys.executable, "-c", script, model, mode], capture_output=True, text=True, env=env, timeout=600)
        assert r.returncode == 0, r.stderr
        kb[mode] = int(r.stdout.split()[-1])
    assert kb["runs"] <= 1.5 * kb["single"] + 64 * 1024, kb


# ---- frozen copy of vnxdna.simulation.errormodels.per_base_errors at 562bf52 (dense insertion-run step); do not edit ----
def _dense_per_base_errors(base: np.ndarray, lengths: np.ndarray | None, rates: tuple, rng: np.random.Generator,
                           aux: np.random.Generator | None, *, sub: SubstitutionModel | None = None,
                           ins: InsertionModel | None = None, dele: DeletionModel | None = None,
                           burst_rate: float = 0.0, burst_max_len: int = 0) -> dict:
    """Joint substitution / insertion / deletion (+ V4 per-read deletion bursts) on a padded batch.

    Per position, one uniform u decides: deletion if u < d; insertion (of a random base *before* the position) if
    d <= u < d + i; substitution if d + i <= u < d + i + s. ``rng`` provides exactly the V4 draws; ``aux`` (needed only
    for a substitution matrix, insertion base weights or clustered deletions) provides the rest. ``lengths`` None means
    every row has the full width (the V4 case). Returns flat codes, lengths, per-base error flags and event counts.
    """
    m, w = base.shape
    sub_r, ins_r, del_r = rates
    u = rng.random((m, w))
    is_del = u < del_r
    is_ins = (u >= del_r) & (u < del_r + ins_r)
    is_sub = (u >= del_r + ins_r) & (u < del_r + ins_r + sub_r)
    valid = None
    if lengths is not None:
        valid = np.arange(w)[None, :] < lengths[:, None]
        is_del &= valid
        is_ins &= valid
        is_sub &= valid
    events = is_del.copy() if dele is not None and dele.clustered else None
    bursts = 0
    if burst_rate:
        burst = np.flatnonzero(rng.random(m) < burst_rate)
        if burst.size:
            blen = rng.integers(1, burst_max_len + 1, burst.size)
            high = w if lengths is None else np.maximum(lengths[burst], 1)
            bpos = rng.integers(0, high, burst.size)
            for r, s, ln in zip(burst.tolist(), bpos.tolist(), blen.tolist()):
                is_del[r, s:s + ln] = True
            bursts = int(burst.size)
    if events is not None and events.any():
        assert aux is not None and dele is not None, "clustered deletions need the auxiliary generator"
        rows, cols = np.nonzero(events)
        runs = aux.geometric(1.0 / dele.run_mean, rows.size)
        starts = np.concatenate([[0], np.cumsum(runs)[:-1]])
        rr = np.repeat(rows, runs)
        cc = np.repeat(cols, runs) + (np.arange(int(runs.sum())) - np.repeat(starts, runs))
        ok = cc < w
        is_del[rr[ok], cc[ok]] = True
    if valid is not None:
        is_del &= valid
    subbed = np.where(is_sub, (base + rng.integers(1, 4, (m, w))) % 4, base).astype(np.uint8)
    if sub is not None and sub.matrix is not None and is_sub.any():
        assert aux is not None, "a substitution matrix needs the auxiliary generator"
        idx = np.nonzero(is_sub)
        subbed[idx] = sample_targets(base[idx], cumulative_matrix(sub.matrix), aux)
    ins_base = rng.integers(0, 4, (m, w)).astype(np.uint8)
    if ins is not None and ins.base_weights is not None and is_ins.any():
        assert aux is not None, "insertion base weights need the auxiliary generator"
        idx = np.nonzero(is_ins)
        ins_base[idx] = aux.choice(4, size=idx[0].size, p=np.asarray(ins.base_weights, dtype=np.float64)).astype(np.uint8)
    keep_base = ~is_del if valid is None else (~is_del & valid)
    if ins is not None and ins.clustered and is_ins.any():
        # /2 insertion runs: Geometric(1/mean) bases inserted before the site (the V4 draw gives the first base)
        assert aux is not None, "insertion runs need the auxiliary generator"
        rows, cols = np.nonzero(is_ins)
        run = np.minimum(aux.geometric(1.0 / ins.run_mean, rows.size), MAX_INSERTION_RUN)
        kmax = int(run.max())
        runlen = np.zeros((m, w), dtype=np.int64)
        runlen[rows, cols] = run
        extra = aux.integers(0, 4, (m, w, kmax - 1)).astype(np.uint8) if kmax > 1 else np.zeros((m, w, 0), np.uint8)
        if ins.base_weights is not None and kmax > 1:
            ew = aux.choice(4, size=(m, w, kmax - 1), p=np.asarray(ins.base_weights, dtype=np.float64)).astype(np.uint8)
            extra = ew
        ibases = np.concatenate([ins_base[:, :, None], extra], axis=2)                    # (m, w, kmax)
        slots = np.concatenate([ibases, subbed[:, :, None]], axis=2).reshape(m, (kmax + 1) * w)
        ikeep = np.arange(kmax)[None, None, :] < runlen[:, :, None]
        keep = np.concatenate([ikeep, keep_base[:, :, None]], axis=2).reshape(m, (kmax + 1) * w)
        err = np.concatenate([ikeep, is_sub[:, :, None]], axis=2).reshape(m, (kmax + 1) * w)
        return {"codes": slots[keep], "lengths": keep.sum(axis=1).astype(np.int64), "err": err[keep],
                "substitutions": int(is_sub.sum()), "insertions": int(runlen.sum()), "deletions": int(is_del.sum()),
                "bursts": bursts}
    slots = np.stack([ins_base, subbed], axis=2).reshape(m, 2 * w)
    keep = np.stack([is_ins, keep_base], axis=2).reshape(m, 2 * w)
    err = np.stack([is_ins, is_sub], axis=2).reshape(m, 2 * w)
    return {"codes": slots[keep], "lengths": keep.sum(axis=1).astype(np.int64), "err": err[keep],
            "substitutions": int(is_sub.sum()), "insertions": int(is_ins.sum()), "deletions": int(is_del.sum()),
            "bursts": bursts}
