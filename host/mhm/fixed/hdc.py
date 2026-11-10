"""Fixed-point binary HDC, the bit-exact reference for rtl/src/hdc_core.v."""

from dataclasses import dataclass, field
from typing import Self

import numpy as np

from mhm.models.hdc import THRESHOLD_BITS, HdcScorer, ItemMemory, item_memory

FRAC_BITS = 16  # features and thresholds are signed Q16.16 in 32 bits
INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1
MAX_BUNDLED = (1 << 16) - 1  # the RTL counts votes and bundled windows in 16 bits


def to_fixed(x: np.ndarray | float) -> np.ndarray:
    """Round to signed Q16.16 and saturate to 32 bits, returned as int64."""
    scaled = np.round(np.asarray(x, np.float64) * (1 << FRAC_BITS))
    return np.clip(scaled, INT32_MIN, INT32_MAX).astype(np.int64)


def level_thresholds(lo: np.ndarray, width: np.ndarray, n_levels: int) -> np.ndarray:
    """Q16.16 lower edges of levels 1 to n_levels - 1, shape (n_features, n_levels - 1)."""
    k = np.arange(1, n_levels)
    lo, width = np.asarray(lo, np.float64), np.asarray(width, np.float64)
    return to_fixed(lo[:, None] + k[None, :] * width[:, None] / n_levels)


def level_flips(thresholds: np.ndarray, level: int, n_levels: int) -> np.ndarray:
    """The RTL's divide-free form of `thresholds < level * 2^15 // (n_levels - 1)`."""
    return (thresholds + 1) * (n_levels - 1) <= level << (THRESHOLD_BITS - 1)


def majority(ones: np.ndarray, votes: int, tiebreak: np.ndarray) -> np.ndarray:
    """1 where more than half the votes are 1; exact ties take the tiebreak bit."""
    return np.where(2 * ones == votes, tiebreak, 2 * ones > votes).astype(np.uint8)


@dataclass
class FixedHdc:
    """Binary HDC on Q16.16 features: compare-only quantizer, XOR binding, majority, Hamming."""

    thresholds: np.ndarray  # (n_features, n_levels - 1) Q16.16 level edges, ascending per feature
    dim: int = 4096
    seed: int = 0x1234ABCD
    n_ids: int | None = None  # IDs drawn from the LFSR (the RTL's NF_MAX); defaults to n_features
    memory: ItemMemory = field(init=False, repr=False)
    votes: np.ndarray = field(init=False, repr=False)
    n_bundled: int = field(init=False, default=0)
    prototype: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.thresholds = np.asarray(self.thresholds, np.int64)
        n_ids = self.n_ids or self.n_features
        if n_ids < self.n_features:
            raise ValueError("n_ids must cover every feature")
        self.memory = item_memory(self.seed, self.dim, self.n_levels, n_ids)
        self.clear()

    @property
    def n_features(self) -> int:
        return self.thresholds.shape[0]

    @property
    def n_levels(self) -> int:
        return self.thresholds.shape[1] + 1

    @classmethod
    def from_scorer(cls, scorer: HdcScorer, n_ids: int | None = None) -> Self:
        """Take the quantizer ranges, dimension and seed from a fitted float scorer."""
        thresholds = level_thresholds(scorer.lo, scorer.width, scorer.n_levels)
        return cls(thresholds, dim=scorer.dim, seed=scorer.seed, n_ids=n_ids)

    def clear(self) -> None:
        self.votes = np.zeros(self.dim, np.int64)
        self.n_bundled = 0

    def quantize(self, x: np.ndarray) -> np.ndarray:
        """Each feature's level: how many of its thresholds the Q16.16 value reaches."""
        return (np.asarray(x, np.int64)[..., None] >= self.thresholds).sum(axis=-1)

    def encode(self, x: np.ndarray) -> np.ndarray:
        """(n, n_features) Q16.16 windows to (n, dim) majority bits of the bound feature vectors."""
        q = self.quantize(np.atleast_2d(x))
        ones = np.zeros((len(q), self.dim), np.int64)
        for f in range(self.n_features):
            ones += self.memory.ids[f] ^ self.memory.levels[q[:, f]]
        return majority(ones, self.n_features, self.memory.tiebreak)

    def bundle(self, x: np.ndarray) -> None:
        encoded = self.encode(x)
        if self.n_bundled + len(encoded) > MAX_BUNDLED:
            raise ValueError(f"the RTL's 16-bit counters hold at most {MAX_BUNDLED} windows")
        self.votes += encoded.sum(axis=0, dtype=np.int64)
        self.n_bundled += len(encoded)

    def finish(self) -> None:
        self.prototype = majority(self.votes, self.n_bundled, self.memory.tiebreak)

    def score(self, x: np.ndarray) -> np.ndarray:
        """Hamming distance to the prototype in bits, 0 to dim, as the RTL reports it."""
        return np.count_nonzero(self.encode(x) != self.prototype, axis=1)
