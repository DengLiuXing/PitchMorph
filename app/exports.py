"""派生产物：`notes.json` / MIDI 双模式 / USTX。

纯函数层（`app/domain`）负责算，这里只负责读写。**不 import torch**，
所以主进程可以直接调用，不需要再开子进程——这一步是毫秒级的。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.domain import (
    PitchCurve,
    SegmentParams,
    fix_octave_jumps,
    median_smooth,
    segment,
    to_bend_midi,
    to_note_midi,
)
from app.domain.ustx import to_ustx

DEFAULT_SMOOTH = 3
# 中值滤波窗口必须是奇数；上限给到 15 帧（150 ms），再大会把颤音也抹平
MAX_SMOOTH = 15

# kind → 文件名。下载接口用 kind 做白名单，避免用户拼路径
EXPORT_FILES: dict[str, str] = {
    "notes_midi": "vocals_notes.mid",
    "bend_midi": "vocals_bend.mid",
    "ustx": "vocals.ustx",
}


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _notes_payload(notes, bpm: float) -> dict:
    return {
        "version": 1,
        "bpm": bpm,
        "count": len(notes),
        "notes": [
            {
                "start_sec": round(note.start_sec, 4),
                "dur_sec": round(note.dur_sec, 4),
                "midi": note.midi,
                "cents_offset": round(note.cents_offset, 2),
            }
            for note in notes
        ],
    }


def normalize_smooth(value: int) -> int:
    """窗口必须是奇数且在 1..15 之间，用户传 4 就取 5。"""
    clamped = max(1, min(MAX_SMOOTH, int(value)))
    return clamped if clamped % 2 == 1 else clamped + 1


def load_onsets(root: Path) -> list[int] | None:
    """读 `features/onset.json` 的起音帧。缺失或损坏一律当作「没有」。

    起音只是切分的辅助证据，导出不能因为它而出错：老任务没有这个文件，
    手动改坏一个字段也不该让 MIDI 下不下来。
    """
    path = root / "features" / "onset.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    frames = payload.get("frames")
    raw = payload.get("onsets")
    if not isinstance(frames, int) or isinstance(frames, bool) or not isinstance(raw, list):
        return None
    return [
        index
        for index in raw
        if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < frames
    ]


def _prepare(root: Path, smooth: int) -> tuple[PitchCurve, list]:
    """读 pitch.json → 去倍频跳变 → 按窗口平滑 → 切音符。

    平滑在这里做一次，`segment` 内部就不再平滑（传 `smooth_window=1`），
    否则会叠两层滤波，颤音被抹两次。
    """
    payload = json.loads((root / "features" / "pitch.json").read_text(encoding="utf-8"))
    raw = PitchCurve.from_payload(payload)

    # 先修倍频跳变：曲线突然跳八度再跳回来，会让切分多出一堆假音符。
    # 中值滤波压不住这种大台阶，必须单独治。
    f0 = fix_octave_jumps(raw.f0)
    window = normalize_smooth(smooth)
    if window > 1:
        f0 = median_smooth(f0, window)

    curve = PitchCurve(hop_sec=raw.hop_sec, f0=f0)
    # 平滑不改变帧索引，所以起音帧号不需要任何偏移补偿
    notes = segment(curve, SegmentParams(smooth_window=1), load_onsets(root))
    return curve, notes


def render_export(root: Path, kind: str, *, smooth: int = DEFAULT_SMOOTH, bpm: float = 120.0) -> bytes:
    """按指定平滑度即时生成一个导出文件（不落盘，供下载接口参数化使用）。"""
    curve, notes = _prepare(root, smooth)
    if kind == "notes_midi":
        return to_note_midi(notes, bpm=bpm)
    if kind == "bend_midi":
        return to_bend_midi(curve, notes, bpm=bpm)
    if kind == "ustx":
        return to_ustx(curve, notes, bpm=bpm).encode("utf-8")
    raise ValueError(f"未知导出类型 {kind}")


def write_exports(root: Path, *, bpm: float = 120.0) -> dict[str, str]:
    """生成 `exports/` 下的三件产物（默认平滑度），返回 ``{kind: 相对路径}``。

    音高数据不存在时返回空字典——没有曲线就导不出音符。
    用户改平滑度时走 `render_export`，不会覆盖这里的默认版本。
    """
    pitch_path = root / "features" / "pitch.json"
    if not pitch_path.exists():
        return {}

    curve, notes = _prepare(root, DEFAULT_SMOOTH)

    features = root / "features"
    features.mkdir(parents=True, exist_ok=True)
    (features / "notes.json").write_text(
        json.dumps(_notes_payload(notes, bpm), ensure_ascii=False),
        encoding="utf-8",
    )

    out_dir = root / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    payloads = {
        "notes_midi": to_note_midi(notes, bpm=bpm),
        "bend_midi": to_bend_midi(curve, notes, bpm=bpm),
        "ustx": to_ustx(curve, notes, bpm=bpm).encode("utf-8"),
    }

    artifacts: dict[str, str] = {}
    for kind, data in payloads.items():
        path = out_dir / EXPORT_FILES[kind]
        _atomic_write(path, data)
        artifacts[kind] = f"exports/{path.name}"
    return artifacts
