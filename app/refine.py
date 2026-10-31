"""人声提纯：Mel-Band RoFormer karaoke 模型的复数掩码 → 人声主体 + 伴奏残差。

模型（`bdsqlsz/mel_band_roformer_karaoke_aufr33-ONNX`，上游未标注许可，按仅自用处理）
只预测复数掩码，STFT 与 iSTFT 都在宿主端做（`app/stft.py`，与 torch 口径逐值一致，
基准见 temp-dev/bench_torch_stft.py 生成的黄金参考）。

- 输入 `stft_features` [B, frames, 4100]（频率 bin × 声道 × 实虚），DC bin 置零；
- 输出 `mask` [B, 1, 2050, frames, 2]，bin 下标 2*f+c，末维 (re, im)；
- 切块：5 s（501 帧）+ 3.04 s 步长（301 帧），相邻重叠 2 s，重叠区掩码线性混合；
- lead = mix × mask；backing = mix − lead（时域相减）。

两条产物都是人声轨的派生轨：lead 是人声主体（含主旋律与和声），backing 是被剥离的伴奏/混响。
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import numpy as np

from . import registry, runtime, stft
from .errors import JobCancelled

logger = logging.getLogger("pitchmorph.refine")

# 5 s 块（501 帧 @10 ms）+ 3.04 s 步长 -> 相邻重叠 2 s。实测 170 s 整曲 56 块。
CHUNK_FRAMES = 501
STEP_FRAMES = 301
RAMP_FRAMES = 100  # 重叠区两端的线性渐变，各 1 s

# 提纯模型 5 s 块要 5.1 GB 显存，全局 4 GB 上限会直接失败，这里单独放宽
GPU_MEM_LIMIT = 6 * 1024 * 1024 * 1024


def _chunk_weights(frames: int) -> np.ndarray:
    """块内权重：两端各 RAMP_FRAMES 帧线性渐变，中间满权重。"""
    weight = np.ones(frames, dtype=np.float64)
    ramp = np.linspace(0.0, 1.0, RAMP_FRAMES, endpoint=False)
    weight[:RAMP_FRAMES] = ramp
    weight[-RAMP_FRAMES:] = ramp[::-1]
    return weight


def refine(
    source: np.ndarray,
    *,
    on_progress: Callable[[float], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, np.ndarray]:
    """(channels, N) -> {"lead": (channels, N), "backing": (channels, N)}。

    lead 是人声主体（含主旋律与和声），backing 是被剥离的伴奏/混响残差。
    """
    if source.ndim != 2:
        raise ValueError("source 必须是 (channels, N)")
    channels, total = source.shape
    path = registry.ensure(registry.KARAOKE_MODEL_KEY)

    # 提纯模型要 5.1 GB 显存，先把 demucs 的常驻会话放掉，别让两块叠加
    runtime.purge("heavy")
    session, _ = runtime.make_session(
        path,
        provider_options={"gpu_mem_limit": str(GPU_MEM_LIMIT)},
    )
    input_name = session.get_inputs()[0].name

    try:
        specs = [stft.stft(channel) for channel in source]
        frames = min(spec.shape[0] for spec in specs)
        spec = np.stack([item[:frames] for item in specs], axis=2)  # (frames, bins, channels)

        weights = _chunk_weights(CHUNK_FRAMES)
        mask_sum = np.zeros((frames, stft.BINS, stft.CHANNELS), dtype=np.complex128)
        weight_sum = np.zeros(frames, dtype=np.float64)

        starts = list(range(0, max(1, frames - CHUNK_FRAMES + 1), STEP_FRAMES))
        if frames < CHUNK_FRAMES:
            starts = [0]
        started = time.perf_counter()
        for index, start in enumerate(starts):
            if should_cancel and should_cancel():
                raise JobCancelled("已取消")
            end = min(start + CHUNK_FRAMES, frames)
            block = spec[start:end]
            if block.shape[0] < CHUNK_FRAMES:
                pad = np.zeros((CHUNK_FRAMES - block.shape[0], block.shape[1], block.shape[2]), dtype=complex)
                block = np.concatenate([block, pad], axis=0)
            features = stft.pack_features(block)
            output = session.run(None, {input_name: features[None, :, :]})[0]
            mask = stft.unpack_mask(np.asarray(output[0, 0]).transpose(1, 0, 2))
            weight = weights if block.shape[0] == CHUNK_FRAMES else weights[: end - start]
            mask_sum[start:end] += mask[: end - start] * weight[:, None, None]
            weight_sum[start:end] += weight
            if on_progress:
                on_progress(min(1.0, (index + 1) / len(starts)))
        logger.info("提纯推理完成 块数=%d 耗时 %.1fs", len(starts), time.perf_counter() - started)

        final_mask = mask_sum / np.maximum(weight_sum, 1e-8)[:, None, None]
        lead_spec = spec * final_mask
        lead = np.stack(
            [stft.istft(lead_spec[:, :, index], total) for index in range(channels)],
            axis=0,
        )
    finally:
        runtime.release(session)

    return {"lead": lead.astype(np.float32), "backing": (source - lead).astype(np.float32)}
