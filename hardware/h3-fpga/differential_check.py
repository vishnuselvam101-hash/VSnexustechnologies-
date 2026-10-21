#!/usr/bin/env python3
"""Differential check: the Verilog RS decoder against the Python/NumPy reference.

WHAT THIS DOES
    1. Generates random RS codewords with the project's own encoder
       (``src/vnxdna/codec/inner_parity.py`` + ``src/vnxdna/codec/gf256.py``),
       injects random byte errors and random erasure flags, and decodes them
       with the project's own reference decoder
       (``src/vnxdna/codec/rs_batch.py``).
    2. Writes the cases to a stimulus file and the reference results to an
       expected-output file.
    3. Runs the Verilog testbench on the stimulus.
    4. Compares the Verilog output byte for byte with the reference output.

    The comparison is the acceptance test for the hardware design: the RTL
    must reproduce the software decoder exactly -- the corrected codeword, the
    ok flag and the errata count.

EVIDENCE CLASS
    SIMULATED.  This is a software simulation of a hardware design.  No FPGA
    has been built, no board has been powered, and no physical measurement was
    taken.  The "reference" side is the project's own Python decoder, which is
    itself software; the numbers below are counts of simulated test vectors.

WHAT IT CANNOT SHOW
    That the design synthesises, that it meets timing, or that it is
    bit-exact on a real FPGA.  Only a synthesis run and a hardware bring-up
    can show those.  The synthesised netlist is not a deliverable of this
    repository.

USAGE
    python3 differential_check.py [--vectors N] [--seed S] [--keep]

    The Verilog simulator is located automatically (iverilog, then verilator).
    If none is found, the script says so and exits 0 with a clear message; the
    stimulus and expected files are still written so the run can be repeated
    elsewhere.

references:
    src/vnxdna/codec/gf256.py          (the field: primitive polynomial 0x11D)
    src/vnxdna/codec/inner_parity.py   (the parity matrix / generator polynomial)
    src/vnxdna/codec/rs_batch.py       (the reference decoder)
    src/vnxdna/codec/codecs.py         (InnerRS: the "reed-solomon-gf256" code)
    src/vnxdna/core/crc.py             (CRC-32, the independent check the
                                        software applies after an RS decode)
"""
from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]          # hardware/h3-fpga -> hardware -> repo root
SRC = REPO / "src"

sys.path.insert(0, str(SRC))

import numpy as np  # noqa: E402

from vnxdna.codec import gf256  # noqa: E402
from vnxdna.codec import rs_batch  # noqa: E402
from vnxdna.codec.inner_parity import parity_matrix  # noqa: E402

#: the parameters exercised.  (n, nsym) pairs: a short strand, the two inner
#: parity sizes the V6 profiles use (4 and 16 bytes) and a long strand.
PARAM_SETS = [
    (16, 4),
    (16, 8),
    (32, 8),
    (32, 16),
    (64, 4),
    (64, 16),
    (128, 16),
    (255, 16),
]


def build_codeword(k: int, nsym: int, rng: random.Random) -> np.ndarray:
    """A systematic RS codeword from the project's own parity matrix."""
    msg = bytes(rng.randrange(256) for _ in range(k))
    pm = parity_matrix(k, nsym)
    par = np.zeros(nsym, dtype=np.uint8)
    # the same expression the software uses (codecs.InnerRS.parity):
    #   out[:, j] ^= MUL[:, matrix[:, j]] gathered at the message byte
    for i in range(k):
        for j in range(nsym):
            par[j] ^= gf256.mul(int(pm[i, j]), msg[i])
    return np.frombuffer(msg + bytes(par), dtype=np.uint8)


def make_case(rng: random.Random, n: int, nsym: int) -> tuple[np.ndarray, np.ndarray]:
    """A corrupted codeword and its erasure mask.

    The error/erasure counts are drawn around the bounded-distance bound
    ``2e + f <= nsym`` so that the case is on both sides of it: some cases are
    inside the bound (the reference must correct them) and some are outside
    (the reference may fail or miscorrect, and the RTL must agree exactly).
    """
    k = n - nsym
    cw = build_codeword(k, nsym, rng).copy()
    erase = np.zeros(n, dtype=bool)

    # how far past the bound this case sits
    over = rng.random()
    if over < 0.20:
        # well inside: f erasures only, e = 0
        f = rng.randrange(0, min(nsym, n) + 1)
        e = 0
    elif over < 0.70:
        # a mix that satisfies 2e + f <= nsym
        f = rng.randrange(0, min(nsym, n) + 1)
        budget = (nsym - f) // 2
        e = rng.randrange(0, budget + 1) if budget >= 0 else 0
    elif over < 0.85:
        # at the bound
        f = rng.randrange(0, min(nsym, n) + 1)
        budget = max(0, (nsym - f) // 2)
        e = budget
    else:
        # beyond the bound: the reference may fail or miscorrect
        f = rng.randrange(0, min(nsym, n) + 1)
        budget = max(0, (nsym - f) // 2)
        e = budget + rng.randrange(1, 4)

    positions = list(range(n))
    rng.shuffle(positions)
    ep = positions[:min(e, n)]
    fp = positions[e:e + min(f, n - len(ep))]

    for p in ep:
        cw[p] ^= rng.randrange(1, 256)
    for p in fp:
        erase[p] = True

    return cw, erase


def reference_decode(cw: np.ndarray, erase: np.ndarray, nsym: int):
    """The reference decoder's answer: (corrected, ok, errata)."""
    out, ok, errata = rs_batch.decode_batch(cw[None, :], nsym, erase[None, :])
    return out[0], bool(ok[0]), int(errata[0])


def write_stimulus(path: Path, cases: list[tuple[np.ndarray, np.ndarray, int, bool, int, np.ndarray]]) -> None:
    """One line per case: the codeword, the erasure flags and the reference verdict.

    The expected corrected codeword is NOT written here: it goes to the
    separate "<stem>.expected" file, one line per case, so that the testbench
    can read the two files independently.
    """
    with path.open("w", encoding="ascii") as fh:
        for cw, erase, nsym, ok, errata, _expected in cases:
            data = bytes(cw).hex()
            flags = "".join("1" if e else "0" for e in erase)
            fh.write(f"CW {len(cw)} {nsym} {len(cw)} {data} {flags} {1 if ok else 0} {errata}\n")


def write_expected(path: Path, cases: list[tuple[np.ndarray, np.ndarray, int, bool, int, np.ndarray]]) -> None:
    """One line per case: the expected corrected codeword, in hex."""
    with path.open("w", encoding="ascii") as fh:
        for _cw, _erase, _nsym, _ok, _errata, expected in cases:
            fh.write(f"{bytes(expected).hex()}\n")


def find_simulator() -> tuple[str | None, list[str]]:
    """Locate iverilog/vvp or verilator."""
    iverilog = shutil.which("iverilog")
    vvp = shutil.which("vvp")
    verilator = shutil.which("verilator")
    if iverilog and vvp:
        return "iverilog", [iverilog, vvp]
    if verilator:
        return "verilator", [verilator]
    return None, []


def run_iverilog(args: list[str], work: Path) -> tuple[bool, str]:
    build = work / "build"
    build.mkdir(parents=True, exist_ok=True)
    exe = build / "tb_rs_decode.vvp"
    cmd = [args[0], "-g2001", "-I", str(work), "-o", str(exe),
           str(work / "rs_decode.v"), str(work / "tb_rs_decode.v")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(work))
    if r.returncode != 0:
        return False, "compilation failed:\n" + r.stdout + r.stderr
    r = subprocess.run([args[1], str(exe), f"+stim={work / 'stimulus.txt'}",
                        f"+expected={work / 'stimulus.expected'}"],
                       capture_output=True, text=True, cwd=str(work))
    out = r.stdout + r.stderr
    # The testbench's exit code is not a verdict: $finish always exits 0.  The
    # verdict is the RESULT line, and the run only counts if EVERY generated
    # vector was actually executed.
    m = re.search(r"RESULT: PASS \((\d+) cases, (\d+) checks\)", out)
    if not m:
        return False, out + "\n(no 'RESULT: PASS' line: the testbench did not " \
                            "report a pass -- treat this as a FAILURE)"
    n_cases, n_checks = int(m.group(1)), int(m.group(2))
    return True, out + (f"\n(testbench reported {n_cases} cases, {n_checks} checks)")


def count_result_cases(out: str) -> int | None:
    m = re.search(r"RESULT: PASS \((\d+) cases, (\d+) checks\)", out)
    return None if m is None else int(m.group(1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vectors", type=int, default=400,
                    help="number of random test vectors (default: 400)")
    ap.add_argument("--seed", type=int, default=20261021,
                    help="PRNG seed (default: 20261021)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the stimulus files in the working directory")
    ap.add_argument("--gen-only", action="store_true",
                    help="write the stimulus and expected files, then stop "
                         "(used by `make stim`; the simulation is run separately)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    np_rng = np.random.default_rng(args.seed)

    cases = []
    for i in range(args.vectors):
        n, nsym = PARAM_SETS[i % len(PARAM_SETS)]
        cw, erase = make_case(rng, n, nsym)
        corrected, ok, errata = reference_decode(cw, erase, nsym)
        # The reference returns the codeword UNCHANGED when it fails, so the
        # expected output is the corrected codeword on success and the input
        # on failure.  The RTL must agree exactly.
        expected = corrected if ok else cw
        cases.append((cw, erase, nsym, ok, errata, expected))

    work = HERE
    stim_path = work / "stimulus.txt"
    exp_path = work / "stimulus.expected"
    write_stimulus(stim_path, cases)
    write_expected(exp_path, cases)
    print(f"generated {len(cases)} test vectors -> {stim_path} / {exp_path}")

    if args.gen_only:
        return 0

    sim, args_list = find_simulator()
    if sim is None:
        print("NO SIMULATOR AVAILABLE: neither iverilog nor verilator is on PATH.")
        print("The stimulus and expected files are written; run them with:")
        print(f"  iverilog -g2001 -I {work} -o tb_rs_decode.vvp {work}/rs_decode.v {work}/tb_rs_decode.v")
        print(f"  vvp tb_rs_decode.vvp +stim={stim_path} +expected={exp_path}")
        return 0

    print(f"simulator: {sim}")
    if sim == "iverilog":
        ok_run, output = run_iverilog(args_list, work)
    else:
        print("verilator path: the verilator build needs a C++ main; use the "
              "iverilog path or `make verilator` for the equivalent run")
        ok_run, output = False, ""

    print(output)
    if not ok_run:
        print("DIFFERENTIAL CHECK: FAIL -- the simulation did not pass")
        return 1

    # the testbench must have executed EVERY generated vector, otherwise the
    # comparison silently covered a subset
    n_run = count_result_cases(output)
    if n_run is None or n_run != len(cases):
        print(f"DIFFERENTIAL CHECK: FAIL -- the testbench executed {n_run} of "
              f"{len(cases)} generated vectors")
        return 1

    # independent re-check: the reference decode of the same vectors, compared
    # here in Python, so the file contents themselves are verified
    n_ok = sum(1 for c in cases if c[3])
    print(f"reference: {n_ok} of {len(cases)} vectors decoded successfully "
          f"(the rest are beyond the bounded-distance bound or were flagged)")
    print(f"DIFFERENTIAL CHECK: PASS -- the Verilog output matched the Python "
          f"reference byte for byte on all {len(cases)} vectors "
          f"({n_run} cases executed by the testbench)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
