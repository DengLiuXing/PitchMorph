"""HTTP 出入参形状。改这里时同步改 `frontend/src/api/types.ts`。"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from app import config
from app.exports import DEFAULT_SMOOTH, normalize_smooth
from app.jobs import Job


class SeparateParams(BaseModel):
    """UVR5 式的分离参数。目前只暴露档位；shifts / overlap 要等实测收益再放出来。"""

    tier: str = config.DEFAULT_TIER

    @field_validator("tier")
    @classmethod
    def _known_tier(cls, value: str) -> str:
        if value not in config.TIERS:
            raise ValueError(f"未知档位 {value!r}，可选 {sorted(config.TIERS)}")
        return value


class JobOut(BaseModel):
    id: str
    filename: str
    tier: str
    scope: str
    parent_id: str | None = None
    params: dict = {}
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
            scope=job.scope,
            parent_id=job.parent_id,
            params=job.params,
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


class PitchParams(BaseModel):
    """重跑音高时的可调项，缺省即用 worker 的默认值。"""

    f0_min: float | None = None
    f0_max: float | None = None
    threshold: float | None = None
    gap_ms: int | None = None
    # 起音切分开关。None = 用默认（开），false = 复现加大音前的旧结果
    onsets: bool | None = None


class NotesParams(BaseModel):
    """重算音符边界时的可调项，缺省即用 worker 的默认值。

    这些是 GAME 的**后处理**参数（`docs/05-game.md` M3）：模型输出不变，只影响解码。
    """

    model: str | None = None
    language: str | None = None
    nsteps: int | None = None
    seg_threshold: float | None = None
    seg_radius: float | None = None
    est_threshold: float | None = None


class ExportParams(BaseModel):
    """导出（派生）参数。`smooth` 是中值滤波窗口，单位帧，必须奇数：越大越平，颤音也会被抹掉。"""

    smooth: int = DEFAULT_SMOOTH

    @field_validator("smooth")
    @classmethod
    def _odd_window(cls, value: int) -> int:
        # 与其报错，不如按导出层已有的规则归一：偶数取下一个奇数，超界夹回 1..15
        return normalize_smooth(value)


class JobParams(BaseModel):
    """创建任务时的全部参数，按阶段分组；缺省即用各阶段默认值。

    任务一旦开跑，这些值就是这一批产物的定义——改参数只能新建任务，
    这样每个任务目录里的 `meta.json` 永远能解释它自己的产物。
    """

    separate: SeparateParams = SeparateParams()
    pitch: PitchParams = PitchParams()
    notes: NotesParams = NotesParams()
    exports: ExportParams = ExportParams()


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
