"""numpy 版 STFT/iSTFT：与 torch.stft / torch.istft 逐值对齐。

提纯模型只预测复数掩码，STFT 与 iSTFT 都在宿主端做（本模块）。
口径必须与训练/导出时一致，否则掩码乘回去的频谱对不上：

- 两侧各补 n_fft//2 个样本，反射填充（torch 的 pad_mode="reflect"）；
- 帧数 = 1 + len(x) // hop，尾部不足一帧丢弃；
- 窗是 periodic Hann（0.5 - 0.5cos(2πn/N)，不是对称 Hann）；
- iSTFT 合成时窗乘一次，归一化用窗平方和（envelope），最后裁掉两侧补零；
- normalized=False（不除 sqrt(N)）。

数值基准由 temp-dev/bench_torch_stft.py 生成（用 V2 的 torch 环境跑一次），
V3 侧逐值比对，误差要求 < 1e-5。
"""
from __future__ import annotations

import numpy as np

N_FFT = 2048
HOP = 441  # 44100 / 100 -> 10 ms 一帧，与 FCPE 音高同栅格
BINS = N_FFT // 2 + 1  # 1025
CHANNELS = 2
PACKED = BINS * CHANNELS * 2  # 4100：频率 bin × 声道 × (re, im)


def periodic_hann(n: int) -> np.ndarray:
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)


def stft(x: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    window = periodic_hann(n_fft)
    padded = np.pad(x, (n_fft // 2, n_fft // 2), mode="reflect")
    frames = 1 + len(x) // hop
    out = np.empty((frames, n_fft // 2 + 1), dtype=np.complex128)
    for index in range(frames):
        segment = padded[index * hop : index * hop + n_fft]
        out[index] = np.fft.rfft(segment * window)
    return out


def istft(spec: np.ndarray, length: int, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    window = periodic_hann(n_fft)
    frames = spec.shape[0]
    total = (frames - 1) * hop + n_fft
    signal = np.zeros(total)
    envelope = np.zeros(total)
    for index in range(frames):
        frame = np.fft.irfft(spec[index], n=n_fft) * window
        start = index * hop
        signal[start : start + n_fft] += frame
        envelope[start : start + n_fft] += window * window
    signal = signal / np.maximum(envelope, 1e-11)
    return signal[n_fft // 2 : n_fft // 2 + length]


def pack_features(spec: np.ndarray, zero_dc: bool = True) -> np.ndarray:
    frames, bins, channels = spec.shape
    packed = np.zeros((frames, bins * channels * 2), dtype=np.float32)
    for channel in range(channels):
        for f in range(bins):
            base = (2 * f + channel) * 2
            packed[:, base] = spec[:, f, channel].real
            packed[:, base + 1] = spec[:, f, channel].imag
    if zero_dc:
        packed[:, 0:4] = 0.0
    return packed


def unpack_mask(mask: np.ndarray) -> np.ndarray:
    frames = mask.shape[0]
    bins = mask.shape[1] // 2
    out = np.zeros((frames, bins, CHANNELS), dtype=np.complex128)
    for channel in range(CHANNELS):
        for f in range(bins):
            index = 2 * f + channel
            out[:, f, channel] = mask[:, index, 0] + 1j * mask[:, index, 1]
    return out
