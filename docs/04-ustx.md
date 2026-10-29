# USTX 契约（对照 OpenUtau 源码核实）

> 这份文档是为了「从 Python 手工生成 .ustx」而写的。每一条都能在
> `OpenUtau.Core/` 里找到行号依据。写错不会报错——反序列化开了
> `IgnoreUnmatchedProperties`，拼错的键会被**静默丢弃**，然后你看到的是默认值。

## 1. 序列化器决定了键名风格

`OpenUtau.Core/Util/Yaml.cs`

| 配置 | 后果 |
|---|---|
| `UnderscoredNamingConvention` | 键名是 **snake_case**：`ustxVersion` → `ustx_version`、`snapFirst` → `snap_first` |
| `DefaultValuesHandling.OmitNull` | 只省略 null。`0` / `false` / `[]` 照样写出 |
| `FlowEmitter`（仅 PitchPoint / UVibrato / UExpression） | 这些写成 `{x: 1, y: 2}` 的行内风格 |
| `IgnoreUnmatchedProperties` | 键名拼错不报错，**静默丢数据** |

枚举没有配置命名约定，所以按 C# 成员名原样输出：`shape: io`、`type: Curve`。

版本常量 `kUstxVersion = 0.10`（`Format/USTx.cs:13`）。写成低于 0.6 的值会命中迁移分支，**用废弃的 `bpm/beat_per_bar/beat_unit` 覆盖你写的 `tempos` / `time_signatures`**。

## 2. 顶层字段（UProject）

| YAML 键 | 说明 |
|---|---|
| `ustx_version` | 写 `"0.10"`，**必须加引号**（类型是 Version，语义 Minor=10） |
| `name` / `comment` / `output_dir` / `cache_dir` | 可省 |
| `key` | 0 = C，可省 |
| `time_signatures` | `[{bar_position, beat_per_bar, beat_unit}]`，`bar_position` 是**小节号**；首元素必须为 0，否则 `TimeAxis` 抛异常 |
| `tempos` | `[{position, bpm}]`，`position` 是 **tick**；首元素必须在 tick 0 |
| `tracks` | 至少一条，否则 part 的 `track_no` 越界 |
| `voice_parts` | 声部放这里 |
| `wave_parts` | 可 `[]` |
| ~~`parts`~~ | `[YamlIgnore]`，**不能写**（`UProject.cs:60`） |
| ~~`expressions`~~ | 可整段省略，`Load` 时会 `AddDefaultExpressions` 补齐 27 个 |

`resolution` 是只读常量 480（`[YamlIgnore]`），写不进去也读不出来。**480 tick = 一个四分音符**。

## 3. UTrack

| 键 | 默认 | 备注 |
|---|---|---|
| `singer` | null | 留空 = 无音源，**能打开、能编辑，但不出声** |
| `track_name` | `"New Track"` | |
| `track_color` | `"Blue"` | |
| `mute` / `solo` | false | |
| `volume` / `pan` | 0 | volume 单位 dB |
| `voice_color_names` | `[""]` | |

`singer` 为空时：`UTrack.AfterLoad:246` 不去找歌手，`URenderSettings.Validate` 直接把 renderer 清 null 返回，`UNote.Validate:105` 只是把音符标记成 Error（红色）。**不抛异常**。

## 4. UVoicePart

| 键 | 备注 |
|---|---|
| `track_no` | **必须 < tracks.Count**，`UProject.AfterLoad:201` 直接 `tracks[part.trackNo]`，越界整个文件打不开 |
| `position` | part 起点 tick |
| `duration` | 可省，`AfterLoad` 会自动放大到最小长度 |
| `notes` | 可 `[]` |
| `curves` / `masked_curves` | `[]` |

## 5. UNote

| 键 | 单位 / 约束 |
|---|---|
| `position` | tick，相对 part 起点 |
| `duration` | tick，会被 `Math.Max(10, duration)` 抬高 |
| `tone` | MIDI 音高，60 = C4 |
| `lyric` | **不能是 null**（`UVoicePart.Validate:131` 会 `lyric.StartsWith`），可省则取默认 `"a"` |
| `tuning` | 1/100 半音 = **1 音分**，`AdjustedTone = tone + tuning/100` |
| `pitch` | **必须存在**，null 会让 `UNote.Validate` 崩 |
| `vibrato` | Core 里缺失不崩，但钢琴卷会崩，建议写默认值 |

## 6. UPitch / PitchPoint（最容易写错）

```yaml
pitch:
  data:
  - {x: 0.0, y: 0.0, shape: io}
  - {x: 480.0, y: -3.2, shape: io}
  snap_first: false
```

| 键 | 单位 | 依据 |
|---|---|---|
| `x` | **毫秒**，相对音符起点 | `UPitch.Sample:525` 用 `note.PositionMs + p1.X` |
| `y` | **0.1 半音（= 10 音分）**，相对 `tone` | `UNote.Validate:110`：`(Prev.AdjustedTone - AdjustedTone) * 10`；`Sample:530` 再 `* 10` 转音分 |
| `shape` | `io` / `l` / `i` / `o` / `sp` | `PitchPointShape`，`sp` 采样时等同 `io` |

- `data` 至少 1 个点，建议 2 个：`snap_first: true` 时 `Validate:108` 无条件读 `data[0]`，空列表直接索引越界。
- **本项目导出一律写 `snap_first: false`**。为 `true` 时 OpenUtau 会把首点的 `y` 改写成
  `(前一个音的音高 - 本音音高) * 10`，我们导入的真实曲线会被覆盖。
- 官方新建音符的两个点是 `x = -40` 与 `x = 40`（滑音起点/长度），我们不用负值，从 0 开始。

## 7. UVibrato

`{length: 0, period: 175, depth: 25, in: 10, out: 10, shift: 0, drift: 0, vol_link: 0}`

| 键 | 范围 | 默认 |
|---|---|---|
| `length` | 0–100（占音符长度 %） | 0 = 无颤音 |
| `period` | 5–500 ms | 175 |
| `depth` | 5–200 音分 | 25 |
| `in` / `out` | 0–100 % | 10 |
| `shift` / `drift` / `vol_link` | — | 0 |

setter 内部 clamp，写超范围不报错。

## 8. 最小可打开骨架

```yaml
ustx_version: "0.10"
name: "PitchMorph"
comment: ""
output_dir: Vocal
cache_dir: UCache
key: 0
time_signatures:
- bar_position: 0
  beat_per_bar: 4
  beat_unit: 4
tempos:
- position: 0
  bpm: 120
tracks:
- singer: ""
  track_name: "PitchMorph 人声"
  track_color: Blue
  mute: false
  solo: false
  volume: 0
  pan: 0
  voice_color_names: [""]
  track_expressions: []
voice_parts:
- name: Part1
  comment: ""
  track_no: 0
  position: 0
  duration: 1920
  notes:
  - position: 0
    duration: 480
    tone: 60
    lyric: "a"
    tuning: 0
    pitch:
      data:
      - {x: 0.0, y: 0.0, shape: io}
      - {x: 480.0, y: 0.0, shape: io}
      snap_first: false
    vibrato: {length: 0, period: 175, depth: 25, in: 10, out: 10, shift: 0, drift: 0, vol_link: 0}
  curves: []
  masked_curves: []
wave_parts: []
```

省略即崩的四项：`tracks` 为空、`voice_parts` 键名写错（写成 `parts`）、part 的 `track_no` 越界、note 缺 `pitch` 或 `data` 为空。

## 9. 实现位置

- 生成器：`app/domain/ustx.py`（纯函数，返回 YAML 文本）
- 落盘与命名：`app/exports.py`
- 测试：`tests/domain/test_ustx.py`（用 `yaml.safe_load` 读回来断言结构，不比对字符串）

曲线按 25 ms 降密写入：10 ms 全量会让 3 分钟工程膨胀到约 720 KB，而 OpenUtau 的插值精度远高于 25 ms。
