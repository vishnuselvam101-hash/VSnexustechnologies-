"""Pass-1 engine internal stage counters (ns_* seconds) of single-archive decodes; argv: label=reads.fastq:workers ...
SIMULATED input. Prints one JSON row per decode."""
import json
import sys
import time

from vnxdna.pipeline.decode import decode_reads
from vnxdna.recovery.options import DecodeOptions

out = sys.argv[1]
for spec in sys.argv[2:]:
    label, rest = spec.split("=", 1)
    path, w = rest.rsplit(":", 1)
    t = time.perf_counter()
    r = decode_reads(path, out, DecodeOptions(workers=int(w)), overwrite=True)
    print(json.dumps({"label": "SIMULATED", "workload": label, "workers": int(w), "status": r.status,
                      "wall_s": round(time.perf_counter() - t, 4), "stage_seconds": r.report.get("stage_seconds"),
                      "engine_pass1": r.report["engine"]["pass1"]}))
