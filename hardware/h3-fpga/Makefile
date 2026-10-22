# Makefile -- simulate the H3 VNX codec accelerator (the GF(256) RS decoder).
#
# DESIGN STATUS: SIMULATED ONLY.  Nothing here builds an FPGA image.  The
# targets elaborate and simulate the RTL and run the differential check against
# the project's own Python/NumPy reference decoder.
#
# Targets
#   make            run the differential check (the acceptance test)
#   make sim        compile and run the testbench on the generated stimulus
#   make stim       generate the stimulus and expected-output files
#   make rom        regenerate gf256_rom.vh from the primitive polynomial
#   make lint       elaborate with iverilog and with verilator (lint only)
#   make verilator  build and run the verilator variant of the testbench
#   make clean      remove the build products
#
# The simulator is auto-detected: Icarus Verilog first, then Verilator.  If
# neither is installed, the targets print the exact commands to run elsewhere
# and exit 0 with a clear "no simulator available" message.

SHELL       := /bin/bash
TOP         := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON      ?= python3

IVERILOG    ?= iverilog
VVP         ?= vvp
VERILATOR   ?= verilator

RTL         := $(TOP)/rs_decode.v
TB          := $(TOP)/tb_rs_decode.v
ROM         := $(TOP)/gf256_rom.vh
BUILD       := $(TOP)/build
VVP_EXE     := $(BUILD)/tb_rs_decode.vvp
STIM        := $(TOP)/stimulus.txt
EXPECTED    := $(TOP)/stimulus.expected
SIM_BIN     := $(BUILD)/obj_dir/Vtb_rs_decode

VECTORS     ?= 400
SEED        ?= 20261021

.PHONY: all sim stim rom lint verilator clean no-simulator

all: sim

# ---------------------------------------------------------------- simulator check
# Prints "ok" and the chosen simulator, or exits 0 with a clear message when no
# simulator is installed.
check-sim:
	@if command -v $(IVERILOG) >/dev/null 2>&1 && command -v $(VVP) >/dev/null 2>&1; then \
	    echo "simulator: iverilog ($(IVERILOG))"; \
	elif command -v $(VERILATOR) >/dev/null 2>&1; then \
	    echo "simulator: verilator ($(VERILATOR))"; \
	else \
	    echo "NO SIMULATOR AVAILABLE: neither $(IVERILOG) nor $(VERILATOR) is on PATH."; \
	    echo "This is not a failure of the design: the RTL has not been simulated here."; \
	    echo "Run the following on a machine with Icarus Verilog:"; \
	    echo "  $(IVERILOG) -g2001 -I $(TOP) -o $(VVP_EXE) $(RTL) $(TB)"; \
	    echo "  $(VVP) $(VVP_EXE) +stim=$(STIM) +expected=$(EXPECTED)"; \
	    echo "or with Verilator:"; \
	    echo "  $(VERILATOR) --binary -Wall --top-module tb_rs_decode $(RTL) $(TB) -Mdir $(BUILD)/obj_dir"; \
	    echo "  $(SIM_BIN) +stim=$(STIM) +expected=$(EXPECTED)"; \
	    exit 0; \
	fi

# ---------------------------------------------------------------- ROM
rom:
	$(PYTHON) $(TOP)/gen_gf256_rom.py $(ROM)

# ---------------------------------------------------------------- stimulus
stim:
	@mkdir -p $(BUILD)
	$(PYTHON) $(TOP)/differential_check.py --vectors $(VECTORS) --seed $(SEED) --gen-only

# ---------------------------------------------------------------- simulate
sim: check-sim
	@mkdir -p $(BUILD)
	@if command -v $(IVERILOG) >/dev/null 2>&1 && command -v $(VVP) >/dev/null 2>&1; then \
	    $(IVERILOG) -g2001 -I $(TOP) -o $(VVP_EXE) $(RTL) $(TB) || exit 1; \
	    $(VVP) $(VVP_EXE) +stim=$(STIM) +expected=$(EXPECTED); \
	else \
	    $(MAKE) --no-print-directory verilator; \
	fi

# ---------------------------------------------------------------- verilator
# The verilator path needs a C++ main; verilator's --binary mode provides one.
verilator:
	@if ! command -v $(VERILATOR) >/dev/null 2>&1; then \
	    echo "verilator is not installed; nothing to do"; exit 0; \
	fi
	@mkdir -p $(BUILD)
	$(VERILATOR) --binary -Wall --top-module tb_rs_decode \
	    -Mdir $(BUILD)/obj_dir $(RTL) $(TB) --exe --build-jobs 4
	$(SIM_BIN) +stim=$(STIM) +expected=$(EXPECTED)

# ---------------------------------------------------------------- lint
lint:
	@if command -v $(IVERILOG) >/dev/null 2>&1; then \
	    $(IVERILOG) -g2001 -I $(TOP) -o /dev/null $(RTL) $(TB) && echo "iverilog elaborate: OK"; \
	else \
	    echo "iverilog not installed: skipping the iverilog lint"; \
	fi
	@if command -v $(VERILATOR) >/dev/null 2>&1; then \
	    $(VERILATOR) --lint-only -Wall --top-module rs_decode -Mdir $(BUILD)/obj_dir $(RTL) \
	      && echo "verilator lint (rtl): OK"; \
	    $(VERILATOR) --lint-only -Wall --timing --top-module tb_rs_decode \
	      -Mdir $(BUILD)/obj_dir_tb $(RTL) $(TB) \
	      && echo "verilator lint (testbench): OK"; \
	else \
	    echo "verilator not installed: skipping the verilator lint"; \
	fi

# ---------------------------------------------------------------- the acceptance test
# The full differential check: generate the vectors with the project's own
# Python reference, run the Verilog testbench on them and compare byte for byte.
differential: check-sim
	$(PYTHON) $(TOP)/differential_check.py --vectors $(VECTORS) --seed $(SEED)

clean:
	rm -rf $(BUILD) $(VVP_EXE) $(STIM) $(EXPECTED)
