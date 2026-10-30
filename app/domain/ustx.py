"""USTX 导出：生成 OpenUtau 能直接打开的工程（YAML）。

字段与单位全部对照 OpenUtau 源码核实，来源见 `docs/04-ustx.md`。几条硬约束：

- 键名是 **snake_case**（`Util/Yaml.cs` 用 `UnderscoredNamingConvention`），
  写成 camelCase 不报错，只会被静默丢弃（反序列化开了 `IgnoreUnmatchedProperties`）。
- 声部必须放 `voice_parts`，`parts` 是 `[YamlIgnore]`。
- `voice_parts[].track_no` 必须指向存在的 track，否则 `AfterLoad` 直接越界、整个文件打不开。
- 每个音符都必须有 `pitch` 且 `data` 至少一个点；`lyric` 不能是 null。
- `PitchPoint.x` 是**相对音符起点的毫秒**，`y` 是**相对 tone 的 0.1 半音**（10 音分）。
- `tempos[0].position` 必须为 0，`time_signatures[0].bar_position` 必须为 0。

音高线写在 `part.curves` 的 **`pitd`** 曲线里，而不是每音符的 `pitch.data`：
`UProject.expSelectors` 的默认前两位就是 `dyn`/`pitd`，OpenUtau 打开就画；
`note.pitch` 那条折线只有勾选「显示音高」才画，而且一条音符一段、彼此断开。
`pitd` 的 `xs` 是 tick，`ys` 是**相对该音符 tone 的音分**，间隔 5 tick（`UCurve.interval`）。

两者不能同时写：渲染时 PITD 会叠加在音符 pitch 之上（`RenderPhrase.cs`），
同时写等于把曲线加两遍。所以音符 pitch 固定成一条平直的 y=0。

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

# UCurve.interval：曲线 x 的最小步长（tick）
CURVE_INTERVAL = 5
# pitd 表达式描述符的范围（音分）
PITD_MIN = -1200
PITD_MAX = 1200
# 超过这个偏移（音分）的帧不采信：切分已经按半音切过，音符内部正常不会偏出
# 两个半音以上。剩下的都是 FCPE 的错帧（实测有掉到 -19 个半音的），
# 画进音高线就是一根根竖线。
PITD_MAX_DEVIATION = 250


def _quote(text: str) -> str:
    """YAML 双引号字符串，用 JSON 转义即可（两者规则一致）。"""
    return json.dumps(text, ensure_ascii=False)


def _tick(seconds: float, bpm: float) -> int:
    return int(round(seconds * RESOLUTION * bpm / 60.0))


def _semitones(f0: float, midi: int) -> float:
    """当前频率相对该音符 MIDI 音高的半音数。"""
    reference = 440.0 * 2 ** ((midi - 69) / 12)
    return 12 * math.log2(f0 / reference)


def _flat_pitch(note: Note) -> list[tuple[float, float]]:
    """音符自己的 pitch 折线：恒为 y=0，音高细节全部交给 pitd 曲线。

    两个点是下限：`UNote.Validate` 会读 `data[0]`，而 `UPitch.Sample` 在
    [首点, 末点] 之外返回 null，覆盖不到整段音符就画不出完整的线。
    """
    return [(0.0, 0.0), (round(note.dur_sec * 1000.0, 1), 0.0)]


def _note_block(note: Note, position: int, duration: int) -> list[str]:
    lines = [
        f"  - position: {position}",
        f"    duration: {duration}",
        f"    tone: {note.midi}",
        f"    lyric: {_quote('a')}",
        # tuning 必须为 0：pitd 的 y 是相对 AdjustedTone（= tone + tuning/100）的，
        # 写了 tuning 就等于把音分偏移加两遍，音符块和曲线会错开。
        "    tuning: 0",
        "    pitch:",
        "      data:",
    ]
    for x_ms, y in _flat_pitch(note):
        lines.append(f"      - {{x: {x_ms}, y: {y}, shape: io}}")
    # snap_first 必须为 false：为 true 时 OpenUtau 会把首点的 Y 改写成
    # (前一个音的音高 - 本音音高) * 10，平直的 y=0 会被改成斜线
    lines.append("      snap_first: false")
    lines.append(
        "    vibrato: {length: 0, period: 175, depth: 25, in: 10, out: 10, shift: 0, drift: 0, vol_link: 0}"
    )
    return lines


def _pitd_curve(
    curve: PitchCurve,
    notes: list[Note],
    bpm: float,
    density_sec: float,
) -> tuple[list[int], list[int]]:
    """真实音高 → `pitd` 曲线（xs 是 tick，ys 是相对音符 tone 的音分）。

    逐音符取样：y 必须相对**该音符**的 tone 算，跨音符的插值没有意义。
    无声帧直接跳过，OpenUtau 会在相邻两点间连线，而静音只可能出现在音符边缘。
    """
    stride = max(1, int(round(density_sec / curve.hop_sec)))
    xs: list[int] = []
    ys: list[int] = []

    for note in notes:
        first = int(round(note.start_sec / curve.hop_sec))
        last = int(round((note.start_sec + note.dur_sec) / curve.hop_sec))
        for index in range(first, min(last, curve.frames - 1) + 1, stride):
            value = curve.f0[index]
            if value is None:
                continue
            x = _tick(index * curve.hop_sec, bpm)
            # x 必须落在 UCurve.interval 的整数倍上，否则 OpenUtau 会自己对齐
            x = int(round(x / CURVE_INTERVAL)) * CURVE_INTERVAL
            y = int(round(_semitones(value, note.midi) * 100.0))
            if abs(y) > PITD_MAX_DEVIATION:
                continue  # 错帧，连起来就是假线
            y = max(PITD_MIN, min(PITD_MAX, y))
            if xs and x <= xs[-1]:
                # 同一 tick 只留最后一个取样（后写覆盖，与 UCurve.Set 一致）
                if x == xs[-1]:
                    ys[-1] = y
                continue
            xs.append(x)
            ys.append(y)
    return xs, ys


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
        blocks.extend(_note_block(note, position, duration))
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
    xs, ys = _pitd_curve(curve, ordered, bpm, density_sec)
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
