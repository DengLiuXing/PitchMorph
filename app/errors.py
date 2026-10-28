"""Exceptions shared by the media / weights / inference layers.

Kept in its own module so ``weights`` and ``media`` can raise the same types the
job layer catches, without importing each other or the inference engine.
"""

from __future__ import annotations

from . import config


class SeparationError(RuntimeError):
    """Base error surfaced to the UI as ``job.error``."""


class UnsupportedFormat(SeparationError):
    """The file could not be decoded as audio."""


class Canceled(SeparationError):
    """The caller's cancel flag was set; the job stops at the next checkpoint."""


#: ORT reports a full VRAM as either its own wrapper error or the raw CUDA one,
#: and the CUDA one arrives with an ``expr=cudaMemcpy(...)`` second line that
#: means nothing to a user. Matched on lowercased text.
_OOM_MARKERS = (
    "out of memory",
    "cuda failure 2",  # cudaErrorMemoryAllocation
    "cuda_error_out_of_memory",
    "cudaerror_memoryallocation",
    "ortoutofmemoryerror",
)


def friendly_error(exc: Exception) -> str:
    """Turn ORT / Hub exceptions into something the user can act on."""
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if any(marker in lowered for marker in _OOM_MARKERS):
        # The original line is kept because it is the only clue left in the UI:
        # jobs.py logs the message without a traceback on this path.
        detail = message.splitlines()[0].strip()
        return (
            "显存不足，分轨中断。可以依次尝试："
            "① 关掉其他占用显存的程序（浏览器硬件加速、游戏、剪辑软件），一次只跑一个任务；"
            "② 改用「仅人声」档，算力约为四轨档的四分之一；"
            "③ 仍不行就设 PITCHMORPH_PROVIDERS=CPUExecutionProvider 用 CPU 跑完，"
            f"速度慢但不受显存限制。（原始错误：{detail}）"
        )
    if "no such file" in lowered or "not found" in lowered:
        if "huggingface" in lowered or "hub" in lowered or "repository" in lowered:
            return (
                "下载模型权重失败。国内网络可设置环境变量 "
                "HF_ENDPOINT=https://hf-mirror.com 后重试。"
            )
    if "could not download" in lowered or "connection" in lowered or "timed out" in lowered:
        return (
            "下载模型权重超时。可设置 HF_ENDPOINT=https://hf-mirror.com 后重试，"
            f"或手动把权重放到 {config.MODEL_DIR}"
        )
    if "provider" in lowered and "not in available providers" in lowered:
        return f"ONNX Runtime 执行器不可用：{message}"
    return message
