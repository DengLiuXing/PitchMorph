"""ONNX Runtime 装配：DLL 预加载、EP 选择与回落、会话创建与释放。

显存是唯一硬约束：四轨档必须一个专家跑完立刻 release，不允许两个会话并存。
"""
from __future__ import annotations

import gc
import logging
from pathlib import Path
from typing import Any

import onnxruntime as ort

from .config import requested_ep

logger = logging.getLogger("pitchmorph.runtime")

import os

# 8 GB 笔记本上显存是硬约束：默认不开 cuDNN 最大 workspace，
# 换取约 1 GB 的显存余量（实测见 V3 构想.md 第 14 节）。需要极限速度可设 PITCHMORPH_CUDNN_MAX_WS=1。
CUDA_OPTIONS = {
    "device_id": 0,
    "cudnn_conv_use_max_workspace": os.environ.get("PITCHMORPH_CUDNN_MAX_WS", "0"),
    "cudnn_conv_algo_search": "HEURISTIC",
    "arena_extend_strategy": "kSameAsRequested",
}

# 给显存池设上限：8 GB 卡上不加限制时单会话峰值 5.6 GB，容易被桌面合成器挤爆。
# 限到 4 GB 后实测峰值 3.2 GB 且速度几乎不变（9.1 s vs 7.6 s，12 s 音频四专家）。
_limit_mb = os.environ.get("PITCHMORPH_GPU_LIMIT_MB", "4096").strip()
if _limit_mb.isdigit():
    CUDA_OPTIONS["gpu_mem_limit"] = str(int(_limit_mb) * 1024 * 1024)

_preloaded = False
_resolved_ep: str | None = None
_fallback_reason: str | None = None

# 会话缓存：建一次 demucs 会话要 4.4 s、首段还要 1.2 s warmup。连续处理多首歌时
# 复用同一个会话能把这 5.6 s 省掉。缓存上限很小（显存是硬约束），换模型时先放旧会话。
_CACHE_ENABLED = os.environ.get("PITCHMORPH_KEEP_SESSION", "1").strip() not in ("0", "false")
_CACHE_MAX = 2
_CACHED: dict[str, ort.InferenceSession] = {}


def preload() -> None:
    """pip 装的 cuDNN 在 site-packages 里，系统 PATH 上没有，必须显式预加载。"""
    global _preloaded
    if _preloaded:
        return
    if hasattr(ort, "preload_dlls"):
        try:
            ort.preload_dlls()
        except Exception as exc:  # 非 Windows 或纯 CPU 包
            logger.info("preload_dlls 跳过：%s", exc)
    _preloaded = True


def available_providers() -> list[str]:
    preload()
    return list(ort.get_available_providers())


def resolve_ep() -> str:
    """返回实际生效的 EP：cuda 或 cpu，并记录回落原因。"""
    global _resolved_ep, _fallback_reason
    if _resolved_ep is not None:
        return _resolved_ep
    want = requested_ep()
    providers = available_providers()
    if want == "cuda" and "CUDAExecutionProvider" in providers:
        _resolved_ep = "cuda"
        _fallback_reason = None
    elif want == "cuda":
        _resolved_ep = "cpu"
        _fallback_reason = (
            "未检测到 CUDAExecutionProvider：可能装的是 CPU 版 onnxruntime，"
            "或驱动低于 580。已回落到 CPU。"
        )
        logger.warning(_fallback_reason)
    else:
        _resolved_ep = "cpu"
        _fallback_reason = "PITCHMORPH_EP=cpu，按环境变量强制使用 CPU。"
    logger.info("推理设备：%s（可用 provider：%s）", _resolved_ep, providers)
    return _resolved_ep


def status() -> dict[str, Any]:
    """给前端顶栏徽标用。"""
    ep = resolve_ep()
    return {
        "ep": ep,
        "providers": available_providers(),
        "version": ort.__version__,
        "fallback_reason": _fallback_reason,
    }


def make_session(
    model_path: Path,
    *,
    provider_options: dict[str, Any] | None = None,
    ep: str | None = None,
) -> tuple[ort.InferenceSession, list[str]]:
    """按当前 EP 建会话，返回 (session, 实际 provider 列表)。

    provider_options 用于按模型覆盖 CUDA 选项：提纯模型 5 s 块要 5.1 GB 显存，
    撞上全局 4 GB 的 gpu_mem_limit 会直接失败。
    """
    preload()
    ep = ep or resolve_ep()
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if ep == "cuda":
        cuda = dict(CUDA_OPTIONS)
        if provider_options:
            cuda.update(provider_options)
        providers: list[Any] = [("CUDAExecutionProvider", cuda), "CPUExecutionProvider"]
    else:
        providers = ["CPUExecutionProvider"]

    session = ort.InferenceSession(str(model_path), sess_options=options, providers=providers)
    actual = list(session.get_providers())
    logger.info("会话就绪 model=%s providers=%s", model_path.name, actual)
    return session, actual


def release(session: ort.InferenceSession | None) -> None:
    """释放会话并促使显存回收。四专家串行依赖这里不留残余引用。

    缓存里的会话不在此处释放：调用方作用域结束后它仍被缓存持有，显存不回收。
    """
    if session is None:
        return
    try:
        del session
    finally:
        gc.collect()  # 显式回收，避免下一个专家启动时两块显存叠加


def _category(name: str) -> str:
    """会话分类：heavy = demucs（显存大户），light = FCPE 之类的小图。"""
    return "light" if "fcpe" in name.lower() else "heavy"


def purge(category: str | None = None) -> None:
    """清掉缓存里的会话并立刻回收显存。

    提纯任务开始前要先把 demucs 会话放掉：提纯模型 5 s 块吃 5.1 GB，
    与 demucs 的 3.2 GB 叠加会顶爆 8 GB 卡。
    """
    if not _CACHE_ENABLED:
        return
    for key in list(_CACHED):
        if category and _category(Path(key).name) != category:
            continue
        session = _CACHED.pop(key)
        del session
    gc.collect()


def acquire(model_path: Path) -> ort.InferenceSession:
    """取一个可用会话：命中缓存直接复用，否则新建并放进缓存。"""
    key = str(model_path)
    if _CACHE_ENABLED:
        hit = _CACHED.pop(key, None)
        if hit is not None:
            _CACHED[key] = hit  # 提到末尾 = 最近用过
            logger.info("复用会话 model=%s", model_path.name)
            return hit
        # 同类旧会话先彻底放掉：两个重会话同时驻留会顶到显存上限
        category = _category(model_path.name)
        for old_key in [k for k in _CACHED if _category(Path(k).name) == category]:
            old = _CACHED.pop(old_key)
            del old
        gc.collect()
    session, _ = make_session(model_path)
    if _CACHE_ENABLED:
        _CACHED[key] = session
        while len(_CACHED) > _CACHE_MAX:
            _, old = _CACHED.popitem()
            del old
        gc.collect()
    return session


def describe_io(session: ort.InferenceSession) -> dict[str, Any]:
    """打印/返回模型 I/O 契约，用于拒绝照抄假设。"""
    def shape(value):
        return [d if isinstance(d, int) else str(d) for d in value.shape]

    return {
        "inputs": [
            {"name": i.name, "type": i.type, "shape": shape(i)} for i in session.get_inputs()
        ],
        "outputs": [
            {"name": o.name, "type": o.type, "shape": shape(o)} for o in session.get_outputs()
        ],
    }
