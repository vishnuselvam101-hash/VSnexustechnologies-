"""Archive-level harness for Phase 3 (EXP-03, EXP-05). SIMULATED.

Builds one archive per (size, seed), encodes it with the default profile, simulates the channel, and decodes the same
read file with V4 ("segment") and V5 ("smart"). Every decode runs in a fresh spawned process so peak memory and
timing are per decode. A result counts as SUCCESS only if the decoder says SUCCESS *and* the extracted file's SHA-256
equals the input's; a SUCCESS with any other output would be a false success (never observed; checked every time).
"""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import tempfile
import time
from pathlib import Path

from vnxdna.v4 import archive as ar
from vnxdna.v4 import channel as ch
from vnxdna.v4 import datagen
from vnxdna.v4 import encoder as en


def build(workdir: Path, size: int, seed: int) -> dict:
    src = workdir / "input.bin"
    sha = datagen.generate(src, size, "random", seed)
    ar.build_archive([src], workdir / "a.vnx", ar.ArchiveOptions(workers=1))
    res = en.encode_container(workdir / "a.vnx", workdir / "s.fasta", en.DNAOptions(workers=1))
    strands_nt = sum(1 for line in open(workdir / "s.fasta") if not line.startswith(">"))
    rep = res if isinstance(res, dict) else getattr(res, "__dict__", {})
    return {"input_sha256": sha, "input_size": size, "strands": strands_nt, "encode": {k: v for k, v in rep.items() if
                                                                                       isinstance(v, (int, float, str))}}


def simulate(workdir: Path, name: str, channel: dict) -> Path:
    out = workdir / f"{name}.fastq"
    if not out.exists():
        ch.simulate_file(workdir / "s.fasta", out, ch.ChannelConfig.from_dict(channel), workers=1)
    return out


def _child(reads: str, workdir: str, mode: str, workers: int, input_sha: str, q) -> None:
    from vnxdna.v4 import decoder as de
    from vnxdna.v4.util import peak_rss_bytes
    out = Path(workdir) / f"out-{mode}-{time.time_ns()}.vnx"
    t = time.perf_counter()
    try:
        res = de.decode_reads(reads, out, de.DecodeOptions(workers=workers, indel_recovery=mode), overwrite=True,
                              partial_dir=None)
        status, report = res.status, res.report
    except Exception as error:                                    # noqa: BLE001 - recorded, never published
        status, report = "FAILURE", {"error": f"{type(error).__name__}: {str(error)[:300]}"}
    secs = time.perf_counter() - t
    out_sha = None
    if status == "SUCCESS":
        with tempfile.TemporaryDirectory() as x:
            ar.extract(out, x)
            out_sha = hashlib.sha256((Path(x) / "input.bin").read_bytes()).hexdigest()
    if out.exists():
        out.unlink()
    keep = ("reads", "groups_decoded", "groups_failed", "indel_recovery", "superblock", "error", "status", "stage_seconds")
    rep = {k: report.get(k) for k in keep if k in report}
    if "superblock" in rep and rep["superblock"]:
        rep["superblock"] = {k: rep["superblock"].get(k) for k in ("container_size", "groups")}
    if rep.get("indel_recovery"):
        rep["indel_recovery"] = {k: v for k, v in rep["indel_recovery"].items() if k != "config"}
    import resource
    child_mb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024
    q.put({"status": status, "seconds": round(secs, 3), "peak_rss_mb": round(peak_rss_bytes() / 2**20, 1),
           "peak_rss_largest_worker_mb": round(child_mb, 1),
           "output_sha256": out_sha, "verified": out_sha == input_sha, "false_success": status == "SUCCESS" and out_sha != input_sha,
           "report": rep})


def decode(reads: Path, workdir: Path, mode: str, workers: int, input_sha: str, timeout: int = 7200) -> dict:
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_child, args=(str(reads), str(workdir), mode, workers, input_sha, q))
    p.start()
    try:
        r = q.get(timeout=timeout)
    finally:
        p.join(timeout=60)
    groups = (r["report"].get("superblock") or {}).get("groups")
    failed = r["report"].get("groups_failed")
    r["groups"] = groups
    r["groups_recovered_fraction"] = None if not groups else round(1 - (failed or 0) / groups, 4)
    return r
