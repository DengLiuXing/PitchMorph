"""FCPE 音高曲线：16 kHz 单声道波形 -> 每 10 ms 一帧的 f0 / voiced / confidence。

社区版模型（HF Scicom-intl/fcpe-onnx）把 STFT+mel 前端放进了计算图，
所以这里不做任何前处理，避免与训练口径不一致导致整体偏移。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np

from . import audio, registry, runtime
from .config import PITCH_HOP_MS, PITCH_SAMPLE_RATE, PITCH_THRESHOLD
from .errors import JobCancelled

logger = logging.getLogger("pitchmorph.pitch")

Progress = Callable[[str], None]

# 360 个 bin 是 CREPE 口径：32.7 Hz 起每 bin 20 音分（对数分布）。
# 注意：模型卡写的"32.7–1975.5 Hz 均匀分布"是错的，按线性映射实测中位误差 2809 音分，
# 按对数映射降到 29.6 音分。详见 V3 构想.md 第 14 节。
FREQS = 32.7 * np.power(2.0, 20.0 * np.arange(360) / 1200.0)
# 分块长度：控制 conformer attention 的 O(T^2) 内存
CHUNK_S = 10.0
CHUNK_OVERLAP_S = 0.5


def _decode_frames(salience: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """salience (T, 360) -> f0(Hz), voiced(bool), confidence。"""
    salience = np.clip(salience, 0.0, None)
    frames = salience.shape[0]
    peak = salience.argmax(axis=1)
    conf = salience[np.arange(frames), peak]

    offsets = np.arange(-4, 5)
    neighbours = np.clip(peak[:, None] + offsets[None, :], 0, 359)
    weights = np.take_along_axis(salience, neighbours, axis=1)
    freqs = FREQS[neighbours]
    total = weights.sum(axis=1)
    f0 = (weights * freqs).sum(axis=1) / np.maximum(total, 1e-8)

    voiced = conf >= PITCH_THRESHOLD
    f0 = np.where(voiced, f0, 0.0)
    return f0.astype(np.float32), voiced, conf.astype(np.float32)


def _infer(
    session,
    mono16k: np.ndarray,
    on_progress: Callable[[float], None] | None,
    should_cancel: Callable[[], bool] | None = None,
) -> np.ndarray:
    """按 10 s 分块跑（0.5 s 重叠），按绝对帧号累加平均后拼回完整 salience。

    用绝对帧号定位而不是首尾裁剪，避免分块边界处整体错位（错位会直接体现为音分误差）。
    """
    total = mono16k.shape[0]
    chunk = int(CHUNK_S * PITCH_SAMPLE_RATE)
    overlap = int(CHUNK_OVERLAP_S * PITCH_SAMPLE_RATE)
    hop = chunk - overlap
    input_name = session.get_inputs()[0].name

    frames_total = total // 160 + 1
    acc = np.zeros((frames_total, 360), dtype=np.float32)
    counts = np.zeros(frames_total, dtype=np.float32)

    starts = list(range(0, max(total, 1), hop))
    for index, start in enumerate(starts):
        if should_cancel and should_cancel():
            raise JobCancelled("已取消")
        segment = mono16k[start : start + chunk]
        if segment.size == 0:
            break
        salience = session.run(None, {input_name: segment[None, :].astype(np.float32)})[0]
        if salience.ndim == 3:
            salience = salience[0]
        base = start // 160
        usable = min(salience.shape[0], frames_total - base)
        if usable > 0:
            acc[base : base + usable] += salience[:usable]
            counts[base : base + usable] += 1.0
        if on_progress:
            on_progress(min(1.0, (index + 1) / max(1, len(starts))))
        if start + chunk >= total:
            break

    return acc / np.maximum(counts[:, None], 1.0)


def pitch_curve(
    audio_44k: np.ndarray,
    on_progress: Callable[[float], None] | None = None,
    progress: Progress | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """返回 {hop_ms, times, f0, voiced, confidence}。FCPE 固定跑 CPU。"""
    if progress:
        progress("准备音高模型")
    model_path = registry.ensure(registry.PITCH_MODEL_KEY, progress=progress)

    mono = audio.to_pitch_input(audio_44k)[0]
    if mono.size == 0:
        raise ValueError("没有可用于音高分析的音频")

    session = runtime.acquire(model_path)
    try:
        if progress:
            progress("计算音高曲线")
        salience = _infer(session, mono, on_progress, should_cancel)
    finally:
        runtime.release(session)

    f0, voiced, conf = _decode_frames(salience)
    frames = f0.shape[0]
    times = (np.arange(frames) * PITCH_HOP_MS + 5.0) / 1000.0

    if frames and not voiced.any():
        raise ValueError("没有检出有效音高（整段都是清音）")

    logger.info("音高完成 frames=%d voiced=%.1f%%", frames, 100.0 * voiced.mean() if frames else 0.0)
    return {
        "hop_ms": PITCH_HOP_MS,
        "sample_rate": PITCH_SAMPLE_RATE,
        "times": np.round(times, 4).tolist(),
        "f0": np.round(f0, 3).tolist(),
        "voiced": voiced.tolist(),
        "confidence": np.round(conf, 4).tolist(),
    }


def pitch_file(wav_path: Path, **kwargs) -> dict:
    audio_44k, _ = audio.read_wav(wav_path)
    return pitch_curve(audio_44k, **kwargs)
