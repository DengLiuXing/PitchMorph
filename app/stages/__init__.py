"""阶段注册表。每个 stage 是「读 job 目录 → 写产物」的纯函数 + 一个副作用边界。"""

from __future__ import annotations

from collections.abc import Callable

from app.domain.artifact import Artifact
from app.domain.job import Job

Handler = Callable[[Job], list[Artifact]]
REGISTRY: dict[str, Handler] = {}


def register(kind: str, handler: Handler) -> None:
    REGISTRY[kind] = handler


def dispatch(job: Job) -> list[Artifact]:
    handler = REGISTRY.get(job.kind)
    if handler is None:
        raise NotImplementedError(f"阶段 {job.kind} 尚未实现")
    return handler(job)
