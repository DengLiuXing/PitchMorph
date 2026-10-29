"""派生产物：`notes.json` / MIDI 双模式 / USTX。

纯函数层（`app/domain`）负责算，这里只负责读写。**不 import torch**，
所以主进程可以直接调用，不需要再开子进程——这一步是毫秒级的。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.domain import PitchCurve, segment, to_bend_midi, to_note_midi
from app.domain.ustx import to_ustx

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


def write_exports(root: Path, *, bpm: float = 120.0) -> dict[str, str]:
    """生成 `exports/` 下的三件产物，返回 ``{kind: 相对任务目录的路径}``。

    音高数据不存在时返回空字典——没有曲线就导不出音符。
    """
    pitch_path = root / "features" / "pitch.json"
    if not pitch_path.exists():
        return {}

    curve = PitchCurve.from_payload(json.loads(pitch_path.read_text(encoding="utf-8")))
    notes = segment(curve)

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
