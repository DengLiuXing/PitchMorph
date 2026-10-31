"""用 vocadito 的 F0 标注验收音高精度（本轮唯一可量化的质量指标）。"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from app import audio, pitch

SAMPLES = Path(__file__).resolve().parent.parent / "temp-dev" / "samples" / "vocadito"

pytestmark = pytest.mark.skipif(not SAMPLES.exists(), reason="缺少 vocadito 样例素材")


def _load_gt(clip: str) -> dict[float, float]:
    path = SAMPLES / "Annotations" / "F0" / f"{clip}_f0.csv"
    gt: dict[float, float] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            try:
                time_s, f0 = float(row[0]), float(row[1])
            except ValueError:
                continue
            gt[time_s] = f0
    return gt


def _cents_error(est: float, ref: float) -> float:
    return abs(1200.0 * np.log2(est / ref))


@pytest.mark.parametrize("clip", ["vocadito_1", "vocadito_10"])
def test_pitch_matches_annotation(clip: str) -> None:
    wav = SAMPLES / "Audio" / f"{clip}.wav"
    curve = pitch.pitch_file(wav)
    times = np.asarray(curve["times"])
    f0 = np.asarray(curve["f0"])
    voiced = np.asarray(curve["voiced"])

    gt = _load_gt(clip)
    errors: list[float] = []
    covered = 0
    total_voiced = 0
    for time_s, ref in gt.items():
        if ref <= 0:
            continue
        total_voiced += 1
        index = int(np.argmin(np.abs(times - time_s)))
        if not voiced[index] or f0[index] <= 0:
            continue
        covered += 1
        errors.append(_cents_error(float(f0[index]), ref))

    assert errors, "没有任何一帧同时被标注和检出"
    errors_arr = np.asarray(errors)
    median = float(np.median(errors_arr))
    within = float((errors_arr <= 50.0).mean())
    coverage = covered / max(1, total_voiced)

    print(f"{clip}: median={median:.1f}c within50={within:.1%} coverage={coverage:.1%}")
    assert median <= 50.0, f"中位误差 {median:.1f} 音分超过 50"
    assert within >= 0.80, f"仅 {within:.1%} 的帧落在 50 音分内"
    assert coverage >= 0.80, f"有声帧覆盖率仅 {coverage:.1%}"
