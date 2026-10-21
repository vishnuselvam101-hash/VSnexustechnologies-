# H3 codec accelerator -- GF(256) Reed-Solomon decoder

**STATUS: DESIGN, SIMULATED. NOT SYNTHESISED, NOT TIMED, NOT BUILT, NOT RUN ON HARDWARE.**

No FPGA has been built. No bitstream has been generated. No FPGA has been
powered or run. Nothing in this directory has been measured on hardware, and
no physical measurement of any kind was taken. The decoder here is a Verilog
*design* whose behaviour was checked by *simulation* against the project's own
Python decoder.

**Evidence rung: SIMULATED-VERIFIED for the simulation; DESIGN ONLY for the
hardware.** The two are different claims and this document keeps them apart:
the decoder reproduces the software reference exactly *in simulation*, and it
is a *design* that has never been through a synthesis flow to a placed,
routed, timed netlist.

## 1. What is here

| file | what it is |
|---|---|
| `rs_decode.v` | the decoder: a GF(2^8) errors-and-erasures Reed-Solomon decoder, one algorithm phase per clock cycle |
| `tb_rs_decode.v` | the testbench: it reads a stimulus file, drives the decoder, and compares the result with an expected-output file |
| `differential_check.py` | the acceptance test: generates the vectors with the project's own encoder, decodes them with the project's own reference decoder, runs the testbench and compares byte for byte |
| `gen_gf256_rom.py` | generates `gf256_rom.vh`, the GF(2^8) log/antilog tables, from the primitive polynomial |
| `gf256_rom.vh` | the generated tables (do not edit by hand; run `make rom`) |
| `Makefile` | `make sim`, `make verilator`, `make lint`, `make differential` |

The decoder is a phase-by-phase transcription of `src/vnxdna/codec/rs_batch.py`
and `src/vnxdna/codec/rs_fast.py`, with the same field (GF(2^8), primitive
polynomial 0x11D, generator 2, first consecutive root alpha^0), the same
polynomial conventions and the same acceptance test. The header of
`rs_decode.v` states the conventions in full.

## 2. What was verified, and by what

The acceptance test is `differential_check.py`. It builds codewords with the
project's own encoder (`src/vnxdna/codec/inner_parity.py`), injects byte errors
and erasure flags, decodes them with the project's own reference decoder
(`src/vnxdna/codec/rs_batch.py`), and then requires the Verilog to produce the
**same corrected codeword, the same ok flag and the same errata count, byte for
byte**. The cases sit on both sides of the bounded-distance bound
`2e + f <= nsym`: some are inside it and some are outside it, where the
reference may fail or miscorrect and the RTL must agree about that too.

The result on this machine (Icarus Verilog and Verilator, independently):

| check | outcome |
|---|---|
| differential check, 400 vectors, Icarus Verilog | 400/400 cases, 400 checks, 0 errors |
| differential check, 400 vectors, Verilator | 400/400 cases, 400 checks, 0 errors |
| `make lint` (iverilog elaborate, verilator `-Wall` on RTL and testbench) | clean, 0 warnings |

Both simulators agree with the Python reference on every vector, so the two
independent implementations of the same algorithm cross-check each other.

## 3. What was NOT verified

- **No synthesis.** No synthesis tool has been run over this design to a
  netlist. There is no placed-and-routed design, no gate-level netlist, no
  timing report and no area estimate.
- **No timing closure.** No clock period is claimed for this design on any
  device. The cycle counts in `rs_decode.v` are counts of the phases in *this*
  implementation, not measured performance.
- **No FPGA image, no bring-up.** Nothing was built, programmed, powered or
  run. No board was involved at any point.
- **No physical measurement of any kind.** Every statement about the hardware
  side of this design is a statement about the source code.
- **The synthesizability of the source has not been established.** The source
  is written to Verilog-2001 with no vendor primitives and no SystemVerilog,
  but "written in a synthesisable style" is not the same claim as "elaborates
  and maps", and only the second one is evidence.

## 4. Yosys: a known limitation of this design

**Yosys 0.52 does not elaborate `rs_decode.v` in a useful budget, and this is a
known limitation of the design as it stands.** The fact is recorded here
because the V15 finalization plan names Yosys in the H3 acceptance wording
("Verilog + Verilator/Yosys simulation of the GF(256) RS decoder, differential
against `vnxdna`"). The Verilator and Icarus Verilog halves of that wording are
met and the Yosys half is not.

What was observed, and what was not:

- `read_verilog` on `rs_decode.v` **does not return** within a useful budget
  under Yosys 0.52. An earlier run was stopped at 657 s of wall time and
  20.6 GB of resident memory, because that is not a useful budget for a
  single-file elaboration and it was starving the rest of the machine.
- The cause is understood well enough to be worth writing down, and it is a
  property of *this* design, not a bug in Yosys: the decoder calls the GF(256)
  ROM from inside loops that unroll to tens of thousands of call sites, and a
  tool that inlines a function body at every call site has to copy the whole
  table into each one. The elaborated design grows to millions of nodes.
- Two changes were tried against a copy of the design and are recorded in this
  paragraph only so that the next engineer does not repeat them: holding the
  log/antilog tables in a memory instead of in a `case` statement removes the
  unbounded growth but leaves a long elaboration; replacing the function wrapper
  with a direct memory read cuts a 16 320-site unrolled loop from 187 s to
  2.4 s. Both change the source of the *design*, which is beyond the scope of
  this note and was not carried into the committed design.
- **The committed `rs_decode.v` is unchanged by any of this.** It is the
  design that the 400/400 differential result was measured on, and that is the
  design that is committed. The experiments were run on copies.

**Consequence: the Yosys half of the H3 acceptance wording is NOT met.** The
evidence for this lane is the simulation: Icarus Verilog and Verilator,
400/400 vectors, byte for byte against the project's own encoder and decoder.
If Yosys elaboration of this design is wanted later, it is a piece of work in
its own right: the ROM access has to be restructured so that the elaborator
sees one memory read per call site, and the unrolled loop bounds have to be
made constant, before the design can be handed to a synthesis flow at all.

## 5. What a synthesis run would still have to prove

A synthesis run is the start of the evidence, not the end of it. It would have
to show, in order: that the design elaborates and maps; that it meets a stated
clock period on a named device; that the routed netlist's behaviour still
matches the simulation (gate-level simulation against the same 400 vectors);
and only then that a board built from it reproduces the reference decode. None
of that has happened, and nothing in this repository should be read as a claim
that it has.

## 6. References

- `src/vnxdna/codec/gf256.py` -- the field and the EXP/LOG tables
- `src/vnxdna/codec/inner_parity.py` -- the parity matrix
- `src/vnxdna/codec/rs_batch.py` -- the reference decoder transcribed here
- `src/vnxdna/codec/rs_fast.py` -- the vectorised twin of the same algorithm
- `docs/V15_FINALIZATION_PLAN.md` -- the H3 row and the LR-series items
- `docs/LIMITATIONS.md` -- the project-wide limitation list
