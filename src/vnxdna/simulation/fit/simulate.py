"""In-memory simulation of reads from a model for fit validation (SIMULATED): the engine's per-batch simulator with fixed
coverage, so reads of one reference are consecutive and can be grouped without a source index."""
from __future__ import annotations

import numpy as np

from vnxdna.simulation import model as cm
from vnxdna.simulation.engine import Simulator
from vnxdna.simulation.fit.tally import ref_codes

BATCH = 1024
_BASES = np.frombuffer(b"ACGTN", dtype=np.uint8)


def validation_model(model: cm.ChannelModel, coverage: int) -> cm.ChannelModel:
    """The model with fixed coverage, no dropout, no reverse-complement reads (the fitting pipeline aligns in reference
    orientation) and no read-length variation; every error parameter is unchanged. The derivation is recorded."""
    return model.with_parameters({
        "sequencing.coverage": {"model": "fixed", "mean": float(coverage), "dispersion": 5.0, "sigma": 0.0},
        "synthesis.dropout_rate": 0.0, "sequencing.reverse_complement_rate": 0.0, "sequencing.n_rate": 0.0,
        "synthesis.yield_sigma": 0.0, "amplification.gc_bias": {"strength": 0.0, "optimum": 0.5},
        "amplification.efficiency_sigma": 0.0, "amplification.duplicate_rate": 0.0, "storage.retention": 1.0,
        "storage.contamination_rate": 0.0, "storage.breakage_rate": 0.0, "sequencing.duplicate_rate": 0.0,
        "sequencing.missing_read_rate": 0.0, "sequencing.shuffle_window": 0,
        "sequencing.read_length": {"max_length": None, "truncation_rate": 0.0, "min_fraction": 0.5}},
        label="validation: fixed coverage")


def simulate_clusters(model: cm.ChannelModel, refs: list, coverage: int, seed: int, *, stats: dict | None = None,
                      quals: list | None = None) -> list:
    """For each reference (bytes, equal lengths), ``coverage`` simulated reads (bytes ACGT/N). Pure function of the inputs.
    If ``stats`` is a dict, the simulator's own event counters (substitutions, insertions = inserted bases, deletions =
    deleted bases) are added to it. If ``quals`` is a list, the Phred+33 quality strings (same shape as the result) are
    appended to it per reference."""
    vm = validation_model(model, coverage)
    sim = Simulator(vm.stages)
    out: list = []
    for b in range(0, len(refs), BATCH):
        part = refs[b:b + BATCH]
        codes = np.stack([ref_codes(r) for r in part])
        res = sim.simulate_batch(codes, seed, b // BATCH)
        text = _BASES[res["codes"]].tobytes()
        offs = np.concatenate([[0], np.cumsum(res["lengths"])])
        reads = [text[offs[i]:offs[i + 1]] for i in range(res["lengths"].size)]
        if stats is not None:
            for k in ("substitutions", "insertions", "deletions"):
                stats[k] = stats.get(k, 0) + res["stats"][k]
        if len(reads) != len(part) * coverage:
            raise RuntimeError("simulation did not give the requested fixed coverage")
        qtext = None
        if quals is not None:
            qtext = (res["quals"].astype(np.uint8) + 33).tobytes()
        for i in range(len(part)):
            out.append(reads[i * coverage:(i + 1) * coverage])
            if quals is not None and qtext is not None:
                quals.append([qtext[offs[i * coverage + j]:offs[i * coverage + j + 1]] for j in range(coverage)])
    return out
