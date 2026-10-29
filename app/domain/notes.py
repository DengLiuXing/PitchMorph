"""音高曲线 → 音符列表。纯函数，不 import torch、不做磁盘 IO。

算法与理由（v2.1 重写，因为 v2.0 切得太碎）：

v2.0 用「相邻帧差 > 80 音分就切」，结果颤音和滑音一路触发切分：
12 s 的片段切出 307 个音符（每秒 25 个），而正常唱歌是每秒 2–5 个音节。

现在的做法是**先量化到半音、再按游程切分**：

1. 平滑后逐帧算 MIDI 号（四舍五入到半音）
2. 对 MIDI 序列做游程编码：连续相同的半音 = 一段
3. 短于 `min_note_sec` 的游程直接丢弃——它们多半是辅音、气声、颤音跨半音边界时的抖动
4. 每段用原始音分的中位数定精确音高，避免中位数落在半音边界上

这样「同一音高的一串帧」天然是一段，颤音（围绕中心振荡、MIDI 号不常变）不会被切开，
而真正的换音（跨半音）会。比逐帧差分稳得多，也更容易解释。

默认不量化到节拍：没有 BPM 的素材量化后更难听，量化交给 DAW。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.curve import MIDIS_A4, PitchCurve, median_smooth, to_cents


@dataclass(frozen=True)
class Note:
    start_sec: float
    dur_sec: float
    midi: int  # 0..127
    cents_offset: float  # 相对该 midi 音的偏差，范围 (-50, 50]


@dataclass(frozen=True)
class SegmentParams:
    # 低于这个长度的游程直接丢弃。120 ms 是「能听成一个音」的下限
    min_note_sec: float = 0.12
    # 平滑窗口（奇数帧）。5 帧 = 50 ms，能压掉单帧毛刺而不抹掉颤音
    smooth_window: int = 5
    # 迟滞容差（音分）。偏离当前半音超过它才换音；越小越敏感、越碎
    tolerance_cents: float = 60.0


def _midi_from_cents(cents: float) -> tuple[int, float]:
    """音分 → (MIDI 号, 音分偏差)。偏差落在 (-50, 50]。

    用「四舍五入到半音中心」而不是就近取整：偏差恰好 -50 音分时改用低半音，
    保证偏差区间是左开右闭，不会出现 -50 这种边界歧义。
    """
    midi = MIDIS_A4 + int(math.floor(cents / 100.0 + 0.5))
    offset = cents - 100.0 * (midi - MIDIS_A4)
    if offset <= -50.0:
        midi -= 1
        offset += 100.0
    return midi, offset


def _median_of(cents: tuple[float | None, ...], lo: int, hi: int) -> float | None:
    values = sorted(value for value in cents[lo : hi + 1] if value is not None)
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def _hysteresis_midi(
    cents: tuple[float | None, ...], tolerance: float
) -> list[int | None]:
    """逐帧量化到半音，带迟滞。

    直接四舍五入会让「在 69.5 附近漂移」的帧在 69 / 70 之间来回跳，
    一个长音被切成几十个碎片（实测 259 → 369 个音符）。
    这里只有当偏离当前半音超过 `tolerance` 才切换，跨多个半音时一次跳到位。
    """
    frames: list[int | None] = []
    current: int | None = None

    for value in cents:
        if value is None:
            frames.append(None)
            current = None
            continue
        if current is None:
            current = _midi_from_cents(value)[0]
        else:
            delta = value - 100.0 * (current - MIDIS_A4)
            while delta > tolerance:
                current += 1
                delta -= 100.0
            while delta < -tolerance:
                current -= 1
                delta += 100.0
        frames.append(current)

    return frames


def _runs(frames: list[int | None]) -> list[tuple[int, int, int]]:
    """游程编码：返回 [(midi, start, end)]，end 是闭区间。None 帧跳过。"""
    result: list[tuple[int, int, int]] = []
    index = 0
    total = len(frames)
    while index < total:
        current = frames[index]
        if current is None:
            index += 1
            continue
        end = index
        while end + 1 < total and frames[end + 1] == current:
            end += 1
        result.append((current, index, end))
        index = end + 1
    return result


def segment(curve: PitchCurve, params: SegmentParams = SegmentParams()) -> list[Note]:
    """把曲线切成音符列表。"""
    smoothed = median_smooth(curve.f0, params.smooth_window)
    # 平滑会把 None 帧「填成」有声，但无声帧必须一直是 None：
    # 否则 100 ms 的换气间隔会被抹平，音符边界跟着漂。
    masked = tuple(
        value if curve.f0[index] is not None else None for index, value in enumerate(smoothed)
    )
    cents = to_cents(masked)

    # 逐帧量化到半音（带迟滞），None 保持 None
    frames = _hysteresis_midi(cents, params.tolerance_cents)

    min_frames = max(1, int(round(params.min_note_sec / curve.hop_sec)))

    notes: list[Note] = []
    for _, start, end in _runs(frames):
        # 短游程：辅音、气声，以及颤音跨半音时的抖动，一律丢弃
        if end - start + 1 < min_frames:
            continue

        median = _median_of(cents, start, end)
        if median is None:
            continue
        midi, offset = _midi_from_cents(median)
        # 唱不出来的音丢弃而不是截断
        if not 0 <= midi <= 127:
            continue

        notes.append(
            Note(
                start_sec=start * curve.hop_sec,
                dur_sec=(end - start + 1) * curve.hop_sec,
                midi=midi,
                cents_offset=offset,
            )
        )

    notes.sort(key=lambda note: note.start_sec)

    # 重叠必须为 0：后一段起点 >= 前一段终点。游程天然不重叠，
    # 但丢弃短游程后可能出现「前一段尾巴被保留」的情况，统一兜一道。
    result: list[Note] = []
    for note in notes:
        if result:
            limit = result[-1].start_sec + result[-1].dur_sec
            if note.start_sec < limit:
                continue
        result.append(note)
    return result
