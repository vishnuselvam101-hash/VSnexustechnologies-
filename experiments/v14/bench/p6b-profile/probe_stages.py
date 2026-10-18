"""Layout-probe sub-step timings (sample, frame heads, whole probe incl. candidate scoring); argv: label=reads ...
3 repetitions each. SIMULATED input. Prints one JSON row per repetition."""
import json
import sys
import time

from vnxdna.recovery import probe as P

for spec in sys.argv[1:]:
    label, f = spec.split("=", 1)
    for rep in range(3):
        t = time.perf_counter()
        c, s = P.sample_reads(f)
        t1 = time.perf_counter()
        P.frame_heads(s)
        t2 = time.perf_counter()
        p = P.probe_reads(f)
        t3 = time.perf_counter()
        P.choose_layout(p)
        t4 = time.perf_counter()
        print(json.dumps({"label": "SIMULATED", "workload": label, "rep": rep, "sample_s": round(t1 - t, 4),
                          "frame_heads_s": round(t2 - t1, 4), "probe_reads_total_s": round(t3 - t2, 4),
                          "choose_layout_s": round(t4 - t3, 6), "sample_reads": len(s), "scores": p.scores}))
