"""cocotb test of rtl/src/hdc_selftest.v: it passes on a true ROM and fails on a corrupted one."""

import os
import subprocess
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, First, ReadOnly, RisingEdge, Timer
from cocotb_tools.runner import get_runner

RTL = Path(__file__).resolve().parents[1]
SOURCES = [RTL / "src" / name for name in ("hdc_selftest.v", "hdc_core.v", "mem/spram.v")]
SCRIPT = RTL / "scripts" / "selftest_rom.py"


@cocotb.test()
async def reports_expected_result(dut) -> None:
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0
    finished = await First(RisingEdge(dut.passed), RisingEdge(dut.failed), Timer(100, "ms"))
    assert not isinstance(finished, Timer), "self-test hung"
    await ReadOnly()
    want_pass = os.environ["SELFTEST_EXPECT"] == "pass"
    assert (int(dut.passed.value), int(dut.failed.value)) == (want_pass, not want_pass)
    assert not int(dut.busy.value)


@pytest.mark.parametrize(
    ("dim", "nf_max", "corrupt"),
    [(4096, 45, False), (256, 8, False), (256, 8, True)],
    ids=["board-rom", "small", "small-corrupt"],
)
def test_hdc_selftest(dim: int, nf_max: int, corrupt: bool) -> None:
    build_dir = RTL / "build" / "sim" / f"hdc_selftest_{dim}_{nf_max}{'_corrupt' * corrupt}"
    rom = build_dir / "selftest.hex"
    args = [str(rom), "--dim", str(dim), "--nf-max", str(nf_max), *["--corrupt"] * corrupt]
    subprocess.run([sys.executable, str(SCRIPT), *args], check=True)
    runner = get_runner("icarus")
    runner.build(
        sources=SOURCES,
        hdl_toplevel="hdc_selftest",
        # Icarus takes the value as written, so a string parameter carries its own quotes.
        parameters={"DIM": dim, "NF_MAX": nf_max, "ROM_FILE": f'"{rom.as_posix()}"'},
        build_dir=build_dir,
        timescale=("1ns", "1ps"),
        always=True,
    )
    runner.test(
        hdl_toplevel="hdc_selftest",
        test_module="test_hdc_selftest",
        extra_env={"SELFTEST_EXPECT": "fail" if corrupt else "pass"},
    )
