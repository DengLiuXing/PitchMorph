import numpy as np
import pytest

from app import tempo

SAMPLE_RATE = 44100


def _click_track(bpm: float, seconds: float = 20.0) -> np.ndarray:
    """每拍一个带指数衰减的噪声爆发 + 底噪，模拟"只有鼓点"的最坏情况。"""
    rng = np.random.default_rng(0)
    total = int(seconds * SAMPLE_RATE)
    data = np.zeros(total, dtype=np.float32)
    length = int(0.04 * SAMPLE_RATE)
    click = (
        rng.standard_normal(length) * np.exp(-np.arange(length) / (0.010 * SAMPLE_RATE))
    ).astype(np.float32)
    position = 0.0
    while position < seconds:
        start = int(position * SAMPLE_RATE)
        end = min(total, start + length)
        if end > start:
            data[start:end] += click[: end - start]
        position += 60.0 / bpm
    data += rng.standard_normal(total).astype(np.float32) * 0.002
    return data[None, :]


@pytest.mark.parametrize("bpm", [72, 90, 120, 138])
def test_detect_tempo_on_clicks(bpm):
    assert abs(tempo.detect_tempo(_click_track(bpm)) - bpm) < 2.0


def test_detect_tempo_rejects_silence():
    with pytest.raises(ValueError):
        tempo.detect_tempo(np.zeros((1, SAMPLE_RATE * 5), dtype=np.float32))


def test_detect_tempo_rejects_short_audio():
    with pytest.raises(ValueError):
        tempo.detect_tempo(np.zeros((1, 100), dtype=np.float32))
