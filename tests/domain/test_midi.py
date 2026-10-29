"""音符 → MIDI 字节的测试。合成数据，不落盘。"""

from __future__ import annotations

import io

import pretty_midi
import pytest

from app.domain.curve import PitchCurve
from app.domain.midi import to_bend_midi, to_note_midi
from app.domain.notes import Note


def _curve(values: list[float | None], hop_sec: float = 0.01) -> PitchCurve:
    return PitchCurve(hop_sec=hop_sec, f0=tuple(values))


def _read(data: bytes) -> pretty_midi.PrettyMIDI:
    return pretty_midi.PrettyMIDI(io.BytesIO(data))


def test_to_note_midi_round_trip():
    notes = [
        Note(start_sec=0.0, dur_sec=0.5, midi=69, cents_offset=0.0),
        Note(start_sec=0.6, dur_sec=0.5, midi=72, cents_offset=0.0),
    ]
    data = to_note_midi(notes)
    assert isinstance(data, bytes)
    midi = _read(data)
    written = midi.instruments[0].notes
    assert len(written) == 2
    assert [n.pitch for n in written] == [69, 72]
    assert written[0].start == pytest.approx(0.0, abs=1e-3)
    assert written[0].end == pytest.approx(0.5, abs=1e-3)
    assert written[1].start == pytest.approx(0.6, abs=1e-3)


def test_to_note_midi_writes_tempo_and_program():
    data = to_note_midi([Note(0.0, 0.5, 69, 0.0)], bpm=100.0, program=54)
    midi = _read(data)
    assert midi.get_tempo_changes()[1][0] == pytest.approx(100.0, abs=1e-3)
    assert midi.instruments[0].program == 54


def test_to_note_midi_velocity_is_100():
    data = to_note_midi([Note(0.0, 0.5, 69, 0.0)])
    midi = _read(data)
    assert midi.instruments[0].notes[0].velocity == 100


def test_to_note_midi_empty_notes_is_valid():
    data = to_note_midi([])
    assert isinstance(data, bytes)
    midi = _read(data)  # 不抛异常即合法
    # pretty_midi 读回时会丢掉没有任何音符的乐器，这里只关心「没音符、没崩」
    assert all(len(i.notes) == 0 for i in midi.instruments)


def test_to_bend_midi_has_pitch_bend_events():
    curve = _curve([440.0] * 100)
    notes = [Note(start_sec=0.0, dur_sec=1.0, midi=69, cents_offset=0.0)]
    data = to_bend_midi(curve, notes)
    midi = _read(data)
    bends = midi.instruments[0].pitch_bends
    assert len(bends) > 0


def test_to_bend_midi_first_event_inside_first_note():
    curve = _curve([440.0] * 100)
    notes = [Note(start_sec=0.2, dur_sec=0.5, midi=69, cents_offset=0.0)]
    data = to_bend_midi(curve, notes)
    midi = _read(data)
    bends = midi.instruments[0].pitch_bends
    assert bends
    assert 0.2 - 1e-3 <= bends[0].time <= 0.7


def test_to_bend_midi_note_covers_note_span():
    curve = _curve([440.0] * 100)
    notes = [Note(start_sec=0.0, dur_sec=1.0, midi=69, cents_offset=0.0)]
    midi = _read(to_bend_midi(curve, notes))
    written = midi.instruments[0].notes
    assert len(written) == 1
    assert written[0].pitch == 69
    assert written[0].end == pytest.approx(1.0, abs=1e-2)


def test_to_bend_midi_skips_unvoiced_frames():
    # 前半段有声、后半段无声：无声帧不该产生弯音事件
    curve = _curve([440.0] * 50 + [None] * 50)
    notes = [Note(start_sec=0.0, dur_sec=1.0, midi=69, cents_offset=0.0)]
    midi = _read(to_bend_midi(curve, notes))
    bends = midi.instruments[0].pitch_bends
    assert bends
    assert all(b.time < 0.55 for b in bends)


def test_to_bend_midi_empty_notes_does_not_raise():
    curve = _curve([440.0] * 10)
    data = to_bend_midi(curve, [])
    assert isinstance(data, bytes)
    midi = _read(data)
    assert all(len(i.notes) == 0 for i in midi.instruments)


def test_to_bend_midi_writes_tempo_and_program():
    curve = _curve([440.0] * 10)
    notes = [Note(start_sec=0.0, dur_sec=0.5, midi=69, cents_offset=0.0)]
    data = to_bend_midi(curve, notes, bpm=90.0, program=54)
    midi = _read(data)
    assert midi.get_tempo_changes()[1][0] == pytest.approx(90.0, abs=1e-3)
    assert midi.instruments[0].program == 54


def test_to_bend_midi_offset_maps_to_bend_value():
    # 曲线比音符基准高 50 音分 → 弯音值约为 50/200*8192
    curve = _curve([440.0 * 2 ** (50 / 1200)] * 100)
    notes = [Note(start_sec=0.0, dur_sec=1.0, midi=69, cents_offset=50.0)]
    midi = _read(to_bend_midi(curve, notes))
    bends = midi.instruments[0].pitch_bends
    assert bends
    assert bends[0].pitch == pytest.approx(round(50 / 200 * 8192), abs=8)


def test_to_bend_midi_rejects_bad_hop():
    curve = _curve([440.0] * 10)
    with pytest.raises(ValueError):
        to_bend_midi(curve, [], bend_hop_sec=0.0)
