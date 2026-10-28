"""Static configuration: paths, limits, model tiers, ffmpeg discovery."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent

DATA_DIR = Path(os.environ.get("PITCHMORPH_DATA_DIR", BASE_DIR / "data")).resolve()
JOBS_DIR = DATA_DIR / "jobs"
MODEL_DIR = DATA_DIR / "models"
STATIC_DIR = APP_DIR / "static"

#: The ONNX graphs are baked for 44.1 kHz stereo; we normalize every input to it
#: so the model's native rate and our output rate are the same number.
SAMPLE_RATE = 44100

#: Generous because a 1080p music video is mostly video payload we throw away;
#: only the extracted audio (12 min max) is kept.
MAX_UPLOAD_BYTES = 300 * 1024 * 1024
MAX_DURATION_SEC = 12 * 60
MAX_QUEUED_JOBS = 3
JOB_TTL_SEC = 24 * 3600

#: Sessions are released after every batch rather than kept resident: one
#: specialist holds ~3 GB of VRAM once its arena has grown, so four of them
#: exceed an 8 GB laptop card. Rebuilding costs ~4 s on CUDA — cheaper than an
#: out-of-memory failure. Numbers in ``docs/perf-audit.md``.

#: Our output is WAV, so the fp16 weights variant is strictly better: half the
#: download, identical runtime precision (the graph still computes in fp32).
PRECISION = "fp16weights"

#: Every fp16 ONNX file in the STemSplit family is ~166 MB. Used only to render
#: a progress percentage while downloading — never for correctness.
WEIGHT_FILE_MB = 166

#: Decodable by libsndfile / soundfile without any external binary.
NATIVE_EXTS = frozenset({".wav", ".flac", ".mp3", ".ogg", ".aif", ".aiff", ".aifc"})

#: Containers we know ffmpeg can demux — video files, fragmented MP4 segments
#: (``.m4s`` from browser caches), and codecs libsndfile does not read.
CONTAINER_EXTS = frozenset(
    {
        ".m4a", ".m4s", ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".ts",
        ".flv", ".3gp", ".wmv", ".wma", ".aac", ".opus", ".amr", ".ape", ".wv",
    },
)


@dataclass(frozen=True)
class ModelTier:
    key: str
    label: str
    model: str
    stems: tuple[str, ...]
    download_mb: int
    eta: str
    detail: str


TIERS: dict[str, ModelTier] = {
    "quality": ModelTier(
        key="quality",
        label="四轨 · 质量优先",
        model="htdemucs_ft",
        stems=("drums", "bass", "other", "vocals"),
        download_mb=660,
        eta="约 2–5 分钟",
        detail="htdemucs_ft 四专家集成，每轨一个独立模型",
    ),
    "vocals": ModelTier(
        key="vocals",
        label="仅人声",
        model="htdemucs_ft_vocals",
        stems=("vocals",),
        download_mb=166,
        eta="约 1–2 分钟",
        detail="只跑人声专家，算力约为四轨档的四分之一",
    ),
    "fast": ModelTier(
        key="fast",
        label="四轨 · 快速",
        model="htdemucs",
        stems=("drums", "bass", "other", "vocals"),
        download_mb=166,
        eta="约 1–3 分钟",
        detail="单文件四轨模型，比集成档快，音质略低",
    ),
}

DEFAULT_TIER = "vocals"

#: Models that emit every stem from a single ONNX file (one inference call).
SINGLE_FILE_MODELS = frozenset({"htdemucs", "htdemucs_6s"})

#: Cap on concurrently loaded specialist graphs inside one job. A single session
#: already saturates the physical cores (docs/perf-audit.md), so going wider
#: mostly buys thread oversubscription and memory.
MAX_PARALLEL = 4


@lru_cache(maxsize=1)
def _bundled_ffmpeg() -> str | None:
    """The ffmpeg shipped with the imageio-ffmpeg wheel (inside .venv)."""
    try:
        import imageio_ffmpeg
    except Exception:  # not installed — fall back to "no ffmpeg"
        return None
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    return exe if exe and Path(exe).is_file() else None


def resolve_ffmpeg() -> tuple[str | None, str | None]:
    """``(path, source)`` where source is env / bundled / path / None.

    The bundled build wins over PATH on purpose: decoding behaviour stays the
    same on every machine instead of following whatever version the user
    happens to have installed. Set ``PITCHMORPH_FFMPEG`` to force your own.
    """
    override = os.environ.get("PITCHMORPH_FFMPEG")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return str(candidate), "env"

    bundled = _bundled_ffmpeg()
    if bundled:
        return bundled, "bundled"

    found = shutil.which("ffmpeg")
    if found:
        return found, "path"
    return None, None


def find_ffmpeg() -> str | None:
    """Executable used for containers libsndfile cannot read."""
    return resolve_ffmpeg()[0]


def ffmpeg_source() -> str | None:
    return resolve_ffmpeg()[1]


def ffmpeg_version(ffmpeg: str | None = None) -> str | None:
    """First line of ``ffmpeg -version``. Display only — never validated."""
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError:
        return None
    line = (out.stdout or "").splitlines()
    return line[0].strip() if line else None


def supported_exts() -> frozenset[str]:
    """Extensions to advertise in the file picker and the drop-zone hint."""
    exts = set(NATIVE_EXTS)
    if find_ffmpeg():
        exts |= CONTAINER_EXTS
    return frozenset(exts)


def can_accept(suffix: str) -> bool:
    """Whether an upload with ``suffix`` is worth attempting.

    With ffmpeg present we do not gate on a whitelist at all: anything it can
    demux gets a shot, and a file without an audio stream fails with a readable
    message a moment later. Without ffmpeg only the libsndfile set works.
    """
    if suffix in NATIVE_EXTS:
        return True
    return find_ffmpeg() is not None


def providers() -> list[str]:
    """ORT execution providers for this process.

    Delegates to :mod:`app.runtime`, which probes CUDA for real: a GPU wheel
    can be installed on a machine with no CUDA DLLs at all, so
    ``get_available_providers()`` alone would pick a provider that cannot
    actually build a session. ``PITCHMORPH_PROVIDERS`` still wins over
    everything, so CPU can be forced even when CUDA works.
    """
    from . import runtime

    return runtime.providers()


def default_parallel() -> int:
    """Specialists running at once: ``PITCHMORPH_PARALLEL``, default 1.

    Read lazily (not at import) so the value can follow the environment between
    calls, matching :func:`providers`. Out-of-range or garbage falls back to the
    safe default instead of failing a job.

    Default is 1 on purpose: measured on i7-14650HX, two 24-thread sessions
    oversubscribe the 24 hardware threads enough that the bag runs *slower*
    than serial (85 s → 143 s on a 60 s clip) while peaking near 10 GB RSS.
    See docs/perf-audit.md. The knob stays for machines with more cores.
    """
    raw = os.environ.get("PITCHMORPH_PARALLEL", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, min(value, MAX_PARALLEL))
