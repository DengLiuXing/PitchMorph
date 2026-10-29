"""音高曲线 → 音符列表。纯函数，不 import torch、不做磁盘 IO。

算法与理由：
- 最短音符 80 ms：低于这个长度的碎片是气声与辅音残留，不是音符。
- 切分阈值 80 音分/帧：半音是 100 音分，80 以下多半是颤音而不是换音。
- 先平滑再切分：中位数滤波去掉孤立尖峰，避免一个爆破音把一个长音切成两半。
- 默认不量化到节拍：没有 BPM 的素材量化后更难听，量化交给 DAW。
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
    min_note_sec: float = 0.08  # 低于这个长度的碎片并入邻居或丢弃
    jump_cents: float = 80.0  # 段内切分：相邻帧超过这个音分差视为换音
    smooth_window: int = 3


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


def _voiced_runs(cents: tuple[float | None, ...]) -> list[tuple[int, int]]:
    """把音分序列按 None 切成连续有声段，返回 [start, end] 闭区间索引。"""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(cents):
        if value is None:
            if start is not None:
                runs.append((start, index - 1))
                start = None
        elif start is None:
            start = index
    if start is not None:
        runs.append((start, len(cents) - 1))
    return runs


def _split_by_jump(
    cents: tuple[float | None, ...], run: tuple[int, int], jump_cents: float
) -> list[tuple[int, int]]:
    """段内按相邻帧音分差切分。"""
    start, end = run
    pieces: list[tuple[int, int]] = []
    piece_start = start
    for index in range(start + 1, end + 1):
        prev = cents[index - 1]
        curr = cents[index]
        assert prev is not None and curr is not None
        if abs(curr - prev) > jump_cents:
            pieces.append((piece_start, index - 1))
            piece_start = index
    pieces.append((piece_start, end))
    return pieces


def segment(curve: PitchCurve, params: SegmentParams = SegmentParams()) -> list[Note]:
    """把曲线切成音符列表。"""
    smoothed = median_smooth(curve.f0, params.smooth_window)
    # 平滑会把 None 帧「填成」有声，但无声帧必须一直是 None：
    # 否则 100 ms 的换气间隔会被抹平，音符边界跟着漂。
    masked = tuple(
        value if curve.f0[index] is not None else None
        for index, value in enumerate(smoothed)
    )
    cents = to_cents(masked)

    raw: list[Note] = []
    for run in _voiced_runs(cents):
        for piece in _split_by_jump(cents, run, params.jump_cents):
            start_idx, end_idx = piece
            values = [cents[i] for i in range(start_idx, end_idx + 1)]
            values = [v for v in values if v is not None]
            if not values:
                continue
            values.sort()
            mid = len(values) // 2
            if len(values) % 2 == 1:
                median = values[mid]
            else:
                median = (values[mid - 1] + values[mid]) / 2.0

            midi, offset = _midi_from_cents(median)
            start_sec = start_idx * curve.hop_sec
            end_sec = (end_idx + 1) * curve.hop_sec
            raw.append(
                Note(
                    start_sec=start_sec,
                    dur_sec=end_sec - start_sec,
                    midi=midi,
                    cents_offset=offset,
                )
            )

    # 碎片处理：与前一段同 midi 就并入前一段，否则丢弃
    merged: list[Note] = []
    for note in raw:
        if note.dur_sec < params.min_note_sec:
            if merged and merged[-1].midi == note.midi:
                prev = merged[-1]
                merged[-1] = Note(
                    start_sec=prev.start_sec,
                    dur_sec=(note.start_sec + note.dur_sec) - prev.start_sec,
                    midi=prev.midi,
                    cents_offset=prev.cents_offset,
                )
            continue
        merged.append(note)

    # MIDI 号越界的是唱不出来的音，丢弃而不是截断
    kept = [n for n in merged if 0 <= n.midi <= 127]
    kept.sort(key=lambda n: n.start_sec)

    # 重叠必须为 0：碎片并入前一段后，前一段的尾巴可能压到后一段头上，
    # 这里统一裁到「前一段终点」，保证「后一段起点 >= 前一段终点」。
    result: list[Note] = []
    for note in kept:
        if result:
            prev = result[-1]
            limit = prev.start_sec + prev.dur_sec
            if note.start_sec < limit:
                dur = limit - note.start_sec
                if dur <= 0:
                    # 完全没有时长 → 这个音符没有存在意义
                    continue
                note = Note(
                    start_sec=note.start_sec,
                    dur_sec=dur,
                    midi=note.midi,
                    cents_offset=note.cents_offset,
                )
        result.append(note)
    return result
