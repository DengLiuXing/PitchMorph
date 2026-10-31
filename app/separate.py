"""分轨：分块推理 + 升余弦 overlap-add。档位决定「跑哪些模型、取哪些轨」。

显存约束：8 GB 卡上一次只允许一个模型驻留。质量四轨档每跑完一个专家，
会话与中间张量立刻丢弃（runtime.release），再进下一个。

快档只用一次前向：单文件 htdemucs 本身就输出 4 条轨（[drums, bass, other, vocals]），
所以不必像质量档那样跑四遍专家。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio, registry, runtime
from .config import HOP, OVERLAP_RATIO, SEGMENT
from .errors import JobCancelled

logger = logging.getLogger("pitchmorph.separate")

Progress = Callable[[str, float, dict | None], None]

# demucs 输出固定为 [drums, bass, other, vocals]
STEM_INDEX = {"drums": 0, "bass": 1, "other": 2, "vocals": 3}
MODEL_KEY = {
    "drums": "demucs_drums",
    "bass": "demucs_bass",
    "other": "demucs_other",
    "vocals": "demucs_vocals",
}
VOCALS_ONLY = ("vocals",)
FOUR = ("drums", "bass", "other", "vocals")


@dataclass(frozen=True)
class Step:
    """一次前向：跑哪个模型，取它输出的哪几条轨。"""

    model_key: str
    stems: tuple[str, ...]


# 三档：
#   vocals   仅人声 —— ft 人声专家，1 次前向，vocals 质量最好
#   fast     快速四轨 —— 单文件 htdemucs，1 次前向出 4 轨，成本与「仅人声」相同
#   quality  质量四轨 —— 4 个 ft 专家串行，4 次前向，SDR 最高
PLANS: dict[str, tuple[Step, ...]] = {
    "vocals": (Step(MODEL_KEY["vocals"], VOCALS_ONLY),),
    "fast": (Step("demucs_htdemucs", FOUR),),
    "quality": (
        Step(MODEL_KEY["drums"], ("drums",)),
        Step(MODEL_KEY["bass"], ("bass",)),
        Step(MODEL_KEY["other"], ("other",)),
        Step(MODEL_KEY["vocals"], ("vocals",)),
    ),
}
# 旧任务里的 mode="four" 就是现在的质量四轨
PLANS["four"] = PLANS["quality"]


def _taper(length: int, overlap: int) -> np.ndarray:
    """两端升余弦权重，保证重叠区权重和为 1 量级。"""
    window = np.ones(length, dtype=np.float32)
    overlap = min(overlap, length // 2)
    if overlap <= 1:
        return window
    ramp = np.sin(np.linspace(0.0, np.pi / 2.0, overlap, dtype=np.float32)) ** 2
    window[:overlap] = ramp
    window[-overlap:] = ramp[::-1]
    return window


def _run_step(
    step: Step,
    source: np.ndarray,
    on_progress: Callable[[float], None] | None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, np.ndarray]:
    """跑一个模型，返回它负责的那几条轨 {stem: (2, N)}。"""
    if should_cancel and should_cancel():
        raise JobCancelled("已取消")
    path = registry.ensure(step.model_key)
    if should_cancel and should_cancel():
        raise JobCancelled("已取消")
    # 模型固定要求 2 声道，单声道素材复制成双声道
    if source.shape[0] == 1:
        source = np.repeat(source, 2, axis=0)
    total = source.shape[1]
    padded = source
    if total < SEGMENT:
        padded = np.pad(source, ((0, 0), (0, SEGMENT - total)))

    session = runtime.acquire(path)
    input_name = session.get_inputs()[0].name
    window = _taper(SEGMENT, int(SEGMENT * OVERLAP_RATIO))
    outs = {stem: np.zeros((2, padded.shape[1]), dtype=np.float32) for stem in step.stems}
    weight = np.zeros(padded.shape[1], dtype=np.float32)

    try:
        starts = list(range(0, max(1, padded.shape[1] - SEGMENT + HOP), HOP))
        for index, start in enumerate(starts):
            if should_cancel and should_cancel():
                raise JobCancelled("已取消")
            segment = padded[:, start : start + SEGMENT]
            if segment.shape[1] < SEGMENT:
                segment = np.pad(segment, ((0, 0), (0, SEGMENT - segment.shape[1])))
            result = session.run(None, {input_name: segment[None, :, :].astype(np.float32)})[0]
            # 末块可能不足一段，按实际剩余长度截断，避免广播错位
            usable = min(SEGMENT, padded.shape[1] - start)
            for stem in step.stems:
                stem_audio = np.asarray(result[0][STEM_INDEX[stem]], dtype=np.float32)
                outs[stem][:, start : start + usable] += stem_audio[:, :usable] * window[:usable]
            weight[start : start + usable] += window[:usable]
            if on_progress:
                on_progress(min(1.0, (index + 1) / len(starts)))
            if start + SEGMENT >= padded.shape[1]:
                break
    finally:
        runtime.release(session)

    results: dict[str, np.ndarray] = {}
    for stem in step.stems:
        track = outs[stem] / np.maximum(weight, 1e-6)
        results[stem] = np.ascontiguousarray(track[:, :total])
    return results


def separate_track(
    source: np.ndarray,
    plan: tuple[Step, ...],
    on_progress: Progress | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, np.ndarray]:
    """按 plan 顺序跑模型，返回 {stem: (2, N) 数组}。

    这里不写盘，由 tasks 决定落盘位置。
    """
    results: dict[str, np.ndarray] = {}
    total = len(plan)
    for position, step in enumerate(plan):
        if should_cancel and should_cancel():
            raise JobCancelled("已取消")
        label = step.stems[0] if len(step.stems) == 1 else "四轨"
        if on_progress:
            on_progress(
                f"分轨 {label}（{position + 1}/{total}）",
                position / total,
                {"stem": label, "index": position + 1, "total": total},
            )
        started = time.perf_counter()
        produced = _run_step(
            step,
            source,
            (lambda p, base=position, label=label: on_progress(
                f"分轨 {label}（{base + 1}/{total}）",
                (base + p) / total,
                {"stem": label, "index": base + 1, "total": total},
            )) if on_progress else None,
            should_cancel=should_cancel,
        )
        results.update(produced)
        logger.info(
            "分轨完成 %s 轨=%s 耗时 %.2fs",
            step.model_key, "/".join(step.stems), time.perf_counter() - started,
        )
    return results
