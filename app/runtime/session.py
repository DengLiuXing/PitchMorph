"""session 池：按 (模型, EP) 缓存，用完可整体释放。"""

from __future__ import annotations

import gc
from pathlib import Path

import onnxruntime as ort

from app.runtime import ep as ep_mod

_SESSIONS: dict[tuple[str, str], ort.InferenceSession] = {}


def options() -> ort.SessionOptions:
    opts = ort.SessionOptions()
    # DirectML EP 明确不支持 memory pattern 与并行执行；CPU 上没有副作用
    opts.enable_mem_pattern = False
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return opts


def get(model_path: str | Path, family: str | None = None) -> tuple[ort.InferenceSession, str]:
    """返回 (session, 实际 EP)。EP 名要写进 artifact，别只存在内存里。"""
    providers = ep_mod.provider_for(family)
    chosen = "dml" if providers[0].startswith("Dml") else "cpu"
    key = (str(model_path), chosen)
    if key not in _SESSIONS:
        if not Path(model_path).exists():
            raise FileNotFoundError(f"模型不存在：{model_path}")
        _SESSIONS[key] = ort.InferenceSession(str(model_path), options(), providers=providers)
    return _SESSIONS[key], chosen


def release(key: tuple[str, str] | None = None) -> None:
    if key is None:
        _SESSIONS.clear()
    else:
        _SESSIONS.pop(key, None)
    gc.collect()
