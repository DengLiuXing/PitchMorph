"""常量、路径、外部依赖探测。凡是「只有一个决策」的东西都放这里。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parent.parent
DATA_DIR: Final = ROOT / "data"
JOBS_DIR: Final = DATA_DIR / "jobs"
STATIC_DIR: Final = ROOT / "app" / "static"

SAMPLE_RATE: Final = 44100
MAX_UPLOAD_BYTES: Final = 300 * 1024 * 1024
MAX_DURATION_SEC: Final = 12 * 60
TTL_HOURS: Final = 24
QUEUE_LIMIT: Final = 3

ALL_SOURCES: Final[tuple[str, ...]] = ("drums", "bass", "other", "vocals")

# 档位 → demucs 模型名
TIERS: Final[dict[str, str]] = {
    "vocals": "htdemucs_ft",
    "fast": "htdemucs",
    "quality": "htdemucs_ft",
}
TIER_LABELS: Final[dict[str, str]] = {
    "vocals": "人声",
    "fast": "四轨 · 快",
    "quality": "四轨 · 质量",
}

# 写盘哪些轨。跑哪些**专家**不用在这里写：htdemucs_ft 的 weights 是对角阵，
# 每个专家只负责一个音源，worker 按权重自动筛掉不相干的专家。
# 「人声」档因此只跑 1/4 的专家，而不是 1/4 的通道。
TIER_SOURCES: Final[dict[str, tuple[str, ...]]] = {
    "vocals": ("vocals",),
    "fast": ALL_SOURCES,
    "quality": ALL_SOURCES,
}

# 默认人声档：抽人声是做音高分析的前提，也是最高频的用法
DEFAULT_TIER: Final = "vocals"

# libsndfile 能直接读的扩展名；其余一律走 ffmpeg 的 `-map 0:a:0`
NATIVE_EXTS: Final = frozenset(
    {".wav", ".flac", ".mp3", ".ogg", ".oga", ".aiff", ".aif", ".opus"}
)


def tier_model(tier: str) -> str:
    try:
        return TIERS[tier]
    except KeyError:
        raise ValueError(f"未知档位 {tier!r}，可选 {sorted(TIERS)}") from None


def tier_sources(tier: str) -> tuple[str, ...]:
    return TIER_SOURCES[tier]


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def find_ffmpeg() -> str | None:
    """环境变量 → imageio-ffmpeg 内置二进制 → PATH。内置优先是刻意的。

    内置版本保证任何机器上解码行为一致，不被用户装的版本带偏。
    """
    override = os.environ.get("PITCHMORPH_FFMPEG")
    if override and Path(override).exists():
        return override

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    return shutil.which("ffmpeg")
