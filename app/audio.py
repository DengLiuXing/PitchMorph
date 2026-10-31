"""音频解码、重采样、落盘与波形峰值。全部走 PyAV，不依赖系统 ffmpeg。"""
from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import soundfile as sf

from .config import PITCH_SAMPLE_RATE, SAMPLE_RATE


def decode_to_stereo(path: Path) -> np.ndarray:
    """任意受支持格式 -> (2, N) float32，44.1 kHz 立体声。"""
    chunks: list[np.ndarray] = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError("文件里没有音频流")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray())
    if not chunks:
        raise ValueError("解码后没有任何音频数据")
    audio = np.concatenate(chunks, axis=1)
    return np.ascontiguousarray(audio, dtype=np.float32)


def resample_mono(audio: np.ndarray, sample_rate: int, target: int) -> np.ndarray:
    """(channels, N) float32 -> (1, M) float32 单声道目标采样率。"""
    channels = audio.shape[0]
    layout = "mono" if channels == 1 else "stereo"
    frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(audio), format="fltp", layout=layout)
    frame.sample_rate = sample_rate
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=target)
    outs: list[np.ndarray] = []
    for out in resampler.resample(frame):
        outs.append(out.to_ndarray())
    for out in resampler.resample(None):
        outs.append(out.to_ndarray())
    if not outs:
        return np.zeros((1, 0), dtype=np.float32)
    return np.ascontiguousarray(np.concatenate(outs, axis=1)[:1], dtype=np.float32)


def to_pitch_input(audio: np.ndarray) -> np.ndarray:
    """44.1 kHz 立体声 -> 16 kHz 单声道 (1, M)。"""
    return resample_mono(audio, SAMPLE_RATE, PITCH_SAMPLE_RATE)


def write_wav(path: Path, audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Path:
    """(channels, N) -> 16-bit PCM wav。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.ascontiguousarray(audio.T)
    sf.write(str(path), data, sample_rate, subtype="PCM_16")
    return path


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    return np.ascontiguousarray(data.T), sample_rate


def wave_peaks(audio: np.ndarray, buckets: int = 2000) -> dict[str, list[float]]:
    """把整段音频降采样成 buckets 个 [min, max] 峰值对，供前端画波形。"""
    mono = audio.mean(axis=0) if audio.shape[0] > 1 else audio[0]
    total = mono.shape[0]
    if total == 0:
        return {"min": [], "max": []}
    buckets = max(1, min(buckets, total))
    edges = np.linspace(0, total, buckets + 1).astype(np.int64)
    mins = np.empty(buckets, dtype=np.float32)
    maxs = np.empty(buckets, dtype=np.float32)
    for i in range(buckets):
        chunk = mono[edges[i] : max(edges[i] + 1, edges[i + 1])]
        mins[i] = float(chunk.min())
        maxs[i] = float(chunk.max())
    return {"min": mins.round(5).tolist(), "max": maxs.round(5).tolist()}


def is_silent(audio: np.ndarray, floor: float = 1e-4) -> bool:
    return float(np.sqrt((audio**2).mean())) < floor
