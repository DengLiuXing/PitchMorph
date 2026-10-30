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
# 产物保留 7 天：常见用法是「几天前分离开好轨，今天回来做音高」，
# 24 小时会把这种用法直接掐死。磁盘靠 TTL 清理兜底。
TTL_HOURS: Final = 7 * 24
QUEUE_LIMIT: Final = 3

# 任务范围：separate = 只分离；analyze = 只分析音高/音符；both = 先分离再分析人声轨。
# 参数在创建时一次性给全，跑完即定稿；改参数 = 新建任务，不覆盖已有产物。
SCOPES: Final[tuple[str, ...]] = ("separate", "analyze", "both")
DEFAULT_SCOPE: Final = "both"

# 旧 meta.json 里的 mode 映射（只用于恢复历史任务）
_LEGACY_MODE_TO_SCOPE: Final[dict[str, str]] = {"full": "both", "pitch": "analyze"}

# 「仅分析」的入参必须是人声干声。文件名里没有任何这类线索时，分轨没做就直接提音高，
# 结果一定是一堆被伴奏干扰的假音符 —— 所以宁可在入队前拒绝并说清怎么做才有意义。
DRY_MARKERS: Final[tuple[str, ...]] = ("vocal", "人声", "干声", "dry", "acap")


def scope_from_meta(meta: dict) -> str:
    """meta.json → scope。新记录直接读，旧记录按 `mode` 推断。"""
    scope = meta.get("scope")
    if scope in SCOPES:
        return str(scope)
    return _LEGACY_MODE_TO_SCOPE.get(str(meta.get("mode") or ""), DEFAULT_SCOPE)


def looks_dry(filename: str) -> bool:
    lowered = filename.lower()
    return any(marker in lowered for marker in DRY_MARKERS)

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


# GAME 权重：不入库（见 .gitignore 与 app/worker/game/VENDOR.md），解包成
# `app/worker/game/weights/<体积>/model.pt`。找不到就跳过音符阶段、导出退回启发式。
GAME_WEIGHTS_DIR: Final = ROOT / "app" / "worker" / "game" / "weights"
GAME_DEFAULT_MODEL: Final = "GAME-1.0-small"


# GAME 的体积名（release 里的 GAME-1.0-<size>.zip）。体积只影响精度与耗时，与分轨档位无关。
GAME_SIZES: Final[tuple[str, ...]] = ("small", "medium", "large")


def game_model_path(size: str | None = None) -> Path | None:
    """按体积名找权重；不指定就用默认体积，再退化到任意一个已解包的。"""
    if size:
        if size not in GAME_SIZES:
            raise ValueError(f"未知 GAME 体积 {size!r}，可选 {list(GAME_SIZES)}")
        candidate = GAME_WEIGHTS_DIR / f"GAME-1.0-{size}" / "model.pt"
        return candidate if candidate.exists() else None
    preferred = GAME_WEIGHTS_DIR / GAME_DEFAULT_MODEL / "model.pt"
    if preferred.exists():
        return preferred
    found = sorted(GAME_WEIGHTS_DIR.glob("*/model.pt"))
    return found[0] if found else None


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
