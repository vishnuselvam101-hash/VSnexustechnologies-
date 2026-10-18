# Disk-style rates of the streaming codec and the volume at 6117262 — SIMULATED

`stream_volume_bench.py --sizes 4,16 --reps 3` (median of 3 repetitions, 1 MiB extents, random input bytes, every
kernel and the engine native): `stream-volume.json` (every number of docs/V13_BENCHMARK_REPORT.md §4 comes from it),
`run.log`, `operator-notes.txt`. Software codec rates on this host (4C/8T Xeon Gold 6240) with strands as files;
never physical DNA throughput. The read pools are SIMULATED (coverage 3, 1 % substitutions) by the tree under test.

Summary: stream encode 2.6–2.9 MB/s at 1 worker, 7.7–9.2 MB/s at 4; stream decode by range 3.4–3.7 MB/s; volume write
3.1 MB/s, cold sequential read 4.2–4.3 MB/s, hot (cached) read 1.6–1.7 GB/s; 4 KiB random read ~250 ms cold (one
extent decode), ~0.02 ms hot. Pool decode by archive ID is O(extents × pool): 6.1 s for 4 extents, 98 s for 16 at
1 worker (documented limitation; the range path and the volume are linear).
