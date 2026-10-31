"""音频工具单测：解码、重采样、峰值、静音判定。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import audio

SAMPLES = Path(__file__).resolve().parent.parent / "temp-dev" / "samples" / "vocadito"


def _tone(seconds: float = 1.0, rate: int = 44100) -> np.ndarray:
    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    wave = 0.5 * np.sin(2 * np.pi * 440.0 * t)
    return np.stack([wave, wave])  # (2, N)


def test_resample_mono_changes_rate() -> None:
    source = _tone(2.0)
    mono = audio.resample_mono(source, 44100, 16000)
    assert mono.shape[0] == 1
    assert abs(mono.shape[1] - 32000) <= 64


def test_wave_peaks_bucket_count() -> None:
    source = _tone(1.0)
    peaks = audio.wave_peaks(source, 500)
    assert len(peaks["min"]) == len(peaks["max"]) == 500
    assert max(peaks["max"]) > 0.4


def test_is_silent() -> None:
    assert audio.is_silent(np.zeros((2, 1000), dtype=np.float32))
    assert not audio.is_silent(_tone(0.1))


def test_write_and_read_roundtrip(tmp_path: Path) -> None:
    source = _tone(0.5)
    path = audio.write_wav(tmp_path / "x.wav", source)
    loaded, rate = audio.read_wav(path)
    assert rate == 44100
    assert loaded.shape == source.shape
    assert np.allclose(loaded[:, :1000], source[:, :1000], atol=1e-3)


@pytest.mark.skipif(not SAMPLES.exists(), reason="缺少 vocadito 样例素材")
def test_decode_real_file() -> None:
    data = audio.decode_to_stereo(SAMPLES / "Audio" / "vocadito_1.wav")
    assert data.shape[0] == 2
    assert data.dtype == np.float32
    assert data.shape[1] > 44100
