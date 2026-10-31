"""session 池：按 (模型, EP) 缓存，用完可整体释放。

为什么需要「释放」：demucs 每个专家跑完一块要占 2.2–3.2 GB 且不回缩，
四个同时常驻约 7.8 GB，8 GB 卡必 OOM（docs/07-spike.md §7 与 doc_old/perf-audit.md）。
"""

from __future__ import annotations

import gc
from pathlib import Path

import onnxruntime as ort

from app.runtime import ep as ep_mod

_SESSIONS: dict[tuple[str, str], ort.InferenceSession] = {}


def options() -> ort.SessionOptions:
    opts = ort.SessionOptions()
    # DirectML EP 明确不支持 memory pattern 与并行执行，必须关
    opts.enable_mem_pattern = False
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return opts


def get(model_path: str | Path, ep: str | None = None) -> tuple[ort.InferenceSession, str]:
    chosen = ep or ep_mod.detect()
    key = (str(model_path), chosen)
    if key not in _SESSIONS:
        if not Path(model_path).exists():
            raise FileNotFoundError(f"模型不存在：{model_path}")
        _SESSIONS[key] = ort.InferenceSession(
            str(model_path), options(), providers=ep_mod.provider_for(chosen)
        )
    return _SESSIONS[key], chosen


def release(key: tuple[str, str] | None = None) -> None:
    """释放一个或全部 session；调用后立刻 gc，让显存真的还回去。"""
    if key is None:
        _SESSIONS.clear()
    else:
        _SESSIONS.pop(key, None)
    gc.collect()
