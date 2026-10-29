"""USTX 导出：生成 OpenUtau 能直接打开的工程（YAML）。

字段与单位全部对照 OpenUtau 源码核实，来源见 `docs/04-ustx.md`。几条硬约束：

- 键名是 **snake_case**（`Util/Yaml.cs` 用 `UnderscoredNamingConvention`），
  写成 camelCase 不报错，只会被静默丢弃（反序列化开了 `IgnoreUnmatchedProperties`）。
- 声部必须放 `voice_parts`，`parts` 是 `[YamlIgnore]`。
- `voice_parts[].track_no` 必须指向存在的 track，否则 `AfterLoad` 直接越界、整个文件打不开。
- 每个音符都必须有 `pitch` 且 `data` 至少一个点；`lyric` 不能是 null。
- `PitchPoint.x` 是**相对音符起点的毫秒**，`y` 是**相对 tone 的 0.1 半音**（10 音分）。
- `tempos[0].position` 必须为 0，`time_signatures[0].bar_position` 必须为 0。

导出的轨道**不写 singer**：导入后由用户在轨道里选自己的音源，此时音符标红属正常。
"""

from __future__ import annotations

import json
import math

from app.domain.curve import PitchCurve
from app.domain.notes import Note

RESOLUTION = 480  # 只读常量，OpenUtau 侧恒为 480：480 tick = 一个四分音符

# 曲线降密：10 ms 全量会让 3 分钟的工程膨胀到 ~720 KB，
# 25 ms 又会让编辑器里挤满控制点（每个点都画一个小红点）。50 ms 是折中。
DENSITY_SEC = 0.05


def _quote(text: str) -> str:
    """YAML 双引号字符串，用 JSON 转义即可（两者规则一致）。"""
    return json.dumps(text, ensure_ascii=False)


def _tick(seconds: float, bpm: float) -> int:
    return int(round(seconds * RESOLUTION * bpm / 60.0))


def _semitones(f0: float, midi: int) -> float:
    """当前频率相对该音符 MIDI 音高的半音数。"""
    reference = 440.0 * 2 ** ((midi - 69) / 12)
    return 12 * math.log2(f0 / reference)


def _pitch_points(curve: PitchCurve, note: Note, density_sec: float) -> list[tuple[float, float]]:
    """把音符区间内的曲线降密成 (x 毫秒, y 0.1 半音) 序列。

    无声帧直接跳过：OpenUtau 会在相邻两点间插值，但音符本身是由有声段切出来的，
    所以静音只可能出现在音符边缘，不会在中间伪造出一条线。
    """
    stride = max(1, int(round(density_sec / curve.hop_sec)))
    start_index = int(round(note.start_sec / curve.hop_sec))
    end_index = int(round((note.start_sec + note.dur_sec) / curve.hop_sec))
    points: list[tuple[float, float]] = []

    for index in range(start_index, min(end_index, curve.frames - 1) + 1, stride):
        value = curve.f0[index]
        if value is None:
            continue
        x_ms = round((index * curve.hop_sec - note.start_sec) * 1000.0, 1)
        y = round(_semitones(value, note.midi) * 10.0, 1)
        points.append((max(0.0, min(x_ms, note.dur_sec * 1000.0)), y))

    if not points:
        # 全段无声也要给两个点：data 为空会让 UNote.Validate 索引越界
        return [(0.0, 0.0), (round(note.dur_sec * 1000.0, 1), 0.0)]
    if len(points) == 1:
        return [points[0], points[0]]
    return points


def _note_block(note: Note, curve: PitchCurve, position: int, duration: int, density_sec: float) -> list[str]:
    points = _pitch_points(curve, note, density_sec)
    lines = [
        f"  - position: {position}",
        f"    duration: {duration}",
        f"    tone: {note.midi}",
        f"    lyric: {_quote('a')}",
        # tuning 必须为 0：OpenUtau 的 pitch 点 y 是相对 **AdjustedTone**
        # （= tone + tuning/100）的，而 _pitch_points 已经按「f0 相对 note.midi」
        # 算过了。两个都写就等于把音分偏移加了两遍，音符块和曲线会错开。
        # 音符落在半音网格上、曲线还原真实音高，这个分工更清楚。
        "    tuning: 0",
        "    pitch:",
        "      data:",
    ]
    for x_ms, y in points:
        lines.append(f"      - {{x: {x_ms}, y: {y}, shape: io}}")
    # snap_first 必须为 false：为 true 时 OpenUtau 会把首点的 Y 改写成
    # (前一个音的音高 - 本音音高) * 10，我们导入的真实曲线会被覆盖
    lines.append("      snap_first: false")
    lines.append(
        "    vibrato: {length: 0, period: 175, depth: 25, in: 10, out: 10, shift: 0, drift: 0, vol_link: 0}"
    )
    return lines


def to_ustx(
    curve: PitchCurve,
    notes: list[Note],
    *,
    bpm: float = 120.0,
    track_name: str = "PitchMorph 人声",
    part_name: str = "PitchMorph",
    density_sec: float = DENSITY_SEC,
) -> str:
    """Notes + 曲线 → 完整 .ustx 文本（调用方负责落盘）。"""
    if bpm <= 0:
        raise ValueError(f"bpm 必须为正数：{bpm}")

    ordered = sorted(notes, key=lambda item: item.start_sec)
    blocks: list[str] = []
    last_end = 0

    for index, note in enumerate(ordered):
        position = _tick(note.start_sec, bpm)
        end = _tick(note.start_sec + note.dur_sec, bpm)
        # 下一个音符的起点：tick 取整可能让相邻音符叠 1 tick，AfterLoad 会判 OverlapError
        limit = _tick(ordered[index + 1].start_sec, bpm) if index + 1 < len(ordered) else None
        if limit is not None:
            end = min(end, limit)
        duration = max(10, end - position)
        blocks.extend(_note_block(note, curve, position, duration, density_sec))
        last_end = max(last_end, position + duration)

    head = [
        f'ustx_version: "0.10"',
        f"name: {_quote(part_name)}",
        'comment: ""',
        "output_dir: Vocal",
        "cache_dir: UCache",
        "key: 0",
        "time_signatures:",
        "- bar_position: 0",  # 首个小节必须为 0，否则 TimeAxis 抛异常
        "  beat_per_bar: 4",
        "  beat_unit: 4",
        "tempos:",
        "- position: 0",  # 首个 tempo 必须在 tick 0
        f"  bpm: {bpm:g}",
        "tracks:",
        '- singer: ""',  # 留空 = 导入后由用户自选音源，能打开但不能出声
        f"  track_name: {_quote(track_name)}",
        "  track_color: Blue",
        "  mute: false",
        "  solo: false",
        "  volume: 0",
        "  pan: 0",
        '  voice_color_names: [""]',
        "  track_expressions: []",
        "voice_parts:",
        "- name: Part1",
        '  comment: ""',
        "  track_no: 0",  # 必须 < tracks.Count
        "  position: 0",
        f"  duration: {max(last_end, RESOLUTION)}",
    ]

    body = ["  notes:"] + blocks if blocks else ["  notes: []"]
    tail = ["  curves: []", "  masked_curves: []", "wave_parts: []"]
    return "\n".join(head + body + tail) + "\n"
