import numpy as np
import pytest

from app import game_decode


def test_d3pm_time_schedule():
    assert game_decode.d3pm_time_schedule(0.0) == pytest.approx(1.0)
    assert game_decode.d3pm_time_schedule(1.0) == pytest.approx(0.0)
    assert game_decode.d3pm_time_schedule(0.5) == pytest.approx(0.5)


def test_remove_mutable_boundaries_keeps_immutable():
    boundaries = np.array([True, True, True, False])
    immutable = np.array([False, True, False, False])
    rng = np.random.default_rng(0)
    for _ in range(20):
        remain = game_decode.remove_mutable_boundaries(boundaries, immutable, 1.0, rng)
        assert remain[1]  # 不可变边界必须留下


def test_decode_soft_boundaries_picks_local_maxima():
    # 上游用 +inf 填充窗口两端，所以贴着边界的极大值不算（与上游行为一致）
    probs = np.array([[0.1, 0.2, 0.9, 0.2, 0.3, 0.95, 0.1, 0.1]])
    mask = np.ones_like(probs, dtype=bool)
    points = game_decode.decode_soft_boundaries(probs, None, mask, threshold=0.5, radius=2)
    assert points[0, 2]
    assert points[0, 5]
    assert not points[0, 0]


def test_decode_soft_boundaries_respects_threshold():
    probs = np.array([[0.4, 0.45, 0.4]])
    mask = np.ones_like(probs, dtype=bool)
    points = game_decode.decode_soft_boundaries(probs, None, mask, threshold=0.5, radius=2)
    assert not points.any()


def test_decode_soft_boundaries_keeps_shape():
    probs = np.random.default_rng(1).random((2, 37))
    mask = np.ones_like(probs, dtype=bool)
    points = game_decode.decode_soft_boundaries(probs, None, mask, 0.5, 2)
    assert points.shape == probs.shape


def test_gaussian_blurred_probs_peaks_at_bin():
    # 257 个 bin 覆盖 0-128 半音，步长 0.5：bin 138 才是 MIDI 69
    probs = np.zeros((1, 257))
    probs[0, 138] = 1.0
    values, presence = game_decode.decode_gaussian_blurred_probs(probs, 0.0, 128.0, 1.5, 0.2)
    assert values[0] == pytest.approx(69.0, abs=1.0)
    assert bool(presence[0])


def test_gaussian_blurred_probs_presence_threshold():
    probs = np.zeros((1, 257))
    probs[0, 60] = 0.1
    _, presence = game_decode.decode_gaussian_blurred_probs(probs, 0.0, 128.0, 1.5, 0.2)
    assert not bool(presence[0])


def test_regions_round_trip():
    boundaries = np.array([[False, True, False, False, True, False]])
    regions = game_decode.boundaries_to_regions(boundaries)
    assert list(regions[0]) == [1, 2, 2, 2, 3, 3]
    durations = game_decode.regions_to_durations(regions)
    assert durations.shape == (1, 3)
    assert durations.sum() == boundaries.shape[-1]
