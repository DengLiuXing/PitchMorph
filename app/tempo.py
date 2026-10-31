"""曲速估算：一段音频 -> 单一 BPM。

纯 numpy 实现，不引入 librosa（numba/scipy 那一串依赖对这个功能太重）：

1. 降采样到 22.05 kHz，STFT（1024/256 = 86 帧/秒）取频谱通量的正向部分作为起音包络；
2. 包络去均值后做自相关（FFT 加速，按重叠样本数归一化）；
3. 40–240 BPM 以 0.05 为步长扫描，得分 = Σ_{m=1..4} ACF(m·lag)/m，
   再乘 120 BPM 附近的对数正态先验——只靠自相关会在倍速/半速之间摇摆；
4. 取最高分并保留一位小数。

BPM 只影响导出时秒 -> tick 的换算：音符与音高线用的是同一个值，
所以即使估算偏几个 BPM，OpenUtau 里的时间位置依然与音频对齐，只是网格不那么"整"。
"""
from __future__ import annotations

import logging

import numpy as np

from . import audio
from .config import (
    SAMPLE_RATE,
    TEMPO_BPM_MAX,
    TEMPO_BPM_MIN,
    TEMPO_HARMONICS,
    TEMPO_HOP,
    TEMPO_N_FFT,
    TEMPO_PRIOR_CENTER,
    TEMPO_PRIOR_OCTAVES,
    TEMPO_SAMPLE_RATE,
)

logger = logging.getLogger("pitchmorph.tempo")

# 每次算多少帧的频谱：整曲一次性做 rfft 会占几百 MB
BLOCK_FRAMES = 512


def _onset_envelope(mono: np.ndarray) -> np.ndarray:
    """频谱通量（仅正向变化）作为起音包络。"""
    pad = TEMPO_N_FFT // 2
    padded = np.pad(mono, (pad, pad)).astype(np.float32)
    count = 1 + (padded.size - TEMPO_N_FFT) // TEMPO_HOP
    window = np.hanning(TEMPO_N_FFT).astype(np.float32)
    offsets = np.arange(TEMPO_N_FFT)
    envelope = np.zeros(count, dtype=np.float32)
    previous: np.ndarray | None = None
    for start in range(0, count, BLOCK_FRAMES):
        stop = min(count, start + BLOCK_FRAMES)
        index = offsets[None, :] + TEMPO_HOP * np.arange(start, stop)[:, None]
        magnitude = np.abs(np.fft.rfft(padded[index] * window, axis=1)).astype(np.float32)
        if previous is not None:
            magnitude = np.vstack([previous, magnitude])
        else:
            magnitude = np.vstack([np.zeros((1, magnitude.shape[1]), dtype=np.float32), magnitude])
        previous = magnitude[-1:]
        envelope[start:stop] = np.maximum(0.0, magnitude[1:] - magnitude[:-1]).sum(axis=1)
    return envelope


def _normalized_acf(envelope: np.ndarray) -> np.ndarray:
    """去均值后的自相关，按重叠样本数归一化，acf[0] = 1。"""
    centered = envelope - envelope.mean()
    size = 1
    while size < centered.size * 2:
        size <<= 1
    spectrum = np.fft.rfft(centered, size)
    acf = np.fft.irfft(spectrum * np.conj(spectrum), size)[: centered.size]
    acf = acf / np.maximum(centered.size - np.arange(centered.size), 1)
    if not np.isfinite(acf[0]) or acf[0] <= 0:
        raise ValueError("没有明显的节拍，无法判断曲速")
    return acf / acf[0]


def detect_tempo(audio_44k: np.ndarray) -> float:
    """44.1 kHz 单/多声道 -> BPM（一位小数）。没有节拍时抛 ValueError。"""
    mono = audio_44k.mean(axis=0) if audio_44k.ndim > 1 else audio_44k
    mono = np.ascontiguousarray(mono[None, :], dtype=np.float32)
    resampled = audio.resample_mono(mono, SAMPLE_RATE, TEMPO_SAMPLE_RATE)[0]
    if resampled.size < TEMPO_N_FFT * 4:
        raise ValueError("音频太短，无法判断曲速")

    envelope = _onset_envelope(resampled)
    acf = _normalized_acf(envelope)
    lags = np.arange(acf.size)
    fps = TEMPO_SAMPLE_RATE / TEMPO_HOP

    candidates = np.arange(TEMPO_BPM_MIN, TEMPO_BPM_MAX + 1e-9, 0.05)
    score = np.zeros_like(candidates)
    for harmonic in TEMPO_HARMONICS:
        tap = harmonic * 60.0 * fps / candidates
        valid = tap < acf.size - 1
        if valid.any():
            score[valid] += np.interp(tap[valid], lags, acf) / harmonic
    # 对数频率上的单峰先验：把 60/120/240 这种整倍数关系拉开
    score *= np.exp(-0.5 * (np.log2(candidates / TEMPO_PRIOR_CENTER) / TEMPO_PRIOR_OCTAVES) ** 2)

    best = float(candidates[int(np.argmax(score))])
    logger.info("曲速估算 %.1f BPM（候选范围 %.0f–%.0f）", best, TEMPO_BPM_MIN, TEMPO_BPM_MAX)
    return round(best, 1)


def tempo_of_file(path) -> float:
    audio_44k, _ = audio.read_wav(path)
    return detect_tempo(audio_44k)
