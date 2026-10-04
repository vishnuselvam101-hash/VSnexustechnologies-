"""Named, versioned mixed-error channel models and a composer over the existing V4/V6 simulators (SIMULATED).

SIMULATION ONLY: no DNA was synthesised, stored or sequenced and nothing here is biological validation. Models are
synthetic stress profiles; the "illumina-like" and "nanopore-like" ones only follow publicly described *qualitative*
characteristics and are not fitted to any measured platform.

This module adds no new error mechanics. It composes
  1. strand loss         vnxdna.v6.loss        (i.i.d. dropout + contiguous bursts in pool order)
  2. per-read channel    vnxdna.v4.channel     (coverage, sub/ins/del, homopolymer effects, duplicates, bursts, N, strand, quality)
and records a provenance sidecar. The output is a pure function of (input strands, model, seed): independent of the
worker count.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import numpy as np

from vnxdna.v4 import channel as v4ch
from vnxdna.v4.constraints import iter_fasta
from vnxdna.v4.errors import VNXConfigurationError
from vnxdna.v6.loss import LossConfig, apply_loss

HERE = Path(__file__).resolve().parent
MODEL_DIR = HERE / "models"
REPO = HERE.parents[2]
CLASSIFICATION = "SIMULATED"
STATEMENT = ("SIMULATED: software-generated strands and software channel models (vnxdna.v6.loss + vnxdna.v4.channel). "
             "No DNA was synthesised, stored or sequenced; not biological validation.")

_CH_FIELDS = [f for f in v4ch.ChannelConfig.__dataclass_fields__ if f != "seed"]
_LOSS_FIELDS = [f for f in LossConfig.__dataclass_fields__ if f != "seed"]
_QUALITY_FIELDS = ["quality_correct", "quality_error", "quality_informative"]


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------------------- models
class Model:
    """A named, versioned error model. Every parameter of the loss and channel layers must be explicit in the file."""

    def __init__(self, doc: dict, path: Path):
        self.path = Path(path)
        self.sha256 = sha256_file(path)
        for key in ("name", "version", "classification", "loss", "channel"):
            if key not in doc:
                raise VNXConfigurationError(f"model {path}: missing key {key!r}")
        if doc["classification"] != CLASSIFICATION:
            raise VNXConfigurationError(f"model {path}: classification must be {CLASSIFICATION}")
        for section, fields in (("loss", _LOSS_FIELDS), ("channel", _CH_FIELDS)):
            missing = [f for f in fields if f not in doc[section]]
            extra = [f for f in doc[section] if f not in fields]
            if missing or extra:
                raise VNXConfigurationError(f"model {path}: section {section!r} missing {missing} / unknown {extra}")
        self.name, self.version, self.description = doc["name"], doc["version"], doc.get("description", "")
        self.loss, self.channel = dict(doc["loss"]), dict(doc["channel"])
        self.channel_config(0)      # validates (raises VNXConfigurationError)
        self.loss_config(0)

    def channel_config(self, seed: int) -> v4ch.ChannelConfig:
        return v4ch.ChannelConfig.from_dict({**self.channel, "seed": seed})

    def loss_config(self, seed: int) -> LossConfig:
        return LossConfig(**self.loss, seed=seed).validate()

    def parameters(self) -> dict:
        return {"loss": self.loss, "channel": self.channel}

    def quality_model(self) -> dict:
        return {k: self.channel[k] for k in _QUALITY_FIELDS}

    def coverage_model(self) -> dict:
        c = self.channel
        return {"model": c["coverage_model"], "mean": c["coverage"], "dispersion": c["coverage_dispersion"],
                "gc_bias_strength": c["gc_bias_strength"], "gc_bias_optimum": c["gc_bias_optimum"]}


def model_names() -> list[str]:
    return sorted(p.stem for p in MODEL_DIR.glob("*.json"))


def load_model(name_or_path) -> Model:
    p = Path(str(name_or_path))
    if not p.is_file():
        p = MODEL_DIR / f"{name_or_path}.json"
    if not p.is_file():
        raise VNXConfigurationError(f"unknown model {name_or_path!r}; named models: {model_names()}")
    try:
        doc = json.loads(p.read_text())
    except ValueError as error:
        raise VNXConfigurationError(f"model {p}: invalid JSON: {error}") from None
    return Model(doc, p)


# ----------------------------------------------------------------------------------------------------------- git
def git_info() -> dict:
    def git(*a):
        r = subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else None
    commit = git("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "dirty": None, "dirty_tracked": None}
    return {"commit": commit, "dirty": bool(git("status", "--porcelain")),
            "dirty_tracked": bool(git("status", "--porcelain", "--untracked-files=no"))}


# ----------------------------------------------------------------------------------------------------------- strands
def read_strand_codes(path) -> np.ndarray:
    table = {"A": 0, "C": 1, "G": 2, "T": 3}
    seqs = [s for _, s in iter_fasta(path)]
    return np.array([[table[c] for c in s] for s in seqs], dtype=np.uint8).reshape(len(seqs), -1)


# ----------------------------------------------------------------------------------------------------------- composer
def compose(model: Model, strands, output, seed: int, *, workers: int = 1, workdir=None) -> dict:
    """Strand loss (v6.loss) → coverage + per-read errors + quality (v4.channel). Returns a report with raw counts."""
    import tempfile
    strands, output = Path(strands), Path(output)
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="vnx-chan-", dir=workdir) as tmp:
        loss_cfg = model.loss_config(seed)
        if loss_cfg.dropout or (loss_cfg.burst_count and loss_cfg.burst_length):
            surviving = Path(tmp) / "surviving.fasta"
            loss = apply_loss(str(strands), str(surviving), loss_cfg)
        else:
            n = sum(1 for _ in iter_fasta(strands))
            surviving, loss = strands, {"strands": n, "kept": n, "lost": 0, "loss": loss_cfg.to_dict()}
        if loss["kept"] == 0:
            raise VNXConfigurationError("the loss model removed every strand")
        cfg = model.channel_config(seed)
        stats = v4ch.simulate_file(surviving, output, cfg, fmt=None, workers=workers, overwrite=True)
    return {"loss": loss, "channel": {k: v for k, v in stats.items() if k not in ("config", "output", "seconds", "workers",
            "reads_per_second", "bases_per_second", "simulation")}, "channel_config": cfg.to_dict(),
            "seconds": round(time.perf_counter() - t0, 3)}


def realised_rates(report: dict, strand_len: int, strands_in: int) -> dict:
    """Per-position realised error rates from the simulator's event counts (reads excluding PCR duplicates)."""
    s = report["channel"]
    reads0 = s["reads"] - s["duplicates"]
    pos = reads0 * strand_len
    bases0 = pos + s["insertions"] - s["deletions"]
    return {
        "strand_loss_fraction": report["loss"]["lost"] / strands_in if strands_in else 0.0,
        "reads_per_input_strand": s["reads"] / strands_in if strands_in else 0.0,
        "reads_per_surviving_strand": s["reads"] / report["loss"]["kept"],
        "substitution_rate": s["substitutions"] / pos if pos else 0.0,
        "insertion_rate": s["insertions"] / pos if pos else 0.0,
        "deletion_rate": s["deletions"] / pos if pos else 0.0,
        "n_rate": s["n_calls"] / bases0 if bases0 else 0.0,
        "burst_rate": s["bursts"] / reads0 if reads0 else 0.0,
        "duplication_rate": s["duplicates"] / reads0 if reads0 else 0.0,
        "reverse_complement_rate": s["reverse_complement"] / s["reads"] if s["reads"] else 0.0,
    }


def expected_rates(model: Model, codes: np.ndarray) -> dict:
    """Configured per-position rates, including homopolymer multipliers and the deletion contributed by per-read bursts."""
    c = model.channel
    sub, ins, dele = c["substitution_rate"], c["insertion_rate"], c["deletion_rate"]
    L = codes.shape[1]
    if c["homopolymer_indel_multiplier"] != 1.0 or c["homopolymer_substitution_multiplier"] != 1.0:
        hp = v4ch._homopolymer_mask(codes, c["homopolymer_min_run"])
        sub_p = np.where(hp, sub * c["homopolymer_substitution_multiplier"], sub).mean()
        ins_p = np.where(hp, ins * c["homopolymer_indel_multiplier"], ins).mean()
        del_p = np.where(hp, dele * c["homopolymer_indel_multiplier"], dele).mean()
    else:
        sub_p, ins_p, del_p = sub, ins, dele
    if c["burst_rate"]:
        starts = np.arange(L)[:, None]
        lens = np.arange(1, c["burst_max_len"] + 1)[None, :]
        mean_cov = np.minimum(lens, L - starts).mean()          # expected positions covered by one burst
        del_p += c["burst_rate"] * mean_cov / L * (1 - del_p)
    return {"substitution_rate": sub_p, "insertion_rate": ins_p, "deletion_rate": del_p, "n_rate": c["n_rate"],
            "burst_rate": c["burst_rate"], "duplication_rate": c["duplication_rate"],
            "reverse_complement_rate": c["reverse_complement_rate"], "strand_loss_fraction": model.loss["dropout"]}


# ----------------------------------------------------------------------------------------------------------- sidecar
def simulate_with_sidecar(model: Model, strands, output, seed: int, *, workers: int = 1, command: str | None = None,
                          extra: dict | None = None) -> dict:
    """Run the composer, write the reads and ``<output>.json``, and return the sidecar dict."""
    strands, output = Path(strands), Path(output)
    t0 = time.time()
    in_sha = sha256_file(strands)
    codes = read_strand_codes(strands)
    report = compose(model, strands, output, seed, workers=workers)
    sidecar = {
        "classification": CLASSIFICATION,
        "statement": STATEMENT,
        "seed": seed,
        "model": {"name": model.name, "version": model.version, "sha256": model.sha256, "file": model.path.name,
                  "parameters": model.parameters()},
        "coverage": {**model.coverage_model(), "realised_reads": report["channel"]["reads"],
                     "realised_reads_per_input_strand": round(report["channel"]["reads"] / max(1, codes.shape[0]), 4)},
        "quality_model": model.quality_model(),
        "input": {"file": strands.name, "sha256": in_sha, "strands": int(codes.shape[0]),
                  "strand_length": int(codes.shape[1]) if codes.size else 0},
        "output": {"file": output.name, "sha256": sha256_file(output), "bytes": output.stat().st_size,
                   "format": report["channel"]["format"], "reads": report["channel"]["reads"]},
        "composition": {"loss_layer": "vnxdna.v6.loss", "read_layer": "vnxdna.v4.channel", "loss": report["loss"],
                        "channel_stats": report["channel"], "channel_config": report["channel_config"]},
        "realised_rates": realised_rates(report, int(codes.shape[1]), int(codes.shape[0])),
        "git": git_info(),
        "decoder": None,
        "decode_result": None,
        "run": {"workers": workers, "seconds": report["seconds"], "command": command,
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)),
                "input_path": str(strands.resolve()), "output_path": str(output.resolve())},
    }
    if extra:
        sidecar.update(extra)
    write_sidecar(output, sidecar)
    return sidecar


def sidecar_path(output) -> Path:
    return Path(str(output) + ".json")


def write_sidecar(output, sidecar: dict) -> None:
    sidecar_path(output).write_text(json.dumps(sidecar, indent=2, sort_keys=True, default=str) + "\n")


REQUIRED_SIDECAR_KEYS = ("classification", "seed", "model", "coverage", "quality_model", "input", "output", "git", "decoder",
                         "decode_result", "realised_rates", "composition", "run")


def deterministic_part(sidecar: dict) -> dict:
    """The sidecar minus run-environment fields (paths, worker count, timings)."""
    d = json.loads(json.dumps(sidecar))
    d.pop("run", None)
    return d


__all__ = ["Model", "load_model", "model_names", "compose", "simulate_with_sidecar", "realised_rates", "expected_rates",
           "sha256_file", "git_info"]
