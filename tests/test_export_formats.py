import io
import math

import mido
import pytest
import yaml

from app import export_formats

# 音分相对 A4=440；MIDI 60/62 对应 -900 / -700 音分，曲线围绕各自音符小幅摆动
CENTS = [-900.0, -888.0, -876.0, None, -870.0, -682.0, -694.0, -700.0, -706.0, -712.0]
NOTES = [
    {"start_sec": 0.0, "dur_sec": 0.1, "midi": 60, "cents_offset": 0.0},
    {"start_sec": 0.1, "dur_sec": 0.1, "midi": 62, "cents_offset": 0.0},
]


def test_ustx_structure():
    text = export_formats.to_ustx(CENTS, NOTES, hop_sec=0.01, bpm=120.0)
    data = yaml.safe_load(text)
    assert data["tempos"][0]["position"] == 0
    assert data["tempos"][0]["bpm"] == 120
    assert data["time_signatures"][0]["bar_position"] == 0
    part = data["voice_parts"][0]
    assert part["track_no"] == 0
    assert len(part["notes"]) == 2
    for note in part["notes"]:
        assert note["lyric"] == "a"
        assert note["tuning"] == 0
        assert len(note["pitch"]["data"]) >= 2
    curves = part["curves"]
    assert len(curves) == 1
    assert curves[0]["abbr"] == "pitd"
    assert len(curves[0]["xs"]) == len(curves[0]["ys"])


def test_pitd_suppresses_frame_jitter():
    """±40 音分的逐帧锯齿（FCPE 抖动）必须被压平，否则导入 OpenUtau 会颤抖。"""
    cents = [-900.0 + (40.0 if index % 2 else -40.0) for index in range(40)]
    notes = [{"start_sec": 0.0, "dur_sec": 0.4, "midi": 60, "cents_offset": 0.0}]
    xs, ys = export_formats._pitd_curve(cents, notes, hop_sec=0.01, bpm=120.0)
    # 压平之后共线简化会把中间点全去掉，只剩起止附近几个点
    assert xs[-1] - xs[0] > 200
    assert max(abs(y) for y in ys) < 20


def test_pitd_keeps_vibrato():
    """颤音（周期 170 ms）不能被低通抹掉。"""
    cents = [-900.0 + 50.0 * math.sin(2 * math.pi * index * 0.01 / 0.17) for index in range(60)]
    notes = [{"start_sec": 0.0, "dur_sec": 0.6, "midi": 60, "cents_offset": 0.0}]
    _, ys = export_formats._pitd_curve(cents, notes, hop_sec=0.01, bpm=120.0)
    assert max(ys) - min(ys) > 55


def _sample(xs, ys, x):
    """沿导出的折线取某个 tick 上的 y。"""
    for index in range(len(xs) - 1):
        if xs[index] <= x <= xs[index + 1]:
            ratio = (x - xs[index]) / (xs[index + 1] - xs[index])
            return ys[index] + (ys[index + 1] - ys[index]) * ratio
    raise AssertionError(f"x={x} 落在线外")


def test_pitd_fills_unvoiced_gap_inside_note():
    """音符内的清音帧要线性补齐，否则 pitd 断成碎片、OpenUtau 用直线跨过缺口。"""
    # 前 10 帧 -900（=MIDI 60），中间 5 帧清音，后 10 帧 -880
    cents = [-900.0] * 10 + [None] * 5 + [-880.0] * 10
    notes = [{"start_sec": 0.0, "dur_sec": 0.25, "midi": 60, "cents_offset": 0.0}]
    xs, ys = export_formats._pitd_curve(cents, notes, hop_sec=0.01, bpm=120.0)
    # 第 12 帧（缺口中间）插值后应落在 0 与 +20 音分之间，而不是被丢掉
    assert 0.0 < _sample(xs, ys, 115) < 20.0


def test_interpolate_gaps_extends_edges():
    filled = export_formats._interpolate_gaps([None, -900.0, None, None, -800.0, None])
    assert filled == pytest.approx([-900.0, -900.0, -866.67, -833.33, -800.0, -800.0], abs=0.01)


def test_ustx_rejects_bad_bpm():
    with pytest.raises(ValueError):
        export_formats.to_ustx(CENTS, NOTES, hop_sec=0.01, bpm=0)


def test_midi_has_pitch_bend():
    data = export_formats.to_midi(CENTS, NOTES, hop_sec=0.01, bpm=120.0)
    midi = mido.MidiFile(file=io.BytesIO(data))
    messages = [message for track in midi.tracks for message in track]
    kinds = {message.type for message in messages}
    assert "note_on" in kinds
    assert "note_off" in kinds
    assert "pitchwheel" in kinds
    assert "control_change" in kinds
    tempo = [m for m in messages if m.type == "set_tempo"]
    assert tempo and abs(mido.tempo2bpm(tempo[0].tempo) - 120.0) < 0.01


def test_midi_pitch_bend_within_range():
    data = export_formats.to_midi(CENTS, NOTES, hop_sec=0.01, bpm=120.0)
    midi = mido.MidiFile(file=io.BytesIO(data))
    for track in midi.tracks:
        for message in track:
            if message.type == "pitchwheel":
                assert -8192 <= message.pitch <= 8191
