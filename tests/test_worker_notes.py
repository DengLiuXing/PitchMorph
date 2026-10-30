"""`_CappedSlicer` 的约束：不超长、不丢样点、offset 连续、切点落在安静处。

它只依赖 numpy 与传入的 slicer，所以不用 GPU、不用权重就能测。
"""

import numpy as np
import pytest

from app.worker.notes import _CappedSlicer

SR = 16000


class _OneChunk:
    """假 slicer：永远返回一整段，模拟「整首歌没有静音可切」。"""

    def __init__(self, samples: np.ndarray, offset: float = 0.0) -> None:
        self._samples = samples
        self._offset = offset

    def slice(self, waveform):  # noqa: ANN001, ANN201
        return [{"waveform": self._samples, "offset": self._offset}]


def _silence_then_tone(seconds: float) -> np.ndarray:
    """前 90% 是恒定音量，最后一段是静音——切点应该被吸到静音处。"""
    total = int(seconds * SR)
    data = np.full(total, 0.5, dtype=np.float32)
    data[int(total * 0.9) :] = 0.0
    return data


def test_short_chunk_passes_through_untouched():
    data = _silence_then_tone(10.0)
    slicer = _CappedSlicer(_OneChunk(data), sr=SR, max_sec=30.0)
    pieces = slicer.slice(data)
    assert len(pieces) == 1
    assert pieces[0]["offset"] == pytest.approx(0.0)
    assert len(pieces[0]["waveform"]) == len(data)


def test_long_chunk_is_split_and_covers_everything():
    data = _silence_then_tone(70.0)
    slicer = _CappedSlicer(_OneChunk(data, offset=5.0), sr=SR, max_sec=30.0)
    pieces = slicer.slice(data)

    assert len(pieces) >= 3
    assert sum(len(piece["waveform"]) for piece in pieces) == len(data)
    for piece in pieces:
        assert len(piece["waveform"]) <= 30 * SR
    # offset 必须首尾相接，否则拼回全局时间轴会错位
    for previous, current in zip(pieces, pieces[1:]):
        expected = previous["offset"] + len(previous["waveform"]) / SR
        assert current["offset"] == pytest.approx(expected)


def test_cut_prefers_quiet_region():
    data = _silence_then_tone(40.0)
    slicer = _CappedSlicer(_OneChunk(data), sr=SR, max_sec=30.0)
    pieces = slicer.slice(data)
    # 唯一的静音段在 36 s 之后，但 30 s 上限要求更早切；这里只要求没切在开头
    assert len(pieces[0]["waveform"]) > 20 * SR
