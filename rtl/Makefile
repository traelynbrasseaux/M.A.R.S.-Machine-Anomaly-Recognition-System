# Lint, simulate, build and program the RTL. Run from rtl/ with OSS CAD Suite on PATH
# (on Windows, inside the shell that start.bat opens).
#
# Board targets build DESIGN=blinky (default) or DESIGN=selftest, e.g. make upduino DESIGN=selftest.

BUILD := build
UV ?= uv
DESIGN ?= blinky

ifeq ($(OS),Windows_NT)
# The Windows suite ships the Verilator binary without its Perl wrapper or a working default root.
VERILATOR ?= verilator_bin
ifdef YOSYSHQ_ROOT
export VERILATOR_ROOT ?= $(subst \,/,$(YOSYSHQ_ROOT))share/verilator
endif
else
VERILATOR ?= verilator
endif

CORE_SRC := src/hdc_core.v
SELFTEST_SRC := src/hdc_selftest.v $(CORE_SRC)
SIM_MEM := src/mem/spram.v
ICE40_MEM := src/mem/ice40/spram.v
# The default ROM_FILE in hdc_selftest.v.
SELFTEST_ROM := $(BUILD)/selftest.hex
UPDUINO_PCF := boards/upduino/upduino.pcf
TANGNANO_CST := boards/tangnano20k/tangnano20k.cst

ifeq ($(DESIGN),selftest)
UPDUINO_SRC := boards/upduino/selftest.v $(SELFTEST_SRC) $(ICE40_MEM)
TANGNANO_SRC := boards/tangnano20k/selftest.v $(SELFTEST_SRC) $(SIM_MEM)
DESIGN_DEPS := $(SELFTEST_ROM)
else ifeq ($(DESIGN),blinky)
UPDUINO_SRC := boards/upduino/blinky.v
TANGNANO_SRC := boards/tangnano20k/blinky.v
DESIGN_DEPS :=
else
$(error DESIGN must be blinky or selftest)
endif

UPDUINO := $(BUILD)/upduino-$(DESIGN)
TANGNANO := $(BUILD)/tangnano20k-$(DESIGN)

.PHONY: all lint sim stats upduino tangnano prog-upduino prog-tangnano flash-tangnano clean

all: lint sim stats upduino tangnano

lint:
	$(VERILATOR) --lint-only -Wall --top-module hdc_core $(CORE_SRC) $(SIM_MEM)
	$(VERILATOR) --lint-only -Wall --top-module hdc_core -GDIM=256 -GNF_MAX=8 $(CORE_SRC) $(SIM_MEM)
	$(VERILATOR) --lint-only -Wall --top-module hdc_selftest $(SELFTEST_SRC) $(SIM_MEM)
	$(UV) run --project ../host ruff check tests scripts
	$(UV) run --project ../host ruff format --check tests scripts

sim:
	$(UV) run --project ../host pytest tests

$(BUILD):
	mkdir -p $@

$(SELFTEST_ROM): scripts/selftest_rom.py | $(BUILD)
	$(UV) run --project ../host python scripts/selftest_rom.py $@

# Synthesis-only resource counts for the HDC core at full size on both chips.
stats: $(BUILD)/hdc_core-ice40.txt $(BUILD)/hdc_core-gowin.txt
	@cat $^

$(BUILD)/hdc_core-ice40.txt: $(CORE_SRC) $(ICE40_MEM) | $(BUILD)
	yosys -q -l $(BUILD)/hdc_core-ice40.log -p "read_verilog $(CORE_SRC) $(ICE40_MEM); synth_ice40 -top hdc_core; tee -q -o $@ stat"

$(BUILD)/hdc_core-gowin.txt: $(CORE_SRC) $(SIM_MEM) | $(BUILD)
	yosys -q -l $(BUILD)/hdc_core-gowin.log -p "read_verilog $(CORE_SRC) $(SIM_MEM); synth_gowin -family gw2a -top hdc_core; tee -q -o $@ stat"

# UPduino v3.1: iCE40 UP5K-SG48.
upduino: $(UPDUINO).bin

$(UPDUINO).json: $(UPDUINO_SRC) $(DESIGN_DEPS) | $(BUILD)
	yosys -q -l $(UPDUINO)-yosys.log -p "read_verilog $(UPDUINO_SRC); synth_ice40 -top top -json $@"

$(UPDUINO).asc: $(UPDUINO).json $(UPDUINO_PCF)
	nextpnr-ice40 -q --up5k --package sg48 --freq 24 --json $< --pcf $(UPDUINO_PCF) --asc $@ --log $(UPDUINO)-pnr.log

$(UPDUINO).bin: $(UPDUINO).asc
	icepack $< $@

# Tang Nano 20K: Gowin GW2AR-LV18QN88C8/I7.
tangnano: $(TANGNANO).fs

$(TANGNANO)-synth.json: $(TANGNANO_SRC) $(DESIGN_DEPS) | $(BUILD)
	yosys -q -l $(TANGNANO)-yosys.log -p "read_verilog $(TANGNANO_SRC); synth_gowin -family gw2a -top top -json $@"

$(TANGNANO).json: $(TANGNANO)-synth.json $(TANGNANO_CST)
	nextpnr-himbaechel -q --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=$(TANGNANO_CST) --freq 27 --json $< --write $@ --log $(TANGNANO)-pnr.log

# Apicula warns that optional speedups are missing from the suite's Python; packing still works.
$(TANGNANO).fs: $(TANGNANO).json
	PYTHONWARNINGS=ignore gowin_pack -d GW2A-18C -o $@ $<

# The UPduino always boots from its flash. The Tang Nano loads into SRAM (lost at power off)
# unless flashed.
prog-upduino: $(UPDUINO).bin
	iceprog $<

prog-tangnano: $(TANGNANO).fs
	openFPGALoader -b tangnano20k $<

flash-tangnano: $(TANGNANO).fs
	openFPGALoader -b tangnano20k -f $<

clean:
	rm -rf $(BUILD)
