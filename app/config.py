"""全局路径与常量。所有魔法数字集中在这里。"""
from __future__ import annotations

import logging
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "jobs"
MODELS_DIR = BASE_DIR / "models"
WEB_DIST = BASE_DIR / "web" / "dist"

SAMPLE_RATE = 44100
# demucs htdemucs 系列的固定输入段长（7.8 s）
SEGMENT = 343980
# 分块重叠比例，重叠区用升余弦窗归一化叠加
OVERLAP_RATIO = 0.25
HOP = SEGMENT - int(SEGMENT * OVERLAP_RATIO)

MAX_UPLOAD_MB = 200
MAX_DURATION_S = 15 * 60

# FCPE：10 ms 一帧
PITCH_HOP_MS = 10
PITCH_SAMPLE_RATE = 16000
PITCH_THRESHOLD = 0.33

STEMS_FOUR = ("drums", "bass", "other", "vocals")
STEM_ORDER_OUT = ("vocals", "drums", "bass", "other")

# 分离档位
MODE_VOCALS = "vocals"  # 仅人声：ft 人声专家
MODE_FAST = "fast"  # 快速四轨：单文件 htdemucs，一次前向出 4 轨
MODE_QUALITY = "quality"  # 质量四轨：4 个 ft 专家串行
MODES = (MODE_VOCALS, MODE_FAST, MODE_QUALITY)

# GAME 推理参数（语义与 OpenUtau 的转录界面一致）
NOTES_SAMPLING_STEPS = (1, 2, 4, 8, 16)
NOTES_DEFAULT_SAMPLING_STEPS = 8
NOTES_BOUNDARY_THRESHOLD = 0.2
NOTES_BOUNDARY_RADIUS = 2
NOTES_SCORE_THRESHOLD = 0.2
NOTES_BATCH_SIZE = 1
NOTES_MAX_BATCH_DURATION = 60.0
NOTES_DEFAULT_TEMPO = 120.0

# 人声提纯产物：lead = 人声主体（含主旋律与和声），backing = 被剥离的伴奏残差
REFINE_ROLES = ("lead", "backing")

JOB_STATES = ("queued", "decoding", "separating", "separated", "pitching", "ready", "failed", "cancelled")

# 分轨完成后自动为每条轨排音高任务；关掉则只按前端请求算
AUTO_PITCH = True


def requested_ep() -> str:
    """环境变量 PITCHMORPH_EP，只接受 cuda / cpu。"""
    value = os.environ.get("PITCHMORPH_EP", "cuda").strip().lower()
    return value if value in ("cuda", "cpu") else "cuda"


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("onnxruntime").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


logger = logging.getLogger("pitchmorph")
