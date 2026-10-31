import io

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
