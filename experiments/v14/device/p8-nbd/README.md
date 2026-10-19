# P8 block-device acceptance transcript and throughput

`run.py` attaches `/dev/nbdN` devices with `vnxd nbd attach` over disposable volumes. Each device is checked against the pid of the `vnxd` that this run started before every tool call (`tests/v14/nbd_support.py`).

What the run does:
- writes the acceptance transcript;
- measures raw-device O_DIRECT throughput for each cache mode.

It needs root, the `nbd` module (`modprobe nbd max_part=8`) and a release build of `vnxd`. Run it with:

    python experiments/v14/device/p8-nbd/run.py --work <scratch dir> --out experiments/v14/device/p8-nbd/results.json

About `results.json`:
- It was produced at commit 86c16c0, with a clean tree, on an idle 8-vCPU host.
- `provenance` holds the kernel and the nbd module parameters.
- `acceptance` holds the per-file SHA-256 values, the exit codes and the timings.
- `throughput_rows` holds 3 repetitions for each cache mode, and `throughput_median` the medians.
- `post_run_*` shows nothing attached or mounted after the run.

The evidence category is B (a software block device) over C (an electronic file-image backend on the host's disk). No DNA is involved.

An earlier development run, on the same code minus the review fixes and with the host heavily loaded by other jobs, measured about 3x lower throughput. It is not committed and not quoted.
