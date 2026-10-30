"""起音检测的单测。全部用合成信号，不读真实音频、不 import torch。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.exports import load_onsets
from app.worker import onsets

SR = onsets.SR
HOP = onsets.HOP


def _bursts(
    *,
    burst_samples: tuple[int, ...] = (),
    seconds: float = 3.0,
    noise: float = 0.02,
    level: float = 0.9,
) -> np.ndarray:
    """白噪声底 + 若干段 700 Hz 短音，模拟辅音起音。"""
    rng = np.random.default_rng(0)
    x = (noise * rng.standard_normal(int(SR * seconds))).astype(np.float32)
    tone = np.arange(400) / SR
    for position in burst_samples:
        x[position : position + 400] = level * np.sin(2 * np.pi * 700 * tone)
    return x


def test_silence_has_no_onsets():
    assert onsets.detect_onsets(np.zeros(SR, dtype=np.float32)) == []


def test_short_input_has_no_onsets():
    # 不足一帧（512 点）时不能崩，也不能编造
    assert onsets.detect_onsets(np.zeros(100, dtype=np.float32)) == []


def test_steady_sine_has_no_onsets():
    # 持续正弦只有开头一次阶跃，之后没有起音；不能把 440 Hz 判成一串音符
    tone = np.sin(2 * np.pi * 440 * np.arange(SR) / SR).astype(np.float32)
    assert onsets.detect_onsets(tone) == []


def test_detects_burst_positions():
    positions = (SR // 2, SR, 2 * SR)
    found = onsets.detect_onsets(_bursts(burst_samples=positions))
    expected = [position // HOP for position in positions]
    assert len(found) == len(expected)
    # 512 点分析窗有前视效应，峰比真实起音早 1~3 帧
    for frame, target in zip(found, expected):
        assert abs(frame - target) <= 3


def test_min_gap_merges_close_peaks():
    # 两段挨得很近的爆发：间隔放宽到 50 帧后只能留一个较强者
    close = _bursts(burst_samples=(SR // 2, SR // 2 + 480))
    narrow = onsets.detect_onsets(close)
    assert len(narrow) == 2
    assert len(onsets.detect_onsets(close, min_gap_frames=50)) == 1


def test_output_is_sorted_unique_and_deterministic():
    samples = _bursts(burst_samples=(SR // 2, SR, 2 * SR))
    first = onsets.detect_onsets(samples)
    second = onsets.detect_onsets(samples)
    assert first == second
    assert first == sorted(set(first))


def test_payload_clips_sorts_and_dedupes():
    payload = onsets.to_payload([5, 3, 3, 9, -1, 999], 8)
    assert payload["onsets"] == [3, 5]
    assert payload["frames"] == 8
    assert payload["hop_sec"] == pytest.approx(0.01)
    assert payload["params"]["min_gap_ms"] == 60


def test_load_onsets_missing_file():
    assert load_onsets(Path("does-not-exist")) is None


def test_load_onsets_rejects_broken_payload(tmp_path: Path):
    features = tmp_path / "features"
    features.mkdir()
    path = features / "onset.json"

    path.write_text("{ not json", encoding="utf-8")
    assert load_onsets(tmp_path) is None

    path.write_text(json.dumps({"onsets": [1, 2]}), encoding="utf-8")
    assert load_onsets(tmp_path) is None  # 缺 frames

    path.write_text(json.dumps({"frames": 4, "onsets": [3, 1, 9, "x", True, -2]}), encoding="utf-8")
    assert load_onsets(tmp_path) == [3, 1]  # 越界、非整数、bool 一律丢掉
