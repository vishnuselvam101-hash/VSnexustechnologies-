"""Bounded memory of the native read parser on a large synthetic FASTQ (generated on the fly into tmp).

The parse runs in a fresh subprocess so ru_maxrss measures only this parse: peak RSS growth over the
post-import baseline must stay bounded (block buffer + one batch of arrays), independent of the file size.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from .native_reads_support import ensure_native

FILE_MB = 200
GROWTH_LIMIT_MB = 64


@pytest.fixture(scope="module", autouse=True)
def _native(tmp_path_factory):
    ensure_native(tmp_path_factory)


def _write_fastq(path: Path, target_bytes: int) -> int:
    rng = np.random.default_rng(11)
    n_tpl = 4096
    lens = rng.integers(100, 300, n_tpl)
    recs = []
    for i, n in enumerate(lens):
        seq = np.frombuffer(b"ACGTN", np.uint8)[rng.integers(0, 5, n)].tobytes()
        qual = rng.integers(33, 75, n).astype(np.uint8).tobytes()
        recs.append(b"@synthetic_read_%d length=%d\n%s\n+\n%s\n" % (i, n, seq, qual))
    block = b"".join(recs)
    reps = target_bytes // len(block) + 1
    with open(path, "wb") as f:
        for _ in range(reps):
            f.write(block)
    return reps * n_tpl


SCRIPT = r"""
import json, os, resource, sys
from vnxdna.v6 import native_reads as nr
assert nr.available(), nr.status()
page = os.sysconf("SC_PAGE_SIZE")
def rss():
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * page
base, base_max = rss(), resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
reads = nt = 0
high = base
for b in nr.iter_reads_native(sys.argv[1], 8192):
    reads += b.count
    nt += int(b.lengths.sum())
    high = max(high, rss())
peak_max = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({"reads": reads, "nt": nt, "rss_base": base, "rss_high": high,
                  "maxrss_base_kb": base_max, "maxrss_peak_kb": peak_max}))
"""


@pytest.mark.slow
def test_bounded_rss_on_200mb_fastq(tmp_path):
    path = tmp_path / "large.fastq"
    expected = _write_fastq(path, FILE_MB << 20)
    size_mb = path.stat().st_size / 2**20
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parents[3] / "src"), env.get("PYTHONPATH", "")])
    out = subprocess.run([sys.executable, "-c", SCRIPT, str(path)], capture_output=True, text=True, env=env, check=True)
    res = json.loads(out.stdout.strip().splitlines()[-1])
    growth_mb = (res["rss_high"] - res["rss_base"]) / 2**20          # sampled at every batch
    peak_growth_mb = (res["maxrss_peak_kb"] - res["maxrss_base_kb"]) / 1024
    assert res["reads"] == expected
    assert size_mb >= FILE_MB
    assert growth_mb > 0          # the sampler sees the parser's own buffers
    assert growth_mb < GROWTH_LIMIT_MB, f"RSS grew {growth_mb:.1f} MiB while parsing {size_mb:.0f} MiB"
    assert peak_growth_mb < GROWTH_LIMIT_MB, f"peak RSS grew {peak_growth_mb:.1f} MiB"
