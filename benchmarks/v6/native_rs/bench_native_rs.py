"""V6 Phase 1 items 6+7: micro-benchmark of the inner RS decoder, reference vs every native dispatch level. SIMULATED.

Backends: reference (vnxdna.v4.rs_fast, NumPy), scalar, avx2, avx512 (only the levels this CPU/OS supports).

Sections (``python benchmarks/v6/native_rs/bench_native_rs.py [--sections micro,e2e]``):
  micro  workloads below, timed per backend. Before any timing, each workload's results from every backend are
         compared with the reference (all three arrays); a mismatch aborts. Repetitions are interleaved across
         backends (rep 1 of every backend, then rep 2, ...) so drifting background load hits all of them alike.
         Statistic: median of REPS; min, max and every repetition are recorded. Nothing is best-of.
  e2e    the profile's whole decode (4 MiB, v4-balanced, EXP-0011 channel, 1 worker) with InnerRS.decode patched to
         the native decoder *inside this benchmark only* vs the unpatched decoder; the decoded container SHA-256 must
         match. Median of 3 each, interleaved.

Workloads (micro):
  captured   every inner-RS call of the profiled decode (profile_decode.py --capture), replayed call by call
  (70,16) within / mixed / beyond / garbage: 8192 synthetic words each (seeded), the v4-balanced code
  (255,32) mixed: 4096 words, a long code with wide vectors

SIMD keep rule (applied to the captured workload, the production mix): a level is kept when its median time is at
least 10 % lower than the level below it AND its slowest repetition is faster than the fastest repetition of the level
below (non-overlapping ranges). The JSON records the decision and the ratios.

usage: python benchmarks/v6/native_rs/bench_native_rs.py [--workload results/rs_workload.npz] [--reps 7]
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")   # keep idle BLAS threads out of the timings

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import platform  # noqa: E402
import statistics  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parents[1] / "v5"))
sys.path.insert(0, str(HERE))
from provenance import provenance  # noqa: E402

import stress_fuzz  # noqa: E402
from vnxdna.v4 import rs_fast  # noqa: E402
from vnxdna.v6 import native_rs as nr  # noqa: E402

KEEP_MARGIN = 1.10


def cpu_info() -> dict:
    flags: set = set()
    model = None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("flags") and not flags:
                flags = set(line.split(":", 1)[1].split())
            if line.startswith("model name") and model is None:
                model = line.split(":", 1)[1].strip()
    except OSError:
        pass
    keep = {"sse2", "ssse3", "sse4_2", "avx", "avx2", "avx512f", "avx512bw", "avx512vl", "avx512dq", "avx512_vbmi",
            "gfni", "vpclmulqdq", "bmi2", "popcnt"}
    return {"model": model, "relevant_flags": sorted(flags & keep), "gfni": "gfni" in flags,
            "logical_cpus": os.cpu_count()}


def compiler_info() -> dict:
    cmd = nr.build_command()
    try:
        ver = subprocess.run([cmd[0], "--version"], capture_output=True, text=True, timeout=20).stdout.splitlines()[0]
    except (OSError, IndexError, subprocess.TimeoutExpired):
        ver = None
    lib = nr.status()["library"]
    sha = hashlib.sha256(Path(lib).read_bytes()).hexdigest() if lib else None
    src = hashlib.sha256((REPO / "src/vnxdna/v6/native/rs.c").read_bytes()).hexdigest()
    return {"compiler": cmd[0], "version": ver, "cflags": nr.CFLAGS, "library": lib, "library_sha256": sha,
            "source_sha256": src}


def loadavg() -> list[float] | None:
    try:
        return [float(x) for x in Path("/proc/loadavg").read_text().split()[:3]]
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------------- workloads
def synthetic(name: str, n: int, nsym: int, words: int, seed: int) -> tuple[str, list]:
    rng = np.random.default_rng(seed)
    cw = stress_fuzz.valid_codewords(rng, words, n, nsym)
    er = np.zeros(cw.shape, dtype=bool)
    t = nsym // 2
    for i in range(words):
        pos = rng.permutation(n)
        if name == "within":
            e, f = int(rng.integers(1, t + 1)), 0
        elif name == "mixed":
            f = int(rng.integers(0, nsym + 1))
            e = int(rng.integers(0, (nsym - f) // 2 + 1))
        elif name == "beyond":
            f = int(rng.integers(0, nsym + 1))
            e = (nsym - f) // 2 + int(rng.integers(1, 4))
        else:  # garbage
            cw[i] = rng.integers(0, 256, n, dtype=np.uint8)
            e, f = 0, int(rng.integers(0, nsym + 1))
        cw[i, pos[:e]] ^= rng.integers(1, 256, e, dtype=np.uint8)
        er[i, pos[e:e + f]] = True
        cw[i, pos[e:e + f]] = rng.integers(0, 256, f, dtype=np.uint8)
    return f"({n},{nsym}) {name}", [(cw, nsym, er if er.any() else None)]


def captured(path: Path) -> tuple[str, list, str]:
    d = np.load(path)
    calls = []
    h = hashlib.sha256()
    i = 0
    while f"cw{i}" in d.files:
        cw, ns = d[f"cw{i}"], int(d[f"nsym{i}"])
        er = d[f"er{i}"] if f"er{i}" in d.files else None
        h.update(cw.tobytes())
        h.update(bytes([ns]))
        if er is not None:
            h.update(er.tobytes())
        calls.append((cw, ns, er))
        i += 1
    return "captured decode workload", calls, h.hexdigest()


def run_backend(calls: list, backend: str):
    if backend == "reference":
        return [rs_fast.decode_batch(cw, ns, er) for cw, ns, er in calls]
    return [nr.decode_batch(cw, ns, er, backend=backend) for cw, ns, er in calls]


def check_equal(calls: list, backends: list[str]) -> None:
    ref = run_backend(calls, "reference")
    for b in backends:
        if b == "reference":
            continue
        got = run_backend(calls, b)
        for r, g in zip(ref, got):
            for x, y in zip(r, g):
                if not (x.dtype == y.dtype and np.array_equal(x, y)):
                    raise SystemExit(f"MISMATCH backend {b}")


def stats(v: list[float]) -> dict:
    return {"median": round(statistics.median(v), 6), "min": round(min(v), 6), "max": round(max(v), 6),
            "reps": [round(x, 6) for x in v]}


def time_workload(calls: list, backends: list[str], reps: int) -> dict:
    words = sum(c[0].shape[0] for c in calls)
    wall = {b: [] for b in backends}
    cpu = {b: [] for b in backends}
    for b in backends:          # warm-up (tables, caches) — not recorded
        run_backend(calls, b)
    for _ in range(reps):
        for b in backends:
            t0, c0 = time.perf_counter(), time.process_time()
            run_backend(calls, b)
            wall[b].append(time.perf_counter() - t0)
            cpu[b].append(time.process_time() - c0)
    out = {"calls": len(calls), "words": words, "backends": {}}
    for b in backends:
        w = stats(wall[b])
        out["backends"][b] = {"wall_s": w, "cpu_s": stats(cpu[b]), "words_per_s": round(words / w["median"]),
                              "speedup_vs_reference": round(statistics.median(wall["reference"]) / w["median"], 2)}
    return out


def keep_decision(row: dict) -> dict:
    b = row["backends"]
    order = [lv for lv in ("scalar", "avx2", "avx512") if lv in b]
    out = {}
    for lower, upper in zip(order, order[1:]):
        lo, up = b[lower]["wall_s"], b[upper]["wall_s"]
        ratio = lo["median"] / up["median"]
        separated = up["max"] < lo["min"]
        out[f"{upper}_vs_{lower}"] = {"median_ratio": round(ratio, 3), "ranges_separated": separated,
                                      "measurably_faster": ratio >= KEEP_MARGIN and separated}
    if "scalar" in b:
        for lv in order[1:]:
            lo, up = b["scalar"]["wall_s"], b[lv]["wall_s"]
            ratio = lo["median"] / up["median"]
            out[f"{lv}_vs_scalar"] = {"median_ratio": round(ratio, 3), "ranges_separated": up["max"] < lo["min"],
                                      "measurably_faster": ratio >= KEEP_MARGIN and up["max"] < lo["min"]}
    return out


# ---------------------------------------------------------------------------------------------------- end to end
def section_e2e(work: Path, reps: int) -> dict:
    import profile_decode
    from vnxdna.v4 import codecs
    from vnxdna.v4 import decoder as de
    reads = profile_decode.prepare(work)
    orig = codecs.InnerRS.decode

    def native_decode(self, codewords, erasures=None):
        return nr.decode_batch(codewords, self.r, erasures)

    rows: dict = {"reference (rs_fast)": [], f"native ({nr.active_backend()})": []}
    shas: dict = {}
    for k in range(reps):
        for label in rows:
            codecs.InnerRS.decode = native_decode if label.startswith("native") else orig
            try:
                t = time.perf_counter()
                res = de.decode_reads(reads, work / f"e2e-{k}.vnx", de.DecodeOptions(workers=1), overwrite=True)
                rows[label].append(time.perf_counter() - t)
            finally:
                codecs.InnerRS.decode = orig
            assert res.status == "SUCCESS"
            shas.setdefault(label, set()).add(res.report.get("container_sha256")
                                              or hashlib.sha256((work / f"e2e-{k}.vnx").read_bytes()).hexdigest())
    same = len({s for v in shas.values() for s in v}) == 1
    if not same:
        raise SystemExit("decoded containers differ between RS backends")
    out = {label: stats(v) for label, v in rows.items()}
    ref, nat = (statistics.median(v) for v in rows.values())
    return {"case": profile_decode.NOISY_CHANNEL | {"input_size": profile_decode.SIZE, "profile": "v4-balanced", "workers": 1},
            "wiring": "codecs.InnerRS.decode patched inside the benchmark process only",
            "container_identical": same, "wall_s": out, "speedup": round(ref / nat, 3), "seconds_saved": round(ref - nat, 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workload", default="/root/vnx-dna-lab/results/native_rs/rs_workload.npz")
    ap.add_argument("--workdir", default="/root/vnx-dna-lab/results/native_rs/work")
    ap.add_argument("--reps", type=int, default=7)
    ap.add_argument("--sections", default="micro,e2e")
    ap.add_argument("--out", default=str(HERE / "results" / "bench.json"))
    args = ap.parse_args()
    if not nr.available():
        raise SystemExit(f"native library unavailable: {nr.status()}")
    backends = ["reference", *nr.supported_levels()]
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    wl = Path(args.workload)
    if not wl.exists():
        import profile_decode
        profile_decode.prepare(work)
        subprocess.run([sys.executable, str(HERE / "profile_decode.py"), "--workdir", str(work), "--capture", str(wl),
                        "--out", str(Path(tempfile.mkdtemp()) / "profile.txt")], check=True)
    payload: dict = {"simulated": True, "backends": backends, "reps": args.reps, "cpu": cpu_info(), "build": compiler_info(),
                     "native_status": nr.status(), "python": platform.python_version(), "numpy": np.__version__,
                     "loadavg_before": loadavg(), "keep_rule": f"median ratio >= {KEEP_MARGIN} and non-overlapping rep ranges"}
    cfg = {"backends": backends, "reps": args.reps, "sections": args.sections}
    payload["provenance"] = provenance(cfg)
    if "micro" in args.sections:
        name, calls, wsha = captured(wl)
        workloads = [(name, calls, wsha)]
        for nm, n, nsym, words, seed in [("within", 70, 16, 8192, 1), ("mixed", 70, 16, 8192, 2), ("beyond", 70, 16, 8192, 3),
                                         ("garbage", 70, 16, 8192, 4), ("mixed", 255, 32, 4096, 5)]:
            label, c = synthetic(nm, n, nsym, words, seed)
            workloads.append((label, c, None))
        rows = []
        for label, calls, wsha in workloads:
            check_equal(calls, backends)
            row = {"workload": label, "workload_sha256": wsha, "outputs_identical_to_reference": True,
                   **time_workload(calls, backends, args.reps)}
            row["simd_keep"] = keep_decision(row)
            rows.append(row)
            print(f"{label:28s} " + "  ".join(f"{b} {row['backends'][b]['wall_s']['median'] * 1e3:9.2f} ms "
                                              f"(x{row['backends'][b]['speedup_vs_reference']})" for b in backends))
        payload["micro"] = rows
        payload["decision"] = rows[0]["simd_keep"]
    if "e2e" in args.sections:
        payload["e2e"] = section_e2e(work, 3)
        print("e2e", json.dumps(payload["e2e"]["wall_s"]), "speedup", payload["e2e"]["speedup"])
    payload["loadavg_after"] = loadavg()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=1, default=list) + "\n")
    print("decision:", json.dumps(payload.get("decision")))
    print("saved", args.out)


if __name__ == "__main__":
    main()
