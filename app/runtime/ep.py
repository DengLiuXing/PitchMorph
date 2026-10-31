"""EP 探测与降级阶梯：winml → dml → cpu（docs/09-contract.md §7）。

这是全项目唯一提到 WinML 的地方。业务代码只认 `provider_for(ep)` 返回的字符串。
"""

from __future__ import annotations

import functools

import onnxruntime as ort

# 当前构建（onnxruntime-windowsml）直接暴露 DmlExecutionProvider；
# 换成 onnxruntime-directml 时这一行不用改，因为它提供的也是同一个名字。
_DML = "DmlExecutionProvider"


@functools.lru_cache(maxsize=1)
def available() -> tuple[str, ...]:
    return tuple(ort.get_available_providers())


@functools.lru_cache(maxsize=1)
def detect() -> str:
    """返回 `winml` / `dml` / `cpu`。`winml` 与 `dml` 目前是同一份 DirectML EP，先返回 winml 以便记录来源。"""
    if _DML in available():
        return "winml"
    return "cpu"


def provider_for(ep: str | None = None) -> list[str]:
    chosen = ep or detect()
    if chosen in ("winml", "dml"):
        return [_DML, "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def describe() -> dict:
    return {
        "detected": detect(),
        "available": list(available()),
        "ort": ort.__version__,
    }
