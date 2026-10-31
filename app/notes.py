"""音符产物：GAME 给边界，音高直接取模型 estimator 的输出（与 OpenUtau 同口径）。

OpenUtau 的 GAME 转录就是 `(int)Math.Round(note.noteScore)`：模型给的浮点 midi
四舍五入成整数音高，presence 未过阈的段直接丢弃。vocadito 人工标注实测：
模型 pitch 一致率 72.7%，FCPE 段内中位数只有 57.5%——V2 时代"FCPE 更准"的结论
是建立在当时边界被双重解码的基础上的，边界管线对齐 OpenUtau 后已经反转。

FCPE 曲线仍用于导出文件里的音高线（USTX 的 pitd / MIDI 的 pitch bend）。

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


def build_notes(segments) -> list[dict]:
    """GAME 的段 -> 最终音符：音高 = 模型浮点 midi 四舍五入，unvoiced 段丢弃。

    round() 与 OpenUtau 的 C# Math.Round 一样是银行家舍入，.5 中点行为一致。
    """
    out: list[dict] = []
    for segment in segments:
        if not segment.get("voiced", True):
            continue
        start = float(segment["start_sec"])
        dur = float(segment["dur_sec"])
        if dur <= 0:
            continue
        midi_float = float(segment.get("midi", 0.0))
        midi = round(midi_float)
        if not 0 <= midi <= 127:
            continue
        out.append(
            {
                "start_sec": round(start, 4),
                "dur_sec": round(dur, 4),
                "midi": midi,
                "cents_offset": round((midi_float - midi) * 100.0, 3),
            }
        )
    return out


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
        "segments": build_notes(segments),
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
