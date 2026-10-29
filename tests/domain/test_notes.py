"""曲线 → 音符的切分测试。全部用合成数据。"""

from __future__ import annotations

import random

import pytest

from app.domain.curve import PitchCurve
from app.domain.notes import Note, SegmentParams, segment

HOP = 0.01


def _curve(values: list[float | None], hop_sec: float = HOP) -> PitchCurve:
    return PitchCurve(hop_sec=hop_sec, f0=tuple(values))


def _steady(hz: float, frames: int) -> list[float | None]:
    return [hz] * frames


def test_segment_single_steady_tone():
    notes = segment(_curve(_steady(440.0, 100)))
    assert len(notes) == 1
    note = notes[0]
    assert note.midi == 69
    assert abs(note.cents_offset) <= 1.0
    assert note.start_sec == pytest.approx(0.0)
    assert note.dur_sec == pytest.approx(1.0)


def test_segment_three_steady_notes():
    values: list[float | None] = []
    values += _steady(440.0, 50)  # 0.5 s
    values += [None] * 10  # 0.1 s 静音
    values += _steady(523.25, 50)
    values += [None] * 10
    values += _steady(659.25, 50)

    notes = segment(_curve(values))
    assert len(notes) == 3
    assert [n.midi for n in notes] == [69, 72, 76]
    assert notes[0].start_sec == pytest.approx(0.0)
    assert notes[1].start_sec == pytest.approx(0.6)
    assert notes[2].start_sec == pytest.approx(1.2)
    for note in notes:
        assert abs(note.cents_offset) <= 1.0


def test_segment_all_none_gives_empty():
    assert segment(_curve([None] * 50)) == []


def test_segment_empty_curve():
    assert segment(_curve([])) == []


def test_segment_drops_short_fragment():
    # 0.5 s 的 440，中间插 40 ms 的 880（音高不同 → 不并入，只能被丢弃）
    values = _steady(440.0, 50) + _steady(880.0, 4) + _steady(440.0, 50)
    notes = segment(_curve(values))
    assert all(n.midi != 81 for n in notes)
    assert len(notes) == 2
    assert [n.midi for n in notes] == [69, 69]


def test_segment_keeps_same_semitone_together():
    # -40 → +45 音分（85 音分的抖动）仍落在同一个半音里，不该切成两个音符。
    # 这是 v2.1 的改动：v2.0 按「相邻帧差 > 80」切，会把这种颤音判成换音。
    low = 440.0 * 2 ** (-40 / 1200)
    high = 440.0 * 2 ** (45 / 1200)
    values = _steady(low, 50) + _steady(high, 4) + _steady(low, 50)
    notes = segment(_curve(values))
    assert len(notes) == 1
    assert notes[0].midi == 69
    assert notes[0].start_sec == pytest.approx(0.0)
    assert notes[0].dur_sec == pytest.approx(1.04)


def test_segment_notes_do_not_overlap():
    values = _steady(440.0, 50) + [None] * 10 + _steady(523.25, 50)
    notes = segment(_curve(values))
    for previous, current in zip(notes, notes[1:]):
        assert current.start_sec >= previous.start_sec + previous.dur_sec


def test_segment_drops_out_of_range_midi():
    # 5 Hz 远低于最低音，midi 号越界必须丢弃而不是截断
    notes = segment(_curve(_steady(5.0, 100)))
    assert notes == []


def test_segment_params_defaults():
    params = SegmentParams()
    assert params.min_note_sec == 0.12
    assert params.smooth_window == 5


def test_segment_uses_jump_cents_to_split():
    # 100 音分（半音）以上视为换音：同一个 voiced 段内应切成两个音
    values = _steady(440.0, 50) + _steady(440.0 * 2 ** (100 / 1200), 50)
    notes = segment(_curve(values))
    assert len(notes) == 2
    assert notes[0].midi == 69
    assert notes[1].midi == 70


def test_segment_returns_notes_dataclass():
    notes = segment(_curve(_steady(440.0, 100)))
    assert isinstance(notes[0], Note)
    assert notes[0].start_sec <= notes[0].start_sec + notes[0].dur_sec


def test_segment_invariants_hold_on_random_input():
    # 随机曲线上的不变量：升序、零重叠、midi 合法、偏差在 (-50, 50]
    rng = random.Random(20240101)
    for _ in range(50):
        length = rng.randint(0, 300)
        values: list[float | None] = []
        for _ in range(length):
            if rng.random() < 0.2:
                values.append(None)
            else:
                values.append(rng.uniform(80.0, 1100.0))
        notes = segment(_curve(values))
        for note in notes:
            assert 0 <= note.midi <= 127
            assert -50.0 < note.cents_offset <= 50.0
            assert note.dur_sec > 0.0
        starts = [n.start_sec for n in notes]
        assert starts == sorted(starts)
        for previous, current in zip(notes, notes[1:]):
            assert current.start_sec >= previous.start_sec + previous.dur_sec
