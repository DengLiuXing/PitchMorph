"""job 目录与 meta.json 的读写。文件树即数据库，没有第二个真相源。"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from app.core import config
from app.domain.job import Job


def new_id() -> str:
    # ULID 需要额外依赖；uuid4 的十六进制前 12 位已足够排序与去重
    return f"{time.strftime('%y%m%d')}-{uuid.uuid4().hex[:12]}"


def job_dir(job_id: str) -> Path:
    return config.DATA_DIR / job_id


def meta_path(job_id: str) -> Path:
    return job_dir(job_id) / "meta.json"


def save(job: Job) -> None:
    job.touch()
    path = meta_path(job.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load(job_id: str) -> Job:
    data = json.loads(meta_path(job_id).read_text(encoding="utf-8"))
    return Job.from_dict(data)


def list_jobs() -> list[Job]:
    root = config.DATA_DIR
    if not root.exists():
        return []
    jobs = []
    for path in root.glob("*/meta.json"):
        jobs.append(Job.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return sorted(jobs, key=lambda j: j.created_at, reverse=True)


def delete(job_id: str, include_children: bool = True) -> None:
    if include_children:
        for child in list_jobs():
            if child.parent == job_id:
                delete(child.id, include_children=True)
    shutil.rmtree(job_dir(job_id), ignore_errors=True)


def purge_expired(ttl_days: int = config.JOB_TTL_DAYS) -> int:
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for job in list_jobs():
        if job.created_at < cutoff and job.state in ("succeeded", "failed", "cancelled"):
            delete(job.id)
            removed += 1
    return removed
