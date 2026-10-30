"""音符切分对照：拿 OpenUtau 导出的 USTX 当参考，量化本工具的误差。

用法（先跑完音高阶段，拿到 features/pitch.json）：

    uv run python scripts/compare_notes.py \
        --pitch data/jobs/<id>/features/pitch.json \
        --ref temp-dev/samples/openutau_official/物凄芙兰.ustx

只依赖 pytorch 之外的东西（yaml / 纯函数层），不加载任何模型。
换模型（GAME 等）之后用同一条命令复测，指标才有可比性。

三个指标各自回答一个问题：
- 音符数 / 时长分布：切得比参考碎还是粗
- 起音召回/精确（±容差）：边界对不上的比例
- 音高一致率：同一个时间点上，两边标的音高是否相同
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.domain import fix_octave_jumps, median_smooth  # noqa: E402
from app.domain.curve import PitchCurve  # noqa: E402
from app.domain.notes import SegmentParams, segment  # noqa: E402
from app.exports import load_onsets  # noqa: E402

RESOLUTION = 480  # tick per quarter，OpenUtau 侧恒为 480


def load_reference(path: Path, part_index: int = 0) -> tuple[list[tuple[float, float, int]], float]:
    """USTX → [(start_sec, dur_sec, midi)]，以及每 tick 的秒数。

    只支持单一 tempo 的工程（参考样例都是 120 bpm）；多 tempo 会取第一个。
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    tempos = data.get("tempos") or [{"position": 0, "bpm": 120}]
    bpm = float(tempos[0]["bpm"])
    sec_per_tick = 60.0 / (bpm * RESOLUTION)

    parts = data.get("voice_parts") or []
    if part_index >= len(parts):
        raise ValueError(f"{path.name} 只有 {len(parts)} 个声部")
    part = parts[part_index]
    offset = int(part.get("position") or 0)

    notes: list[tuple[float, float, int]] = []
    for note in part.get("notes") or []:
        start = (offset + int(note["position"])) * sec_per_tick
        duration = int(note["duration"]) * sec_per_tick
        notes.append((start, duration, int(note["tone"])))
    notes.sort()
    return notes, sec_per_tick


def load_ours(
    pitch_path: Path,
    smooth: int,
    use_onsets: bool,
    min_note_sec: float = 0.12,
    tolerance_cents: float = 60.0,
) -> list[tuple[float, float, int]]:
    """features/pitch.json → [(start_sec, dur_sec, midi)]，走导出用的同一条路径。"""
    payload = json.loads(pitch_path.read_text(encoding="utf-8"))
    raw = PitchCurve.from_payload(payload)
    f0 = fix_octave_jumps(raw.f0)
    if smooth > 1:
        f0 = median_smooth(f0, smooth)
    curve = PitchCurve(hop_sec=raw.hop_sec, f0=f0)
    onsets = load_onsets(pitch_path.parent.parent) if use_onsets else None
    params = SegmentParams(
        smooth_window=1,
        min_note_sec=min_note_sec,
        tolerance_cents=tolerance_cents,
    )
    notes = segment(curve, params, onsets)
    return [(note.start_sec, note.dur_sec, note.midi) for note in notes]


def onset_stats(
    ours: list[tuple[float, float, int]],
    ref: list[tuple[float, float, int]],
    tolerance: float,
) -> tuple[float, float]:
    """(召回, 精确)。召回 = 参考的边界里有多少被我们找到。"""
    ours_onsets = [start for start, _, _ in ours]
    ref_onsets = [start for start, _, _ in ref]

    def hit(values: list[float], targets: list[float]) -> int:
        return sum(1 for value in values if any(abs(value - t) <= tolerance for t in targets))

    recall = hit(ref_onsets, ours_onsets) / len(ref_onsets) if ref_onsets else 0.0
    precision = hit(ours_onsets, ref_onsets) / len(ours_onsets) if ours_onsets else 0.0
    return recall, precision


def pitch_stats(
    ours: list[tuple[float, float, int]],
    ref: list[tuple[float, float, int]],
    step: float = 0.02,
) -> tuple[int, int, int]:
    """(一致, 差 1 个半音, 无覆盖)。在参考音符内部逐点比较。"""
    same = near = missing = 0
    for start, duration, tone in ref:
        point = start + duration / 2
        while point < start + duration:
            match = next(
                (m for s, d, m in ours if s <= point < s + d),
                None,
            )
            if match is None:
                missing += 1
            elif match == tone:
                same += 1
            elif abs(match - tone) == 1:
                near += 1
            point += step
    return same, near, missing


def describe(label: str, notes: list[tuple[float, float, int]]) -> str:
    if not notes:
        return f"{label}: 0 音符"
    durations = [duration for _, duration, _ in notes]
    span = max(start + duration for start, duration, _ in notes) - min(start for start, _, _ in notes)
    return (
        f"{label}: {len(notes)} 音符 · 均值 {statistics.fmean(durations):.3f}s · "
        f"中位 {statistics.median(durations):.3f}s · 密度 {len(notes) / max(span, 1e-6):.2f}/s"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="音符切分对照")
    parser.add_argument("--pitch", required=True, type=Path, help="features/pitch.json")
    parser.add_argument("--ref", required=True, type=Path, help="参考 USTX")
    parser.add_argument("--smooth", type=int, default=3, help="导出用的中值滤波窗口")
    parser.add_argument("--tolerance", type=float, default=0.05, help="起音匹配容差（秒）")
    parser.add_argument("--no-onsets", action="store_true", help="不用起音切分（复现旧行为）")
    parser.add_argument("--part", type=int, default=0, help="参考里用第几个声部")
    parser.add_argument("--min-note", type=float, default=0.12, help="最短音符（秒），短于此并入邻居")
    parser.add_argument("--cents", type=float, default=60.0, help="换半音的迟滞容差（音分）")
    args = parser.parse_args()

    ref, _ = load_reference(args.ref, args.part)
    ours = load_ours(
        args.pitch,
        args.smooth,
        not args.no_onsets,
        min_note_sec=args.min_note,
        tolerance_cents=args.cents,
    )

    print(describe("参考 OpenUtau", ref))
    print(describe("本工具      ", ours))
    recall, precision = onset_stats(ours, ref, args.tolerance)
    print(f"起音 召回 {recall * 100:5.1f}% · 精确 {precision * 100:5.1f}%（±{args.tolerance * 1000:.0f} ms）")
    same, near, missing = pitch_stats(ours, ref)
    total = same + near + missing
    if total:
        print(
            f"音高 一致 {same / total * 100:5.1f}% · 差 1 半音 {near / total * 100:5.1f}% · "
            f"无覆盖 {missing / total * 100:5.1f}%"
        )

    ref_same_pitch = sum(1 for a, b in zip(ref, ref[1:]) if a[2] == b[2])
    our_same_pitch = sum(1 for a, b in zip(ours, ours[1:]) if a[2] == b[2])
    print(f"同音高相邻音符：参考 {ref_same_pitch} · 本工具 {our_same_pitch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
