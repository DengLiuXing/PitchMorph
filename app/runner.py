"""单进程任务执行器：SSE 事件、取消、状态机都从这里走。

并发刻意保持为 1：单卡多专家并发是负优化（doc_old/perf-audit.md），
而且同一 job 目录不该有两个写者。
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from app import storage
from app.domain.job import Job
from app.storage import jobs as job_store

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stage")
_listeners: dict[str, list[queue.Queue]] = defaultdict(list)
_lock = threading.Lock()
_cancelled: set[str] = set()

STAGES = ("separate", "pitch", "notes", "export")


def _emit(job: Job, event: str, **payload) -> None:
    message = {"event": event, "id": job.id, "state": job.state, "progress": job.progress, **payload}
    with _lock:
        listeners = list(_listeners[job.id])
    for sink in listeners:
        sink.put(message)


def subscribe(job_id: str) -> queue.Queue:
    sink: queue.Queue = queue.Queue()
    with _lock:
        _listeners[job_id].append(sink)
    return sink


def unsubscribe(job_id: str, sink: queue.Queue) -> None:
    with _lock:
        if sink in _listeners[job_id]:
            _listeners[job_id].remove(sink)
        if not _listeners[job_id]:
            _listeners.pop(job_id, None)


def cancel(job_id: str) -> None:
    _cancelled.add(job_id)


def is_cancelled(job_id: str) -> bool:
    return job_id in _cancelled


def submit(job: Job) -> None:
    job_store.save(job)
    _executor.submit(_run, job.id)


def _run(job_id: str) -> None:
    from app.stages import dispatch  # 延迟导入：避免加载阶段时就把 ORT 拉起来

    job = job_store.load(job_id)
    job.transition("running")
    job_store.save(job)
    _emit(job, "running")
    try:
        artifacts = dispatch(job)
        if is_cancelled(job_id):
            job.transition("cancelled")
        else:
            job.artifacts.extend(artifacts)
            job.progress = 1.0
            job.transition("succeeded")
    except Exception as exc:  # noqa: BLE001 - 失败要落到 meta.json，不能只打日志
        job.fail(type(exc).__name__, str(exc))
        traceback.print_exc()
    finally:
        _cancelled.discard(job_id)
        job_store.save(job)
        _emit(job, job.state)


def progress(job: Job, value: float, note: str = "") -> None:
    job.progress = max(0.0, min(1.0, value))
    job_store.save(job)
    _emit(job, "progress", note=note)


def wait(job_id: str, timeout: float = 600.0) -> Job:
    """测试与 CLI 用：阻塞到终态。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = job_store.load(job_id)
        if job.state in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise TimeoutError(job_id)


__all__ = ["submit", "cancel", "is_cancelled", "subscribe", "unsubscribe", "progress", "wait", "STAGES"]
_ = storage  # 保持包导入顺序明确
