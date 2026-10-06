"""The staged channel simulator (SIMULATED): synthesis → storage → amplification → sequencing, from a channel model.

Input: a strand file (FASTA/FASTQ/VXS, equal lengths within each batch of 1024 strands). Output: reads (FASTQ or FASTA)
plus machine-readable metadata (``vnx.simulation-metadata/1``: model, parameters, versions, seed, input/output hashes).

**Order of operations.** Pool level: storage strand loss (i.i.d. + contiguous bursts in pool order,
:mod:`vnxdna.simulation.loss`). Then, per batch of 1024 surviving strands:

1. synthesis dropout; molecule variants (synthesis substitutions/insertions/deletions, truncation), storage damage and
   breakage (only when one of those is configured);
2. abundance: amplification GC bias × synthesis yield bias × amplification efficiency × storage retention;
3. coverage (fixed / Poisson / negative binomial / lognormal) → reads; each read samples an intact molecule variant;
   amplification duplicates; PCR substitutions;
4. sequencing: joint per-base substitution/insertion/deletion (homopolymer multipliers, position profile, matrix,
   clustered deletions), per-read deletion bursts, N calls, quality, reverse complement, quality variation,
   identical duplicates, read-length variation, missing reads; storage contamination reads are appended.

**Determinism and compatibility.** Batch b draws the V4 mechanisms from ``default_rng([seed, b])`` in exactly the V4 order
(``vnxdna.simulation.channel.simulate_batch``); every other mechanism draws from its stage's own generator
``default_rng([seed, b, TAG])``. So a model that uses only V4 mechanisms gives the V4 reads byte for byte, a mechanism
switched on does not re-randomise the others (common random numbers), and the output is a pure function of (strands,
model, seed), independent of the worker count.
"""
from __future__ import annotations

import hashlib
import platform
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from vnxdna._version import __version__
from vnxdna.core.errors import VNXOutputError
from vnxdna.core.util import atomic_output
from vnxdna.dnaenc.strandio import iter_batches
from vnxdna.simulation import errormodels as em
from vnxdna.simulation.channel import BATCH, _homopolymer_mask, _records, _run_lengths, _serialize, _stack
from vnxdna.simulation.loss import LossConfig, loss_mask
from vnxdna.simulation.model import SCHEMA_V1, ChannelModel

SIMULATOR = "vnxdna.simulation.engine"
SIMULATOR_VERSION = "1.0.0"
METADATA_SCHEMA = "vnx.simulation-metadata/1"
DATA_SOURCE = "SIMULATED"
STATEMENT = ("SIMULATED: software strands through a software channel model. No DNA was synthesised, stored, amplified or "
             "sequenced; not biological validation.")
#: auxiliary generator tags (stage streams); the V4 mechanisms use default_rng([seed, batch])
TAG_SYNTHESIS, TAG_STORAGE, TAG_AMPLIFICATION, TAG_SEQUENCING = 0x53594E, 0x53544F, 0x414D50, 0x534551
TAG_HETEROGENEITY = 0x484554                          # /2 read_heterogeneity: its own stream, so models without it are unchanged
HETEROGENEITY_SITE_CAP = 0.95                         # a scaled site's total event probability never exceeds this
SHUFFLE_KEY = 0x5F5F          # V4: default_rng([seed, 0x5F5F]) permutes shuffle windows
LOSS_KEY = 0x56360001         # V6 loss: default_rng([seed, 0x56360001])

V4_STATS = ("strands", "dropped", "zero_coverage", "reads", "substitutions", "insertions", "deletions", "bursts", "n_calls",
            "reverse_complement", "duplicates", "bases")
EXT_STATS = ("synthesis_substitutions", "synthesis_insertions", "synthesis_deletions", "truncated_molecules",
             "damaged_bases", "broken_molecules", "broken_strands", "amplification_duplicates", "pcr_substitutions",
             "truncated_reads", "missing_reads", "contaminant_reads")


class Simulator:
    """A compiled channel model: the stage error models built once from the /1 stages."""

    def __init__(self, stages: dict):
        from vnxdna.simulation.model2 import refuse_unsupported
        refuse_unsupported(stages)                    # /2 effects the simulator cannot honour are refused, never ignored
        self.stages = stages
        syn, sto, amp, seq = (stages[k] for k in ("synthesis", "storage", "amplification", "sequencing"))
        P = em.PositionProfile.from_json
        self.syn_dropout = em.DropoutModel(syn["dropout_rate"])
        sp = P(syn["position_profile"])
        self.syn_sub = em.SubstitutionModel.from_json(syn["substitution"], sp)
        self.syn_ins = em.InsertionModel.from_json(syn["insertion"], sp)
        self.syn_del = em.DeletionModel.from_json(syn["deletion"], sp)
        self.syn_profile = sp
        self.truncation = syn["truncation"]
        self.yield_sigma = syn["yield_sigma"]
        self.molecules = syn["molecules_per_strand"]
        self.strand_loss = sto["strand_loss"]
        self.retention = sto["retention"]
        self.damage = em.SubstitutionModel.from_json(sto["damage"])
        self.breakage = sto["breakage_rate"]
        self.contamination = sto["contamination_rate"]
        self.gc_strength, self.gc_optimum = amp["gc_bias"]["strength"], amp["gc_bias"]["optimum"]
        self.efficiency_sigma = amp["efficiency_sigma"]
        self.pcr = em.SubstitutionModel.from_json(amp["substitution_per_cycle"])
        self.pcr_cycles = amp["cycles"]
        self.amp_duplicates = amp["duplicate_rate"]
        self.coverage = em.CoverageModel.from_json(seq["coverage"])
        qp = P(seq["position_profile"])
        self.seq_profile = qp
        self.seq_sub = em.SubstitutionModel.from_json(seq["substitution"], qp)
        self.seq_ins = em.InsertionModel.from_json(seq["insertion"], qp)
        self.seq_del = em.DeletionModel.from_json(seq["deletion"], qp)
        hp = seq["homopolymer"]
        self.hp_min, self.hp_indel, self.hp_sub = hp["min_run"], hp["indel_multiplier"], hp["substitution_multiplier"]
        by_length = hp.get("indel_by_length")        # /2 (V7 7.4): indel multiplier per run length 1..5, 6+
        self.hp_by_length = None if by_length is None else np.asarray([1.0] + list(by_length), dtype=np.float64)
        self.burst_rate, self.burst_max = seq["bursts"]["rate"], seq["bursts"]["max_length"]
        self.n_rate = seq["n_rate"]
        self.rc_rate = seq["reverse_complement_rate"]
        self.quality = em.QualityModel.from_json(seq["quality"])
        self.read_length = seq["read_length"]
        self.seq_duplicates = seq["duplicate_rate"]
        self.missing = seq["missing_read_rate"]
        self.shuffle_window = seq["shuffle_window"]
        self.syn_per_base = self.syn_sub.active or self.syn_ins.active or self.syn_del.active
        self.molecular = self.syn_per_base or self.truncation["rate"] > 0 or self.damage.active or self.breakage > 0
        self.seq_aux = (self.seq_sub.matrix is not None or self.seq_ins.base_weights is not None or self.seq_del.clustered
                        or self.seq_ins.clustered)
        self.seq_context = seq.get("context")         # /2: 3-mer multipliers (None for /1 models)
        het = seq.get("read_heterogeneity")           # /2: per-read gamma rate multiplier (None for /1 models)
        self.het_shape = None if het is None else float(het["shape"])

    @property
    def pool_loss(self) -> bool:
        sl = self.strand_loss
        return bool(sl["rate"] or (sl["burst_count"] and sl["burst_length"]))

    # ------------------------------------------------------------------------------------------------------ molecules
    def build_molecules(self, codes: np.ndarray, a_syn, a_sto, stats: dict):
        """Molecule variants (n*M, W) after synthesis and storage; returns (codes, lengths, intact)."""
        n, L = codes.shape
        M = self.molecules
        base = np.repeat(codes, M, axis=0)
        if self.syn_per_base:
            rates = em.rate_arrays(base, None, self.syn_sub, self.syn_ins, self.syn_del, self.syn_profile)
            res = em.per_base_errors(base, None, rates, a_syn(), a_syn(), sub=self.syn_sub, ins=self.syn_ins,
                                     dele=self.syn_del)
            lens = res["lengths"]
            var = em.pad(res["codes"], lens)
            stats["synthesis_substitutions"] += res["substitutions"]
            stats["synthesis_insertions"] += res["insertions"]
            stats["synthesis_deletions"] += res["deletions"]
        else:
            var, lens = base.copy(), np.full(n * M, L, dtype=np.int64)
        w = var.shape[1]
        if self.truncation["rate"]:
            new = em.truncation_lengths(lens, self.truncation["rate"], self.truncation["min_fraction"], a_syn())
            shift = lens - new
            idx = np.minimum(np.arange(w)[None, :] + shift[:, None], w - 1)
            var = np.where(np.arange(w)[None, :] < new[:, None], np.take_along_axis(var, idx, axis=1), em.PAD)
            var = var.astype(np.uint8)
            stats["truncated_molecules"] += int((new < lens).sum())
            lens = new
        valid = np.arange(w)[None, :] < lens[:, None]
        if self.damage.active:
            rng = a_sto()
            hit = self.damage.hits(var, valid, rng)
            var = self.damage.mutate(var, hit, rng)
            stats["damaged_bases"] += int(hit.sum())
        intact = np.ones(n * M, dtype=bool)
        if self.breakage:
            intact = a_sto().random(n * M) < (1.0 - self.breakage) ** lens
            stats["broken_molecules"] += int((~intact).sum())
        return var, lens, intact

    # ------------------------------------------------------------------------------------------------------ batch
    def simulate_batch(self, codes: np.ndarray, seed: int, batch_index: int) -> dict:
        """Reads for one batch of equal-length strands (n, L): a pure function of (codes, model, seed, batch_index)."""
        rng = np.random.default_rng([seed, batch_index])
        streams: dict[int, np.random.Generator] = {}

        def aux(tag: int):
            def get() -> np.random.Generator:
                if tag not in streams:
                    streams[tag] = np.random.default_rng([seed, batch_index, tag])
                return streams[tag]
            return get
        a_syn, a_sto, a_amp, a_seq = aux(TAG_SYNTHESIS), aux(TAG_STORAGE), aux(TAG_AMPLIFICATION), aux(TAG_SEQUENCING)
        n, L = codes.shape
        stats = {k: 0 for k in V4_STATS + EXT_STATS}
        stats["strands"] = n
        # ---- synthesis dropout (V4 draw), molecules
        drop = self.syn_dropout.lost(n, rng)
        mol = self.build_molecules(codes, a_syn, a_sto, stats) if self.molecular else None
        # ---- abundance and coverage
        gc = ((codes == 1) | (codes == 2)).mean(axis=1)
        weighted = bool(self.gc_strength)
        weight = (np.exp(-self.gc_strength * ((gc - self.gc_optimum) / 0.1) ** 2) if self.gc_strength else np.ones(n))
        if self.yield_sigma:
            weight = weight * em.lognormal_weights(self.yield_sigma, n, a_syn())
            weighted = True
        if self.efficiency_sigma:
            weight = weight * em.lognormal_weights(self.efficiency_sigma, n, a_amp())
            weighted = True
        if self.retention != 1.0:
            weight = weight * self.retention
            weighted = True
        reads = self.coverage.sample(self.coverage.mean * weight, rng, weighted)
        reads = np.where(drop, 0, reads).astype(np.int64)
        stats["dropped"] = int(drop.sum())
        broken = np.zeros(n, dtype=bool)
        if mol is not None:
            per_strand = mol[2].reshape(n, self.molecules).sum(axis=1)
            broken = (per_strand == 0) & ~drop
            reads = np.where(per_strand == 0, 0, reads)
            stats["broken_strands"] = int(broken.sum())
        stats["zero_coverage"] = int(((reads == 0) & ~drop & ~broken).sum())
        src = np.repeat(np.arange(n), reads)
        vidx = None
        if mol is not None and src.size:
            ids = np.flatnonzero(mol[2])
            start = np.concatenate([[0], np.cumsum(per_strand)[:-1]])
            cnt = per_strand[src]
            k = np.minimum((a_syn().random(src.size) * cnt).astype(np.int64), cnt - 1)
            vidx = ids[start[src] + k]
        if self.amp_duplicates and src.size:
            dup = a_amp().random(src.size) < self.amp_duplicates
            order = np.repeat(np.arange(src.size), np.where(dup, 2, 1))
            src = src[order]
            vidx = None if vidx is None else vidx[order]
            stats["amplification_duplicates"] = int(dup.sum())
        m = src.size
        if m == 0:
            flat, lengths, q = np.zeros(0, dtype=np.uint8), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.uint8)
        else:
            flat, lengths, q = self._sequence(codes, mol, src, vidx, rng, a_amp, a_seq, stats,
                                              a_het=aux(TAG_HETEROGENEITY))
        flat, lengths, q = self._post(flat, lengths, q, L, a_seq, a_sto, stats)
        stats["reads"] = int(lengths.size)
        stats["bases"] = int(flat.size)
        return {"codes": flat, "lengths": lengths, "quals": q, "stats": stats}

    def _sequence(self, codes, mol, src, vidx, rng, a_amp, a_seq, stats, a_het=None):
        m = src.size
        if mol is not None:
            base, lens = mol[0][vidx], mol[1][vidx]
            hp_codes, hp_index = mol[0], vidx
        else:
            base, lens = codes[src], None
            hp_codes, hp_index = codes, src
        w = base.shape[1]
        if self.pcr_cycles and self.pcr.active:
            valid = np.ones(base.shape, dtype=bool) if lens is None else np.arange(w)[None, :] < lens[:, None]
            r = a_amp()
            hit = self.pcr.hits(base, valid, r, scale=self.pcr_cycles)
            base = self.pcr.mutate(base, hit, r)
            stats["pcr_substitutions"] = int(hit.sum())
        hp = None
        if self.hp_indel != 1.0 or self.hp_sub != 1.0:
            hp = _homopolymer_mask(hp_codes, self.hp_min)[hp_index]
        indel_site = None
        if self.hp_by_length is not None:
            runs = np.minimum(_run_lengths(hp_codes), self.hp_by_length.size - 1)[hp_index]
            indel_site = self.hp_by_length[runs]
        rates = em.rate_arrays(base, lens, self.seq_sub, self.seq_ins, self.seq_del, self.seq_profile, hp, self.hp_indel,
                               self.hp_sub, self.seq_context, indel_site)
        if self.het_shape is not None:
            rates = read_heterogeneity(rates, self.het_shape, a_het())
        res = em.per_base_errors(base, lens, rates, rng, a_seq() if self.seq_aux else None, sub=self.seq_sub,
                                 ins=self.seq_ins, dele=self.seq_del, burst_rate=self.burst_rate,
                                 burst_max_len=self.burst_max)
        flat, lengths, flat_err = res["codes"], res["lengths"], res["err"]
        stats["bursts"] = res["bursts"]
        if self.n_rate:
            nmask = rng.random(flat.size) < self.n_rate
            flat = np.where(nmask, 4, flat).astype(np.uint8)
            flat_err |= nmask
            stats["n_calls"] = int(nmask.sum())
        q = self.quality.base_scores(flat_err, rng)
        rc = rng.random(m) < self.rc_rate
        if rc.any():
            em.reverse_complement_flat(flat, q, lengths, np.flatnonzero(rc))
            stats["reverse_complement"] = int(rc.sum())
        if self.quality.varies:
            q = self.quality.vary(q, lengths, a_seq())
        stats["substitutions"] = res["substitutions"]
        stats["insertions"] = res["insertions"]
        stats["deletions"] = res["deletions"]
        if self.seq_duplicates:
            dup = rng.random(m) < self.seq_duplicates
            if dup.any():
                offs = np.concatenate([[0], np.cumsum(lengths)])
                order = np.repeat(np.arange(m), np.where(dup, 2, 1))
                flat = np.concatenate([flat[offs[r]:offs[r + 1]] for r in order])
                q = np.concatenate([q[offs[r]:offs[r + 1]] for r in order])
                lengths = lengths[order]
                stats["duplicates"] = int(dup.sum())
        return flat, lengths, q

    def _post(self, flat, lengths, q, L, a_seq, a_sto, stats):
        rl = self.read_length
        if lengths.size and (rl["max_length"] is not None or rl["truncation_rate"]):
            new = lengths if rl["max_length"] is None else np.minimum(lengths, rl["max_length"])
            if rl["truncation_rate"]:
                new = em.truncation_lengths(new, rl["truncation_rate"], rl["min_fraction"], a_seq())
            if (new < lengths).any():
                starts = np.concatenate([[0], np.cumsum(lengths)[:-1]])
                pos = np.arange(flat.size) - np.repeat(starts, lengths)
                keep = pos < np.repeat(new, lengths)
                flat, q = flat[keep], q[keep]
                stats["truncated_reads"] = int((new < lengths).sum())
                lengths = new
        if lengths.size and self.missing:
            miss = a_seq().random(lengths.size) < self.missing
            if miss.any():
                keep = np.repeat(~miss, lengths)
                flat, q, lengths = flat[keep], q[keep], lengths[~miss]
                stats["missing_reads"] = int(miss.sum())
        if self.contamination and lengths.size:
            r = a_sto()
            k = int(r.poisson(lengths.size * self.contamination / (1.0 - self.contamination)))
            if k:
                flat = np.concatenate([flat, r.integers(0, 4, k * L).astype(np.uint8)])
                q = np.concatenate([q, np.full(k * L, self.quality.correct, dtype=np.uint8)])
                lengths = np.concatenate([lengths, np.full(k, L, dtype=np.int64)])
                stats["contaminant_reads"] = k
        return flat, lengths, q


def read_heterogeneity(rates: tuple, shape: float, rng: np.random.Generator) -> tuple:
    """Scale each read's (row's) per-site sub/ins/del probabilities by one multiplier m ~ Gamma(shape, 1/shape) (mean 1);
    where a site's scaled total would exceed ``HETEROGENEITY_SITE_CAP`` the three are scaled down together to the cap."""
    sub_r, ins_r, del_r = rates
    m = rng.gamma(shape, 1.0 / shape, size=sub_r.shape[0])[:, None]
    total = (sub_r + ins_r + del_r) * m
    scale = np.where(total > HETEROGENEITY_SITE_CAP, m * HETEROGENEITY_SITE_CAP / np.maximum(total, 1e-300), m)
    return sub_r * scale, ins_r * scale, del_r * scale


# ================================================================================================================ files
def _count_strands(path) -> int:
    return sum(b.count for b in iter_batches(path, 65536))


def strand_batches(path, mask: np.ndarray | None = None):
    """(batch index, (n, L) codes) over the strands of ``path`` whose ``mask`` entry is True, in batches of BATCH."""
    buf: list[np.ndarray] = []
    index = 0
    g = 0
    for batch in iter_batches(path, BATCH):
        offs = batch.offsets
        for i in range(batch.count):
            if mask is None or mask[g]:
                buf.append(batch.codes[offs[i]:offs[i + 1]])
            g += 1
            if len(buf) == BATCH:
                yield index, _stack(buf)
                index += 1
                buf = []
    if buf:
        yield index, _stack(buf)


_W: dict = {}


def _w_init(stages: dict, seed: int) -> None:
    _W["sim"], _W["seed"] = Simulator(stages), seed


def _w_task(args):
    index, codes = args
    return index, _W["sim"].simulate_batch(codes, _W["seed"], index)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(1 << 22):
            h.update(block)
    return h.hexdigest()


def simulate_file(strands, output, model: ChannelModel, seed: int, *, fmt: str | None = None, workers: int = 1,
                  overwrite: bool = False, progress=None, overrides: dict | None = None) -> dict:
    """Simulate ``model`` over a strand file. Returns event counts plus ``metadata`` (``vnx.simulation-metadata/1``)."""
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 63:
        from vnxdna.core.errors import VNXConfigurationError
        raise VNXConfigurationError("seed must be a non-negative 63-bit integer", details={"seed": seed})
    t0 = time.perf_counter()
    sim = Simulator(model.stages)
    fmt = fmt or ("fasta" if str(output).endswith((".fa", ".fasta")) else "fastq")
    if fmt not in ("fasta", "fastq"):
        raise VNXOutputError("channel output must be fasta or fastq")
    pool_strands = None
    mask = None
    if sim.pool_loss:
        pool_strands = _count_strands(strands)
        sl = sim.strand_loss
        mask = loss_mask(pool_strands, LossConfig(dropout=sl["rate"], burst_count=sl["burst_count"],
                                                  burst_length=sl["burst_length"], seed=seed))
    totals = {k: 0 for k in V4_STATS + EXT_STATS}
    count = 0
    shuffle = sim.shuffle_window
    rng_shuffle = np.random.default_rng([seed, SHUFFLE_KEY])
    with atomic_output(output, overwrite=overwrite, mode=0o644) as tmp:
        with open(tmp, "wb", buffering=1 << 20) as out:
            window: list[bytes] = []

            def flush(force: bool = False) -> None:
                nonlocal window
                if shuffle and (len(window) >= shuffle or force) and window:
                    for i in rng_shuffle.permutation(len(window)).tolist():
                        out.write(window[i])
                    window = []

            def take(res: dict) -> None:
                nonlocal count
                for k, v in res["stats"].items():
                    totals[k] = totals.get(k, 0) + v
                if shuffle:
                    window.extend(_records(res, fmt, count))
                    flush()
                else:
                    out.write(_serialize(res, fmt, count))
                count += int(res["lengths"].size)
                if progress:
                    progress({"stage": "channel", "reads": count, "elapsed": time.perf_counter() - t0})

            if workers == 1:
                for index, codes in strand_batches(strands, mask):
                    take(sim.simulate_batch(codes, seed, index))
            else:
                with ProcessPoolExecutor(max_workers=workers, initializer=_w_init, initargs=(model.stages, seed)) as pool:
                    q: deque = deque()
                    for item in strand_batches(strands, mask):
                        q.append(pool.submit(_w_task, item))
                        if len(q) >= 2 * workers:
                            take(q.popleft().result()[1])
                    while q:
                        take(q.popleft().result()[1])
            flush(force=True)
    seconds = time.perf_counter() - t0
    surviving = totals["strands"]
    pool_n = surviving if pool_strands is None else pool_strands
    totals.update({"pool_strands": pool_n, "storage_lost": pool_n - surviving})
    meta = metadata(model, seed, strands, output, fmt, totals, overrides=overrides)
    body: dict[str, Any] = dict(totals)
    body.update({"output": str(output), "format": fmt, "seconds": seconds, "workers": workers,
                 "reads_per_second": round(count / seconds, 1) if seconds else None,
                 "bases_per_second": round(totals.get("bases", 0) / seconds, 1) if seconds else None,
                 "simulation": "SIMULATED channel; parameters are stress settings, not fitted to a sequencing platform"
                 if model.doc["data_source"] == "SIMULATED" else STATEMENT,
                 "metadata": meta})
    return body


def versions() -> dict:
    return {"software": __version__, "simulator": SIMULATOR, "simulator_version": SIMULATOR_VERSION,
            "model_schema": SCHEMA_V1, "metadata_schema": METADATA_SCHEMA, "numpy": np.__version__,
            "python": platform.python_version()}


def metadata(model: ChannelModel, seed: int, strands, output, fmt: str, stats: dict, *,
             overrides: dict | None = None) -> dict:
    """``vnx.simulation-metadata/1``: everything needed to reproduce and label one simulation."""
    strands, output = Path(strands), Path(output)
    strand_len = None
    for _, codes in strand_batches(strands):
        strand_len = int(codes.shape[1])
        break
    return {
        "schema": METADATA_SCHEMA,
        "data_source": DATA_SOURCE,
        "evidence_class": "SIMULATED",
        "statement": STATEMENT,
        "seed": seed,
        "model": {"name": model.name, "version": model.version, "schema": model.doc["schema"], "read_as": model.read_as,
                  "sha256": model.sha256, "source": model.source, "data_source": model.doc["data_source"],
                  "evidence_class": model.doc["evidence_class"], "provenance": model.doc["provenance"]},
        "parameters": model.parameters(),
        "overrides": dict(overrides or {}),
        "rng": {"bit_generator": "PCG64", "batch_strands": BATCH, "main": "default_rng([seed, batch])",
                "stages": {"synthesis": TAG_SYNTHESIS, "storage": TAG_STORAGE, "amplification": TAG_AMPLIFICATION,
                           "sequencing": TAG_SEQUENCING}, "pool_loss": "default_rng([seed, 0x56360001])",
                "shuffle": "default_rng([seed, 0x5F5F])"},
        "versions": versions(),
        "input": {"file": strands.name, "sha256": sha256_file(strands), "strands": stats.get("pool_strands"),
                  "strand_length": strand_len},
        "output": {"file": output.name, "sha256": sha256_file(output), "bytes": output.stat().st_size, "format": fmt,
                   "reads": stats.get("reads")},
        "stats": {k: stats[k] for k in V4_STATS + EXT_STATS + ("pool_strands", "storage_lost") if k in stats},
    }


__all__ = ["Simulator", "simulate_file", "metadata", "versions", "strand_batches", "SIMULATOR_VERSION", "METADATA_SCHEMA",
           "DATA_SOURCE", "STATEMENT"]
