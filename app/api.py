"""REST + SSE 端点。所有路径前缀 /api。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from urllib.parse import quote

from . import export_formats, notes as notes_module, registry, runtime
from .config import DATA_DIR, MAX_UPLOAD_MB
from .tasks import manager

logger = logging.getLogger("pitchmorph.api")
router = APIRouter(prefix="/api")

ALLOWED_SUFFIX = {".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aac", ".mp4", ".opus", ".aiff", ".aif"}

STEM_LABEL = {"vocals": "人声", "drums": "鼓", "bass": "贝斯", "other": "其他"}


def source_base(meta: dict) -> str:
    """源文件名去掉扩展名并清洗，作为导出文件前缀。"""
    raw = Path(meta.get("filename") or "track").stem
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", raw).strip("_")
    return cleaned or "track"


def stem_filename(meta: dict, stem: str) -> str:
    """(源文件名)_(音轨类型).wav"""
    return f"{source_base(meta)}_{stem}.wav"


def _require(job_id: str) -> dict:
    meta = manager.meta(job_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return meta


@router.get("/status")
def status() -> dict:
    info = runtime.status()
    return {
        "ep": info["ep"],
        "providers": info["providers"],
        "version": info["version"],
        "fallback_reason": info["fallback_reason"],
    }


@router.get("/jobs")
def list_jobs() -> list[dict]:
    return manager.all_meta()


@router.post("/jobs")
async def create_job(file: UploadFile = File(...), mode: str = Form("vocals")) -> dict:
    if mode not in ("vocals", "fast", "quality", "four"):
        raise HTTPException(status_code=400, detail="mode 只能是 vocals / fast / quality")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(status_code=400, detail=f"不支持的格式 {suffix or '(无扩展名)'}")

    limit = MAX_UPLOAD_MB * 1024 * 1024
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=DATA_DIR.parent) as handle:
        size = 0
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                handle.close()
                Path(handle.name).unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_MB} MB 上限")
            handle.write(chunk)
        temp_path = Path(handle.name)

    job_id = manager.create(temp_path, file.filename or "audio", "quality" if mode == "four" else mode)
    manager.submit_separate(job_id)
    logger.info("新建任务 id=%s mode=%s size=%d", job_id, mode, size)
    return {"id": job_id}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    meta = _require(job_id)
    return {**meta, "queue_busy": False}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    _require(job_id)
    manager.cancel(job_id)
    return {"ok": True}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    _require(job_id)
    manager.delete(job_id)
    return {"ok": True}


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    _require(job_id)

    async def stream():
        last = ""
        while True:
            if await request.is_disconnected():
                break
            meta = manager.meta(job_id) or {}
            payload = {k: meta.get(k) for k in ("state", "stage", "message", "fraction", "extra", "failure", "stems", "pitch")}
            text = json.dumps(payload, ensure_ascii=False)
            if text != last:
                last = text
                yield f"data: {text}\n\n"
            if meta.get("state") in ("ready", "failed", "cancelled") and meta.get("stage") != "pitching":
                yield f"data: {text}\n\n"
                break
            await asyncio.sleep(0.3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _safe_file(job_id: str, name: str) -> Path:
    directory = manager.job_dir(job_id)
    path = (directory / f"{name}.wav").resolve()
    if not path.exists() or directory.resolve() not in path.parents:
        raise HTTPException(status_code=404, detail="音轨不存在")
    return path


@router.get("/jobs/{job_id}/audio/{name}")
def get_audio(job_id: str, name: str, request: Request):
    path = _safe_file(job_id, name)
    meta = _require(job_id)
    download_name = stem_filename(meta, name) if name != "source" else f"{source_base(meta)}_source.wav"
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type="audio/wav", filename=download_name)

    try:
        unit, _, span = range_header.partition("=")
        start_s, _, end_s = span.partition("-")
        start = int(start_s or 0)
        end = int(end_s) if end_s else file_size - 1
    except ValueError:
        raise HTTPException(status_code=400, detail="Range 头格式错误")
    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))

    def iter_chunk():
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                block = handle.read(min(1 << 16, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block

    return StreamingResponse(
        iter_chunk(),
        status_code=206,
        media_type="audio/wav",
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(download_name)}",
        },
    )


@router.get("/jobs/{job_id}/export")
def export_stems(job_id: str):
    """把已生成的全部音轨打包成 zip，文件名统一为 (源文件名)_(音轨类型).wav。"""
    meta = _require(job_id)
    stems = meta.get("stems") or []
    if not stems:
        raise HTTPException(status_code=409, detail="还没有可导出的音轨")

    directory = manager.job_dir(job_id)
    archive = directory / "export.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        for entry in stems:
            source = directory / f"{entry['name']}.wav"
            if source.exists():
                bundle.write(source, arcname=stem_filename(meta, entry["name"]))
    logger.info("打包导出 id=%s files=%d", job_id, len(stems))
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"{source_base(meta)}_stems.zip",
    )


@router.get("/jobs/{job_id}/peaks/{name}")
def get_peaks(job_id: str, name: str) -> JSONResponse:
    directory = manager.job_dir(job_id)
    path = directory / f"{name}.peaks.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="波形未就绪")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.post("/jobs/{job_id}/pitch/{stem}")
def start_pitch(job_id: str, stem: str) -> dict:
    meta = _require(job_id)
    available = {entry["name"] for entry in meta.get("stems", [])}
    if stem not in available:
        raise HTTPException(status_code=404, detail="该音轨不存在")
    state = meta.get("pitch", {}).get(stem, {}).get("state")
    if state in ("queued", "running"):
        return {"ok": True, "state": state}
    manager.submit_pitch(job_id, stem)
    return {"ok": True, "state": "queued"}


@router.get("/jobs/{job_id}/pitch/{stem}")
def get_pitch(job_id: str, stem: str):
    directory = manager.job_dir(job_id)
    path = directory / f"pitch_{stem}.json"
    if not path.exists():
        meta = _require(job_id)
        state = meta.get("pitch", {}).get(stem, {}).get("state", "idle")
        return JSONResponse({"state": state})
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


class NotesParams(BaseModel):
    """GAME 推理参数 + 导出曲速。语义与 OpenUtau 的转录界面一致。"""

    tempo: float = Field(default=120.0, gt=0, le=400)
    sampling_steps: int = Field(default=8, ge=1, le=16)
    boundary_threshold: float = Field(default=0.2, gt=0, le=1)
    boundary_radius: int = Field(default=2, ge=1, le=20)
    score_threshold: float = Field(default=0.2, gt=0, le=1)
    batch_size: int = Field(default=1, ge=1, le=16)
    max_batch_duration: float = Field(default=60.0, ge=0, le=600)
    language: str | None = None


def _available_languages() -> list[str]:
    try:
        location = registry.ensure_bundle(registry.GAME_BUNDLE_KEY)
        payload = json.loads((location / "config.json").read_text(encoding="utf-8"))
        return sorted(payload.get("languages") or {})
    except Exception:
        return []


@router.get("/languages")
def languages() -> dict:
    return {"languages": _available_languages()}


@router.post("/jobs/{job_id}/refine/{stem}")
def start_refine(job_id: str, stem: str) -> dict:
    meta = _require(job_id)
    available = {entry["name"] for entry in meta.get("stems", [])}
    if stem not in available:
        raise HTTPException(status_code=404, detail="该音轨不存在")
    state = (meta.get("refine") or {}).get(stem, {}).get("state")
    if state in ("queued", "running"):
        return {"ok": True, "state": state}
    manager.submit_refine(job_id, stem)
    return {"ok": True, "state": "queued"}


@router.post("/jobs/{job_id}/notes/{stem}")
def start_notes(job_id: str, stem: str, params: NotesParams) -> dict:
    meta = _require(job_id)
    available = {entry["name"] for entry in meta.get("stems", [])}
    if stem not in available:
        raise HTTPException(status_code=404, detail="该音轨不存在")
    if params.language:
        supported = _available_languages()
        if supported and params.language not in supported:
            raise HTTPException(
                status_code=400,
                detail=f"该权重不支持语言 {params.language!r}，可选 {supported}",
            )
    state = (meta.get("notes") or {}).get(stem, {}).get("state")
    if state in ("queued", "running"):
        return {"ok": True, "state": state}
    manager.submit_notes(job_id, stem, params.model_dump())
    return {"ok": True, "state": "queued"}


@router.get("/jobs/{job_id}/notes/{stem}")
def get_notes(job_id: str, stem: str):
    directory = manager.job_dir(job_id)
    path = directory / f"notes_{stem}.json"
    if not path.exists():
        meta = _require(job_id)
        state = (meta.get("notes") or {}).get(stem, {}).get("state", "idle")
        return JSONResponse({"state": state})
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/jobs/{job_id}/export/notes/{stem}.{fmt}")
def export_notes(job_id: str, stem: str, fmt: str):
    meta = _require(job_id)
    directory = manager.job_dir(job_id)
    notes_path = directory / f"notes_{stem}.json"
    if not notes_path.exists():
        raise HTTPException(status_code=404, detail="还没有音符数据")
    pitch_path = directory / f"pitch_{stem}.json"
    if not pitch_path.exists():
        raise HTTPException(status_code=404, detail="还没有音高曲线")
    payload = json.loads(notes_path.read_text(encoding="utf-8"))
    curve = json.loads(pitch_path.read_text(encoding="utf-8"))
    segments = payload.get("segments") or []
    cents = notes_module.to_cents(curve["f0"], curve["voiced"])
    hop_sec = float(payload.get("hop_ms", 10)) / 1000.0
    tempo = float(payload.get("tempo", 120.0))
    filename = f"{source_base(meta)}_{stem}.{fmt}"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    if fmt == "ustx":
        text = export_formats.to_ustx(cents, segments, hop_sec=hop_sec, bpm=tempo)
        return Response(
            text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )
    if fmt == "mid":
        data = export_formats.to_midi(cents, segments, hop_sec=hop_sec, bpm=tempo)
        return Response(
            data,
            media_type="audio/midi",
            headers={"Content-Disposition": disposition},
        )
    raise HTTPException(status_code=404, detail="只支持 ustx 与 mid")
