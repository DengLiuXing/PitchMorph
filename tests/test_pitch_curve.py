"""F0 → 音高曲线的语义转换。核心一条：无声必须是 None，不能是 0。"""

from __future__ import annotations

import math

from app.worker.pitch import to_curve


def test_nan_becomes_none():
    assert to_curve([float("nan"), 440.0]) == [None, 440.0]


def test_zero_becomes_none():
    # 0 Hz 在前端会被当成有效低音，画出一条假线
    assert to_curve([0.0, 440.0]) == [None, 440.0]


def test_negative_becomes_none():
    assert to_curve([-1.0]) == [None]


def test_infinity_becomes_none():
    assert to_curve([math.inf, -math.inf]) == [None, None]


def test_values_are_rounded_to_three_decimals():
    assert to_curve([439.98765]) == [439.988]


def test_empty_input():
    assert to_curve([]) == []
