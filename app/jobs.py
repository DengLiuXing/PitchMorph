"""In-memory job table + single-worker queue.

One worker is intentional: four concurrent ONNX sessions would push peak RSS
into multi-gigabyte territory and slow the machine down more than they speed a
single track up.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import config, separator
from .schemas import JobStage

log = logging.getLogger("pitchmorph.jobs")

ACTIVE_STAGES = frozenset(
    {JobStage.QUEUED, JobStage.DOWNLOADING, JobStage.LOADING, JobStage.SEPARATING},
)


@dataclass
class Job:
    id: str
    filename: str
    size_bytes: int
    tier: config.ModelTier
    duration_sec: float
    dir: Path
    stage: JobStage = JobStage.QUEUED
    stem: str | None = None
    done: int = 0
    total: int = 1
    frac: float = 0.0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    delete_requested: bool = False
    stems: dict[str, str] = field(default_factory=dict)

    @property
    def input_wav(self) -> Path:
        return self.dir / "input.wav"

    @property
    def stems_dir(self) -> Path:
        return self.dir / "stems"

    @property
    def wait_sec(self) -> float:
        """Time spent queued before a worker picked the job up."""
        end = self.started_at or time.time()
        return max(0.0, round(end - self.created_at, 1))

    @property
    def elapsed_sec(self) -> float:
        """Processing time only — 0 while the job is still queued."""
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.time()
        return max(0.0, round(end - self.started_at, 1))

    @property
    def progress(self) -> float:
        """Finer than ``done/total``: tracks the 7.8 s inference chunks too."""
        if self.stage is JobStage.DONE:
            return 1.0
        if self.frac > 0:
            return round(min(self.frac, 0.999), 3)
        return round(self.done / self.total, 3) if self.total else 0.0

    @property
    def is_active(self) -> bool:
        return self.stage in ACTIVE_STAGES


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="separator")

    # -- lifecycle ---------------------------------------------------------

    def create(self, *, filename: str, size_bytes: int, tier: config.ModelTier) -> Job:
        job_id = uuid.uuid4().hex
        job_dir = config.JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=job_id,
            filename=filename,
            size_bytes=size_bytes,
            tier=tier,
            duration_sec=0.0,
            dir=job_dir,
            total=len(tier.stems),
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        """Newest first — the order the task list renders in."""
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.is_active)

    def submit(self, job: Job) -> None:
        job.stage = JobStage.QUEUED
        self._pool.submit(self._process, job)

    def cancel(self, job: Job) -> None:
        job.cancel.set()
        if job.stage is JobStage.QUEUED:
            job.stage = JobStage.CANCELED
            job.finished_at = time.time()

    def discard(self, job_id: str) -> None:
        """Drop a job rejected before it ever reached the queue."""
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is not None:
            shutil.rmtree(job.dir, ignore_errors=True)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        job.cancel.set()
        if job.is_active:
            # The worker owns the directory while it runs; let it clean up.
            job.delete_requested = True
            return True
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(job.dir, ignore_errors=True)
        return True

    def reap(self) -> None:
        """Drop finished jobs older than the TTL, plus orphaned directories."""
        now = time.time()
        expired: list[str] = []
        with self._lock:
            for job_id, job in self._jobs.items():
                if job.is_active:
                    continue
                age = now - (job.finished_at or job.created_at)
                if age > config.JOB_TTL_SEC:
                    expired.append(job_id)
        for job_id in expired:
            self.delete(job_id)

        if not config.JOBS_DIR.exists():
            return
        with self._lock:
            known = set(self._jobs)
        for path in config.JOBS_DIR.iterdir():
            if not path.is_dir() or path.name in known:
                continue
            try:
                if now - path.stat().st_mtime > config.JOB_TTL_SEC:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    # -- worker ------------------------------------------------------------

    def _process(self, job: Job) -> None:
        job.started_at = time.time()

        if job.cancel.is_set():
            job.stage = JobStage.CANCELED
            job.finished_at = time.time()
            self._finish(job)
            return

        def on_stage(stage: str, stem: str | None, done: int, total: int) -> None:
            job.stage = JobStage(stage)
            job.stem = stem
            job.done = done
            job.total = total

        def on_progress(frac: float) -> None:
            job.frac = frac

        try:
            produced = separator.separate_stems(
                job.input_wav,
                job.stems_dir,
                tier=job.tier,
                on_stage=on_stage,
                on_progress=on_progress,
                should_cancel=job.cancel.is_set,
            )
        except separator.Canceled:
            job.stage = JobStage.CANCELED
            job.stem = None
            job.done = 0
        except separator.SeparationError as exc:
            job.stage = JobStage.ERROR
            job.error = str(exc)
            log.error("job %s failed: %s", job.id, exc)
        except Exception as exc:  # unexpected — keep the job queryable
            job.stage = JobStage.ERROR
            job.error = separator.friendly_error(exc)
            log.exception("job %s crashed", job.id)
        else:
            job.stems = {
                name: f"/media/{job.id}/stems/{path.name}"
                for name, path in produced.items()
            }
            job.stem = None
            job.done = job.total
            job.stage = JobStage.DONE
            log.info("job %s done in %.1fs", job.id, job.elapsed_sec)
        finally:
            job.finished_at = time.time()

        self._finish(job)

    def _finish(self, job: Job) -> None:
        if not job.delete_requested:
            return
        with self._lock:
            self._jobs.pop(job.id, None)
        shutil.rmtree(job.dir, ignore_errors=True)


registry = JobRegistry()
