"""导出：OpenUtau 工程（USTX）与 MIDI。纯函数，不落盘。

USTX 的字段与单位全部对照 OpenUtau 源码核实过（V2 已在真机 OpenUtau 里验证过能打开）：

- 键名是 snake_case（`Util/Yaml.cs` 用 `UnderscoredNamingConvention`），写成 camelCase
  不报错，只会被静默丢弃；
- 声部必须放 `voice_parts`，`parts` 是 `[YamlIgnore]`；
- `voice_parts[].track_no` 必须指向存在的 track，否则 `AfterLoad` 直接越界、工程打不开；
- 每个音符都必须有 `pitch` 且 `data` 至少一个点，`lyric` 不能是 null；
- 音高线写在 `part.curves` 的 **`pitd`** 曲线里（xs 是 tick，ys 是相对该音符 tone 的
  音分，间隔 5 tick = `UCurve.interval`），**不能同时写音符 pitch 折线**：
  渲染时 PITD 会叠加在音符 pitch 之上，同时写等于把曲线加两遍，所以音符 pitch 固定成 y=0；
- `tempos[0].position` 与 `time_signatures[0].bar_position` 必须为 0。

MIDI 用 mido 实现（不引入 pretty_midi）：每个音符一条长音符 + pitch bend 还原颤音与滑音，
弯音范围用 RPN 显式写成 ±200 音分（不写的话播放器按默认 ±2 半音解释）。
"""
from __future__ import annotations

import json
import math

# OpenUtau 侧恒为 480：480 tick = 一个四分音符
RESOLUTION = 480
# UCurve.interval：曲线 x 的最小步长（tick）
CURVE_INTERVAL = 5
PITD_MIN = -1200
PITD_MAX = 1200
# 超过这个偏移（音分）的帧不采信：切分已按半音切过，剩下的都是音高曲线的错帧
PITD_MAX_DEVIATION = 250
# 曲线降密：10 ms 全量会让 3 分钟的工程膨胀到几百 KB，50 ms 是折中
DENSITY_SEC = 0.05

# MIDI：弯音范围 ±200 音分，必须显式写 RPN，否则播放器按默认 ±2 半音解释
BEND_RANGE_CENTS = 200.0
BEND_HOP_SEC = 0.01
MIDI_PROGRAM = 54  # Voice Oohs
VELOCITY = 100


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _tick(seconds: float, bpm: float) -> int:
    return int(round(seconds * RESOLUTION * bpm / 60.0))


def _flat_pitch(dur_sec: float) -> list[tuple[float, float]]:
    # 音符自己的 pitch 折线恒为 y=0：真实音高写在 part.curves 的 pitd 里，
    # 两者同时写会在渲染时叠加两遍。
    return [(0.0, 0.0), (round(dur_sec * 1000.0, 1), 0.0)]


def _note_block(note: dict, position: int, duration: int) -> list[str]:
    lines = [
        f"  - position: {position}",
        f"    duration: {duration}",
        f"    tone: {note['midi']}",
        f"    lyric: {_quote('a')}",
        "    tuning: 0",
        "    pitch:",
        "      data:",
    ]
    for x_ms, y in _flat_pitch(float(note["dur_sec"])):
        lines.append(f"      - {{x: {x_ms}, y: {y}, shape: io}}")
    lines.append("    snap_first: false")
    lines.append(
        "    vibrato: {length: 0, period: 175, depth: 25, in: 10, out: 10, shift: 0, drift: 0, vol_link: 0}"
    )
    return lines


def _pitd_curve(cents, notes: list[dict], hop_sec: float, bpm: float) -> tuple[list[int], list[int]]:
    """真实音高 -> pitd 曲线（xs 是 tick，ys 是相对该音符 tone 的音分）。

    逐音符取样：y 必须相对**该音符**的 tone 算，跨音符的插值没有意义。
    """
    stride = max(1, int(round(DENSITY_SEC / hop_sec)))
    xs: list[int] = []
    ys: list[int] = []
    for note in notes:
        first = int(round(note["start_sec"] / hop_sec))
        last = int(round((note["start_sec"] + note["dur_sec"]) / hop_sec))
        midi = int(note["midi"])
        for index in range(first, min(last, len(cents) - 1) + 1, stride):
            value = cents[index]
            if value is None:
                continue
            x = _tick(index * hop_sec, bpm)
            x = int(round(x / CURVE_INTERVAL)) * CURVE_INTERVAL
            y = int(round((value - 100.0 * (midi - 69)) / 1.0))
            if abs(y) > PITD_MAX_DEVIATION:
                continue
            y = max(PITD_MIN, min(PITD_MAX, y))
            if xs and x <= xs[-1]:
                if x == xs[-1]:
                    ys[-1] = y
                continue
            xs.append(x)
            ys.append(y)
    return xs, ys


def to_ustx(cents, notes: list[dict], *, hop_sec: float, bpm: float = 120.0) -> str:
    """音符 + 音高曲线 -> 完整 .ustx 文本（OpenUtau 工程）。

    字段与单位全部对照 OpenUtau 源码核实过：键名 snake_case、声部放 voice_parts、
    track_no 必须指向存在的 track、每音符都要有 pitch.data 与非 null 的 lyric、
    tempos[0].position 与 time_signatures[0].bar_position 必须是 0。
    """
    if bpm <= 0:
        raise ValueError("bpm 必须为正数")
    ordered = sorted(notes, key=lambda item: item["start_sec"])
    blocks: list[str] = []
    last_end = 0
    for index, note in enumerate(ordered):
        position = _tick(note["start_sec"], bpm)
        end = _tick(note["start_sec"] + note["dur_sec"], bpm)
        limit = _tick(ordered[index + 1]["start_sec"], bpm) if index + 1 < len(ordered) else None
        if limit is not None:
            end = min(end, limit)
        duration = max(10, end - position)
        blocks.extend(_note_block(note, position, duration))
        last_end = max(last_end, position + duration)

    head = [
        "ustx_version: \"0.10\"",
        f"name: {_quote("PitchMorph")}",
        "comment: \"\"",
        "output_dir: Vocal",
        "cache_dir: UCache",
        "key: 0",
        "time_signatures:",
        "- bar_position: 0",
        "  beat_per_bar: 4",
        "  beat_unit: 4",
        "tempos:",
        "- position: 0",
        f"  bpm: {bpm:g}",
        "tracks:",
        "- singer: \"\"",
        f"  track_name: {_quote("PitchMorph 人声")}",
        "  track_color: Blue",
        "  mute: false",
        "  solo: false",
        "  volume: 0",
        "  pan: 0",
        "  voice_color_names: [\"\"]",
        "  track_expressions: []",
        "voice_parts:",
        "- name: Part1",
        "  comment: \"\"",
        "  track_no: 0",
        "  position: 0",
        f"  duration: {max(last_end, RESOLUTION)}",
    ]
    body = ["  notes:"] + blocks if blocks else ["  notes: []"]
    xs, ys = _pitd_curve(cents, ordered, hop_sec, bpm)
    if xs:
        tail = [
            "  curves:",
            "  - abbr: pitd",
            f"    xs: {xs}",
            f"    ys: {ys}",
        ]
    else:
        tail = ["  curves: []"]
    tail += ["  masked_curves: []", "wave_parts: []"]
    return "\n".join(head + body + tail) + "\n"


def to_midi(cents, notes: list[dict], *, hop_sec: float, bpm: float = 120.0) -> bytes:
    """音符 + 音高曲线 -> MIDI 文件字节。

    每个音符一条**长音符**，用 pitch bend 还原颤音与滑音（不量化成短音符，
    否则 80 音分级别的颤音会被抹平）。弯音范围必须显式写 RPN：
    MIDI 默认 ±2 半音，不写的话播放器会按 200 音分解释我们写的值。
    """
    import io

    import mido

    if bpm <= 0:
        raise ValueError("bpm 必须为正数")

    midi = mido.MidiFile(ticks_per_beat=RESOLUTION)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    track.append(mido.Message("program_change", program=MIDI_PROGRAM, channel=0, time=0))

    cents_range = int(round(BEND_RANGE_CENTS))
    for control, value in ((101, 0), (100, 0), (6, cents_range >> 7), (38, cents_range & 0x7F)):
        track.append(mido.Message("control_change", control=control, value=value, channel=0, time=0))

    def to_tick(seconds: float) -> int:
        return int(round(seconds * RESOLUTION * bpm / 60.0))

    previous = 0
    for note in sorted(notes, key=lambda item: item["start_sec"]):
        midi_note = int(note["midi"])
        if not 0 <= midi_note <= 127:
            continue
        start_tick = to_tick(float(note["start_sec"]))
        end_tick = to_tick(float(note["start_sec"]) + float(note["dur_sec"]))
        track.append(
            mido.Message(
                "note_on", note=midi_note, velocity=VELOCITY, channel=0,
                time=max(0, start_tick - previous),
            )
        )
        previous = start_tick

        base = 100.0 * (midi_note - 69)
        first = int(round(float(note["start_sec"]) / hop_sec))
        last = int(round((float(note["start_sec"]) + float(note["dur_sec"])) / hop_sec))
        for frame in range(first, min(last, len(cents) - 1) + 1):
            value = cents[frame]
            if value is None:
                continue
            bend = int(round((value - base) / BEND_RANGE_CENTS * 8192))
            bend = max(-8192, min(8191, bend))
            tick = to_tick(frame * hop_sec)
            track.append(
                mido.Message("pitchwheel", pitch=bend, channel=0, time=max(0, tick - previous))
            )
            previous = tick

        track.append(
            mido.Message(
                "note_off", note=midi_note, velocity=0, channel=0,
                time=max(0, end_tick - previous),
            )
        )
        previous = end_tick

    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()
