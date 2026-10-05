"""Spill buckets between pass 1 and pass 2: fixed-width records in bucket files (bucket = group mod B), so
memory stays bounded by one bucket. Formerly in ``vnxdna.v4.decoder`` (V6 Phase 2, M4)."""
from __future__ import annotations

from pathlib import Path

import numpy as np


# ============================================================================ spill
class Spill:
    """Fixed-width records in bucket files (bucket = group mod B); memory stays bounded by one bucket."""

    def __init__(self, workdir: Path, buckets: int, payload: int, frame_nt: int, raw_nt: int = 0):
        self.dir = workdir
        self.B = buckets
        self.acc_dtype = np.dtype([("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("payload", "u1", (payload,))])
        pend = [("kind", "u1"), ("tag", ">u2"), ("group", ">u4"), ("symbol", ">u2"), ("alt", ">i8", (4,)), ("bases", "u1", (frame_nt,))]
        if raw_nt:     # V5 smart indel recovery keeps the raw read for consensus realignment
            pend += [("raw", "u1", (raw_nt,)), ("rawq", "u1", (raw_nt,)), ("rawlen", ">u2"), ("hasq", "u1")]
        self.pend_dtype = np.dtype(pend)
        self.acc_files = [open(workdir / f"acc{b}.bin", "wb") for b in range(buckets)]
        self.pend_files = [open(workdir / f"pend{b}.bin", "wb") for b in range(buckets)]
        self.orph_file = open(workdir / "orph.bin", "wb") if raw_nt else None

    def write(self, res: dict) -> None:
        if self.orph_file is not None and "orph_raw" in res and len(res["orph_raw"]):
            rec = np.zeros(len(res["orph_raw"]), dtype=self.pend_dtype)     # address fields stay 0: unknown
            rec["raw"], rec["rawq"], rec["rawlen"], rec["hasq"] = (res["orph_raw"], res["orph_rawq"], res["orph_rawlen"],
                                                                   res["orph_hasq"])
            self.orph_file.write(rec.tobytes())
        for fields, data, files, dtype, name in ((res["acc_fields"], res["acc_payload"], self.acc_files, self.acc_dtype, "payload"),
                                                  (res["pend_fields"], res["pend_bases"], self.pend_files, self.pend_dtype, "bases")):
            if not len(fields):
                continue
            rec = np.zeros(len(fields), dtype=dtype)
            rec["kind"], rec["tag"], rec["group"], rec["symbol"] = fields[:, 0], fields[:, 1], fields[:, 2], fields[:, 3]
            rec[name] = data
            if name == "bases":
                rec["alt"] = res["pend_alt"]
                if "raw" in dtype.names:
                    rec["raw"], rec["rawq"], rec["rawlen"], rec["hasq"] = (res["pend_raw"], res["pend_rawq"], res["pend_rawlen"],
                                                                           res["pend_hasq"])
            b = fields[:, 2] % self.B
            for k in np.unique(b):
                files[int(k)].write(rec[b == k].tobytes())

    def close(self) -> None:
        for f in self.acc_files + self.pend_files + ([self.orph_file] if self.orph_file is not None else []):
            f.close()

    def load(self, b: int) -> tuple[np.ndarray, np.ndarray]:
        return (np.fromfile(self.dir / f"acc{b}.bin", dtype=self.acc_dtype), np.fromfile(self.dir / f"pend{b}.bin", dtype=self.pend_dtype))

    def load_orphans(self) -> np.ndarray:
        """Read-only memory map of the unaddressed reads (pages are read on demand, so memory stays bounded)."""
        path = self.dir / "orph.bin"
        if not path.exists() or path.stat().st_size < self.pend_dtype.itemsize:
            return np.zeros(0, dtype=self.pend_dtype)
        return np.memmap(path, dtype=self.pend_dtype, mode="r")

    # after close(): the deferred recovery stage adds verified frames and removes the pending records they came from
    def append_acc(self, fields: np.ndarray, payload: np.ndarray) -> None:
        """Verified frames go to the bucket of their *verified* group (never the read's tentative one)."""
        if not len(fields):
            return
        rec = np.zeros(len(fields), dtype=self.acc_dtype)
        rec["kind"], rec["tag"], rec["group"], rec["symbol"] = fields[:, 0], fields[:, 1], fields[:, 2], fields[:, 3]
        rec["payload"] = payload
        b = fields[:, 2] % self.B
        for k in np.unique(b):
            with open(self.dir / f"acc{int(k)}.bin", "ab") as f:
                f.write(rec[b == k].tobytes())

    def rewrite_pend(self, b: int, keep: np.ndarray) -> None:
        pend = np.fromfile(self.dir / f"pend{b}.bin", dtype=self.pend_dtype)
        pend[keep].tofile(self.dir / f"pend{b}.bin")


# ============================================================================ main entry
READS_PER_BUCKET = 200_000


def _bucket_count(est_reads: int) -> int:
    """About 200 000 reads per spill bucket, so pass-2 memory is bounded by one bucket. V4/V5 capped this at 256
    buckets (unbounded bucket size beyond ~51 M reads); the cap now follows the open-file limit (two files per bucket
    stay open in pass 1), up to 4096. Below 51 M reads the count is unchanged."""
    want = max(1, est_reads // READS_PER_BUCKET)
    try:
        import resource
        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        cap = 4096 if soft == resource.RLIM_INFINITY else max(256, min(4096, (soft - 128) // 2))
    except (ImportError, OSError, ValueError):
        cap = 256
    return int(min(cap, want))
