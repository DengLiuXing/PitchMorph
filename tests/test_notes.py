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


def test_build_notes_rounds_model_midi():
    segments = [{"start_sec": 0.0, "dur_sec": 0.1, "midi": 60.4, "voiced": True}]
    result = notes.build_notes(segments)
    assert len(result) == 1
    assert result[0]["midi"] == 60
    assert abs(result[0]["cents_offset"] - 40.0) < 1e-6
    assert result[0]["start_sec"] == 0.0
    assert abs(result[0]["dur_sec"] - 0.1) < 1e-6


def test_build_notes_rounds_halfway_to_even():
    # 与 OpenUtau 的 C# Math.Round 一致：.5 中点取偶数
    assert notes.build_notes([{"start_sec": 0.0, "dur_sec": 0.1, "midi": 60.5, "voiced": True}])[0]["midi"] == 60
    assert notes.build_notes([{"start_sec": 0.0, "dur_sec": 0.1, "midi": 61.5, "voiced": True}])[0]["midi"] == 62


def test_build_notes_drops_unvoiced_segments():
    segments = [{"start_sec": 0.1, "dur_sec": 0.05, "midi": 60.0, "voiced": False}]
    assert notes.build_notes(segments) == []


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
