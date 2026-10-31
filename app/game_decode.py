"""GAME 图外后处理的 numpy 版：ONNX 只跑网络，边界解码全在这里。

逐函数对照上游 PyTorch 实现（`openvpi/GAME` 的 `modules/d3pm.py`、`modules/decoding.py`、
`modules/functional.py`），语义一一对应，不做"更好的改写"：

- `d3pm_time_schedule`：余弦时间表，t=0 全噪声、t=1 无噪声；
- `remove_mutable_boundaries`：按概率随机抹掉可变边界，并按可变边界占比修正概率，
  目的是让保留下来的边界数期望不变；
- `decode_soft_boundaries`：半径内局部极大（argmax 落在窗口中心）且不低于阈值才算边界；
- `decode_gaussian_blurred_probs`：257 个 MIDI bin 的概率按高斯窗加权平均成连续音高，
  presence 取最大概率是否过阈；
- `boundaries_to_regions` / `regions_to_durations`：边界 -> 音符占帧数。

纯函数、无 IO，方便单测。
"""
from __future__ import annotations

import math

import numpy as np


def d3pm_time_schedule(t: float) -> float:
    """D3PM 余弦时间表：t=0 全噪声（p=1），t=1 无噪声（p=0）。"""
    return (1.0 + math.cos(t * math.pi)) / 2.0


def remove_mutable_boundaries(boundaries, immutable, p: float, rng=None):
    """按概率 p 随机抹掉可变边界，尽量保住边界总数不变。"""
    rng = rng if rng is not None else np.random.default_rng()
    boundaries = np.asarray(boundaries, dtype=bool)
    immutable = np.asarray(immutable, dtype=bool)
    mutable = boundaries & ~immutable
    total = float(boundaries.sum())
    mutable_total = float(mutable.sum())
    probability = min(total * p / (mutable_total + 1e-8), 1.0)
    remain = (rng.random(boundaries.shape) <= (1.0 - probability)) & mutable
    return remain | immutable


def decode_soft_boundaries(probs, barriers=None, mask=None, threshold: float = 0.5, radius: int = 2):
    """软边界概率 -> 布尔边界：半径内局部极大且不低于阈值。"""
    values = np.array(probs, dtype=np.float64, copy=True)
    if mask is not None:
        values = np.where(np.asarray(mask, dtype=bool), values, np.inf)
    if barriers is not None:
        values = np.where(np.asarray(barriers, dtype=bool), np.inf, values)
    padded = np.pad(values, [(0, 0)] * (values.ndim - 1) + [(radius, radius)], constant_values=np.inf)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * radius + 1, axis=-1)
    points = (windows.argmax(axis=-1) == radius) & (values >= threshold)
    if barriers is not None:
        points = points | np.asarray(barriers, dtype=bool)
    if mask is not None:
        points = points & np.asarray(mask, dtype=bool)
    return points


def decode_gaussian_blurred_probs(probs, min_val: float, max_val: float, deviation: float, threshold: float = 0.1):
    """257 bin 的音高概率 -> 连续 MIDI 值与 presence 标志。"""
    probs = np.asarray(probs, dtype=np.float64)
    bins = probs.shape[-1]
    width = math.ceil(deviation / (max_val - min_val) * (bins - 1))
    idx = np.arange(bins)
    center_values = np.linspace(min_val, max_val, bins)
    centers = np.argmax(probs, axis=-1, keepdims=True)
    start = np.clip(centers - width, 0, None)
    end = np.clip(centers + width + 1, None, bins)
    idx_mask = (idx >= start) & (idx < end)
    weights = probs * idx_mask
    values = (weights * center_values).sum(axis=-1) / (weights.sum(axis=-1) + 1e-8)
    presence = probs.max(axis=-1) >= threshold
    return values, presence


def boundaries_to_regions(boundaries, mask=None):
    """布尔边界 -> 区域序号（1 起，0 表示 padding）。"""
    regions = np.cumsum(np.asarray(boundaries, dtype=np.int64), axis=-1) + 1
    if mask is not None:
        regions = regions * np.asarray(mask, dtype=np.int64)
    return regions


def regions_to_durations(regions, max_n=None):
    """区域序号 -> 每个音符占多少帧，形状 [B, N]（与上游 regions_to_durations 同义）。"""
    regions = np.asarray(regions)
    if regions.ndim == 1:
        regions = regions[None, :]
    if max_n is None:
        max_n = int(regions.max()) if regions.size else 0
    if max_n <= 0:
        return np.zeros((regions.shape[0], 0), dtype=np.int64)
    out = np.zeros((regions.shape[0], max_n), dtype=np.int64)
    for index in range(regions.shape[0]):
        counts = np.bincount(regions[index].ravel(), minlength=max_n + 1)
        out[index] = counts[1 : max_n + 1]
    return out
