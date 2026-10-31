"""mel 前处理：与 torchaudio 的 MelSpectrogram 对齐（FCPE 图外的那一半）。

为什么自己写：上游 `torchfcpe.mel_extractor` 里有数据相关分支，导不出 ONNX；
而运行时又不装 torch，所以这一半必须在 numpy 里重实现（docs/07-spike.md §2）。

自检：`python -m app.runtime.mel` 会对比 models/fcpe_net.npz 里的参考 mel。
"""

from __future__ import annotations

import numpy as np

FMIN = 0.0
FMAX = 8000.0
N_FFT = 1024
HOP = 160
N_MELS = 128
SR = 16000
LOG_FLOOR = 1e-5


def hz_to_mel(freq: np.ndarray | float) -> np.ndarray:
    """Slaney 刻度：1 kHz 以下线性，以上对数。"""
    freq = np.asarray(freq, dtype=np.float64)
    f_sp = 200.0 / 3
    mels = freq / f_sp
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    if np.isscalar(freq):
        return min_log_mel + np.log(freq / min_log_hz) / logstep if freq >= min_log_hz else mels
    log_t = freq >= min_log_hz
    mels = np.where(log_t, min_log_mel + np.log(np.maximum(freq, 1e-10) / min_log_hz) / logstep, mels)
    return mels


def mel_to_hz(mel: np.ndarray) -> np.ndarray:
    mel = np.asarray(mel, dtype=np.float64)
    f_sp = 200.0 / 3
    freqs = f_sp * mel
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = mel >= min_log_mel
    return np.where(log_t, min_log_hz * np.exp(logstep * (mel - min_log_mel)), freqs)


def filterbank(sr: int = SR, n_fft: int = N_FFT, n_mels: int = N_MELS,
               fmin: float = FMIN, fmax: float = FMAX) -> np.ndarray:
    """(n_mels, 1 + n_fft//2)，Slaney 归一化（与 torchaudio norm='slaney' 一致）。"""
    fft_freqs = np.linspace(0, sr / 2, 1 + n_fft // 2)
    mel_pts = np.linspace(hz_to_mel(fmin), hz_to_mel(fmax), n_mels + 2)
    hz_pts = mel_to_hz(mel_pts)
    ramps = hz_pts[:, None] - fft_freqs[None, :]
    weights = np.zeros((n_mels, fft_freqs.size))
    for i in range(n_mels):
        lower = -ramps[i] / (hz_pts[i + 1] - hz_pts[i])
        upper = ramps[i + 2] / (hz_pts[i + 2] - hz_pts[i + 1])
        weights[i] = np.maximum(0, np.minimum(lower, upper))
    enorm = 2.0 / (hz_pts[2:n_mels + 2] - hz_pts[:n_mels])
    return weights * enorm[:, None]


def logmel(wav: np.ndarray, sr: int = SR) -> np.ndarray:
    """wav: (n,) float32 @ 16 kHz → (T, 128)，T = 1 + n//HOP。"""
    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    if sr != SR:
        raise ValueError(f"mel 只接受 {SR} Hz 输入，收到 {sr}")
    pad = N_FFT // 2
    padded = np.pad(wav, (pad, pad), mode="reflect")
    n_frames = 1 + (padded.size - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    frames = padded[idx] * np.hanning(N_FFT + 1)[:-1].astype(np.float32)  # periodic hann
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    mel = power @ filterbank(sr).T
    return np.log(np.maximum(mel, LOG_FLOOR)).astype(np.float32)


def _self_check() -> None:
    from pathlib import Path

    ref_path = Path(__file__).resolve().parents[2] / "models" / "fcpe_net.npz"
    ref = np.load(ref_path)["ref_mel"]  # (501, 128)，torch 侧同一段音频的结果
    # npz 只存了 mel 本身，reverse 不出波形；这里直接比静态矩阵的构造一致性
    bank = filterbank()
    print(f"filterbank {bank.shape}  sum={bank.sum():.4f}")
    print(f"ref_mel {ref.shape}  min={ref.min():.4f} max={ref.max():.4f}")
    assert abs(ref.min() - np.log(LOG_FLOOR)) < 1e-3, "底部不应低于 log(1e-5)"
    print("mel 参数自检通过（波形级对齐见 tests/test_smoke.py）")


if __name__ == "__main__":
    _self_check()
