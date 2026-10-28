"""API payload models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class JobStage(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    SEPARATING = "separating"
    DONE = "done"
    CANCELED = "canceled"
    ERROR = "error"


class JobCreateResponse(BaseModel):
    id: str
    tier: str
    filename: str
    duration_sec: float
    size_bytes: int


class JobStatusResponse(BaseModel):
    id: str
    stage: JobStage
    progress: float
    stem: str | None
    done: int
    total: int
    parallel: int = 1
    elapsed_sec: float
    wait_sec: float
    duration_sec: float
    stems: dict[str, str]
    error: str | None
    filename: str
    size_bytes: int
    tier: str
    cancel_requested: bool
    created_at: float


class ModelInfo(BaseModel):
    key: str
    label: str
    model: str
    stems: list[str]
    download_mb: int
    eta: str
    detail: str
    cached: bool


class HealthResponse(BaseModel):
    ok: bool
    python: str
    onnxruntime: str
    available_providers: list[str]
    active_providers: list[str]
    #: True only when a real CUDA session could be built, not merely when the
    #: wheel advertises CUDAExecutionProvider.
    gpu_ready: bool = False
    #: Why CUDA was not used, ready to show in the UI. None when it was.
    gpu_reason: str | None = None
    ffmpeg: str | None
    ffmpeg_version: str | None
    ffmpeg_source: str | None
    model_dir: str
    supported_exts: list[str]
    max_upload_mb: int
    max_duration_sec: int
    default_parallel: int = 1
    max_parallel: int = 4
