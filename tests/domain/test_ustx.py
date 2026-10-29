"""USTX 导出：用 yaml 解析回来看结构，而不是比对字符串。"""

from __future__ import annotations

import yaml

from app.domain.curve import PitchCurve
from app.domain.notes import Note
from app.domain.ustx import to_ustx


def make_curve(values: list[float | None], hop_sec: float = 0.01) -> PitchCurve:
    return PitchCurve(hop_sec=hop_sec, f0=tuple(values))


def flat_curve(hz: float, frames: int) -> PitchCurve:
    return make_curve([hz for _ in range(frames)])


def parse(text: str) -> dict:
    return yaml.safe_load(text)


def test_version_is_quoted_string():
    text = to_ustx(flat_curve(440.0, 100), [])
    raw = text.splitlines()[0]
    # "0.10" 必须是字符串：写成 0.1 会掉进 >0.6 的迁移分支
    assert raw == 'ustx_version: "0.10"'
    assert parse(text)["ustx_version"] == "0.10"


def test_keys_are_snake_case():
    payload = parse(to_ustx(flat_curve(440.0, 100), [Note(0.0, 1.0, 69, 0.0)]))
    assert "voice_parts" in payload
    assert "parts" not in payload  # parts 是 [YamlIgnore]，写了也会被忽略
    part = payload["voice_parts"][0]
    assert part["track_no"] == 0
    note = part["notes"][0]
    assert note["pitch"]["snap_first"] is False


def test_track_no_points_at_existing_track():
    payload = parse(to_ustx(flat_curve(440.0, 100), [Note(0.0, 1.0, 69, 0.0)]))
    # track_no 越界会让 OpenUtau 的 AfterLoad 直接抛异常，整个文件打不开
    for part in payload["voice_parts"]:
        assert 0 <= part["track_no"] < len(payload["tracks"])


def test_time_axis_starts_at_zero():
    payload = parse(to_ustx(flat_curve(440.0, 100), []))
    assert payload["tempos"][0]["position"] == 0
    assert payload["time_signatures"][0]["bar_position"] == 0


def test_note_tick_conversion():
    # 120 bpm 下 1 秒 = 960 tick（480 tick = 四分音符）
    payload = parse(to_ustx(flat_curve(440.0, 200), [Note(1.0, 0.5, 69, 0.0)], bpm=120.0))
    note = payload["voice_parts"][0]["notes"][0]
    assert note["position"] == 960
    assert note["duration"] == 480


def test_every_note_has_pitch_points():
    notes = [Note(0.0, 0.5, 60, 0.0), Note(0.6, 0.5, 62, 0.0)]
    payload = parse(to_ustx(flat_curve(261.63, 200), notes))
    for note in payload["voice_parts"][0]["notes"]:
        # data 为空会让 UNote.Validate 索引越界
        assert len(note["pitch"]["data"]) >= 1
        assert note["pitch"]["snap_first"] is False


def test_pitch_point_units():
    # A4=440 对应 midi 69，偏差应为 0（y 的单位是 0.1 半音）
    payload = parse(to_ustx(flat_curve(440.0, 100), [Note(0.0, 1.0, 69, 0.0)]))
    points = payload["voice_parts"][0]["notes"][0]["pitch"]["data"]
    assert all(abs(point["y"]) < 0.2 for point in points)
    assert points[0]["x"] >= 0.0


def test_fully_unvoiced_note_still_has_two_points():
    # 曲线全为 None 时也必须给两个点，否则 UNote.Validate 读 data[0] 崩
    payload = parse(to_ustx(make_curve([None] * 100), [Note(0.0, 1.0, 69, 0.0)]))
    points = payload["voice_parts"][0]["notes"][0]["pitch"]["data"]
    assert len(points) == 2
    assert all(point["y"] == 0 for point in points)


def test_notes_do_not_overlap_after_rounding():
    notes = [Note(0.0, 0.3333, 69, 0.0), Note(0.3333, 0.3333, 69, 0.0)]
    payload = parse(to_ustx(flat_curve(440.0, 100), notes))
    items = payload["voice_parts"][0]["notes"]
    end = items[0]["position"] + items[0]["duration"]
    assert end <= items[1]["position"]


def test_empty_notes_still_valid():
    payload = parse(to_ustx(flat_curve(440.0, 50), []))
    assert payload["voice_parts"][0]["notes"] == []
    assert payload["voice_parts"][0]["duration"] > 0


def test_bad_bpm_rejected():
    try:
        to_ustx(flat_curve(440.0, 10), [], bpm=0)
    except ValueError:
        return
    raise AssertionError("bpm=0 应该抛 ValueError")
