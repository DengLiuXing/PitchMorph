"""谱通量起音检测：16 kHz 单声道波形 → 与音高帧同栅格的起音帧索引。

为什么要这个：音高游程只能回答「音变了没」，回答不了「字换了没」。
同音高的两个音节在音高上完全一样，唯一的证据是辅音的起音——那是一次
能量/频谱的突变，谱通量抓的就是它。

帧栅格与 FCPE 对齐（hop=160 @16 kHz = 10 ms）：产出的帧号可以直接当
`pitch.json` 里 `f0` 的下标用，domain 层不需要任何时间换算。

只依赖 numpy，**不 import torch**，因此可以脱离 torch 单测。
"""

from __future__ import annotations

import typing as tp

import numpy as np

SR = 16000
HOP = 160
N_FFT = 512
# 512 = 3.2 × hop：覆盖一个辅音爆破的持续时间，FFT 又足够便宜
BLOCK_FRAMES = 1024

# 归一化后的 z 分数门槛。谱通量在段内是「底噪 + 尖峰」的形态，
# 用局部中位数/MAD 归一化后，这个值跨素材才有可比性。
# 3.0 是按「白噪声底 + 已知位置起音」实测出来的：再低会把噪声的局部极大放进来。
DEFAULT_THRESHOLD = 3.0
MIN_GAP_SEC = 0.06
SMOOTH_FRAMES = 31
# 通量先做 3 帧滑动平均再归一化：单帧毛刺不是起音，是 FFT 的抖动
FLUX_SMOOTH_FRAMES = 3
# 低于全曲最大通量的这个比例视为静音：否则近静音段的 MAD 接近 0，
# 会把 1e-5 级别的数值噪声放大成假峰
MIN_FLUX_RATIO = 1e-3
# 绝对下限（log1p 压缩后的谱通量之和）。持续正弦的 float32 舍入噪声只有
# 0.01 量级，而真实起音是 10 以上 —— 只有 z 分数会被噪声骗，加一道绝对闸。
MIN_ABS_FLUX = 0.5


def frame_count(samples: int, hop: int = HOP, n_fft: int = N_FFT) -> int:
    """这段采样能切出多少个整帧。不足一帧返回 0。"""
    if samples < n_fft:
        return 0
    return 1 + (samples - n_fft) // hop


def _spectral_flux(samples: np.ndarray, hop: int, n_fft: int, block_frames: int) -> np.ndarray:
    """半波整流谱通量。分块做 FFT，避免整张幅度谱常驻内存。

    12 分钟 = 7.2 万帧，整张谱约 74 MB；分块后峰值只有一帧块的量级。
    """
    frames = frame_count(samples.shape[0], hop, n_fft)
    if frames <= 0:
        return np.zeros(0, dtype=np.float32)

    window = np.hanning(n_fft).astype(np.float32)
    # sliding_window_view 是视图，不复制采样
    view = np.lib.stride_tricks.sliding_window_view(samples, n_fft)[::hop]
    view = view[:frames]

    flux = np.zeros(frames, dtype=np.float32)
    previous: np.ndarray | None = None
    for start in range(0, frames, block_frames):
        block = view[start : start + block_frames] * window
        magnitude = np.abs(np.fft.rfft(block, axis=1)).astype(np.float32)
        compressed = np.log1p(magnitude)
        if previous is not None:
            compressed = np.vstack((previous[None, :], compressed))
        diff = np.diff(compressed, axis=0)
        # 半波整流：只有变亮的部分算起音，衰减不算
        positive = np.maximum(diff, 0.0).sum(axis=1)
        offset = 0 if previous is not None else 1
        flux[start + offset : start + offset + positive.shape[0]] = positive
        previous = compressed[-1]
    return flux


def _local_z(flux: np.ndarray, smooth_frames: int) -> np.ndarray:
    """按局部中位数/MAD 归一化。

    人声整首歌的响度能差 20 dB，固定幅度阈值必然两头失效；
    归一化成「相对局部底的 z 分数」后阈值才可解释。
    """
    if flux.size == 0:
        return flux
    window = max(3, int(smooth_frames) | 1)  # 强制奇数
    half = window // 2
    padded = np.pad(flux, (half, half), mode="edge")
    rolling = np.lib.stride_tricks.sliding_window_view(padded, window)
    median = np.median(rolling, axis=1)
    deviation = np.abs(rolling - median[:, None])
    mad = np.median(deviation, axis=1)
    # 1.4826 把 MAD 折算成正态分布的标准差
    scale = 1.4826 * mad + 1e-6
    return ((flux - median) / scale).astype(np.float32)


def detect_onsets(
    samples: tp.Any,
    *,
    hop: int = HOP,
    n_fft: int = N_FFT,
    threshold: float = DEFAULT_THRESHOLD,
    min_gap_frames: int = int(round(MIN_GAP_SEC * SR / HOP)),
    smooth_frames: int = SMOOTH_FRAMES,
    block_frames: int = BLOCK_FRAMES,
) -> list[int]:
    """返回起音帧索引：升序、去重、任意两峰间隔 >= `min_gap_frames`。

    三条件挑峰：局部极大 + z 分数达标 + 最小间隔；间隔冲突时留较强的那个。
    输出是确定性的：同样输入永远同样输出。

    帧号是**谱通量峰所在的帧**。分析窗长 512（32 ms）会让峰比真实起音早
    1~2 帧，这是窗函数的前视效应；切音符时 10~20 ms 的提前量无害。
    """
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size == 0:
        return []

    flux = _spectral_flux(values, hop, n_fft, block_frames)
    if flux.size < 3:
        return []
    peak_flux = float(flux.max())
    if peak_flux <= 0.0:
        return []
    # 单帧毛刺不是起音，先抹平再归一化
    if FLUX_SMOOTH_FRAMES > 1:
        kernel = np.ones(FLUX_SMOOTH_FRAMES, dtype=np.float32) / FLUX_SMOOTH_FRAMES
        flux = np.convolve(flux, kernel, mode="same").astype(np.float32)
    score = _local_z(flux, smooth_frames)

    floor = max(peak_flux * MIN_FLUX_RATIO, MIN_ABS_FLUX)
    gap = max(1, int(min_gap_frames))
    kept: list[int] = []
    for index in range(1, score.shape[0] - 1):
        if score[index] < threshold or flux[index] < floor:
            continue
        if not (score[index] > score[index - 1] and score[index] >= score[index + 1]):
            continue
        if kept and index - kept[-1] < gap:
            # 离上一个峰太近：谁强留谁
            if score[index] > score[kept[-1]]:
                kept[-1] = index
            continue
        kept.append(index)
    return kept


def to_payload(
    onsets: tp.Sequence[int],
    frames: int,
    *,
    sample_rate: int = SR,
    hop: int = HOP,
    threshold: float = DEFAULT_THRESHOLD,
    min_gap_frames: int = int(round(MIN_GAP_SEC * SR / HOP)),
    smooth_frames: int = SMOOTH_FRAMES,
) -> dict:
    """`features/onset.json` 的结构。帧号必须落在 `[0, frames)` 内。"""
    limit = max(0, int(frames))
    cleaned = sorted({int(index) for index in onsets if 0 <= int(index) < limit})
    return {
        "version": 1,
        "sample_rate": sample_rate,
        "hop_sec": round(hop / sample_rate, 6),
        "frames": limit,
        "params": {
            "threshold": threshold,
            "min_gap_ms": round(min_gap_frames * hop * 1000 / sample_rate),
            "smooth_frames": max(3, int(smooth_frames) | 1),
        },
        "onsets": cleaned,
    }
