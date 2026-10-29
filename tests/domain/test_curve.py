"""音高曲线解析与音分工具的测试。全部用合成数据，不依赖 torch 与真实音频。"""

from __future__ import annotations

import math
import re

import pytest

from app.domain.curve import (
    PitchCurve,
    fix_octave_jumps,
    median_smooth,
    to_cents,
)


def _payload(f0: list[float | None], hop_sec: float = 0.01) -> dict:
    return {
        "version": 1,
        "model": "fcpe",
        "sample_rate": 16000,
        "hop_sec": hop_sec,
        "frames": len(f0),
        "f0": f0,
    }


def test_from_payload_reads_fields():
    curve = PitchCurve.from_payload(_payload([440.0, None, 220.0]))
    assert curve.hop_sec == 0.01
    assert curve.f0 == (440.0, None, 220.0)
    assert curve.frames == 3


def test_from_payload_rejects_missing_field():
    payload = _payload([440.0])
    del payload["hop_sec"]
    with pytest.raises(ValueError):
        PitchCurve.from_payload(payload)


def test_from_payload_rejects_frames_mismatch():
    payload = _payload([440.0, 441.0])
    payload["frames"] = 5
    with pytest.raises(ValueError):
        PitchCurve.from_payload(payload)


def test_from_payload_rejects_negative_value():
    with pytest.raises(ValueError):
        PitchCurve.from_payload(_payload([440.0, -1.0]))


def test_from_payload_rejects_bad_hop():
    with pytest.raises(ValueError):
        PitchCurve.from_payload(_payload([440.0], hop_sec=0.0))


def test_to_payload_round_trip():
    payload = _payload([440.0, None, 880.0], hop_sec=0.01)
    curve = PitchCurve.from_payload(payload)
    out = curve.to_payload()
    assert out["f0"] == payload["f0"]
    assert out["hop_sec"] == payload["hop_sec"]
    assert out["frames"] == payload["frames"]
    assert out["model"] == "fcpe"
    assert out["sample_rate"] == 16000
    # 再解析一次仍然一致
    assert PitchCurve.from_payload(out).f0 == curve.f0


def test_to_cents_reference_points():
    cents = to_cents((440.0, 880.0, None))
    assert cents[0] == pytest.approx(0.0)
    assert cents[1] == pytest.approx(1200.0)
    assert cents[2] is None


def test_to_cents_halfway():
    cents = to_cents((440.0 * 2 ** (0.5),))
    assert cents[0] == pytest.approx(600.0)


def test_median_smooth_removes_isolated_spike():
    values = (440.0, 440.0, 880.0, 440.0, 440.0)
    smoothed = median_smooth(values, window=3)
    assert smoothed[2] == pytest.approx(440.0)
    assert smoothed[0] == pytest.approx(440.0)
    assert smoothed[4] == pytest.approx(440.0)


def test_median_smooth_keeps_all_none():
    assert median_smooth((None, None, None), window=3) == (None, None, None)


def test_median_smooth_ignores_none_in_window():
    # None 不参与中位数：窗口里只剩两个 440，中位数仍是 440（不会掉到 0）
    smoothed = median_smooth((440.0, None, 440.0), window=3)
    assert smoothed[0] == pytest.approx(440.0)
    assert smoothed[1] == pytest.approx(440.0)
    assert smoothed[2] == pytest.approx(440.0)


def test_median_smooth_rejects_even_window():
    with pytest.raises(ValueError):
        median_smooth((440.0,), window=2)


def test_fix_octave_jumps_pulls_isolated_frame_back():
    values = (440.0, 440.0, 880.0, 440.0, 440.0)
    fixed = fix_octave_jumps(values)
    assert fixed[2] == pytest.approx(440.0, rel=1e-3)
    assert fixed[0] == pytest.approx(440.0)


def test_fix_octave_jumps_keeps_real_octave_run():
    values = (
        440.0, 440.0, 440.0,
        880.0, 880.0, 880.0,
        440.0, 440.0, 440.0,
    )
    fixed = fix_octave_jumps(values)
    assert fixed[3] == pytest.approx(880.0)
    assert fixed[4] == pytest.approx(880.0)
    assert fixed[5] == pytest.approx(880.0)


def test_fix_octave_jumps_keeps_none():
    values = (None, 440.0, 880.0, 440.0, None)
    fixed = fix_octave_jumps(values)
    assert fixed[0] is None
    assert fixed[4] is None


def test_duration_and_frames():
    curve = PitchCurve(hop_sec=0.01, f0=(440.0, 440.0, None))
    assert curve.frames == 3
    assert curve.duration_sec == pytest.approx(0.03)


def test_no_torch_dependency():
    import app.domain.curve as module

    with open(module.__file__, encoding="utf-8") as handle:
        text = handle.read()
    # 只允许文档里提到 torch，不允许真的 import
    assert re.search(r"^\s*(import torch|from torch)", text, re.MULTILINE) is None
    assert math.isfinite(1.0)
