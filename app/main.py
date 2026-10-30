"""HTTP 层：上传、任务列表、取消/重试、下载、静态托管。

所有推理都在子进程里（见 `app/orchestrator.py`），本文件不 import torch。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import config, media, orchestrator
from app.errors import JobRejected, PitchMorphError, friendly_error
from app.exports import DEFAULT_SMOOTH, EXPORT_FILES, normalize_smooth, render_export
from app.packaging import build_zip
from app.jobs import registry
from app.schemas import (
    HealthOut,
    JobOut,
    JobParams,
    ModelListOut,
    TierOut,
)

logger = logging.getLogger(__name__)

STEMS = ("drums", "bass", "other", "vocals")
CHUNK = 1 << 20


async def _reaper() -> None:
    while True:
        await asyncio.sleep(3600)
        try:
            removed = registry.reap()
            if removed:
                logger.info("清理了 %d 个过期任务", removed)
        except Exception:
            logger.exception("清理失败")


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    # 任务表在内存，重启就空；但产物和 meta.json 还在磁盘上。
    # 不恢复的话，「几天前分好轨、今天回来做音高」这种用法根本用不了。
    restored = registry.restore()
    if restored:
        logger.info("从磁盘恢复 %d 个历史任务", restored)
    task = asyncio.create_task(_reaper())
    logger.info("PitchMorph 就绪，产物目录 %s", config.JOBS_DIR)
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="PitchMorph", lifespan=lifespan)


# ---- 工具 ---------------------------------------------------------


def _source_stem(job_id: str) -> str:
    """下载文件名的主干。写在 meta.json 里，所以进程重启后仍然有源文件名。"""
    meta = config.job_dir(job_id) / "meta.json"
    if meta.exists():
        try:
            name = json.loads(meta.read_text(encoding="utf-8")).get("filename") or ""
        except (OSError, ValueError):
            name = ""
        if name:
            return Path(name).stem
    return job_id


def _flags(job_id: str) -> tuple[bool, bool, bool, list[str]]:
    root = config.job_dir(job_id)
    has_pitch = (root / "features" / "pitch.json").exists()
    has_stems = (root / "stems" / "vocals.wav").exists()
    has_exports = (root / "exports" / EXPORT_FILES["ustx"]).exists()
    # 「人声」档只有 vocals.wav，前端按真实存在的轨渲染，不要写死四条
    sources = [name for name in config.ALL_SOURCES if (root / "stems" / f"{name}.wav").exists()]
    if not sources and (root / "input.wav").exists():
        # 跳过分轨的任务：只有一条「原始音频」可播，别再假装有四轨
        sources = ["input"]
    return has_pitch, has_stems, has_exports, sources


def _job_smooth(job) -> int:
    """任务创建时定下的平滑窗口；老任务没有这个参数就回默认值。"""
    section = (job.params or {}).get("exports") or {}
    value = section.get("smooth")
    return normalize_smooth(value) if isinstance(value, int | float) else DEFAULT_SMOOTH


def _out(job) -> JobOut:
    has_pitch, has_stems, has_exports, sources = _flags(job.id)
    return JobOut.of(
        job,
        has_pitch=has_pitch,
        has_stems=has_stems,
        has_exports=has_exports,
        sources=sources,
    )


async def _save_upload(file: UploadFile, dst: Path) -> None:
    """流式落盘，边写边卡体积上限，不把整个文件读进内存。"""
    total = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dst.open("wb") as handle:
            while chunk := await file.read(CHUNK):
                total += len(chunk)
                if total > config.MAX_UPLOAD_BYTES:
                    raise JobRejected(
                        f"文件超过 {config.MAX_UPLOAD_BYTES // 2**20} MB 上限"
                    )
                handle.write(chunk)
    except BaseException:
        dst.unlink(missing_ok=True)
        raise


def _inspect(path: Path) -> tuple[float | None, bool]:
    """返回 ``(时长秒, 是否有音轨)``。libsndfile 能读的就本地读，容器才请 ffmpeg。"""
    if path.suffix.lower() in config.NATIVE_EXTS:
        try:
            info = sf.info(str(path))
            return (info.frames / info.samplerate if info.samplerate else None), True
        except Exception:
            return None, True  # 让解码层给出人话错误
    return media.probe_with_ffmpeg(path)


def _parse_params(raw: str, tier: str | None) -> dict:
    """表单里的 JSON 参数 → 校验过的字典。非法值在入口就拒绝，不流到 worker 里才炸。"""
    try:
        data = json.loads(raw or "{}")
    except ValueError as exc:
        raise JobRejected(f"参数不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise JobRejected("参数必须是一个 JSON 对象")
    if tier:  # 兼容只传 tier 的旧前端
        separate = data.setdefault("separate", {})
        if isinstance(separate, dict):
            separate.setdefault("tier", tier)
    try:
        return JobParams.model_validate(data).model_dump(exclude_none=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "参数"
        raise JobRejected(f"{where} 不合法：{first['msg']}") from exc


def _derive_meta(job, *, filename: str, scope: str, params: dict, parent_id: str | None) -> None:
    """把参数快照写进任务目录：产物永远能用自己的 meta.json 解释。"""
    payload = {
        "version": 2,
        "filename": filename,
        "scope": scope,
        "parent_id": parent_id,
        "tier": job.tier,
        "params": params,
    }
    (config.job_dir(job.id) / "meta.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# ---- 路由 ---------------------------------------------------------


@app.post("/api/jobs", response_model=JobOut, status_code=202)
async def create_job(
    file: UploadFile | None = File(None),
    scope: str = Form(config.DEFAULT_SCOPE),
    params: str = Form("{}"),
    parent_id: str | None = Form(None),
    tier: str | None = Form(None),
) -> JobOut:
    """创建任务。参数一次给全，跑完即定稿——改参数只能新建任务。

    `scope`: `separate` 只分离 / `analyze` 只分析音高与音符 / `both` 先分离再分析人声轨。
    分析任务的音频来自父任务的 `stems/vocals.wav`（`parent_id`），或本次上传的干声。
    """
    if scope not in config.SCOPES:
        raise HTTPException(400, f"未知范围 {scope}，可选 {'/'.join(config.SCOPES)}")

    parent = None
    if parent_id:
        parent = registry.get(parent_id)
        if parent is None:
            raise HTTPException(404, "父任务不存在")
        if scope != "analyze":
            raise HTTPException(400, "已有任务只能派生出「仅分析」任务")
        if file is not None:
            raise HTTPException(400, "派生分析任务不需要重新上传文件")
        if not (config.job_dir(parent.id) / "stems" / "vocals.wav").exists():
            raise HTTPException(400, "父任务没有 vocals 轨，无法派生分析任务")
        name = parent.filename
    else:
        if file is None:
            raise HTTPException(400, "缺少要处理的文件")
        name = Path(file.filename or "未命名").name
        if scope == "analyze" and not config.looks_dry(name):
            raise HTTPException(
                400,
                "仅分析音高时应当给人声干声：这个文件名看不出是干声（不含 vocal / 人声 / 干声 / dry / acap）。"
                "直接在混音上提音高，得到的只会是被伴奏干扰的假音符——"
                "要么选「分离 + 分析」，要么先分离出人声，再用它新建分析任务。",
            )

    try:
        parsed = _parse_params(params, tier)
    except JobRejected as exc:
        raise HTTPException(400, str(exc)) from exc

    job = registry.create(
        name,
        parsed["separate"]["tier"],
        scope,
        parsed,
        parent.id if parent is not None else None,
    )
    dst = config.job_dir(job.id) / f"source{Path(name).suffix.lower()}"

    try:
        if file is not None:
            await _save_upload(file, dst)
            duration, has_audio = _inspect(dst)
            if not has_audio:
                raise JobRejected("该文件里没有音频轨（视频与音频分开缓存时，请选音频那一段）")
            if duration is not None and duration > config.MAX_DURATION_SEC:
                raise JobRejected(
                    f"音频长 {duration / 60:.1f} 分钟，超过 {config.MAX_DURATION_SEC // 60} 分钟上限"
                )
        else:
            duration = parent.duration_sec if parent is not None else None
        registry.update(job.id, duration_sec=duration)
        _derive_meta(
            job,
            filename=name,
            scope=scope,
            params=parsed,
            parent_id=job.parent_id,
        )
    except (JobRejected, PitchMorphError) as exc:
        registry.remove(job.id)
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        registry.remove(job.id)
        raise
    except Exception as exc:
        registry.remove(job.id)
        raise HTTPException(400, friendly_error(exc)) from exc

    registry.submit(job, orchestrator.run_pipeline)
    fresh = registry.get(job.id) or job
    return _out(fresh)


@app.get("/api/jobs", response_model=list[JobOut])
def list_jobs(scope: str | None = Query(None)) -> list[JobOut]:
    if scope is not None and scope not in config.SCOPES:
        raise HTTPException(400, f"未知范围 {scope}，可选 {'/'.join(config.SCOPES)}")
    return [_out(job) for job in registry.snapshot(scope)]


@app.get("/api/models", response_model=ModelListOut)
def list_models() -> ModelListOut:
    return ModelListOut(
        default=config.DEFAULT_TIER,
        tiers=[TierOut(id=key, label=config.TIER_LABELS[key]) for key in config.TIERS],
    )


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(
        ok=True,
        ffmpeg=config.find_ffmpeg() is not None,
        max_upload_mb=config.MAX_UPLOAD_BYTES // 2**20,
        max_duration_sec=config.MAX_DURATION_SEC,
    )


@app.post("/api/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str) -> JobOut:
    registry.cancel(job_id)
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return _out(job)


@app.post("/api/jobs/{job_id}/analyze", response_model=JobOut, status_code=202)
def derive_analysis(job_id: str, body: JobParams | None = None) -> JobOut:
    """用新的分析参数、基于这个任务的 vocals 轨新建一个分析任务。

    这是「调参数」的唯一入口：原任务与它的产物一律不动，新任务跑完并排对比。
    """
    parent = registry.get(job_id)
    if parent is None:
        raise HTTPException(404, "任务不存在")
    if parent.active:
        raise HTTPException(409, "任务正在进行中")

    if not (config.job_dir(job_id) / "stems" / "vocals.wav").exists():
        raise HTTPException(
            400,
            "这个任务没有 vocals 轨：只有跑过分离的任务才能派生分析。"
            "也可以直接用「仅分析」上传人声干声。",
        )

    params = (body or JobParams()).model_dump(exclude_none=True)
    params["separate"] = {"tier": parent.tier}  # 不重新分离，只沿用它做展示
    job = registry.create(parent.filename, parent.tier, "analyze", params, job_id)
    try:
        _derive_meta(
            job,
            filename=parent.filename,
            scope="analyze",
            params=params,
            parent_id=job_id,
        )
        registry.update(job.id, duration_sec=parent.duration_sec)
    except Exception:
        registry.remove(job.id)
        raise

    registry.submit(job, orchestrator.run_pipeline)
    return _out(registry.get(job.id) or job)


@app.post("/api/jobs/{job_id}/retry", response_model=JobOut, status_code=202)
def retry_job(job_id: str) -> JobOut:
    try:
        job = registry.retry(job_id)
    except JobRejected as exc:
        raise HTTPException(400, str(exc)) from exc
    registry.submit(job, orchestrator.run_pipeline)
    return _out(registry.get(job.id) or job)


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> None:
    if not registry.remove(job_id):
        raise HTTPException(404, "任务不存在")


@app.get("/api/jobs/{job_id}/pitch")
def get_pitch(job_id: str) -> FileResponse:
    path = config.job_dir(job_id) / "features" / "pitch.json"
    if not path.exists():
        raise HTTPException(404, "这个任务还没有音高数据")
    return FileResponse(path, media_type="application/json")


@app.get("/api/jobs/{job_id}/onset")
def get_onset(job_id: str) -> FileResponse:
    path = config.job_dir(job_id) / "features" / "onset.json"
    if not path.exists():
        raise HTTPException(404, "这个任务还没有起音数据")
    return FileResponse(path, media_type="application/json")


@app.get("/api/jobs/{job_id}/stems/{stem}")
def download_stem(job_id: str, stem: str) -> FileResponse:
    if stem not in STEMS:
        raise HTTPException(404, "没有这条轨")
    path = config.job_dir(job_id) / "stems" / f"{stem}.wav"
    if not path.exists():
        raise HTTPException(404, "这条轨还没生成")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{_source_stem(job_id)}_{stem}.wav",
    )


@app.get("/api/jobs/{job_id}/package/{kind}")
def download_package(job_id: str, kind: str, smooth: int | None = None) -> Response:
    """音轨与工程分开打包：它们用途不同，混在一起用户还得自己解压挑文件。"""
    root = config.job_dir(job_id)
    stem = _source_stem(job_id)

    if kind == "stems":
        sources = [name for name in config.ALL_SOURCES if (root / "stems" / f"{name}.wav").exists()]
        entries = [(root / "stems" / f"{name}.wav", f"{stem}/{name}.wav") for name in sources]
        target = build_zip(root, "package_stems.zip", entries)
        if target is None:
            raise HTTPException(404, "还没有可打包的音轨")
        return FileResponse(
            target,
            media_type="application/zip",
            filename=f"{stem}_stems.zip",
        )

    if kind == "project":
        if not (root / "features" / "pitch.json").exists():
            raise HTTPException(404, "还没有可打包的工程文件（需要先完成音高提取）")
        arcs = {
            "notes_midi": f"{stem}/notes.mid",
            "bend_midi": f"{stem}/bend.mid",
            "ustx": f"{stem}/vocal.ustx",
        }
        job = registry.get(job_id)
        window = smooth if smooth is not None else (_job_smooth(job) if job else DEFAULT_SMOOTH)
        window = normalize_smooth(window)
        # 工程文件很小，直接内存打包，才能带上平滑度参数
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
            for key, arc in arcs.items():
                archive.writestr(arc, render_export(root, key, smooth=window))
        name = f"{stem}_project.zip"
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
        )

    raise HTTPException(404, "没有这种打包")


@app.get("/api/jobs/{job_id}/exports/{kind}")
def download_export(job_id: str, kind: str, smooth: int | None = None) -> Response:
    """按平滑窗口即时生成，不读 `exports/` 里的落盘版本。

    窗口缺省取任务创建时定的值（`JobParams.exports.smooth`）：导出是那一批产物的定义，
    想换平滑度就该新建任务；带上 `smooth` 参数则临时覆盖，只为快速比对。
    """
    if kind not in EXPORT_FILES:
        raise HTTPException(404, "没有这种导出")
    root = config.job_dir(job_id)
    if not (root / "features" / "pitch.json").exists():
        raise HTTPException(404, "这个导出还没生成（需要先完成音高提取）")

    job = registry.get(job_id)
    window = smooth if smooth is not None else (_job_smooth(job) if job else DEFAULT_SMOOTH)
    data = render_export(root, kind, smooth=normalize_smooth(window))
    suffix = {"notes_midi": "_notes.mid", "bend_midi": "_bend.mid"}.get(kind, ".ustx")
    media_type = "audio/midi" if suffix.endswith(".mid") else "application/x-yaml"
    name = f"{_source_stem(job_id)}{suffix}"
    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


# 播放器拖动进度依赖 Range 请求，StaticFiles 走 FileResponse 天然支持
app.mount("/media", StaticFiles(directory=config.JOBS_DIR, check_dir=False), name="media")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR, check_dir=False), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    page = config.STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(503, "前端还没构建：cd frontend && npm install && npm run build")
    return FileResponse(page)
