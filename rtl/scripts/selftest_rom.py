"""Write the ROM for rtl/src/hdc_selftest.v: windows and the scores FixedHdc gives them."""

import argparse
from pathlib import Path

import numpy as np

from mhm.fixed.hdc import FixedHdc, level_thresholds, to_fixed

SEED = 0x1234ABCD
N_LEVELS = 16


def rom_words(dim: int, nf_max: int, n_train: int, n_test: int, corrupt: bool) -> list[int]:
    """Header (features, training windows, test windows), then data in the order it is read."""
    rng = np.random.default_rng(2026)
    n = nf_max
    scale = rng.uniform(0.01, 30.0, n)
    offset = rng.uniform(-200.0, 200.0, n)
    train = rng.normal(size=(n_train, n)) * scale + offset
    test = rng.normal(size=(n_test, n)) * scale + offset
    test[1, : n // 2] += 2.5 * scale[: n // 2]  # anomalous
    test[-1] = offset - 1e6  # below every level edge, saturating to INT32_MIN
    thresholds = level_thresholds(offset - 2 * scale, 4 * scale, N_LEVELS)

    model = FixedHdc(thresholds, dim=dim, seed=SEED, n_ids=nf_max)
    train, test = to_fixed(train), to_fixed(test)
    model.bundle(train)
    model.finish()
    expected = model.score(test)
    if corrupt:
        expected[-1] ^= 1

    words = [n, n_train, n_test, *thresholds.ravel(), *train.ravel()]
    for window, score in zip(test, expected, strict=True):
        words += [*window, score]
    return [int(w) & 0xFFFFFFFF for w in words]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="hex file for $readmemh")
    parser.add_argument("--dim", type=int, default=4096)
    parser.add_argument("--nf-max", type=int, default=45)
    parser.add_argument("--train", type=int, default=4, help="an even count gives prototype ties")
    parser.add_argument("--test", type=int, default=3)
    parser.add_argument("--rom-words", type=int, default=1024, help="hdc_selftest's 2^ROM_AW")
    parser.add_argument("--corrupt", action="store_true", help="flip a bit of the last score")
    args = parser.parse_args()

    words = rom_words(args.dim, args.nf_max, args.train, args.test, args.corrupt)
    if len(words) > args.rom_words:
        parser.error(f"{len(words)} words do not fit a {args.rom_words}-word ROM")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    padded = words + [0] * (args.rom_words - len(words))
    args.out.write_text("".join(f"{w:08x}\n" for w in padded))


if __name__ == "__main__":
    main()
