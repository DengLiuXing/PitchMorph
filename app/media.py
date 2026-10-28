"""Decoding any input into the 44.1 kHz stereo WAV the model expects.

Two paths, never mixed: ``soundfile``/libsndfile first (wav/flac/mp3/ogg/aiff),
``ffmpeg`` only for containers it cannot open (mp4/m4s/mkv/…). ffmpeg ships with
the project via ``imageio-ffmpeg``, so "no ffmpeg" is a broken-install case, not
a normal one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from . import config
from .errors import UnsupportedFormat

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_AUDIO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Audio:")


def probe_with_ffmpeg(src: Path) -> tuple[float | None, bool]:
    """Demux-only probe returning ``(duration_sec, has_audio_stream)``.

    ``ffmpeg -i`` with no output prints the container summary to stderr and
    exits non-zero, which is exactly what we want: a few milliseconds of work
    instead of transcoding a 40-minute video before rejecting it.
    """
    ffmpeg = config.find_ffmpeg()
    if not ffmpeg:
        return None, True  # cannot probe; let the decoder have its say

    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(src)],
        capture_output=True,
        check=False,
    )
    stderr = (proc.stderr or b"").decode("utf-8", "replace")

    duration: float | None = None
    match = _DURATION_RE.search(stderr)
    if match:
        duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    return duration, bool(_AUDIO_STREAM_RE.search(stderr))


def _ffmpeg_error(exc: subprocess.CalledProcessError) -> str:
    """Turn ffmpeg's stderr into something a user can act on."""
    stderr = (exc.stderr or b"").decode("utf-8", "replace")
    lowered = stderr.lower()
    if "matches no streams" in lowered or "does not contain any stream" in lowered:
        return "该文件里没有音频轨（视频与音频分开缓存时，请选音频那一段）"
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return f"ffmpeg 无法解码该文件：{lines[-1] if lines else 'unknown error'}"


def decode_to_wav(src: Path, dst: Path) -> float:
    """Decode ``src`` into a 44.1 kHz stereo PCM-16 WAV at ``dst``.

    Returns the duration in seconds. Raises :class:`UnsupportedFormat` when
    neither libsndfile nor ffmpeg can read the file.
    """
    audio: np.ndarray | None = None
    rate = 0

    try:
        data, rate = sf.read(str(src), dtype="float32", always_2d=True)
        audio = data.T
    except Exception as libsndfile_error:
        ffmpeg = config.find_ffmpeg()
        if not ffmpeg:
            raise UnsupportedFormat(
                f"无法解码 {src.suffix or '该文件'}：ffmpeg 不可用（imageio-ffmpeg 可能损坏，"
                f"重跑 uv sync 可修复），当前仅支持 {', '.join(sorted(config.NATIVE_EXTS))}",
            ) from libsndfile_error
        tmp = dst.with_suffix(".transcoded.wav")
        try:
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-i", str(src),
                    # Take the first audio stream only — this is what makes mp4 /
                    # m4s / mkv / ts work without caring about the video payload.
                    "-map", "0:a:0",
                    "-ac", "2", "-ar", str(config.SAMPLE_RATE), "-c:a", "pcm_s16le",
                    str(tmp),
                ],
                check=True,
                capture_output=True,
            )
            data, rate = sf.read(str(tmp), dtype="float32", always_2d=True)
            audio = data.T
        except subprocess.CalledProcessError as exc:
            raise UnsupportedFormat(_ffmpeg_error(exc)) from exc
        finally:
            tmp.unlink(missing_ok=True)

    if audio.shape[0] == 1:
        audio = np.tile(audio, (2, 1))
    elif audio.shape[0] > 2:
        audio = audio[:2]

    if rate != config.SAMPLE_RATE:
        audio = soxr.resample(audio.T, rate, config.SAMPLE_RATE, quality="HQ").T

    audio = np.ascontiguousarray(audio, dtype=np.float32)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), audio.T, config.SAMPLE_RATE, subtype="PCM_16")
    return audio.shape[1] / config.SAMPLE_RATE
