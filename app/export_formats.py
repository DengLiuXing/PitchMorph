"""导出：OpenUtau 工程（USTX）与 MIDI。纯函数，不落盘。

USTX 的字段与单位全部对照 OpenUtau 源码核实过（V2 已在真机 OpenUtau 里验证过能打开）：

- 键名是 snake_case（`Util/Yaml.cs` 用 `UnderscoredNamingConvention`），写成 camelCase
  不报错，只会被静默丢弃；
- 声部必须放 `voice_parts`，`parts` 是 `[YamlIgnore]`；
- `voice_parts[].track_no` 必须指向存在的 track，否则 `AfterLoad` 直接越界、工程打不开；
- 每个音符都必须有 `pitch` 且 `data` 至少一个点，`lyric` 不能是 null；
- 音高线写在 `part.curves` 的 **`pitd`** 曲线里（xs 是 tick，ys 是相对该音符 tone 的
  音分，间隔 5 tick = `UCurve.interval`），渲染时 PITD 叠加在音符 pitch 之上，
  所以音符 pitch.data 只放 OpenUtau 新音符的默认锚点（±40ms 平直段 + snap_first，
  让 OpenUtau 自己在音符连接处渲染短促的滑音连接线）；
- pitd 的写法对照 `Rmvpe.ApplyToPart`（OpenUtau 转录后写音高线的同一套管线）：
  音符首尾各修剪 min(25ms, 15% 时长)（这段留给滑音连接线）、5 tick 网格、
  ≤60 tick 的短隙线性填充、中值滤波 + 自适应尖峰抑制；
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
# Rmvpe.ApplyToPart 的同款参数
PITD_EDGE_TRIM_MS = 25.0
PITD_EDGE_TRIM_RATIO = 0.15
PITD_GAP_FILL_STEPS = 12  # 每步 5 tick，12 步 = 60 tick
PITD_MEDIAN_RADIUS = 2
PITD_SPIKE_THRESHOLD_CENTS = 75.0
PITD_SPIKE_BLEND = 0.7
# OpenUtau 新音符的默认 pitch 锚点：±40ms
PITCH_ANCHOR_MS = 40.0

# MIDI：弯音范围 ±200 音分，必须显式写 RPN，否则播放器按默认 ±2 半音解释
BEND_RANGE_CENTS = 200.0
BEND_HOP_SEC = 0.01
MIDI_PROGRAM = 54  # Voice Oohs
VELOCITY = 100


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _tick(seconds: float, bpm: float) -> int:
    return int(round(seconds * RESOLUTION * bpm / 60.0))


def _pitch_data() -> list[tuple[float, float]]:
    # 音符自己的 pitch 折线恒为 y=0：真实音高写在 part.curves 的 pitd 里，
    # 两者同时写会在渲染时叠加两遍。±40ms 锚点 + snap_first 让 OpenUtau 在
    # 音符连接处渲染 prev tone -> tone 的短促滑音连接线（新音符的默认形态）。
    return [(-PITCH_ANCHOR_MS, 0.0), (PITCH_ANCHOR_MS, 0.0)]


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
    for x_ms, y in _pitch_data():
        lines.append(f"      - {{x: {x_ms}, y: {y}, shape: io}}")
    lines.append("    snap_first: true")
    lines.append(
        "    vibrato: {length: 0, period: 175, depth: 25, in: 10, out: 10, shift: 0, drift: 0, vol_link: 0}"
    )
    return lines


def _median_filter(values: list[int]) -> list[int]:
    result: list[int] = []
    for i in range(len(values)):
        window = sorted(values[max(0, i - PITD_MEDIAN_RADIUS): i + PITD_MEDIAN_RADIUS + 1])
        result.append(window[len(window) // 2])
    return result


def _adaptive_smooth(values: list[int]) -> list[int]:
    if len(values) <= 2:
        return list(values)
    result = list(values)
    for i in range(1, len(values) - 1):
        neighbor_average = (result[i - 1] + result[i + 1]) / 2.0
        delta = result[i] - neighbor_average
        if abs(delta) <= PITD_SPIKE_THRESHOLD_CENTS:
            continue
        result[i] = int(round(result[i] - delta * PITD_SPIKE_BLEND))
    return result


def _fill_short_gaps(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """≤60 tick 的空隙线性填充（OpenUtau 的 FillShortGaps）。"""
    if not points:
        return points
    expanded = [points[0]]
    for point in points[1:]:
        prev = expanded[-1]
        gap_steps = max(0, (point[0] - prev[0]) // CURVE_INTERVAL - 1)
        if 0 < gap_steps <= PITD_GAP_FILL_STEPS:
            for step in range(1, gap_steps + 1):
                ratio = step / (gap_steps + 1)
                expanded.append(
                    (
                        prev[0] + step * CURVE_INTERVAL,
                        int(round(prev[1] + (point[1] - prev[1]) * ratio)),
                    )
                )
        expanded.append(point)
    return expanded


def _simplify(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """去掉共线的中间点（OpenUtau 的 UCurve.Simplify）。"""
    if len(points) < 3:
        return points
    result = [points[0]]
    for i in range(1, len(points) - 1):
        x0, y0 = result[-1]
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        if (y1 - y0) * (x2 - x1) != (y2 - y1) * (x1 - x0):
            result.append((x1, y1))
    result.append(points[-1])
    return result


def _pitd_curve(cents, notes: list[dict], hop_sec: float, bpm: float) -> tuple[list[int], list[int]]:
    """真实音高 -> pitd 曲线（xs 是 tick，ys 是相对该音符 tone 的音分）。

    对照 OpenUtau 的 `Rmvpe.ApplyToPart`（转录后写音高线的同一套管线）：
    音符首尾修剪 min(25ms, 15% 时长) 留给滑音连接线，5 tick 网格，
    组内短隙填充，中值滤波 + 自适应尖峰抑制，最后共线简化。
    y 必须相对**该音符**的 tone 算，跨音符的插值没有意义。
    """
    groups: list[list[tuple[int, int]]] = []
    for note in notes:
        midi = int(note["midi"])
        start = float(note["start_sec"])
        dur_ms = float(note["dur_sec"]) * 1000.0
        edge_ms = min(PITD_EDGE_TRIM_MS, dur_ms * PITD_EDGE_TRIM_RATIO)
        first = int(round(start / hop_sec))
        last = int(round((start + float(note["dur_sec"])) / hop_sec))
        group: list[tuple[int, int]] = []
        for index in range(first, min(last, len(cents) - 1) + 1):
            value = cents[index]
            if value is None:
                continue
            offset_ms = (index * hop_sec - start) * 1000.0
            if dur_ms > edge_ms * 2 and (offset_ms < edge_ms or dur_ms - offset_ms <= edge_ms):
                continue
            x = int(round(_tick(index * hop_sec, bpm) / CURVE_INTERVAL)) * CURVE_INTERVAL
            y = int(round(value - 100.0 * (midi - 69)))
            y = max(PITD_MIN, min(PITD_MAX, y))
            if group and group[-1][0] == x:
                group[-1] = (x, y)
            elif not group or x > group[-1][0]:
                group.append((x, y))
        if len(group) >= 2:
            groups.append(group)

    points: list[tuple[int, int]] = []
    for group in groups:
        points.extend(_fill_short_gaps(group))
    if not points:
        return [], []
    ys = _adaptive_smooth(_median_filter([y for _, y in points]))
    smoothed = [(x, y) for (x, _), y in zip(points, ys)]
    smoothed = _simplify(smoothed)
    xs = [x for x, _ in smoothed]
    ys = [y for _, y in smoothed]
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
