"""内存任务表 + 单 worker 队列 + TTL 清理。

没有数据库、没有消息队列：单机单用户，重启后看不到历史任务是可以接受的，
但**产物必须留在磁盘上**——重试依赖 `input.wav`，播放依赖 `stems/*.wav`。

单 worker 是刻意的：单个模型已经吃满 8 GB 卡的显存，两个任务并行只会一起变慢或 OOM。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from app import config
from app.errors import JobRejected


class Stage(str, Enum):
    QUEUED = "queued"
    DECODING = "decoding"
    SEPARATING = "separating"
    PITCH = "pitch"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


ACTIVE = frozenset({Stage.QUEUED, Stage.DECODING, Stage.SEPARATING, Stage.PITCH})


class JobCanceled(Exception):
    """内部信号：用户取消了任务，不是错误。"""


@dataclass
class Job:
    id: str
    filename: str
    tier: str
    mode: str = "full"  # full = 分轨 + 音高；pitch = 跳过分轨直接提音高
    stage: Stage = Stage.QUEUED
    frac: float = 0.0
    detail: str = "排队中"
    error: str | None = None
    duration_sec: float | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def active(self) -> bool:
        return self.stage in ACTIVE

    @property
    def progress(self) -> int:
        return int(round(self.frac * 100))

    @property
    def elapsed_sec(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.time()
        return round(end - self.started_at, 1)


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pending = 0
        self._queued: set[str] = set()  # 哪些任务占着队列名额（create 出来的）
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pitchmorph")

    # ---- 生命周期 -------------------------------------------------

    def create(self, filename: str, tier: str, mode: str = "full") -> Job:
        with self._lock:
            if self._pending >= config.QUEUE_LIMIT:
                raise JobRejected(
                    f"已有 {self._pending} 个任务在排队（上限 {config.QUEUE_LIMIT}），等一个跑完再提交"
                )
            job = Job(id=uuid.uuid4().hex[:12], filename=filename, tier=tier, mode=mode)
            self._jobs[job.id] = job
            self._pending += 1
            self._queued.add(job.id)
        config.job_dir(job.id).mkdir(parents=True, exist_ok=True)
        return job

    def restore(self) -> int:
        """从磁盘恢复历史任务。

        任务表在内存，进程重启就空了；但 `meta.json` 与产物还在。
        不恢复的话，「几天前分离开好轨、今天回来做音高」这个用法根本用不了。
        只恢复有产物的目录，中断留下的空目录直接跳过。
        """
        if not config.JOBS_DIR.exists():
            return 0

        restored = 0
        for entry in config.JOBS_DIR.iterdir():
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            meta = entry / "meta.json"
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue

            has_stems = (entry / "stems" / "vocals.wav").exists()
            has_pitch = (entry / "features" / "pitch.json").exists()
            if not has_stems and not has_pitch:
                continue  # 中断的残留，交给 TTL 清理

            job = Job(
                id=entry.name,
                filename=data.get("filename") or entry.name,
                tier=data.get("tier") or config.DEFAULT_TIER,
                mode=data.get("mode") or "full",
                stage=Stage.DONE,
                frac=1.0,
                detail="完成",
                created_at=entry.stat().st_ctime,
                finished_at=entry.stat().st_mtime,
            )
            with self._lock:
                self._jobs[job.id] = job
            restored += 1
        return restored

    def submit(self, job: Job, runner: Callable[[Job], None]) -> None:
        self._executor.submit(self._run, runner, job)

    def _run(self, runner: Callable[[Job], None], job: Job) -> None:
        self.update(job.id, started_at=time.time())
        try:
            runner(job)
        except JobCanceled:
            self._finish(job.id, Stage.CANCELED, "已取消", clean=True)
        except BaseException as exc:  # 任何异常都不能让队列停摆
            from app.errors import friendly_error

            self._finish(job.id, Stage.FAILED, friendly_error(exc), clean=True)
        finally:
            with self._lock:
                # 只有 create 出来的任务占队列名额；对历史任务「只重跑音高」不该减别人的额度
                if job.id in self._queued:
                    self._queued.discard(job.id)
                    self._pending = max(0, self._pending - 1)

    def _finish(self, job_id: str, stage: Stage, detail: str, *, clean: bool = False) -> None:
        if clean:
            self._clean_artifacts(job_id)
        error = detail if stage is Stage.FAILED else None
        self.update(
            job_id,
            stage=stage,
            detail=detail,
            error=error,
            frac=1.0 if stage is Stage.DONE else None,
            finished_at=time.time(),
        )

    def _clean_artifacts(self, job_id: str) -> None:
        """取消/失败后不留半成品：stems/ 与 features/ 整个删掉，input.wav 留着给重试。"""
        root = config.job_dir(job_id)
        for name in ("stems", "features"):
            shutil.rmtree(root / name, ignore_errors=True)

    # ---- 读写 -----------------------------------------------------

    def update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            frac = fields.pop("frac", None)
            if frac is not None:
                # 进度只增不减：倒退比不动更让人困惑
                job.frac = max(job.frac, float(frac))
            for key, value in fields.items():
                if value is not None or key in ("error", "finished_at", "duration_sec"):
                    setattr(job, key, value)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None or not job.active:
            return False
        job.cancel.set()
        if job.stage is Stage.QUEUED:
            self._finish(job_id, Stage.CANCELED, "已取消")
        else:
            self.update(job_id, detail="正在取消…")
        return True

    def retry(self, job_id: str) -> Job:
        old = self.get(job_id)
        if old is None:
            raise JobRejected("任务不存在")
        if not (config.job_dir(job_id) / "input.wav").exists():
            raise JobRejected("这个任务的输入已被清理，请重新上传")
        job = self.create(old.filename, old.tier)
        shutil.copy2(config.job_dir(job_id) / "input.wav", config.job_dir(job.id) / "input.wav")
        self.update(job.id, duration_sec=old.duration_sec)
        return job

    def remove(self, job_id: str) -> bool:
        self.cancel(job_id)
        with self._lock:
            if self._jobs.pop(job_id, None) is None:
                return False
        shutil.rmtree(config.job_dir(job_id), ignore_errors=True)
        return True

    # ---- 清理 -----------------------------------------------------

    def reap(self, ttl_hours: float = config.TTL_HOURS) -> int:
        """删掉超过 TTL 的已完成任务目录。孤儿目录（进程崩溃留下的）也靠这里兜底。"""
        cutoff = time.time() - ttl_hours * 3600
        removed = 0
        with self._lock:
            stale = [
                job.id
                for job in self._jobs.values()
                if job.finished_at is not None and job.finished_at < cutoff
            ]
            for job_id in stale:
                self._jobs.pop(job_id, None)
        for job_id in stale:
            shutil.rmtree(config.job_dir(job_id), ignore_errors=True)
            removed += 1

        if config.JOBS_DIR.exists():
            for entry in config.JOBS_DIR.iterdir():
                if not entry.is_dir() or entry.name in self._jobs:
                    continue
                try:
                    if entry.stat().st_mtime < cutoff:
                        shutil.rmtree(entry, ignore_errors=True)
                        removed += 1
                except OSError:
                    continue
        return removed


registry = JobRegistry()
