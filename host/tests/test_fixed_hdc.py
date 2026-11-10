import numpy as np
import pytest

from mhm.fixed.hdc import (
    FRAC_BITS,
    INT32_MAX,
    FixedHdc,
    level_flips,
    level_thresholds,
    to_fixed,
)
from mhm.models.hdc import THRESHOLD_BITS, HdcScorer

N_FEATURES = 12


@pytest.fixture(scope="module")
def data() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(3)
    scale = rng.uniform(0.01, 50.0, N_FEATURES)
    offset = rng.uniform(-100.0, 100.0, N_FEATURES)
    train = rng.normal(size=(400, N_FEATURES)) * scale + offset
    test = rng.normal(size=(200, N_FEATURES)) * scale + offset
    test[100:, :4] += 3 * scale[:4]
    return train, test


def test_to_fixed_rounds_and_saturates():
    assert to_fixed(1.5).item() == 3 << (FRAC_BITS - 1)
    assert to_fixed(-0.25).item() == -(1 << (FRAC_BITS - 2))
    assert to_fixed(1e9).item() == INT32_MAX


def test_level_flips_match_the_division():
    thresholds = np.arange(1 << THRESHOLD_BITS)
    for n_levels in (4, 16):
        for level in range(n_levels):
            cut = level * (1 << (THRESHOLD_BITS - 1)) // (n_levels - 1)
            assert np.array_equal(level_flips(thresholds, level, n_levels), thresholds < cut)


def test_compare_quantizer_matches_float(data):
    train, test = data
    scorer = HdcScorer(range_sigma=2.0).fit_quantizer(train)
    fixed = FixedHdc.from_scorer(scorer)
    float_levels, fixed_levels = scorer.quantize(test), fixed.quantize(to_fixed(test))
    # Only a value within an LSB or so of a level edge may land on the other side.
    differs = float_levels != fixed_levels
    assert differs.mean() < 1e-3
    edges = fixed.thresholds[np.nonzero(differs)[1]]
    near = np.abs(to_fixed(test)[differs][:, None] - edges).min(axis=1, initial=0)
    assert np.all(near <= 2)


@pytest.mark.parametrize("n_features", [N_FEATURES, N_FEATURES - 1])
def test_scores_match_float_binary(data, n_features: int):
    train, test = data[0][:, :n_features], data[1][:, :n_features]
    scorer = HdcScorer(dim=1024, range_sigma=2.0).fit(train)
    fixed = FixedHdc.from_scorer(scorer)
    fixed.bundle(to_fixed(train))
    fixed.finish()
    assert np.array_equal(fixed.prototype, scorer.prototype)
    same = np.all(scorer.quantize(test) == fixed.quantize(to_fixed(test)), axis=1)
    assert same.mean() > 0.95
    expected = np.round(scorer.score(test) * scorer.dim).astype(np.int64)
    assert np.array_equal(fixed.score(to_fixed(test))[same], expected[same])


def test_spare_ids_leave_scores_unchanged(data):
    train, test = data
    thresholds = level_thresholds(train.mean(0) - 2 * train.std(0), 4 * train.std(0), 16)
    exact, padded = FixedHdc(thresholds, dim=512), FixedHdc(thresholds, dim=512, n_ids=45)
    for model in (exact, padded):
        model.bundle(to_fixed(train))
        model.finish()
    assert np.array_equal(exact.score(to_fixed(test)), padded.score(to_fixed(test)))


def test_counters_are_bounded():
    model = FixedHdc(np.zeros((2, 15), np.int64), dim=64)
    model.n_bundled = (1 << 16) - 1
    with pytest.raises(ValueError):
        model.bundle(np.zeros((1, 2), np.int64))
