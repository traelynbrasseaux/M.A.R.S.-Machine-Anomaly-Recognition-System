"""cocotb tests for rtl/src/hdc_core.v, checked bit for bit against host/mhm/fixed/hdc.py."""

import os
from pathlib import Path

import cocotb
import numpy as np
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge
from cocotb_tools.runner import get_runner

from mhm.fixed.hdc import FixedHdc, level_thresholds, to_fixed
from mhm.models.hdc import item_memory

RTL = Path(__file__).resolve().parents[1]
SOURCES = [RTL / "src" / "hdc_core.v", RTL / "src" / "mem" / "spram.v"]
OP_CLEAR, OP_BUNDLE, OP_FINISH, OP_SCORE = range(4)
N_LEVELS = 16
SEED = 0x1234ABCD


def words(bits: np.ndarray) -> list[int]:
    """Pack a bit vector into 16-bit words, dimension 16 w + b at bit b of word w."""
    return [int(x) for x in bits.reshape(-1, 16).astype(np.int64) @ (1 << np.arange(16))]


def u32(value: int) -> int:
    return int(value) & 0xFFFFFFFF


class Core:
    """Drives hdc_core, changing inputs on falling clock edges so each is sampled once."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.dim = int(os.environ["HDC_DIM"])
        self.nf_max = int(os.environ["HDC_NF_MAX"])

    async def wait_high(self, signal) -> None:
        """Return at a falling clock edge with signal high."""
        await FallingEdge(self.dut.clk)
        while not signal.value:
            await RisingEdge(signal)
            await FallingEdge(self.dut.clk)

    async def reset(self) -> None:
        dut = self.dut
        for name in ("op_valid", "op", "op_ctx", "op_nfeat", "feat_valid", "feat_data"):
            getattr(dut, name).value = 0
        for name in ("thr_we", "thr_ctx", "thr_feat", "thr_level", "thr_value"):
            getattr(dut, name).value = 0
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
        dut.rst.value = 1
        await ClockCycles(dut.clk, 2)
        dut.rst.value = 0
        await self.wait_high(dut.ready)

    async def write_thresholds(self, ctx: int, thresholds: np.ndarray) -> None:
        dut = self.dut
        for f, row in enumerate(thresholds):
            for level, value in enumerate(row, start=1):
                await self.wait_high(dut.ready)
                dut.thr_ctx.value, dut.thr_feat.value, dut.thr_level.value = ctx, f, level
                dut.thr_value.value = u32(value)
                dut.thr_we.value = 1
                await FallingEdge(dut.clk)
                dut.thr_we.value = 0

    async def run(self, op: int, ctx: int, window: np.ndarray | None = None) -> int:
        """Run one operation, streaming a window's features for BUNDLE and SCORE."""
        dut = self.dut
        await self.wait_high(dut.ready)
        dut.op.value, dut.op_ctx.value = op, ctx
        dut.op_nfeat.value = 0 if window is None else len(window)
        dut.op_valid.value = 1
        await FallingEdge(dut.clk)
        dut.op_valid.value = 0
        for value in [] if window is None else window:
            await self.wait_high(dut.feat_ready)
            dut.feat_data.value = u32(value)
            dut.feat_valid.value = 1
            await FallingEdge(dut.clk)
            dut.feat_valid.value = 0
        await self.wait_high(dut.ready)
        return int(dut.score.value)

    def read_words(self, mem, start: int, count: int) -> list[int]:
        return [int(mem[start + i].value) for i in range(count)]


@cocotb.test()
async def item_memory_matches_python(dut) -> None:
    core = Core(dut)
    await core.reset()
    expected = item_memory(SEED, core.dim, N_LEVELS, core.nf_max)
    w = core.dim // 16
    mem = dut.u_item.mem
    for level in range(N_LEVELS):
        assert core.read_words(mem, level * w, w) == words(expected.levels[level]), level
    assert core.read_words(mem, 16 * w, w) == words(expected.tiebreak)
    for f in range(core.nf_max):
        assert core.read_words(mem, (17 + f) * w, w) == words(expected.ids[f]), f


def context_data(rng: np.random.Generator, n_features: int) -> tuple[np.ndarray, ...]:
    """Training and test windows for one context, with level edges from the training set."""
    scale = rng.uniform(0.01, 30.0, n_features)
    offset = rng.uniform(-200.0, 200.0, n_features)
    train = rng.normal(size=(12, n_features)) * scale + offset
    test = rng.normal(size=(10, n_features)) * scale + offset
    test[5:8, : n_features // 2] += 2.5 * scale[: n_features // 2]  # anomalous
    test[8] = offset - 1e6  # below every level edge, saturating to INT32_MIN
    test[9] = offset + 50 * scale  # above every level edge
    std = train.std(axis=0)
    thresholds = level_thresholds(train.mean(axis=0) - 2 * std, 4 * std, N_LEVELS)
    return to_fixed(train), to_fixed(test), thresholds


@cocotb.test()
async def scores_match_model(dut) -> None:
    core = Core(dut)
    await core.reset()
    rng = np.random.default_rng(core.dim + core.nf_max)
    # Context 0 uses every ID and context 1 one fewer, so one feature count is even and its
    # encoder ties take the tiebreak bit. Twelve training windows give prototype ties too.
    contexts = {0: core.nf_max, 1: core.nf_max - 1}
    data, models = {}, {}
    for ctx, n_features in contexts.items():
        data[ctx] = context_data(rng, n_features)
        models[ctx] = FixedHdc(data[ctx][2], dim=core.dim, seed=SEED, n_ids=core.nf_max)
        await core.write_thresholds(ctx, data[ctx][2])
        await core.run(OP_CLEAR, ctx)

    # Interleave the contexts so a leak between them would show up.
    for i in range(len(data[0][0])):
        for ctx in contexts:
            await core.run(OP_BUNDLE, ctx, data[ctx][0][i])
    for ctx, model in models.items():
        model.bundle(data[ctx][0])
        model.finish()
        await core.run(OP_FINISH, ctx)
        w = core.dim // 16
        proto = core.read_words(dut.u_ctx.mem, 4096 + 2 * core.dim + ctx * w, w)
        assert proto == words(model.prototype), f"context {ctx} prototype"

    for ctx, model in models.items():
        test = data[ctx][1]
        expected = model.score(test)
        got = [await core.run(OP_SCORE, ctx, window) for window in test]
        dut._log.info(f"context {ctx}: scores {got}")
        assert got == expected.tolist(), f"context {ctx}"
        assert len(set(got)) > 3, "test windows should score differently"


@pytest.mark.parametrize(("dim", "nf_max"), [(256, 8), (4096, 45)])
def test_hdc_core(dim: int, nf_max: int) -> None:
    runner = get_runner("icarus")
    build_dir = RTL / "build" / "sim" / f"hdc_core_{dim}_{nf_max}"
    runner.build(
        sources=SOURCES,
        hdl_toplevel="hdc_core",
        parameters={"DIM": dim, "NF_MAX": nf_max},
        build_dir=build_dir,
        timescale=("1ns", "1ps"),
        always=True,
    )
    runner.test(
        hdl_toplevel="hdc_core",
        test_module="test_hdc_core",
        extra_env={"HDC_DIM": str(dim), "HDC_NF_MAX": str(nf_max)},
    )
