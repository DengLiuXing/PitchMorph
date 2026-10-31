"""音符产物：GAME 给边界，FCPE 段内中位数给音高。

**借边界、不借音高**：GAME 只负责切分音符边界，音高用该段内 FCPE 曲线的中位数。
V2 实测 FCPE 段内中位数的音高一致率比 GAME 自带的 pitch 高 14 个点，所以模型的 midi
只作为对照留在 `model_segments` 里，不参与导出。

纯函数，不做磁盘 IO：读音高曲线（`pitch_{stem}.json`）与 GAME 的段，产出最终音符。
"""
from __future__ import annotations

import math

REF_HZ = 440.0
MIDIS_A4 = 69
NOTES_VERSION = 1


def to_cents(f0: list[float], voiced: list[bool]) -> list[float | None]:
    """Hz -> 音分。无声帧（voiced=False 或 f0<=0）记 None，绝不用 0 Hz 冒充。"""
    cents: list[float | None] = []
    for value, is_voiced in zip(f0, voiced):
        if not is_voiced or value <= 0:
            cents.append(None)
            continue
        cents.append(1200.0 * math.log2(value / REF_HZ))
    return cents


def midi_from_cents(cents: float) -> tuple[int, float]:
    """音分 -> (MIDI 号, 相对该号的音分偏差)。偏差落在 (-50, 50]。"""
    midi = MIDIS_A4 + int(math.floor(cents / 100.0 + 0.5))
    offset = cents - 100.0 * (midi - MIDIS_A4)
    if offset <= -50.0:
        midi -= 1
        offset += 100.0
    return midi, offset


def median_of(values: list[float | None], lo: int, hi: int) -> float | None:
    items = sorted(value for value in values[lo : hi + 1] if value is not None)
    if not items:
        return None
    mid = len(items) // 2
    if len(items) % 2 == 1:
        return items[mid]
    return (items[mid - 1] + items[mid]) / 2.0


def spans_from_segments(segments, hop_sec: float, frames: int) -> list[tuple[int, int]]:
    """GAME 的秒值段 -> 帧区间（闭区间），越界裁掉、零长丢弃。"""
    spans: list[tuple[int, int]] = []
    for segment in segments:
        start = float(segment["start_sec"])
        dur = float(segment["dur_sec"])
        if dur <= 0 or hop_sec <= 0:
            continue
        lo = max(0, round(start / hop_sec))
        hi = min(frames - 1, round((start + dur) / hop_sec) - 1)
        if hi < lo:
            continue
        spans.append((lo, hi))
    return spans


def build_notes(segments, curve: dict) -> list[dict]:
    """GAME 边界 + FCPE 段内中位数 -> 最终音符。

    **借边界不借音高**：V2 实测 FCPE 段内中位数的音高一致率比模型自带 pitch 高 14 个点，
    所以音高一律用段内中位数，模型给的 midi 只留在 model_segments 里做对照。
    """
    hop_sec = float(curve["hop_ms"]) / 1000.0
    cents = to_cents(curve["f0"], curve["voiced"])
    frames = len(cents)
    notes: list[dict] = []
    for lo, hi in spans_from_segments(segments, hop_sec, frames):
        median = median_of(cents, lo, hi)
        if median is None:
            continue
        midi, offset = midi_from_cents(median)
        if not 0 <= midi <= 127:
            continue
        notes.append(
            {
                "start_sec": round(lo * hop_sec, 4),
                "dur_sec": round((hi - lo + 1) * hop_sec, 4),
                "midi": midi,
                "cents_offset": round(offset, 3),
            }
        )
    return notes


def build_payload(
    segments: list[dict],
    curve: dict,
    *,
    stem: str,
    model: str,
    tempo: float,
    params: dict,
) -> dict:
    """notes_{stem}.json 的结构。"""
    return {
        "version": NOTES_VERSION,
        "model": model,
        "stem": stem,
        "hop_ms": curve["hop_ms"],
        "tempo": tempo,
        "params": params,
        "segments": build_notes(segments, curve),
        "model_segments": [
            {
                "start_sec": round(float(item["start_sec"]), 4),
                "dur_sec": round(float(item["dur_sec"]), 4),
                "midi": round(float(item.get("midi", 0.0)), 3),
                "voiced": bool(item.get("voiced", True)),
            }
            for item in segments
        ],
    }
