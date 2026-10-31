"""6 个端点，定义见 docs/09-contract.md §4。"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app import runner
from app.api.schemas import CreateJob, JobOut, RuntimeOut
from app.domain import params as param_mod
from app.domain.job import Job
from app.runtime import ep as ep_mod
from app.storage import jobs as store

router = APIRouter(prefix="/api")


def _to_out(job: Job) -> JobOut:
    return JobOut(**job.to_dict())


@router.post("/jobs", response_model=JobOut)
def create_job(body: CreateJob) -> JobOut:
    try:
        normalized = param_mod.normalize(body.kind, body.params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if body.kind != "separate" and not body.parent:
        raise HTTPException(status_code=422, detail="派生任务必须给 parent")

    job = Job(
        id=store.new_id(),
        kind=body.kind,
        params=normalized,
        parent=body.parent,
        target=body.target,
    )
    store.job_dir(job.id).mkdir(parents=True, exist_ok=True)
    runner.submit(job)
    return _to_out(job)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs() -> list[JobOut]:
    return [_to_out(job) for job in store.list_jobs()]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str) -> JobOut:
    try:
        return _to_out(store.load(job_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="job 不存在") from exc


@router.get("/jobs/{job_id}/events")
def events(job_id: str) -> StreamingResponse:
    if not store.meta_path(job_id).exists():
        raise HTTPException(status_code=404, detail="job 不存在")

    def stream():
        sink = runner.subscribe(job_id)
        try:
            job = store.load(job_id)
            yield f"data: {json.dumps({'event': job.state, 'progress': job.progress})}\n\n"
            while True:
                try:
                    message = sink.get(timeout=15)
                except Exception:  # noqa: BLE001 - 心跳，避免连接被中间层掐断
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
                if message.get("event") in ("succeeded", "failed", "cancelled"):
                    break
        finally:
            runner.unsubscribe(job_id, sink)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str) -> JobOut:
    job = store.load(job_id)
    runner.cancel(job_id)
    if job.state in ("queued", "running"):
        job.transition("cancelled")
        store.save(job)
    return _to_out(job)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    if not store.meta_path(job_id).exists():
        raise HTTPException(status_code=404, detail="job 不存在")
    store.delete(job_id)
    return {"deleted": job_id}


@router.get("/params/schema")
def params_schema() -> dict:
    return param_mod.schema()


@router.get("/runtime", response_model=RuntimeOut)
def runtime() -> RuntimeOut:
    return RuntimeOut(**ep_mod.describe())
