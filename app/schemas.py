"""HTTP 出参形状。改这里时同步改 `frontend/src/api/types.ts`。"""

from __future__ import annotations

from pydantic import BaseModel

from app.jobs import Job


class JobOut(BaseModel):
    id: str
    filename: str
    tier: str
    mode: str
    stage: str
    progress: int
    detail: str
    error: str | None = None
    duration_sec: float | None = None
    elapsed_sec: float
    created_at: float
    has_pitch: bool
    has_stems: bool
    has_exports: bool
    sources: list[str]

    @classmethod
    def of(
        cls,
        job: Job,
        *,
        has_pitch: bool = False,
        has_stems: bool = False,
        has_exports: bool = False,
        sources: list[str] | None = None,
    ) -> "JobOut":
        return cls(
            id=job.id,
            filename=job.filename,
            tier=job.tier,
            mode=job.mode,
            stage=job.stage.value,
            progress=job.progress,
            detail=job.detail,
            error=job.error,
            duration_sec=job.duration_sec,
            elapsed_sec=job.elapsed_sec,
            created_at=job.created_at,
            has_pitch=has_pitch,
            has_stems=has_stems,
            has_exports=has_exports,
            sources=sources or [],
        )


class TierOut(BaseModel):
    id: str
    label: str


class ModelListOut(BaseModel):
    default: str
    tiers: list[TierOut]


class HealthOut(BaseModel):
    ok: bool
    ffmpeg: bool
    max_upload_mb: int
    max_duration_sec: int
