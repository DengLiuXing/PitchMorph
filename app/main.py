"""FastAPI app: upload → queue → poll → play/download stems."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import config, jobs, runtime, separator
from .schemas import (
    HealthResponse,
    JobCreateResponse,
    JobStatusResponse,
    ModelInfo,
)

# Harmless on Windows: the Hub just falls back to copying instead of symlinking
# the weight cache, which would otherwise print a scary warning on every run.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("pitchmorph")

config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
config.MODEL_DIR.mkdir(parents=True, exist_ok=True)

REAP_INTERVAL_SEC = 3600
CHUNK = 1 << 20


@asynccontextmanager
async def lifespan(_: FastAPI):
    async def reaper() -> None:
        while True:
            await asyncio.sleep(REAP_INTERVAL_SEC)
            try:
                await run_in_threadpool(jobs.registry.reap)
            except Exception:  # never kill the loop over cleanup
                log.exception("reaper failed")

    task = asyncio.create_task(reaper())

    # Probe before serving: it takes tens of milliseconds and decides the
    # provider the UI shows on first paint. It runs in a thread because the
    # probe builds a real ONNX session, and on CUDA that means loading DLLs.
    usable, reason = await run_in_threadpool(runtime.gpu_status)
    if usable:
        log.info("PitchMorph ready — data dir %s, CUDA execution provider", config.DATA_DIR)
    else:
        log.info(
            "PitchMorph ready — data dir %s, CPU fallback (%s)",
            config.DATA_DIR,
            reason,
        )

    # Warm the default tier off the critical path: on CUDA the first session
    # build compiles the graph, which would otherwise land on the first upload.
    tier = config.TIERS[config.DEFAULT_TIER]

    async def warm() -> None:
        await run_in_threadpool(runtime.prewarm, tier.model, tier.stems)

    warm_task = asyncio.create_task(warm())

    try:
        yield
    finally:
        warm_task.cancel()
        task.cancel()
        jobs.registry._pool.shutdown(wait=False)  # noqa: SLF001 - process exit


app = FastAPI(title="PitchMorph", version="0.1.0", lifespan=lifespan)
app.mount("/media", StaticFiles(directory=str(config.JOBS_DIR), follow_symlink=False), name="media")
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR), follow_symlink=False), name="static")


def _safe_stem(filename: str) -> str:
    """Source filename without extension, safe for use in a download name."""
    stem = Path(filename).stem or "audio"
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", stem).strip()
    return cleaned or "audio"


def _status(job: jobs.Job) -> JobStatusResponse:
    return JobStatusResponse(
        id=job.id,
        stage=job.stage,
        progress=job.progress,
        stem=job.stem,
        done=job.done,
        total=job.total,
        parallel=job.parallel,
        elapsed_sec=job.elapsed_sec,
        wait_sec=job.wait_sec,
        duration_sec=round(job.duration_sec, 2),
        stems=job.stems,
        error=job.error,
        filename=job.filename,
        size_bytes=job.size_bytes,
        tier=job.tier.key,
        cancel_requested=job.cancel.is_set(),
        created_at=job.created_at,
    )


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/models", response_model=list[ModelInfo])
def list_models() -> list[ModelInfo]:
    # The frontend preselects the first entry, so the default tier has to lead
    # the list — DEFAULT_TIER alone only affects the form field default.
    tiers = sorted(config.TIERS.values(), key=lambda tier: tier.key != config.DEFAULT_TIER)
    return [
        ModelInfo(
            key=tier.key,
            label=tier.label,
            model=tier.model,
            stems=list(tier.stems),
            download_mb=tier.download_mb,
            eta=tier.eta,
            detail=tier.detail,
            cached=separator.weights_cached(tier.model, tier.stems),
        )
        for tier in tiers
    ]


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    import onnxruntime as ort

    gpu_usable, gpu_reason = runtime.gpu_status()
    ffmpeg = config.find_ffmpeg()
    return HealthResponse(
        ok=True,
        python=sys.version.split()[0],
        onnxruntime=ort.__version__,
        available_providers=list(ort.get_available_providers()),
        active_providers=runtime.providers(),
        gpu_ready=gpu_usable,
        gpu_reason=gpu_reason,
        ffmpeg=ffmpeg,
        ffmpeg_version=config.ffmpeg_version(ffmpeg),
        ffmpeg_source=config.ffmpeg_source(),
        model_dir=str(config.MODEL_DIR),
        supported_exts=sorted(ext.lstrip(".") for ext in config.supported_exts()),
        max_upload_mb=config.MAX_UPLOAD_BYTES // (1024 * 1024),
        max_duration_sec=config.MAX_DURATION_SEC,
        default_parallel=config.default_parallel(),
        max_parallel=config.MAX_PARALLEL,
    )


@app.post("/api/jobs", response_model=JobCreateResponse, status_code=202)
async def create_job(
    file: UploadFile = File(...),
    tier: str = Form(config.DEFAULT_TIER),
    parallel: int | None = Form(None),
) -> JobCreateResponse:
    if tier not in config.TIERS:
        raise HTTPException(status_code=400, detail=f"未知模型档位：{tier}")
    if parallel is None:
        parallel = config.default_parallel()
    elif not 1 <= parallel <= config.MAX_PARALLEL:
        raise HTTPException(
            status_code=400,
            detail=f"并行度需在 1–{config.MAX_PARALLEL} 之间，收到 {parallel}",
        )
    # One specialist per stem: asking for more than the stem count would be
    # echoed back to the UI as a concurrency the job never has.
    parallel = min(parallel, len(config.TIERS[tier].stems))
    if jobs.registry.active_count() >= config.MAX_QUEUED_JOBS:
        raise HTTPException(
            status_code=429,
            detail=f"队列已满（上限 {config.MAX_QUEUED_JOBS} 个任务），请等当前任务完成",
        )

    suffix = Path(file.filename or "").suffix.lower()
    if not config.can_accept(suffix):
        raise HTTPException(
            status_code=415,
            detail=(
                f"不支持 {suffix or '(无扩展名)'}：本机未安装 ffmpeg，只能读 "
                f"{', '.join(sorted(config.NATIVE_EXTS))}"
            ),
        )

    job = jobs.registry.create(
        filename=Path(file.filename or "audio").name,
        size_bytes=0,
        tier=config.TIERS[tier],
        parallel=parallel,
    )
    raw_path = job.dir / f"source{suffix}"
    size = 0
    try:
        with raw_path.open("wb") as sink:
            while chunk := await file.read(CHUNK):
                size += len(chunk)
                if size > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限",
                    )
                sink.write(chunk)
        await file.close()

        if size == 0:
            raise HTTPException(status_code=400, detail="文件为空")

        # Containers (mp4 / m4s / mkv …) get a cheap header probe first, so a
        # video without an audio track or a 40-minute film is rejected before we
        # spend time transcoding it.
        if suffix not in config.NATIVE_EXTS:
            probed, has_audio = await run_in_threadpool(
                separator.probe_with_ffmpeg, raw_path,
            )
            if not has_audio:
                raise HTTPException(
                    status_code=415,
                    detail="该文件里没有音频轨（视频与音频分开缓存时，请选音频那一段）",
                )
            if probed and probed > config.MAX_DURATION_SEC:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"音频过长（{probed / 60:.1f} 分钟），"
                        f"上限 {config.MAX_DURATION_SEC // 60} 分钟，请先裁剪"
                    ),
                )

        try:
            duration = await run_in_threadpool(separator.decode_to_wav, raw_path, job.input_wav)
        except separator.UnsupportedFormat as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc

        if duration < 1:
            raise HTTPException(status_code=400, detail="音频过短，至少需要 1 秒")
        if duration > config.MAX_DURATION_SEC:
            raise HTTPException(
                status_code=413,
                detail=f"音频过长（{duration / 60:.1f} 分钟），上限 {config.MAX_DURATION_SEC // 60} 分钟，请先裁剪",
            )
    except Exception:
        jobs.registry.discard(job.id)
        raise
    finally:
        raw_path.unlink(missing_ok=True)

    job.size_bytes = size
    job.duration_sec = duration
    jobs.registry.submit(job)
    log.info(
        "job %s queued — %s, %.1fs, tier=%s, parallel=%d",
        job.id, job.filename, duration, tier, parallel,
    )
    return JobCreateResponse(
        id=job.id,
        tier=tier,
        filename=job.filename,
        duration_sec=round(duration, 2),
        size_bytes=size,
    )


@app.get("/api/jobs", response_model=list[JobStatusResponse])
def list_jobs() -> list[JobStatusResponse]:
    return [_status(job) for job in jobs.registry.list_jobs()]


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return _status(job)


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> None:
    if not jobs.registry.delete(job_id):
        raise HTTPException(status_code=404, detail="任务不存在或已过期")


@app.post("/api/jobs/{job_id}/cancel", response_model=JobStatusResponse)
def cancel_job(job_id: str) -> JobStatusResponse:
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    jobs.registry.cancel(job)
    return _status(job)


@app.post("/api/jobs/{job_id}/retry", response_model=JobCreateResponse, status_code=202)
def retry_job(job_id: str) -> JobCreateResponse:
    """Re-run a finished job from its stored 44.1 kHz input — no re-upload."""
    source = jobs.registry.get(job_id)
    if source is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if source.is_active:
        raise HTTPException(status_code=409, detail="任务正在进行中")
    if not source.input_wav.is_file():
        raise HTTPException(status_code=410, detail="原始音频已被清理，请重新上传")
    if jobs.registry.active_count() >= config.MAX_QUEUED_JOBS:
        raise HTTPException(
            status_code=429,
            detail=f"队列已满（上限 {config.MAX_QUEUED_JOBS} 个任务），请等当前任务完成",
        )

    job = jobs.registry.create(
        filename=source.filename,
        size_bytes=source.size_bytes,
        tier=source.tier,
        parallel=source.parallel,
    )
    try:
        shutil.copy2(source.input_wav, job.input_wav)
    except OSError as exc:
        jobs.registry.discard(job.id)
        raise HTTPException(status_code=500, detail=f"复制原始音频失败：{exc}") from exc

    job.duration_sec = source.duration_sec
    jobs.registry.submit(job)
    log.info("job %s retried from %s (tier=%s)", job.id, source.id, job.tier.key)
    return JobCreateResponse(
        id=job.id,
        tier=job.tier.key,
        filename=job.filename,
        duration_sec=round(job.duration_sec, 2),
        size_bytes=job.size_bytes,
    )


@app.get("/api/jobs/{job_id}/download")
async def download_all(job_id: str) -> FileResponse:
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if not job.stems:
        raise HTTPException(status_code=409, detail="分轨尚未完成")

    archive = job.dir / "package.zip"
    folder = _safe_stem(job.filename)

    def build() -> Path:
        # Everything sits under a folder named after the source track so two
        # archives can be extracted into the same directory without colliding.
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as bundle:
            for name in sorted(job.stems):
                bundle.write(
                    job.dir / "stems" / f"{name}.wav",
                    arcname=f"{folder}/{name}.wav",
                )
        return archive

    if not archive.exists():
        await run_in_threadpool(build)
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"{folder}-stems.zip",
    )
