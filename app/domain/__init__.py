"""纯函数层：音高曲线 → 音符 → MIDI。

与 `app/worker`（唯一 import torch 的地方）通过 `features/*.json` 解耦，
本层不 import torch、不做磁盘 IO：输入什么就输出什么。
"""

from __future__ import annotations

from app.domain.curve import (
    PitchCurve,
    fix_octave_jumps,
    median_smooth,
    to_cents,
)
from app.domain.midi import to_bend_midi, to_note_midi
from app.domain.notes import (
    Note,
    SegmentParams,
    durations_from_boundaries,
    notes_from_spans,
    segment,
    spans_from_segments,
)

__all__ = [
    "PitchCurve",
    "Note",
    "SegmentParams",
    "segment",
    "durations_from_boundaries",
    "notes_from_spans",
    "spans_from_segments",
    "to_cents",
    "median_smooth",
    "fix_octave_jumps",
    "to_note_midi",
    "to_bend_midi",
]
