"""GAME 音符提取：ONNX 四图管线（encoder → segmenter → bd2dur → estimator）。

上游 `openvpi/GAME` v1.0.3 medium ONNX 包（`registry.ensure_bundle` 下载并校验）。
图只跑网络，边界解码在 `app/game_decode.py`（逐函数对照上游 PyTorch 版翻译）。

流程与上游一致：

1. 静音切片（slicer2.py 口径）+ 超长片在能量最低处再切一刀（O(T²) 注意力，30 s 上限）；
2. 按「并行大小 / 一批总秒数」打包，批内补齐到同一长度；
3. encoder 出 x_seg / x_est / maskT；
4. segmenter 做 D3PM 采样循环（步数可调），每步 sigmoid 后按半径局部极大解码边界；
5. bd2dur 出每个音符的时长与音符掩码，estimator 出 presence 与浮点音高。

**借边界不借音高**：这里返回的 midi 是模型给的浮点值，最终以 FCPE 段内中位数为准
（V2 实测 FCPE 中位数的一致率比模型 pitch 高 14 个点）。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import game_decode, registry, runtime
from .errors import JobCancelled

logger = logging.getLogger("pitchmorph.game")

# GAME 会话要单独的显存预算：与 demucs 常驻会话叠加会顶爆 8 GB 卡
GPU_MEM_LIMIT = 4 * 1024 * 1024 * 1024

# 切片参数（OpenUtau AudioSlicer 的口径，hop=441 样本 = 10 ms）
SLICE_THRESHOLD = 0.02
SLICE_HOP = 441
SLICE_WIN = 1764
SLICE_MIN_LENGTH = 100  # 帧，1 s
SLICE_MIN_INTERVAL = 20  # 帧，0.2 s
SLICE_MAX_SIL_KEPT = 10  # 帧，0.1 s

# segmenter 的注意力是 O(T^2)：整段 188 s 要 5.6 GB，直接 OOM。
# 单片超长就在能量最低处再切一刀（与 V2 同一套做法）。
MAX_SLICE_SEC = 30.0

def get_rms(samples: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    """逐帧 RMS（librosa 口径：前后各补半帧，constant 填充）。"""
    padding = int(frame_length // 2)
    padded = np.pad(samples, (padding, padding), mode="constant")
    frames = 1 + (len(padded) - frame_length) // hop_length
    if frames <= 0:
        return np.zeros(0, dtype=np.float64)
    view = np.lib.stride_tricks.as_strided(
        padded,
        shape=(frames, frame_length),
        strides=(padded.strides[0] * hop_length, padded.strides[0]),
    )
    return np.sqrt(np.mean(np.abs(view) ** 2, axis=-1))


def slice_audio(
    samples: np.ndarray,
    *,
    threshold: float | None = None,
    hop: int | None = None,
    win: int | None = None,
    min_length: int | None = None,
    min_interval: int | None = None,
    max_sil_kept: int | None = None,
) -> list[tuple[float, np.ndarray]]:
    """按静音切片，返回 [(起点秒, 波形) ]。移植 slicer2.py + OpenUtau 的参数。

    参数默认取模块常量（OpenUtau 的口径：10 ms 一帧、阈值 0.02）。传入 None 表示用常量，
    这样外部可以按需要换成别的口径（比如与上游 PyTorch 基准对齐时用 20 ms / 0.01）。
    """
    thr = SLICE_THRESHOLD if threshold is None else threshold
    step = SLICE_HOP if hop is None else hop
    window = SLICE_WIN if win is None else win
    min_len = SLICE_MIN_LENGTH if min_length is None else min_length
    min_gap = SLICE_MIN_INTERVAL if min_interval is None else min_interval
    sil_kept = SLICE_MAX_SIL_KEPT if max_sil_kept is None else max_sil_kept
    if samples.ndim == 1:
        samples = samples[None, :]
    mono = samples.mean(axis=0) if samples.ndim > 1 else samples
    total_frames = (len(mono) + step - 1) // step
    if total_frames <= min_len:
        return [(0.0, samples)]

    rms_list = get_rms(mono, window, step)
    sil_tags: list[tuple[int, int]] = []
    silence_start = None
    clip_start = 0
    for index, rms in enumerate(rms_list):
        if rms < thr:
            if silence_start is None:
                silence_start = index
            continue
        if silence_start is None:
            continue
        is_leading_silence = silence_start == 0 and index > sil_kept
        need_slice_middle = (
            index - silence_start >= min_gap and index - clip_start >= min_len
        )
        if not is_leading_silence and not need_slice_middle:
            silence_start = None
            continue
        if index - silence_start <= sil_kept:
            pos = int(rms_list[silence_start : index + 1].argmin()) + silence_start
            sil_tags.append((0, pos) if silence_start == 0 else (pos, pos))
            clip_start = pos
        elif index - silence_start <= sil_kept * 2:
            pos = int(rms_list[index - sil_kept : silence_start + sil_kept + 1].argmin())
            pos += index - sil_kept
            pos_l = int(rms_list[silence_start : silence_start + sil_kept + 1].argmin()) + silence_start
            pos_r = int(rms_list[index - sil_kept : index + 1].argmin()) + index - sil_kept
            if silence_start == 0:
                sil_tags.append((0, pos_r))
                clip_start = pos_r
            else:
                sil_tags.append((min(pos_l, pos), max(pos_r, pos)))
                clip_start = max(pos_r, pos)
        else:
            pos_l = int(rms_list[silence_start : silence_start + sil_kept + 1].argmin()) + silence_start
            pos_r = int(rms_list[index - sil_kept : index + 1].argmin()) + index - sil_kept
            if silence_start == 0:
                sil_tags.append((0, pos_r))
                clip_start = pos_r
            else:
                sil_tags.append((pos_l, pos_r))
            clip_start = pos_r
        silence_start = None

    total = rms_list.shape[0]
    if silence_start is not None and total - silence_start >= min_gap:
        silence_end = min(total, silence_start + sil_kept)
        pos = int(rms_list[silence_start : silence_end + 1].argmin()) + silence_start
        sil_tags.append((pos, total + 1))

    if not sil_tags:
        return [(0.0, samples)]

    chunks: list[tuple[float, np.ndarray]] = []
    if sil_tags[0][0] > 0:
        chunks.append((0.0, samples[:, : sil_tags[0][0] * step]))
    for index in range(len(sil_tags) - 1):
        begin = sil_tags[index][1] * step
        end = sil_tags[index + 1][0] * step
        chunks.append((begin / 44100.0, samples[:, begin:end]))
    if sil_tags[-1][1] < total:
        begin = sil_tags[-1][1] * step
        chunks.append((begin / 44100.0, samples[:, begin:]))
    return chunks


def cap_slices(chunks: list[tuple[float, np.ndarray]], max_sec: float = MAX_SLICE_SEC) -> list[tuple[float, np.ndarray]]:
    """超长片在能量最低处再切一刀（按固定时长切会把长音从中间劈开）。"""
    limit = int(max_sec * 44100)
    result: list[tuple[float, np.ndarray]] = []
    for offset, waveform in chunks:
        if waveform.shape[-1] <= limit:
            result.append((offset, waveform))
            continue
        start = 0
        mono = waveform.mean(axis=0)
        while waveform.shape[-1] - start > limit:
            search_from = start + int(limit * 0.75)
            search_to = start + limit
            step = max(1, 44100 // 100)
            best_index, best_energy = search_to, None
            for index in range(search_from, max(search_from + 1, search_to - step), step):
                energy = float(np.abs(mono[index : index + step]).mean())
                if best_energy is None or energy < best_energy:
                    best_energy, best_index = energy, index
            cut = int(best_index)
            result.append((offset + start / 44100.0, waveform[:, start:cut]))
            start = cut
        result.append((offset + start / 44100.0, waveform[:, start:]))
    return result

def durations_from_boundaries(boundaries, mask, timestep: float):
    """边界 -> 每个音符的时长（秒）与音符掩码。

    语义照抄上游 `Boundaries2Durations`，但**不跑 bd2dur.onnx**：那张图运行时输出的
    形状与图声明对不上（声明 [B,N]，实测给出别的维度），下游 estimator 直接按它取
    掩码就会维度冲突。这步本来就只有几行 numpy，自己算更可控。
    """
    regions = game_decode.boundaries_to_regions(boundaries, mask)
    max_idx = regions.max(axis=-1, keepdims=True)
    count = int(max_idx.max()) if regions.size else 0
    if count <= 0:
        return np.zeros((regions.shape[0], 0)), np.zeros((regions.shape[0], 0), dtype=bool)
    idx = np.arange(count)[None, :]
    n_mask = idx < max_idx
    frames = game_decode.regions_to_durations(regions, count)
    return frames * timestep, n_mask


class GameRunner:
    """GAME 的四个 ONNX 会话 + config.json（一次前向出一批切片的音符）。"""

    def __init__(self, location: Path) -> None:
        self.location = location
        self.config = json.loads((location / "config.json").read_text(encoding="utf-8"))
        self.timestep = float(self.config["timestep"])
        self.samplerate = int(self.config["samplerate"])
        self.languages: dict[str, int] = self.config.get("languages") or {}
        options = {"gpu_mem_limit": str(GPU_MEM_LIMIT)}
        self.encoder, _ = runtime.make_session(location / "encoder.onnx", provider_options=options)
        self.segmenter, _ = runtime.make_session(location / "segmenter.onnx", provider_options=options)
        self.estimator, _ = runtime.make_session(location / "estimator.onnx", provider_options=options)

    def close(self) -> None:
        for session in (self.encoder, self.segmenter, self.estimator):
            runtime.release(session)

    def language_id(self, language: str | None) -> int:
        if not language:
            return 0
        if language not in self.languages:
            raise ValueError(f"该权重不支持语言 {language!r}，可选 {sorted(self.languages)}")
        return int(self.languages[language])


def _pack_batches(chunks, batch_size: int, max_batch_duration: float):
    """按切片数与一批总秒数上限打包。"""
    batches = []
    current = []
    current_seconds = 0.0
    for chunk in chunks:
        seconds = chunk[1].shape[-1] / 44100.0
        too_long = current and (
            len(current) >= batch_size
            or (max_batch_duration > 0 and current_seconds + seconds > max_batch_duration)
        )
        if too_long:
            batches.append(current)
            current = []
            current_seconds = 0.0
        current.append(chunk)
        current_seconds += seconds
    if current:
        batches.append(current)
    return batches


def extract_notes(
    samples: np.ndarray,
    *,
    sampling_steps: int = 8,
    boundary_threshold: float = 0.2,
    boundary_radius: int = 2,
    score_threshold: float = 0.2,
    batch_size: int = 1,
    max_batch_duration: float = 60.0,
    language: str | None = None,
    slice_params: dict | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> list[dict]:
    """单声道波形 -> 音符列表 [{start_sec, dur_sec, midi, voiced}]。

    借边界不借音高：midi 是模型给的浮点音高，最终导出用 FCPE 段内中位数覆盖。
    """
    if sampling_steps < 1:
        raise ValueError("采样步数至少为 1")
    if batch_size < 1:
        raise ValueError("并行大小至少为 1")
    audio = samples[0] if samples.ndim == 2 else samples

    runtime.purge("heavy")
    location = registry.ensure_bundle(registry.GAME_BUNDLE_KEY)
    runner = GameRunner(location)
    rng = np.random.default_rng()
    notes: list[dict] = []
    try:
        chunks = cap_slices(slice_audio(audio, **(slice_params or {})))
        batches = _pack_batches(chunks, batch_size, max_batch_duration)
        logger.info(
            "GAME 切片 %d 段，批次 %d（步数=%d 阈值=%.2f 半径=%d 语言=%s）",
            len(chunks), len(batches), sampling_steps, boundary_threshold,
            boundary_radius, language or "通用",
        )
        for batch_index, batch in enumerate(batches):
            if should_cancel and should_cancel():
                raise JobCancelled("已取消")
            lengths = [item[1].shape[-1] for item in batch]
            max_len = max(lengths)
            count = len(batch)
            waveform = np.zeros((count, max_len), dtype=np.float32)
            duration = np.zeros(count, dtype=np.float32)
            for index, (_, chunk) in enumerate(batch):
                mono = chunk.mean(axis=0) if chunk.ndim > 1 else chunk
                waveform[index, : mono.shape[0]] = mono
                duration[index] = mono.shape[0] / runner.samplerate

            x_seg, x_est, mask_t = runner.encoder.run(
                None, {"waveform": waveform, "duration": duration}
            )
            frames = x_seg.shape[1]
            known = np.zeros((count, frames), dtype=bool)
            previous = known.copy()
            language_id = runner.language_id(language)
            for step in range(sampling_steps):
                t_value = step / sampling_steps
                probability = game_decode.d3pm_time_schedule(t_value)
                previous = game_decode.remove_mutable_boundaries(previous, known, probability, rng)
                logits = runner.segmenter.run(
                    None,
                    {
                        "x_seg": x_seg,
                        "language": np.full(count, language_id, dtype=np.int64),
                        "known_boundaries": known,
                        "prev_boundaries": previous,
                        "t": np.full(count, t_value, dtype=np.float32),
                        "maskT": mask_t,
                        "threshold": np.array(boundary_threshold, dtype=np.float32),
                        "radius": np.array(boundary_radius, dtype=np.int64),
                    },
                )[0]
                soft = 1.0 / (1.0 + np.exp(-np.clip(logits, -60.0, 60.0)))
                boundaries = game_decode.decode_soft_boundaries(
                    soft, known, mask_t, boundary_threshold, boundary_radius
                )

            durations, mask_n = durations_from_boundaries(boundaries, mask_t, runner.timestep)
            presence, scores = runner.estimator.run(
                None,
                {
                    "x_est": x_est,
                    "boundaries": boundaries,
                    "maskT": mask_t,
                    "maskN": mask_n,
                    "threshold": np.array(score_threshold, dtype=np.float32),
                },
            )

            for index, (offset, _) in enumerate(batch):
                note_durations = np.asarray(durations[index], dtype=np.float64)
                if note_durations.size == 0:
                    continue
                frame_counts = note_durations / runner.timestep
                starts = np.cumsum(frame_counts) - frame_counts
                voiced = np.asarray(presence[index], dtype=bool)
                pitch = np.asarray(scores[index], dtype=np.float64)
                for note_index in range(note_durations.size):
                    start_sec = float(offset + starts[note_index] * runner.timestep)
                    dur_sec = float(note_durations[note_index])
                    if dur_sec <= 0:
                        continue
                    notes.append(
                        {
                            "start_sec": round(start_sec, 4),
                            "dur_sec": round(dur_sec, 4),
                            "midi": round(float(pitch[note_index]), 3),
                            "voiced": bool(voiced[note_index]),
                        }
                    )
            if on_progress:
                on_progress(min(1.0, (batch_index + 1) / len(batches)))
    finally:
        runner.close()

    notes.sort(key=lambda item: item["start_sec"])
    return notes
