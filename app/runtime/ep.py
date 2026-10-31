"""EP 选择：默认全部走 CPU，需要时再用环境变量打开 GPU。

2026-10-31 的决定是「先把功能在 CPU 上跑通，性能优化留到后面」。实测依据：

- GAME 在 DirectML 上数值等价、segmenter 快 2.5×，是唯一可以放心切 GPU 的模型
- FCPE 在 DirectML 上主干失真（RPA 28.4%），必须 CPU
- demucs 在 DirectML 上图融合阶段直接 E_OUTOFMEMORY，跑不起来

所以这里只保留两个入口：默认 `cpu`，以及 `PITCHMORPH_EP=dml` 时给 GAME 用 GPU。
换运行时只改这个文件。
"""

from __future__ import annotations

import functools
import os

import onnxruntime as ort

_DML = "DmlExecutionProvider"
_GPU_CAPABLE = {"game"}  # 只有它实测可用


@functools.lru_cache(maxsize=1)
def available() -> tuple[str, ...]:
    return tuple(ort.get_available_providers())


@functools.lru_cache(maxsize=1)
def requested() -> str:
    return os.environ.get("PITCHMORPH_EP", "cpu").strip().lower()


def detect() -> str:
    """实际会用到的 EP：默认 cpu；只有显式请求且显卡可用时才返回 dml。"""
    if requested() in ("dml", "winml") and _DML in available():
        return "dml"
    return "cpu"


def provider_for(model_family: str | None = None) -> list[str]:
    """`model_family` 用 game / fcpe / demucs。不给就是「按全局设置」。"""
    if detect() == "cpu":
        return ["CPUExecutionProvider"]
    if model_family is None or model_family in _GPU_CAPABLE:
        return [_DML, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def describe() -> dict:
    return {
        "detected": detect(),
        "available": list(available()),
        "ort": ort.__version__,
        "requested": requested(),
    }
