"""音符 → MIDI 字节。纯函数：返回完整 .mid 文件的 bytes，调用方负责落盘。

两种模式：
- `to_note_midi`：每个音符一个普通音符，只有量化后的音高。
- `to_bend_midi`：每个音符一条长音符，用 pitch bend 还原细��的音高曲线，
  这样颤音和滑音能保留下来，而不是被 80 音分的阈值抹平。

用 `pretty_midi.PrettyMIDI` + `io.BytesIO`，不碰磁盘。
"""

from __future__ import annotations

import io
import math

import pretty_midi

from app.domain.curve import MIDIS_A4, PitchCurve, to_cents
from app.domain.notes import Note

VELOCITY = 100

# MIDI pitch bend 的取值范围：-8192..8191 对应 -bend_range..+bend_range 音分。
# 上界是 8191 而不是 8192：mido 会拒绝 8192，写出去直接抛异常。
_BEND_MIN = -8192
_BEND_MAX = 8191


def _new_midi(bpm: float, program: int) -> pretty_midi.PrettyMIDI:
    """空壳 MIDI：带 tempo 和 program。空 notes 也要返回合法文件。"""
    if bpm <= 0:
        raise ValueError("bpm 必须为正数")
    midi = pretty_midi.PrettyMIDI(initial_tempo=bpm)
    instrument = pretty_midi.Instrument(program=program)
    midi.instruments.append(instrument)
    return midi


def _set_bend_range(instrument: pretty_midi.Instrument, bend_range_cents: float) -> None:
    """写 RPN 把弯音范围设成 bend_range_cents 音分。

    MIDI 默认弯音范围是 ±2 半音（=200 音分），正好是本项目的默认值；
    范围不是默认值时必须显式写 RPN，否则播放器会按 200 音分解释弯音值。
    """
    cents = int(round(bend_range_cents))
    if cents <= 0:
        raise ValueError("bend_range_cents 必须为正数")
    msb = (cents >> 7) & 0x7F
    lsb = cents & 0x7F
    # RPN 0（弯音范围）= 数据进入 MSB/LSB
    instrument.control_changes.append(pretty_midi.ControlChange(number=101, value=0, time=0.0))
    instrument.control_changes.append(pretty_midi.ControlChange(number=100, value=0, time=0.0))
    instrument.control_changes.append(pretty_midi.ControlChange(number=6, value=msb, time=0.0))
    instrument.control_changes.append(pretty_midi.ControlChange(number=38, value=lsb, time=0.0))


def to_note_midi(notes: list[Note], *, bpm: float = 120.0, program: int = 54) -> bytes:
    """音符列表 → MIDI 文件字节。每个 Note 一个音符，velocity 统一 100。"""
    midi = _new_midi(bpm, program)
    instrument = midi.instruments[0]
    for note in notes:
        if not 0 <= note.midi <= 127:
            continue
        end = note.start_sec + max(note.dur_sec, 0.0)
        if end <= note.start_sec:
            continue
        instrument.notes.append(
            pretty_midi.Note(
                velocity=VELOCITY,
                pitch=int(note.midi),
                start=float(note.start_sec),
                end=float(end),
            )
        )

    buffer = io.BytesIO()
    midi.write(buffer)
    return buffer.getvalue()


def to_bend_midi(
    curve: PitchCurve,
    notes: list[Note],
    *,
    bpm: float = 120.0,
    program: int = 54,
    bend_hop_sec: float = 0.01,
    bend_range_cents: float = 200.0,
) -> bytes:
    """音符 + 音高曲线 → 带 pitch bend 的 MIDI 文件字节。

    每个 Note 生成一条覆盖其时长的长音符（pitch = note.midi），
    然后按 `bend_hop_sec` 的密度采样曲线，把「相对该音符 midi 音的音分差」
    换算成弯音值。曲线为 None（无声）的采样点直接跳过，不写事件。
    """
    if bend_hop_sec <= 0:
        raise ValueError("bend_hop_sec 必须为正数")

    midi = _new_midi(bpm, program)
    instrument = midi.instruments[0]
    _set_bend_range(instrument, bend_range_cents)

    cents = to_cents(curve.f0)
    hop = curve.hop_sec if curve.hop_sec > 0 else bend_hop_sec

    for note in notes:
        if not 0 <= note.midi <= 127:
            continue
        end = note.start_sec + max(note.dur_sec, 0.0)
        if end <= note.start_sec:
            continue
        instrument.notes.append(
            pretty_midi.Note(
                velocity=VELOCITY,
                pitch=int(note.midi),
                start=float(note.start_sec),
                end=float(end),
            )
        )

        # 该音符的基准音分：弯音值是相对它的偏差，不是相对 A4
        base_cents = 100.0 * (note.midi - MIDIS_A4)

        samples = math.ceil((end - note.start_sec) / bend_hop_sec)
        for index in range(samples):
            time = note.start_sec + index * bend_hop_sec
            if time >= end:
                break
            frame = int(round(time / hop))
            if frame < 0 or frame >= len(cents):
                continue
            value = cents[frame]
            if value is None:
                continue
            offset = value - base_cents
            bend = int(round(offset / bend_range_cents * 8192))
            bend = max(_BEND_MIN, min(_BEND_MAX, bend))
            instrument.pitch_bends.append(
                pretty_midi.PitchBend(pitch=bend, time=float(time))
            )

    buffer = io.BytesIO()
    midi.write(buffer)
    return buffer.getvalue()
