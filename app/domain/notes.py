"""音高曲线（+ 起音帧）→ 音符列表。纯函数，不 import torch、不做磁盘 IO。

算法与理由（v2.1 重写，因为 v2.0 切得太碎）：

v2.0 用「相邻帧差 > 80 音分就切」，结果颤音和滑音一路触发切分：
12 s 的片段切出 307 个音符（每秒 25 个），而正常唱歌是每秒 2–5 个音节。

现在的做法是**先量化到半音、再按切点做完全划分**：

1. 平滑后逐帧算 MIDI 号（四舍五入到半音）
2. 取「连续有声帧」的区间（静音处自然断开）
3. 区间内部按两类切点切开：**换半音**、**辅音起音**
4. 短于 `min_note_sec` 的片段并入相邻片段——它们多半是颤音跨半音边界时的抖动，
   本该属于前一个音
5. 每个片段用原始音分的中位数定精确音高，避免中位数落在半音边界上

第 4 步曾经写成「直接丢弃」，结果每个被丢的抖动都在非静音区里挖一个洞，
音符看起来一小段一小段（实测空洞占非静音时长的 24%–37%）。
现在是完全划分：**非静音区间内音符首尾相接，只有静音才留缝**。

这样「同一音高的一串帧」天然是一段，颤音（围绕中心振荡、MIDI 号不常变）不会被切开，
而真正的换音（跨半音）会。

**游程的盲区**：同一个音高连着唱两个字时，音高根本没变，游程不会断。
能断开它的只有辅音起音，所以区间内部还会按 `onsets`（起音帧）再切一刀——
起音与游程是并集关系，不是替代关系。

默认不量化到节拍：没有 BPM 的素材量化后更难听，量化交给 DAW。
"""

from __future__ import annotations

import math
import typing as tp
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
    # 起音切分：游程内部按起音帧再切。关掉就退回「只有换音才切」
    split_on_onsets: bool = True
    # 两个切点之间的最小间隔。起音检测在辅音爆破上常连着出两峰，
    # 60 ms 与下面 min_note_sec 配合，保证不会切出碎片
    onset_min_gap_sec: float = 0.06


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


def _voiced_spans(frames: list[int | None]) -> list[tuple[int, int]]:
    """连续有声帧的区间 [start, end]，闭区间。静音处断开。"""
    spans: list[tuple[int, int]] = []
    index = 0
    total = len(frames)
    while index < total:
        if frames[index] is None:
            index += 1
            continue
        start = index
        while index + 1 < total and frames[index + 1] is not None:
            index += 1
        spans.append((start, index))
        index += 1
    return spans


def _cut_points(
    frames: list[int | None],
    start: int,
    end: int,
    marks: set[int],
    gap_frames: int,
) -> list[int]:
    """区间内部的切点：换半音必切，起音按最小间隔切。"""
    cuts: list[int] = []
    last = start
    for index in range(start + 1, end + 1):
        changed = frames[index] != frames[index - 1]
        on_onset = index in marks and index - last >= gap_frames
        if changed or on_onset:
            cuts.append(index)
            last = index
    return cuts


def _pieces(
    frames: list[int | None],
    start: int,
    end: int,
    cuts: list[int],
) -> list[list[int]]:
    """按切点切出的片段：[lo, hi]，闭区间、首尾相接。"""
    bounds = [start, *cuts, end + 1]
    return [[bounds[index], bounds[index + 1] - 1] for index in range(len(bounds) - 1)]


def _merge_short(pieces: list[list[int]], min_frames: int) -> list[list[int]]:
    """把短于 `min_frames` 的片段并进相邻片段。

    早先这里是「直接丢弃」，结果每个被丢的抖动都在非静音区里挖一个洞，
    音符看起来一小段一小段。并入邻居能把洞填掉，而碎片的音高本来也不该
    单独成一个音符，所以听感上等价、时间轴却连续了。
    """
    index = 0
    while index < len(pieces) and len(pieces) > 1:
        lo, hi = pieces[index]
        if hi - lo + 1 >= min_frames:
            index += 1
            continue
        if index > 0:
            # 并入前一段：前一段的起点不变，只把尾巴延长
            pieces[index - 1][1] = hi
            del pieces[index]
        else:
            # 第一段太短：让后一段吃掉它
            pieces[1][0] = lo
            del pieces[0]
    return pieces


def _dominant_midi(frames: list[int | None], lo: int, hi: int) -> tuple[int | None, float]:
    """区间内出现最多的半音（众数）及其占比。

    不能用「起始帧的半音」：合并把几段不同音高连成一整段后，起始帧往往只是
    滑音路过的过渡音，音符会停在完全错误的音上（实测有 0.46 s 的音符标成
    58、实际是 67）。众数才是这段真正停留的音。

    返回的占比决定这段到底「有没有一个确定的音」：占比高 = 停在这个音上，
    占比低 = 一段滑音，没有哪个音算主导，交给中位数去定。
    """
    counts: dict[int, int] = {}
    total = 0
    for index in range(lo, hi + 1):
        value = frames[index]
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
        total += 1
    if not counts:
        return None, 0.0
    # dict 保序，并列时取先出现的那个（最早唱到的音高）
    best = max(counts, key=lambda value: counts[value])
    return best, counts[best] / total


def _note_from(
    cents: tuple[float | None, ...],
    lo: int,
    hi: int,
    hop_sec: float,
    midi_hint: int | None = None,
) -> Note | None:
    """取 [lo, hi] 这一段的中位数音高，生成一个音符。

    `midi_hint` 是整条游程量化出的半音：拆分时沿用它，避免两段各自四舍五入
    落到相邻半音——那等于「切一刀顺手把音改了」。只有中位数偏离 hint 超过
    半个半音（理论上只出现在游程端点）时才回落到独立量化。
    """
    median = _median_of(cents, lo, hi)
    if median is None:
        return None
    midi, offset = _midi_from_cents(median)
    if midi_hint is not None:
        # 有确定的音就把它钉在这个音上。中位数偏离超过半音时**不能**重新量化：
        # 那会把音符拽到滑音路过的过渡音上（实测错到 9 个半音）。
        # 偏移截到合法区间，真正的音高细节由 pitch 曲线承担。
        hinted = median - 100.0 * (midi_hint - MIDIS_A4)
        midi, offset = midi_hint, max(-49.9, min(50.0, hinted))
    # 唱不出来的音丢弃而不是截断
    if not 0 <= midi <= 127:
        return None
    return Note(
        start_sec=lo * hop_sec,
        dur_sec=(hi - lo + 1) * hop_sec,
        midi=midi,
        cents_offset=offset,
    )


def segment(
    curve: PitchCurve,
    params: SegmentParams = SegmentParams(),
    onsets: tp.Iterable[int] | None = None,
) -> list[Note]:
    """把曲线切成音符列表。`onsets` 是起音帧索引，与 `curve.f0` 同栅格。

    切分是**完全划分**：每个非静音区间被切点分成首尾相接的片段，中间不留缝；
    只有静音（None 帧）才会在音符之间留下间隙。
    起音与换半音是并集：游程决定「换音」，起音决定「换字」。
    """
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
    gap_frames = max(1, int(round(params.onset_min_gap_sec / curve.hop_sec)))
    marks = (
        {int(index) for index in onsets}
        if onsets and params.split_on_onsets
        else set()
    )

    notes: list[Note] = []
    for start, end in _voiced_spans(frames):
        # 孤立的一小段（气口、噪声）：整段丢掉，不单独成一个音符
        if end - start + 1 < min_frames:
            continue
        cuts = _cut_points(frames, start, end, marks, gap_frames)
        for lo, hi in _merge_short(_pieces(frames, start, end, cuts), min_frames):
            midi, share = _dominant_midi(frames, lo, hi)
            if midi is None:
                continue
            # 占比过低的片段是一段滑音，没有主导音，交给中位数
            note = _note_from(cents, lo, hi, curve.hop_sec, midi if share >= 0.5 else None)
            if note is not None:
                notes.append(note)

    notes.sort(key=lambda note: note.start_sec)

    # 兜底：完全划分天然不重叠，这里只挡真正越界的。
    # 1e-6 秒的容差必须有：`lo * hop + n * hop` 与 `(lo + n) * hop` 在二进制
    # 浮点下会差 1e-16，严格比较会把首尾相接的音符误判成重叠并丢掉（实测丢 3/34）。
    epsilon = 1e-6
    result: list[Note] = []
    for note in notes:
        if result:
            limit = result[-1].start_sec + result[-1].dur_sec
            if note.start_sec < limit - epsilon:
                continue
        result.append(note)
    return result
