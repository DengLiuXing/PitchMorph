"""把任意输入解码成模型要的 44.1 kHz 立体声 PCM-16 WAV。

两条路，永不交叉：`soundfile`/libsndfile 优先（wav/flac/mp3/ogg/aiff），
容器类（mp4/m4s/mkv/ts）交给 ffmpeg。ffmpeg 用 `imageio-ffmpeg` 的内置二进制，
所以「没有 ffmpeg」是安装坏了，不是正常情况。

搬自 v1，逻辑一行没改：这段解码在 ONNX 时代已经验过容器格式全集。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from app import config
from app.errors import UnsupportedFormat

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_AUDIO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Audio:")


def probe_with_ffmpeg(src: Path) -> tuple[float | None, bool]:
    """只读头部，返回 ``(时长秒, 是否有音频轨)``。

    ``ffmpeg -i`` 不带输出时把容器摘要打到 stderr 然后非零退出——正是我们要的：
    几毫秒就能判断「没有音轨」或「超长」，不必先把 40 分钟视频转完再拒绝。
    """
    ffmpeg = config.find_ffmpeg()
    if not ffmpeg:
        return None, True

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
    stderr = (exc.stderr or b"").decode("utf-8", "replace")
    lowered = stderr.lower()
    if "matches no streams" in lowered or "does not contain any stream" in lowered:
        return "该文件里没有音频轨（视频与音频分开缓存时，请选音频那一段）"
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return f"ffmpeg 无法解码该文件：{lines[-1] if lines else 'unknown error'}"


def decode_to_wav(src: Path, dst: Path) -> float:
    """解码到 ``dst``，返回时长（秒）。两条路都失败时抛 ``UnsupportedFormat``。"""
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
                f"重跑 uv sync 可修复），当前仅支持 {', '.join(sorted(config.NATIVE_EXTS))}"
            ) from libsndfile_error

        tmp = dst.with_suffix(".transcoded.wav")
        try:
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                    "-i", str(src),
                    # 只取第一条音轨。这一条让 mp4 / m4s / mkv / ts 全部走同一条路。
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

    if audio is None or audio.size == 0:
        raise UnsupportedFormat("该文件没有可用的音频数据")

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
