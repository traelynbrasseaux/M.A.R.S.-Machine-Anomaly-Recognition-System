"""Draw the README figures and their LinkedIn versions: system diagram and detection timeline."""

import argparse
import csv
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.ticker import PercentFormatter

from mhm import REPO_ROOT, RESULTS_DIR, device_ml
from mhm.capture.frame import ACCEL_FS, MODELS, PHASES
from mhm.capture.simulate import FakeNode, SimConfig
from mhm.features.framing import frames
from mhm.features.vibration import VibrationConfig, vibration_features
from mhm.models.hdc import HdcScorer

IMG_DIR = REPO_ROOT / "docs" / "img"
SOCIAL_DIR = RESULTS_DIR / "social"
REPO_URL = "github.com/traelynbrasseaux/\nM.A.R.S.-Machine-Anomaly-Recognition-System-"
DPI = 200
FPS = 30
FAULT_S = 100.0
END_S = 140.0
LEARN_S = 40.0
CALIBRATE_S = 20.0

plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"],
    }
)


@dataclass(frozen=True)
class Theme:
    """Chart tokens for one surface, from the dataviz reference palette."""

    surface: str
    ink: str
    ink_2: str
    muted: str
    grid: str
    axis: str
    accent: str  # categorical slot 1, the first or only data series
    accent_2: str  # categorical slot 2, validated against slot 1 in both modes
    critical: str  # status color, always shown with an icon and a label


LIGHT = Theme(
    "#fcfcfb",
    "#0b0b0b",
    "#52514e",
    "#898781",
    "#e1e0d9",
    "#c3c2b7",
    "#2a78d6",
    "#eb6834",
    "#d03b3b",
)
DARK = Theme(
    "#1a1a19",
    "#f0efec",
    "#c3c2b7",
    "#898781",
    "#2c2c2a",
    "#383835",
    "#3987e5",
    "#d95926",
    "#d03b3b",
)


def save(fig: Figure, path: Path) -> None:
    fig.savefig(path, dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)


# Detection timeline


@dataclass
class Timeline:
    """One replay of the node's detector: the HDC binary score per block while monitoring."""

    t: np.ndarray  # seconds at the end of each ~1 s block
    score: np.ndarray  # percent of the 4,096 bits that differ from the learned normal
    threshold: float  # percent
    detect_start: float  # seconds of learning and calibration before monitoring
    first_alert: float
    false_alarms: int  # alerts in blocks that ended before the fault started


def detection_timeline() -> Timeline:
    """Simulate normal running then unbalance, and replay it through the node's C++ detector."""
    n_fault, n_end = int(FAULT_S * ACCEL_FS), int(END_S * ACCEL_FS)
    # One seed for both halves keeps the shaft's phase and speed wander continuous at the switch.
    normal = FakeNode(SimConfig(seed=0)).accel(0, n_fault)
    fault = FakeNode(SimConfig(seed=0, unbalance=True)).accel(n_fault, n_end - n_fault)
    device_ml.build()
    samples = np.concatenate([normal, fault]).reshape(-1)
    r = device_ml.replay("accel", samples, LEARN_S, CALIBRATE_S)
    m = MODELS.index("hdc-binary")
    detect = r.phase == PHASES.index("detect")
    t, alerts = r.t[detect], r.alerts[detect, m]
    return Timeline(
        t=t,
        score=100 * r.scores[detect, m],
        threshold=100 * float(r.thresholds[detect, m][0]),
        detect_start=float(r.t[np.flatnonzero(detect)[0] - 1]),
        first_alert=float(t[alerts][0]),
        false_alarms=int(np.sum(alerts & (t < FAULT_S))),
    )


class TimelineChart:
    """The timeline drawn into one axes, revealable up to any moment for stills and video."""

    def __init__(self, ax: Axes, tl: Timeline, theme: Theme, size: float):
        self.tl = tl
        top = float(tl.score.max()) * 1.4
        text = {"fontsize": size, "color": theme.ink_2}
        ax.set_facecolor(theme.surface)
        ax.set_xlim(0, END_S)
        ax.set_ylim(0, top)
        # No gridline in the top band, where the phase and event labels sit.
        ax.set_yticks(np.arange(0, tl.score.max(), 5))
        ax.yaxis.set_major_formatter(PercentFormatter(decimals=0))
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(theme.axis)
        ax.tick_params(colors=theme.axis, labelcolor=theme.ink_2, labelsize=size)
        ax.tick_params(axis="y", length=0)
        ax.grid(axis="y", color=theme.grid, linewidth=0.7)
        ax.set_axisbelow(True)
        ax.set_xlabel("Time (s)", **text)

        ax.axvspan(0, tl.detect_start, color=theme.grid, alpha=0.5, linewidth=0)
        learning = "Learning normal,\nno fault examples\nneeded"
        ax.text(tl.detect_start / 2, top * 0.96, learning, ha="center", va="top", **text)
        ax.text(tl.detect_start + 2, top * 0.96, "Monitoring", ha="left", va="top", **text)

        (self.line,) = ax.plot([], [], color=theme.accent, linewidth=2, zorder=3)
        self.line.set_solid_capstyle("round")
        self.line.set_solid_joinstyle("round")
        dot = {"markeredgecolor": theme.surface, "markeredgewidth": 1.5}
        (self.head,) = ax.plot([], [], "o", color=theme.accent, markersize=7, zorder=4, **dot)
        self.cursor = ax.axvline(0, color=theme.muted, linewidth=0.8, zorder=1)
        thr = tl.threshold
        self.threshold: list[Artist] = [
            ax.plot([tl.detect_start, END_S], [thr, thr], color=theme.ink_2, linewidth=1)[0],
            ax.text(tl.detect_start + 2, thr + top * 0.02, "Alert threshold", va="bottom", **text),
        ]
        self.fault: list[Artist] = [
            ax.axvline(FAULT_S, color=theme.ink_2, linewidth=1, zorder=2),
            ax.text(FAULT_S + 2, top * 0.96, "Unbalance\nstarts", va="top", **text),
        ]
        quiet = "No false alarms" if tl.false_alarms == 0 else f"{tl.false_alarms} false alarms"
        mid = (tl.detect_start + FAULT_S) / 2
        self.quiet: list[Artist] = [
            ax.text(mid, thr * 0.3, quiet, ha="center", va="center", **text)
        ]
        # The alert is a status: red triangle icon plus a label in ink, never color alone.
        y = float(tl.score[tl.t == tl.first_alert][0])
        label = f"Alert at {tl.first_alert:.1f} s"
        self.alert: list[Artist] = [
            ax.plot(tl.first_alert, y, "^", color=theme.critical, markersize=10, zorder=5, **dot)[
                0
            ],
            ax.text(
                tl.first_alert - 3.5,
                y,
                label,
                ha="right",
                va="center",
                **text | {"color": theme.ink},
            ),
        ]

    def show_until(self, tau: float, live: bool = False) -> None:
        """Reveal what exists at time tau; live adds a cursor and a head dot for video."""
        keep = self.tl.t <= tau
        self.line.set_data(self.tl.t[keep], self.tl.score[keep])
        self.head.set_data(self.tl.t[keep][-1:], self.tl.score[keep][-1:])
        self.head.set_visible(live and bool(keep.any()))
        self.cursor.set_xdata([tau, tau])
        # The cursor shows time passing while there is no data yet; after that the head dot does.
        self.cursor.set_visible(live and tau < self.tl.detect_start)
        reveal = (
            (self.threshold, self.tl.detect_start),
            (self.fault, FAULT_S),
            (self.quiet, FAULT_S),
            (self.alert, self.tl.first_alert),
        )
        for group, start in reveal:
            for artist in group:
                artist.set_visible(tau >= start)


TIMELINE_TITLE = "Rotor unbalance flagged within a second"
TIMELINE_SUBTITLE = (
    "HDC anomaly score per second: the share of a 4,096-bit pattern that differs from the learned"
    " normal"
)
TIMELINE_NOTE = (
    "Simulated 25 Hz motor replayed through the node's C++ detector. Real-rig results to follow."
)


def plot_timeline_wide(tl: Timeline, theme: Theme, path: Path) -> None:
    """README version of the detection timeline."""
    fig = plt.figure(figsize=(10, 4.8), facecolor=theme.surface)
    TimelineChart(fig.add_axes((0.065, 0.16, 0.91, 0.6)), tl, theme, 9.5).show_until(END_S)
    fig.text(0.065, 0.95, TIMELINE_TITLE, fontsize=15, weight="bold", color=theme.ink, va="top")
    fig.text(0.065, 0.875, TIMELINE_SUBTITLE, fontsize=10.5, color=theme.ink_2, va="top")
    fig.text(0.065, 0.025, TIMELINE_NOTE, fontsize=8.5, color=theme.muted, va="bottom")
    save(fig, path)


def portrait_timeline(tl: Timeline, theme: Theme) -> tuple[Figure, TimelineChart]:
    """LinkedIn feed version, 1080 x 1350, with the type sized for a phone."""
    fig = plt.figure(figsize=(5.4, 6.75), facecolor=theme.surface)
    chart = TimelineChart(fig.add_axes((0.12, 0.255, 0.83, 0.445)), tl, theme, 10.5)
    title = "M.A.R.S. flags a rotor\nunbalance within a second"
    subtitle = (
        "HDC anomaly score per second: the share\n"
        "of a 4,096-bit pattern that differs from\n"
        "the learned normal"
    )
    fig.text(0.06, 0.955, title, fontsize=19, weight="bold", color=theme.ink, va="top")
    fig.text(0.06, 0.825, subtitle, fontsize=11.5, color=theme.ink_2, va="top", linespacing=1.3)
    note = "Simulated motor data. Real-rig results to follow."
    fig.text(0.06, 0.15, note, fontsize=10, color=theme.muted, va="top")
    name = "M.A.R.S.  Machine Anomaly Recognition System"
    fig.text(0.06, 0.103, name, fontsize=10.5, weight="bold", color=theme.ink, va="top")
    fig.text(0.06, 0.07, REPO_URL, fontsize=10, color=theme.ink_2, va="top", linespacing=1.3)
    return fig, chart


def plot_timeline_portrait(tl: Timeline, theme: Theme, path: Path) -> None:
    fig, chart = portrait_timeline(tl, theme)
    chart.show_until(END_S)
    save(fig, path)


def animate_timeline(tl: Timeline, theme: Theme, path: Path) -> None:
    """MP4 of the portrait timeline: learning goes by fast, the fault plays slower, then a hold."""
    segments = ((0.0, tl.detect_start, 3.0), (tl.detect_start, FAULT_S, 3.0), (FAULT_S, END_S, 4.0))
    taus = [np.linspace(a, b, round(seconds * FPS), endpoint=False) for a, b, seconds in segments]
    frames = np.concatenate(taus)
    fig, chart = portrait_timeline(tl, theme)
    args = ["-pix_fmt", "yuv420p", "-crf", "18"]
    writer = FFMpegWriter(fps=FPS, codec="libx264", extra_args=args)
    with writer.saving(fig, str(path), dpi=DPI):
        for tau in frames:
            chart.show_until(float(tau), live=True)
            writer.grab_frame()
        chart.show_until(END_S)
        for _ in range(3 * FPS):
            writer.grab_frame()
    plt.close(fig)


# System diagram


@dataclass(frozen=True)
class Stage:
    """One box in the system diagram."""

    title: str
    lines: tuple[str, ...]
    highlight: bool = False


STAGES = (
    Stage("On the machine", ("Magnet-mounted", "carrier on a motor,", "pump or fan")),
    Stage("Sense", ("Accelerometer,", "3 axes at 3,200 Hz", "Microphone, 16 kHz")),
    Stage("Features", ("1,024-sample windows", "33 vibration and", "45 sound features")),
    Stage("HDC detector", ("Encode as 4,096 bits", "and compare with", "the learned normal"), True),
    Stage("Alert", ("Wi-Fi to a gateway,", "with LoRa as a", "stretch goal")),
)
ROADMAP = (
    ("NOW", "ESP32 firmware, C++"),
    ("NEXT", "iCE40 FPGA, Verilog"),
    ("LATER", "SKY130 custom chip"),
)


def canvas(theme: Theme, size: tuple[float, float]) -> tuple[Figure, Axes]:
    """A figure whose one axes measures in inches from the bottom left, for drawing diagrams."""
    fig = plt.figure(figsize=size, facecolor=theme.surface)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, size[0])
    ax.set_ylim(0, size[1])
    ax.set_axis_off()
    return fig, ax


def box(ax: Axes, theme: Theme, x: float, y: float, w: float, h: float, highlight: bool) -> None:
    """A rounded box: a neutral hairline, or the accent over a 10% wash when highlighted."""
    face = (*to_rgb(theme.accent), 0.10) if highlight else theme.surface
    edge = theme.accent if highlight else theme.axis
    style = "round,pad=0,rounding_size=0.08"
    lw = 1.3 if highlight else 0.9
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=style, fc=face, ec=edge, lw=lw))


def arrow(ax: Axes, theme: Theme, x0: float, x1: float, y: float) -> None:
    props = {"arrowstyle": "-|>", "color": theme.muted, "lw": 1.2, "shrinkA": 0, "shrinkB": 0}
    ax.annotate("", xy=(x1, y), xytext=(x0, y), arrowprops=props | {"mutation_scale": 11})


def plot_system(theme: Theme, path: Path) -> None:
    """The node's pipeline from machine to alert, with the software to silicon roadmap."""
    width, height = 10.0, 4.2
    fig, ax = canvas(theme, (width, height))
    ax.text(0.2, 4.0, "How M.A.R.S. works", fontsize=15, weight="bold", color=theme.ink, va="top")

    bw, bh, gap, y0 = 1.68, 1.45, 0.31, 2.05
    x0 = (width - len(STAGES) * bw - (len(STAGES) - 1) * gap) / 2
    for i, stage in enumerate(STAGES):
        x = x0 + i * (bw + gap)
        box(ax, theme, x, y0, bw, bh, stage.highlight)
        ax.text(
            x + 0.14,
            y0 + bh - 0.2,
            stage.title,
            fontsize=11,
            weight="bold",
            color=theme.ink,
            va="top",
        )
        for j, line in enumerate(stage.lines):
            ax.text(
                x + 0.14, y0 + bh - 0.58 - j * 0.22, line, fontsize=9.2, color=theme.ink_2, va="top"
            )
        if i < len(STAGES) - 1:
            arrow(ax, theme, x + bw + 0.05, x + bw + gap - 0.05, y0 + bh / 2)

    # A bracket under Sense, Features and HDC: the part that runs on the node.
    left, right = x0 + (bw + gap) + 0.1, x0 + 3 * (bw + gap) + bw - 0.1
    yb, center = y0 - 0.22, (x0 + (bw + gap) + x0 + 3 * (bw + gap) + bw) / 2
    ax.plot([left, left, right, right], [yb + 0.1, yb, yb, yb + 0.1], color=theme.muted, lw=1)
    ax.plot([center, center], [yb, yb - 0.14], color=theme.muted, lw=1)
    caption = "Runs on the node, moving from software to silicon"
    ax.text(center, yb - 0.2, caption, fontsize=9.5, color=theme.ink_2, ha="center", va="top")

    cw, ch, cgap, cy = 2.25, 0.6, 0.42, 0.95
    cx0 = center - (len(ROADMAP) * cw + (len(ROADMAP) - 1) * cgap) / 2
    for i, (tag, what) in enumerate(ROADMAP):
        x = cx0 + i * (cw + cgap)
        box(ax, theme, x, cy - ch / 2, cw, ch, i == 0)
        ax.text(
            x + cw / 2,
            cy + 0.11,
            tag,
            fontsize=7.5,
            weight="bold",
            color=theme.muted,
            ha="center",
            va="center",
        )
        ax.text(x + cw / 2, cy - 0.1, what, fontsize=9.5, color=theme.ink, ha="center", va="center")
        if i < len(ROADMAP) - 1:
            arrow(ax, theme, x + cw + 0.06, x + cw + cgap - 0.06, cy)
    footnote = "HDC needs only XOR, counting and comparing, so it maps directly onto logic gates."
    ax.text(center, 0.27, footnote, fontsize=9, color=theme.muted, ha="center", va="center")
    save(fig, path)


def plot_social_preview(theme: Theme, path: Path) -> None:
    """1280 x 640 card for GitHub's social preview, which LinkedIn shows for repo links."""
    fig, ax = canvas(theme, (6.4, 3.2))
    ax.text(0.4, 2.85, "M.A.R.S.", fontsize=30, weight="bold", color=theme.ink, va="top")
    ax.text(
        0.4, 2.28, "Machine Anomaly Recognition System", fontsize=13, color=theme.ink_2, va="top"
    )
    tagline = (
        "Learns a machine's normal vibration and sound, then flags\n"
        "faults with hyperdimensional computing (HDC)."
    )
    ax.text(0.4, 1.9, tagline, fontsize=10.5, color=theme.ink_2, va="top", linespacing=1.35)
    pw, ph, pgap, py = 1.1, 0.42, 0.35, 0.95
    for i, name in enumerate(("Sense", "Features", "HDC", "Alert")):
        x = 0.4 + i * (pw + pgap)
        box(ax, theme, x, py - ph / 2, pw, ph, name == "HDC")
        ax.text(x + pw / 2, py, name, fontsize=11, color=theme.ink, ha="center", va="center")
        if i < 3:
            arrow(ax, theme, x + pw + 0.05, x + pw + pgap - 0.05, py)
    roadmap = "ESP32 today, FPGA next, SKY130 chip later"
    ax.text(0.4, 0.35, roadmap, fontsize=9.5, color=theme.muted, va="center")
    ax.text(
        6.0,
        0.35,
        "Nexus Technology Cup 2026",
        fontsize=9.5,
        color=theme.muted,
        ha="right",
        va="center",
    )
    save(fig, path)


# HDC explainer

EXPLAINER_SIZE = (8.0, 10.0)  # 4:5, so the same image works as a LinkedIn post
SHOWN_BITS = 40
EXAMPLE_FEATURE = 1  # x axis, 15 to 35 Hz: the band the 25 Hz shaft vibration lands in
PITCH, CELL, GAP = 0.11, 0.2, 0.02  # bit cell pitch, height and surface gap, inches
LABEL_X, STRIP_X = 2.15, 2.3


def vibration_names(cfg: VibrationConfig) -> list[str]:
    """Feature names in the extractor's order: per axis, the bands, RMS, kurtosis, crest factor."""
    bands = (f"{lo:g}-{hi:g} Hz" for lo, hi in pairwise(cfg.band_edges_hz))
    per_axis = [*bands, "RMS", "kurtosis", "crest factor"]
    return [f"{axis}, {name}" for axis in "xyz" for name in per_axis]


def simulated_windows(unbalance: bool, seed: int, seconds: float) -> np.ndarray:
    """Python vibration features for simulated accelerometer data."""
    cfg = VibrationConfig(fs=ACCEL_FS)
    accel = FakeNode(SimConfig(seed=seed, unbalance=unbalance)).accel(0, int(seconds * ACCEL_FS))
    return vibration_features(frames(accel, cfg.size, cfg.hop), cfg)


@dataclass
class HdcExample:
    """Patterns from the Python HDC model for a typical normal window and a typical faulty one."""

    names: list[str]
    scorer: HdcScorer
    levels: dict[str, np.ndarray]  # quantized level of each feature, by window kind
    patterns: dict[str, np.ndarray]  # the window's bundled bits, by window kind
    scores: dict[str, float]  # percent of all bits that differ from the learned normal


def hdc_example() -> HdcExample:
    """Fit HDC on simulated normal running, then take the median-scoring window of each kind."""
    scorer = HdcScorer().fit(simulated_windows(False, seed=0, seconds=60))
    levels, patterns, scores = {}, {}, {}
    for kind, unbalance in (("normal", False), ("unbalance", True)):
        x = simulated_windows(unbalance, seed=1, seconds=10)
        s = scorer.score(x)
        i = int(np.argmin(np.abs(s - np.median(s))))
        levels[kind] = scorer.quantize(x[i : i + 1])[0]
        patterns[kind] = next(scorer.encoded_chunks(x[i : i + 1]))[0]
        scores[kind] = 100 * float(s[i])
    return HdcExample(vibration_names(VibrationConfig()), scorer, levels, patterns, scores)


def strip_label(ax: Axes, theme: Theme, y: float, label: str, bold: bool = False) -> None:
    weight = "bold" if bold else "normal"
    ax.text(LABEL_X, y, label, fontsize=11, color=theme.ink, ha="right", va="center", weight=weight)


def bit_strip(
    ax: Axes,
    theme: Theme,
    top: float,
    bits: np.ndarray,
    label: str,
    differ: np.ndarray | None = None,
    bold: bool = False,
) -> None:
    """A row of bit cells: ones dark, zeros light, and bits that differ in the highlight color."""
    y = EXPLAINER_SIZE[1] - top - CELL
    for i, bit in enumerate(bits[:SHOWN_BITS]):
        if differ is not None and differ[i]:
            color = theme.accent_2
        else:
            color = theme.ink_2 if bit else theme.grid
        ax.add_patch(Rectangle((STRIP_X + i * PITCH, y), PITCH - GAP, CELL, fc=color, lw=0))
    strip_label(ax, theme, y + CELL / 2, label, bold)


def level_row(ax: Axes, theme: Theme, top: float, level: int, n_levels: int, label: str) -> None:
    """The quantizer's levels side by side, with the window's level filled in and numbered."""
    width = SHOWN_BITS * PITCH / n_levels
    y = EXPLAINER_SIZE[1] - top - CELL
    for q in range(n_levels):
        color = theme.ink if q == level else theme.grid
        ax.add_patch(Rectangle((STRIP_X + q * width, y), width - GAP, CELL, fc=color, lw=0))
    x = STRIP_X + (level + 0.5) * width
    ax.text(
        x,
        y + CELL / 2,
        str(level),
        fontsize=9,
        weight="bold",
        color=theme.surface,
        ha="center",
        va="center",
    )
    strip_label(ax, theme, y + CELL / 2, label)


def step(ax: Axes, theme: Theme, top: float, title: str, text: str) -> None:
    h = EXPLAINER_SIZE[1]
    ax.text(0.45, h - top, title, fontsize=14, weight="bold", color=theme.ink, va="top")
    ax.text(0.45, h - top - 0.33, text, fontsize=11, color=theme.ink_2, va="top", linespacing=1.35)


def plot_hdc_explainer(ex: HdcExample, theme: Theme, path: Path) -> None:
    """Quantize, bind, bundle and compare, drawn with the model's real bit patterns."""
    h = EXPLAINER_SIZE[1]
    fig, ax = canvas(theme, EXPLAINER_SIZE)
    title = "How the HDC detector scores a window"
    ax.text(0.45, h - 0.4, title, fontsize=20, weight="bold", color=theme.ink, va="top")
    subtitle = (
        "Real bit patterns from the model on simulated vibration.\n"
        f"Only the first {SHOWN_BITS} of each pattern's {ex.scorer.dim:,} bits are shown."
    )
    ax.text(0.45, h - 0.85, subtitle, fontsize=12, color=theme.ink_2, va="top", linespacing=1.35)
    n, n_levels, f = len(ex.names), ex.scorer.n_levels, EXAMPLE_FEATURE
    memory, bound = ex.scorer.memory, ex.scorer.bound
    q = int(ex.levels["normal"][f])

    text = (
        f"Each of the {n} vibration features is rounded to one of {n_levels} levels across its\n"
        "normal range. Example: the x-axis 15 to 35 Hz band, where the shaft vibration lands."
    )
    step(ax, theme, 1.6, "1  Quantize", text)
    level_row(ax, theme, 2.45, q, n_levels, "Normal window")
    level_row(ax, theme, 2.75, int(ex.levels["unbalance"][f]), n_levels, "Unbalance window")
    ends = {"fontsize": 9.5, "color": theme.muted, "va": "top"}
    ax.text(STRIP_X, h - 3.02, "level 0", **ends)
    ax.text(STRIP_X + SHOWN_BITS * PITCH, h - 3.02, f"level {n_levels - 1}", ha="right", **ends)

    text = "XOR the feature's fixed random ID pattern with the pattern for its level."
    step(ax, theme, 3.45, "2  Bind", text)
    bit_strip(ax, theme, 4.1, memory.ids[f], "Feature ID")
    bit_strip(ax, theme, 4.37, memory.levels[q], f"Level {q}")
    bit_strip(ax, theme, 4.64, bound[f, q], "ID XOR level", bold=True)

    text = f"A majority vote over all {n} bound patterns gives one pattern for the whole window."
    step(ax, theme, 5.15, "3  Bundle", text)
    shown = (0, 1, 2)
    for row, g in enumerate(shown):
        bit_strip(ax, theme, 5.8 + row * 0.27, bound[g, ex.levels["normal"][g]], ex.names[g])
    strip_label(ax, theme, h - 6.66, "...")
    last = n - 1
    bit_strip(ax, theme, 6.75, bound[last, ex.levels["normal"][last]], ex.names[last])
    bit_strip(ax, theme, 7.08, ex.patterns["normal"], "Window pattern", bold=True)

    text = "Count the bits that differ from the pattern learned from normal running."
    step(ax, theme, 7.55, "4  Compare", text)
    prototype = ex.scorer.prototype
    bit_strip(ax, theme, 8.2, prototype, "Learned normal", bold=True)
    right = STRIP_X + SHOWN_BITS * PITCH + 0.15
    for row, kind in enumerate(("normal", "unbalance")):
        top = 8.47 + row * 0.27
        bits = ex.patterns[kind]
        bit_strip(ax, theme, top, bits, f"{kind.capitalize()} window", differ=bits != prototype)
        score = f"{ex.scores[kind]:.1f}% differ"
        ax.text(right, h - top - CELL / 2, score, fontsize=11, color=theme.ink, va="center")

    for x, color, label in (
        (0.45, theme.ink_2, "bit is 1"),
        (1.65, theme.grid, "bit is 0"),
        (2.85, theme.accent_2, "differs from the learned normal"),
    ):
        ax.add_patch(Rectangle((x, h - 9.3), 0.2, 0.2, fc=color, lw=0))
        ax.text(x + 0.3, h - 9.2, label, fontsize=10.5, color=theme.ink_2, va="center")
    footer = "Only XOR, counting and comparing, which is why HDC fits in an FPGA."
    ax.text(0.45, h - 9.65, footer, fontsize=10.5, color=theme.muted, va="top")
    save(fig, path)


# Results on public datasets

RESULT_SETS = (  # evaluate.py output stem, row label, source, phrase for the title
    ("mimii_pump_id_00_6dB", "Pump faults, audio", "MIMII pump, 6 dB", "pump audio"),
    ("mafaulda_imbalance", "Rotor imbalance, vibration", "MAFAULDA", "rotor imbalance"),
)
RESULT_MODELS = (
    ("hdc-binary", "HDC binary, the hardware target"),
    ("mahalanobis", "Mahalanobis, the classical baseline"),
)


def load_auc() -> dict[str, dict[str, float]] | None:
    """Device-backend AUC per dataset and model from evaluate.py, or None if a run is missing."""
    auc = {}
    for stem, *_ in RESULT_SETS:
        path = RESULTS_DIR / f"{stem}_device_metrics.csv"
        if not path.exists():
            return None
        with path.open(newline="") as f:
            auc[stem] = {row["model"]: float(row["auc"]) for row in csv.DictReader(f)}
    return auc


def verdict(hdc: float, baseline: float) -> str:
    """How HDC compares with the baseline, treating AUC within 0.005 as a match."""
    if abs(hdc - baseline) <= 0.005:
        return "matches the baseline"
    return "leads" if hdc > baseline else "trails"


def plot_results(auc: dict[str, dict[str, float]], theme: Theme, path: Path) -> None:
    """AUC of HDC against the Mahalanobis baseline on the public datasets, as a dot plot."""
    fig = plt.figure(figsize=(10, 4.4), facecolor=theme.surface)
    ax = fig.add_axes((0.3, 0.2, 0.62, 0.44))
    rows = len(RESULT_SETS)
    ax.set_facecolor(theme.surface)
    ax.set_xlim(0.5, 1.0)
    ax.set_ylim(-0.6, rows - 0.4)
    ax.set_xticks(np.arange(0.5, 1.01, 0.1))
    ax.set_yticks([])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme.axis)
    ax.tick_params(colors=theme.axis, labelcolor=theme.ink_2, labelsize=10)
    ax.grid(axis="x", color=theme.grid, linewidth=0.7)
    ax.set_axisbelow(True)
    ax.set_xlabel("Area under the ROC curve (AUC)", fontsize=10, color=theme.ink_2)
    for x, label, ha in ((0.5, " coin flip", "left"), (1.0, "perfect ", "right")):
        ax.text(x, rows - 0.45, label, fontsize=9.5, color=theme.muted, ha=ha, va="top")

    colors = (theme.accent, theme.accent_2)
    # Unclipped so a dot at AUC 1.0 stays whole; the surface ring separates overlapping dots.
    dot = {
        "markersize": 11,
        "markeredgecolor": theme.surface,
        "markeredgewidth": 2,
        "clip_on": False,
    }
    phrases = []
    for row, (stem, name, source, phrase) in enumerate(RESULT_SETS):
        y = rows - 1 - row
        values = [auc[stem][model] for model, _ in RESULT_MODELS]
        ax.plot(values, [y, y], color=theme.axis, linewidth=2, zorder=2)
        # HDC's value sits above its dot and the baseline's below, so close values never collide.
        # HDC draws last, on top, since it is the model the figure is about.
        pairs = zip(values, colors, (0.17, -0.17), ("bottom", "top"), (4, 3), strict=True)
        for v, color, dy, va, z in pairs:
            ax.plot(v, y, "o", color=color, zorder=z, **dot)
            ax.text(v, y + dy, f"{v:.3f}", fontsize=10, color=theme.ink_2, ha="center", va=va)
        trans = ax.get_yaxis_transform()
        ax.text(
            -0.04,
            y + 0.05,
            name,
            transform=trans,
            fontsize=11,
            color=theme.ink,
            ha="right",
            va="bottom",
        )
        ax.text(
            -0.04,
            y - 0.05,
            source,
            transform=trans,
            fontsize=9.5,
            color=theme.muted,
            ha="right",
            va="top",
        )
        phrases.append(f"{verdict(*values)} on {phrase}")

    handles = [
        plt.Line2D(
            [], [], marker="o", linestyle="", color=c, markersize=9, markeredgecolor=theme.surface
        )
        for c in colors
    ]
    labels = [label for _, label in RESULT_MODELS]
    legend = ax.legend(
        handles, labels, loc="lower left", bbox_to_anchor=(0, 1.04), ncol=2, frameon=False
    )
    for text in legend.get_texts():
        text.set_color(theme.ink)
        text.set_fontsize(10)

    title = f"HDC {phrases[0]} and {phrases[1]}"
    subtitle = (
        "AUC of the node's C++ models on public datasets: 1.0 means every faulty recording scores\n"
        "above every normal one, and 0.5 is a coin flip"
    )
    note = "From evaluate.py --backend device. Details and caveats in docs/evaluation-results.md."
    fig.text(0.03, 0.95, title, fontsize=15, weight="bold", color=theme.ink, va="top")
    fig.text(0.03, 0.87, subtitle, fontsize=10.5, color=theme.ink_2, va="top", linespacing=1.35)
    fig.text(0.03, 0.025, note, fontsize=8.5, color=theme.muted, va="bottom")
    save(fig, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-video", action="store_true", help="skip the LinkedIn MP4")
    args = parser.parse_args()
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    SOCIAL_DIR.mkdir(parents=True, exist_ok=True)

    tl = detection_timeline()
    example = hdc_example()
    auc = load_auc()
    for theme, suffix in ((LIGHT, ""), (DARK, "-dark")):
        plot_system(theme, IMG_DIR / f"system-diagram{suffix}.png")
        plot_timeline_wide(tl, theme, IMG_DIR / f"detection-timeline{suffix}.png")
        plot_hdc_explainer(example, theme, IMG_DIR / f"hdc-explainer{suffix}.png")
        if auc is not None:
            plot_results(auc, theme, IMG_DIR / f"results{suffix}.png")
    if auc is None:
        print("Results chart skipped: run evaluate.py on mimii and mafaulda with --backend device")
    plot_social_preview(LIGHT, SOCIAL_DIR / "social-preview.png")
    plot_timeline_portrait(tl, LIGHT, SOCIAL_DIR / "detection-timeline-portrait.png")
    if args.no_video:
        pass
    elif FFMpegWriter.isAvailable():
        animate_timeline(tl, LIGHT, SOCIAL_DIR / "detection-timeline.mp4")
    else:
        print("ffmpeg is not on PATH, so the MP4 was skipped")
    print(f"README figures in {IMG_DIR}, LinkedIn and social preview files in {SOCIAL_DIR}")


if __name__ == "__main__":
    main()
