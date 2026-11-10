# rtl

Verilog for the feature extraction and HDC pipeline. The UPduino v3.1 (iCE40 UP5K) is the target and the Tang Nano 20K (Gowin GW2AR-18, four times the logic) is the development board and fallback. Both build from the same vendor-neutral core with the open-source flow; SKY130 comes after.

The golden model is `host/mhm/fixed/`, an integer version of the float Python in `host/mhm/features/` and `host/mhm/models/hdc.py`. The cocotb tests in `tests/` run the Verilog in Icarus and compare every output with it bit for bit, the same way `firmware/node/lib/ml` is checked against the float Python.

## Status

- [x] M0: blinky on both boards, build flow, CI
- [x] M1: HDC core (LFSR item memory, quantizer, bundling, Hamming score), bit-exact in simulation
- [ ] M2: SPI link from the ESP32; the ESP32 computes features and the FPGA runs HDC
- [ ] M3: fixed-point features, FFT and vibration features in Verilog
- [ ] M4: audio features and the full samples-to-score pipeline on the FPGA
- [ ] M5: the FPGA reads the sensors itself

## Layout

| Path | What |
|------|------|
| `src/hdc_core.v` | Binary HDC detector, two contexts (accel and audio) sharing one item memory |
| `src/mem/spram.v` | Generic single-port 16-bit RAM for simulation and the Tang Nano |
| `src/mem/ice40/spram.v` | The same RAM as one iCE40 `SB_SPRAM256KA` block |
| `boards/upduino/` | UPduino top level and pins |
| `boards/tangnano20k/` | Tang Nano 20K top level and pins |
| `tests/` | cocotb tests, run with pytest from the host's uv environment |

## Setup

- **OSS CAD Suite** (yosys, nextpnr, icestorm, Apicula, Icarus, Verilator, openFPGALoader): unpack the [latest release](https://github.com/YosysHQ/oss-cad-suite-build/releases/latest) and work in the shell its `start.bat` (Windows) or `environment` script opens. That puts the suite first on PATH, which matters on Windows: other MinGW tools on PATH (Git, w64devkit) carry DLLs with the same names, and a tool that loads the wrong one exits with code 127 and no message.
- **Python:** `uv sync` in `host/` installs cocotb with the other dev dependencies.
- **GNU make:** comes with Linux and macOS. Windows needs one installed, such as w64devkit's or `winget install ezwinports.make`. Flashing a prebuilt bitstream needs no make, only the suite.
- **USB drivers for programming (Windows):** the open-source programmers need a libusb driver, installed once per PC with [Zadig](https://zadig.akeo.ie/) while the board is plugged in. For the UPduino pick the FTDI device and install libusbK. For the Tang Nano replace only interface 0 (JTAG), so interface 1 keeps its COM port.

## Commands

Run from `rtl/`:

| Command | Does |
|---------|------|
| `make lint` | Verilator lint of the core at full and test size, ruff on the tests |
| `make sim` | cocotb tests against `host/mhm/fixed/` (about 80 s, mostly the full-size run) |
| `make stats` | Synthesizes the full-size core for both chips and prints the resource counts |
| `make upduino`, `make tangnano` | Builds `build/upduino.bin` and `build/tangnano20k.fs` |
| `make prog-upduino` | Writes the UPduino's flash with iceprog |
| `make prog-tangnano`, `make flash-tangnano` | Loads the Tang Nano's SRAM (lost at power off), or writes its flash |

Bring-up check: the UPduino's RGB LED steps through red, green, blue and white, and the Tang Nano's six LEDs count in binary.

## HDC core

`hdc_core` matches `FixedHdc` in `host/mhm/fixed/hdc.py`. One sequential datapath handles 16 dimensions per step, which spends time to save LUTs: windows arrive every 32 ms (audio) or 160 ms (accel), and one window takes about 2 ms at 24 MHz.

**Item memory.** SPRAM has no power-on contents, so after reset the core replays the LFSR stream from `hdc.py` (tiebreak, base, thresholds, IDs) into one SPRAM block. Level vectors need each dimension's 16-bit threshold compared with each level's cut, so the core reruns the threshold part of the stream once per level from a saved LFSR state instead of storing the thresholds. The cut comparison `thr < l * 2^15 // 15` becomes `(thr + 1) * 15 <= l << 15`, which needs no divider (`level_flips` in the Python). Start-up takes about 1.25 million cycles, 52 ms at 24 MHz.

**Quantizer.** Features arrive as signed Q16.16. Rather than computing `floor((x - lo) / width * 16)`, the core counts how many of a feature's 15 level edges the value reaches, which gives the same level with only a comparator. The ESP32 works out the edges in float from the ranges phase (`level_thresholds`) and writes them in.

**Interface.** While `ready` is high:

1. `thr_we` writes one level edge (`thr_ctx`, `thr_feat`, `thr_level` 1 to 15, `thr_value`).
2. `op_valid` starts an operation on context `op_ctx`: `CLEAR` zeroes the votes, `BUNDLE` encodes one window into the votes, `FINISH` turns the votes into the prototype, and `SCORE` encodes one window and sets `score` to its Hamming distance from the prototype (0 to 4096).
3. `BUNDLE` and `SCORE` then take `op_nfeat` features through `feat_valid` and `feat_ready`, in feature order.
4. `done` pulses when the operation ends and `ready` returns.

Vote counters and the bundled-window count are 16 bits, so one prototype can bundle at most 65,535 windows (35 minutes of audio).

**Memory map.** Item memory, 16-bit words of W = DIM / 16: level l at `l * W`, tiebreak at `16 * W`, ID f at `(17 + f) * W`. Context memory: level edges at `{ctx, feature, level, half}` from 0, votes at `4096 + ctx * DIM`, prototypes at `4096 + 2 * DIM + ctx * W`.

**Resources** at DIM 4096 with 45 IDs, from `make stats` (yosys only; place and route comes when a board top uses the core):

| | iCE40 UP5K | Gowin GW2AR-18 |
|---|---|---|
| LUTs | 1,042 of 5,280, plus 375 carry cells | 1,265 of 20,736 |
| Flip-flops | 390 | 382 |
| RAM | 2 of 4 SPRAM, 1 of 30 EBR | 13 of 46 BSRAM |
