import math

from app import notes


CURVE = {
    "hop_ms": 10,
    "f0": [440.0] * 10 + [0.0] * 5 + [440.0] * 10,
    "voiced": [True] * 10 + [False] * 5 + [True] * 10,
}


def test_to_cents_marks_unvoiced_as_none():
    cents = notes.to_cents(CURVE["f0"], CURVE["voiced"])
    assert cents[0] == 0.0
    assert cents[12] is None
    assert cents[20] == 0.0


def test_to_cents_uses_log_scale():
    cents = notes.to_cents([440.0, 880.0], [True, True])
    assert abs(cents[1] - 1200.0) < 1e-9


def test_midi_from_cents_rounds_to_semitone():
    midi, offset = notes.midi_from_cents(0.0)
    assert midi == 69
    assert offset == 0.0
    midi, offset = notes.midi_from_cents(-1000.0)
    assert midi == 59
    assert offset == 0.0


def test_spans_from_segments_clips_out_of_range():
    spans = notes.spans_from_segments(
        [{"start_sec": 0.0, "dur_sec": 0.1}, {"start_sec": 100.0, "dur_sec": 1.0}],
        0.01,
        100,
    )
    assert spans[0] == (0, 9)
    assert len(spans) == 1  # 越界的段被裁掉


def test_build_notes_uses_median_inside_segment():
    # 段内 261.6 Hz（C4）附近取中位数 -> MIDI 60
    segments = [{"start_sec": 0.0, "dur_sec": 0.1}]
    curve = dict(CURVE, f0=[261.63] * 10, voiced=[True] * 10)
    result = notes.build_notes(segments, curve)
    assert len(result) == 1
    assert result[0]["midi"] == 60
    assert abs(result[0]["cents_offset"]) < 1.0
    assert result[0]["start_sec"] == 0.0
    assert abs(result[0]["dur_sec"] - 0.1) < 1e-6


def test_build_notes_drops_silent_segments():
    segments = [{"start_sec": 0.1, "dur_sec": 0.05}]
    assert notes.build_notes(segments, CURVE) == []


def test_build_payload_keeps_model_segments():
    payload = notes.build_payload(
        [{"start_sec": 0.0, "dur_sec": 0.1, "midi": 60.0, "voiced": True}],
        CURVE,
        stem="vocals",
        model="GAME-1.0.3-medium",
        tempo=120.0,
        params={"tempo": 120.0},
    )
    assert payload["stem"] == "vocals"
    assert payload["tempo"] == 120.0
    assert payload["model_segments"][0]["voiced"] is True
    assert payload["segments"]
