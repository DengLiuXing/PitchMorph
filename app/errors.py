"""异常类型 + 用户能照做的错误文案。不依赖项目内任何模块。"""

from __future__ import annotations

_OOM_MARKERS = (
    "out of memory",
    "cuda error: out of memory",
    "cudamemory",
    "cublas_status_alloc_failed",
)


class PitchMorphError(Exception):
    """可直接展示给用户的中文错误。"""


class UnsupportedFormat(PitchMorphError):
    """解码失败、没有音轨、超出时长上限。"""


class JobRejected(PitchMorphError):
    """入队前就被拒（体积、队列已满、档位非法）。"""


def friendly_error(exc: BaseException) -> str:
    """把底层异常翻成三步可操作提示，保留原始首行供排查。"""
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if any(marker in lowered for marker in _OOM_MARKERS):
        return (
            "显存不足。请依次尝试：关掉占用显存的程序（浏览器硬件加速、游戏、录屏）→ "
            "改用「快速」档 → 仍不行就设 PITCHMORPH_DEVICE=cpu 强制走 CPU。\n"
            f"原始错误：{text.splitlines()[0]}"
        )
    if isinstance(exc, PitchMorphError):
        return str(exc)
    return text
