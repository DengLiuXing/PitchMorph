"""Stem separation core.

Wraps ``demucs-onnx`` (numpy + onnxruntime, no PyTorch, no ffmpeg) and keeps
every ONNX detail out of the web layer.

We drive the ONNX sessions ourselves instead of calling
``demucs_onnx.separate()``: that helper hides the chunk loop, and with it the
only place cancellation and real progress can be threaded through. Its chunking
math is reproduced here verbatim (quarter-segment overlap-add, triangular
window), so the output is the same audio — ``scripts/smoke_offline.py`` keeps an
eye on that.

Decoding order is deliberate: ``soundfile`` (libsndfile) first, ``ffmpeg`` only
as a fallback for containers libsndfile cannot open (m4a/aac/wma). ffmpeg is
optional — if it is missing those formats are refused instead of the app
failing to boot.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

from . import config, runtime
from .weights import missing_files, required_files, weights_cached

log = logging.getLogger("pitchmorph.separator")

#: ``(stage, stem, done, total)`` — stage is one of downloading/loading/separating.
StageCallback = Callable[[str, str | None, int, int], None]
#: Overall job progress in 0..1, valid during downloading and separating.
ProgressCallback = Callable[[float], None]
CancelCheck = Callable[[], bool]

#: Stem order baked into the htdemucs graphs.
SOURCES: tuple[str, ...] = ("drums", "bass", "other", "vocals")


class SeparationError(RuntimeError):
    """Base error surfaced to the UI as ``job.error``."""


class UnsupportedFormat(SeparationError):
    pass


class Canceled(SeparationError):
    pass


# ---------------------------------------------------------------------------
# Model weights
# ---------------------------------------------------------------------------


def _cache_bytes() -> int:
    """Total size of the weight cache. Used to estimate download progress."""
    if not config.MODEL_DIR.exists():
        return 0
    total = 0
    for path in config.MODEL_DIR.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def ensure_weights(
    model: str,
    stems: Sequence[str],
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> None:
    """Download any missing ONNX weights, cancellably.

    ``demucs-onnx`` downloads inside :func:`separate`, which gives no progress
    hook and cannot be interrupted. Fetching here means we can report real bytes
    and abandon the transfer the moment the user hits cancel (the daemon thread
    keeps filling the cache, which is harmless).
    """
    pending = missing_files(model, stems)
    if not pending:
        return

    expected = max(1, len(pending) * config.WEIGHT_FILE_MB * 1024 * 1024)
    baseline = _cache_bytes()
    errors: list[BaseException] = []

    def fetch() -> None:
        from huggingface_hub import hf_hub_download

        try:
            for _, repo_id, filename in pending:
                hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    cache_dir=str(config.MODEL_DIR),
                )
        except BaseException as exc:  # surfaced on the worker thread below
            errors.append(exc)

    worker = threading.Thread(target=fetch, name="weights-download", daemon=True)
    worker.start()
    while worker.is_alive():
        if should_cancel and should_cancel():
            log.info("download abandoned after cancel request")
            raise Canceled("任务已取消")
        if on_progress:
            on_progress(min(0.99, max(0.0, (_cache_bytes() - baseline) / expected)))
        worker.join(0.6)

    if errors:
        raise SeparationError(friendly_error(errors[0]))
    if on_progress:
        on_progress(1.0)


def _resolve_paths(model: str, stems: Sequence[str]) -> dict[str, Path]:
    """Local ONNX paths for ``model``, keyed by the label used in the plan."""
    from huggingface_hub import hf_hub_download

    return {
        label: Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                cache_dir=str(config.MODEL_DIR),
                local_files_only=True,
            ),
        )
        for label, repo_id, filename in required_files(model, stems)
    }


def _model_sources(model: str) -> tuple[str, ...]:
    from demucs_onnx import list_models

    return tuple(list_models()[model]["sources"].split(","))


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


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
                f"无法解码 {src.suffix or '该文件'}：本机未安装 ffmpeg，"
                f"目前仅支持 {', '.join(sorted(config.NATIVE_EXTS))}",
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


def probe_duration(src: Path) -> float:
    """Duration in seconds, or 0.0 when libsndfile cannot tell."""
    try:
        info = sf.info(str(src))
    except Exception:
        return 0.0
    return info.frames / info.samplerate if info.samplerate else 0.0


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def _transition_window(segment: int, overlap_fraction: float = 0.25) -> np.ndarray:
    """Triangular fade-in/fade-out window for overlap-add chunked inference."""
    transition = int(segment * overlap_fraction)
    window = np.ones(segment, dtype=np.float32)
    fade = np.linspace(0, 1, transition, dtype=np.float32)
    window[:transition] = fade
    window[-transition:] = fade[::-1]
    return window


def _chunked_infer(
    session,
    wanted: dict[str, int],
    mix: np.ndarray,
    *,
    on_chunk: Callable[[int, int], None] | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, np.ndarray]:
    """Overlap-add inference, checking for cancellation between chunks.

    A chunk is 7.8 s of audio, so that is the real cancellation latency — not
    the length of a whole stem. ``wanted`` maps stem name to its row in the
    model output.
    """
    from demucs_onnx.inference import N_CHANNELS, N_SAMPLES

    total = mix.shape[1]
    overlap = N_SAMPLES // 4
    stride = N_SAMPLES - overlap
    n_chunks = max(1, (total + stride - 1) // stride)
    window = _transition_window(N_SAMPLES)

    out = {name: np.zeros((N_CHANNELS, total), dtype=np.float32) for name in wanted}
    weight = np.zeros(total, dtype=np.float32)

    for index in range(n_chunks):
        if should_cancel and should_cancel():
            raise Canceled("任务已取消")

        start = index * stride
        end = min(start + N_SAMPLES, total)
        chunk = mix[:, start:end]
        if chunk.shape[1] < N_SAMPLES:
            chunk = np.pad(chunk, ((0, 0), (0, N_SAMPLES - chunk.shape[1])), mode="constant")

        stems = session.run(["stems"], {"mix": chunk[np.newaxis, ...].astype(np.float32, copy=False)})[0][0]
        chunk_len = end - start
        fade = window[:chunk_len]

        for name, row in wanted.items():
            out[name][:, start:end] += stems[row, :, :chunk_len] * fade
        weight[start:end] += fade

        if on_chunk:
            on_chunk(index + 1, n_chunks)

    weight = np.maximum(weight, 1e-8)
    for name in out:
        out[name] /= weight
    return out


def _write_stem(path: Path, audio: np.ndarray) -> None:
    sf.write(str(path), audio.T, config.SAMPLE_RATE, subtype="PCM_16")


def separate_stems(
    src_wav: Path,
    out_dir: Path,
    *,
    tier: config.ModelTier,
    parallel: int = 1,
    on_stage: StageCallback | None = None,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCheck | None = None,
) -> dict[str, Path]:
    """Run separation for ``tier`` and return ``{stem: path}``.

    ``src_wav`` must already be 44.1 kHz stereo (see :func:`decode_to_wav`) so
    that the emitted stems share the input's sample rate.

    The ``htdemucs_ft`` bag runs its specialists in batches of ``parallel`` (one
    session per stem, so it is clamped to ``len(tier.stems)``). Two constraints
    shape the batch shape: a single session already saturates the physical cores
    (``docs/perf-audit.md``), and ``session_pool().clear()`` drops the whole
    process-wide pool, so it may only run while no worker holds a session.
    Peak memory is ``parallel`` sessions (~0.6 GB each) instead of all four.
    """
    from demucs_onnx import load_audio

    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(tier.stems)
    report = on_progress or (lambda frac: None)

    def notify(stage: str, stem: str | None, done: int) -> None:
        if on_stage:
            on_stage(stage, stem, done, total)

    def check() -> None:
        if should_cancel and should_cancel():
            raise Canceled("任务已取消")

    started_all = time.monotonic()
    try:
        if missing_files(tier.model, tier.stems):
            notify("downloading", None, 0)
            ensure_weights(
                tier.model, tier.stems, on_progress=report, should_cancel=should_cancel,
            )
            check()

        paths = _resolve_paths(tier.model, tier.stems)
        mix, _ = load_audio(str(src_wav), target_sr=config.SAMPLE_RATE)

        if tier.model in config.SINGLE_FILE_MODELS:
            sources = _model_sources(tier.model)
            wanted = {stem: sources.index(stem) for stem in tier.stems}
            notify("loading", None, 0)
            session = runtime.ensure_session(paths[tier.model])
            notify("separating", tier.stems[0], 0)
            audio = _chunked_infer(
                session,
                wanted,
                mix,
                on_chunk=lambda done, chunks: report(done / chunks),
                should_cancel=should_cancel,
            )
            for stem in tier.stems:
                _write_stem(out_dir / f"{stem}.wav", audio[stem])
            notify("separating", tier.stems[-1], total)
        else:
            workers = max(1, min(parallel, total))
            batch_count = (total + workers - 1) // workers
            ratios: dict[str, float] = {}
            reported = 0.0
            durations: dict[str, float] = {}
            lock = threading.Lock()
            abort = threading.Event()

            def worker_cancel() -> bool:
                return abort.is_set() or (should_cancel() if should_cancel else False)

            def run_stem(stem: str, session) -> np.ndarray:
                def on_chunk(done: int, chunks: int) -> None:
                    nonlocal reported
                    with lock:
                        ratios[stem] = done / chunks
                        # Reported inside the lock: workers finish out of order,
                        # so only the guarded max keeps the bar monotonic.
                        progress = sum(ratios.values()) / total
                        if progress > reported:
                            reported = progress
                            report(progress)

                started = time.monotonic()
                audio = _chunked_infer(
                    session,
                    {stem: SOURCES.index(stem)},
                    mix,
                    on_chunk=on_chunk,
                    should_cancel=worker_cancel,
                )[stem]
                durations[stem] = time.monotonic() - started
                return audio

            # Sessions are built up front on this thread: SessionPool.get() is an
            # unlocked get-or-create, so concurrent misses would race.
            for batch_index, start in enumerate(range(0, total, workers)):
                batch = tier.stems[start:start + workers]
                check()
                sessions = {}
                for stem in batch:
                    notify("loading", stem, start)
                    sessions[stem] = runtime.ensure_session(paths[stem])
                # Flip to "separating" as soon as the sessions exist: the chunk
                # loops below are where the time actually goes.
                notify("separating", batch[0], start)
                log.info(
                    "batch %d/%d — %s (parallel=%d)",
                    batch_index + 1,
                    batch_count,
                    " + ".join(batch),
                    len(batch),
                )
                with ThreadPoolExecutor(
                    max_workers=len(batch), thread_name_prefix="stem",
                ) as pool:
                    futures = {
                        pool.submit(run_stem, stem, sessions[stem]): stem
                        for stem in batch
                    }
                    audios: dict[str, np.ndarray] = {}
                    try:
                        for future in as_completed(futures):
                            audios[futures[future]] = future.result()
                    except BaseException:
                        # Release the siblings at their next chunk boundary
                        # instead of letting them finish the whole song.
                        abort.set()
                        raise
                for offset, stem in enumerate(batch, start=start):
                    _write_stem(out_dir / f"{stem}.wav", audios[stem])
                    log.info("%s: %s in %.1fs", tier.model, stem, durations[stem])
                    notify("separating", stem, offset + 1)
                    ratios[stem] = 1.0
                progress = sum(ratios.values()) / total
                if progress > reported:
                    reported = progress
                    report(progress)
    except Canceled:
        _cleanup(out_dir)
        raise
    except SeparationError:
        _cleanup(out_dir)
        raise
    except Exception as exc:
        _cleanup(out_dir)
        raise SeparationError(friendly_error(exc)) from exc
    finally:
        # Runs on success, cancel and failure alike: trimming only when the
        # pool outgrew its cap keeps the bag warm across jobs without letting
        # a future 6-stem model grow the process without bound.
        _trim_sessions()

    log.info("job separation finished in %.1fs", time.monotonic() - started_all)

    produced = {stem: out_dir / f"{stem}.wav" for stem in tier.stems}
    produced = {stem: path for stem, path in produced.items() if path.is_file()}
    if len(produced) != total:
        missing = sorted(set(tier.stems) - set(produced))
        raise SeparationError(f"模型未产出预期轨道：{', '.join(missing)}")
    return produced


def _trim_sessions() -> None:
    """Drop cached sessions only when the pool outgrew its cap.

    Clearing after every job would re-pay the ~4 s load per specialist on CPU
    and a full graph compile per specialist on CUDA — the exact cost the
    resident pool exists to avoid. The cap leaves headroom for future models.
    """
    try:
        from demucs_onnx import session_pool

        pool = session_pool()
        if len(pool) > config.MAX_RESIDENT_SESSIONS:
            log.info(
                "session pool trimmed (%d > %d)",
                len(pool),
                config.MAX_RESIDENT_SESSIONS,
            )
            pool.clear()
    except Exception:  # cleanup must never fail an already-finished job
        log.debug("session trim skipped", exc_info=True)


def _cleanup(out_dir: Path) -> None:
    """Remove half-written stems after a failure or cancellation."""
    for path in out_dir.glob("*.wav"):
        path.unlink(missing_ok=True)


def friendly_error(exc: Exception) -> str:
    """Turn ORT / Hub exceptions into something a user can act on."""
    message = str(exc) or exc.__class__.__name__
    lowered = message.lower()
    if "no such file" in lowered or "not found" in lowered:
        if "huggingface" in lowered or "hub" in lowered or "repository" in lowered:
            return (
                "下载模型权重失败。国内网络可设置环境变量 "
                "HF_ENDPOINT=https://hf-mirror.com 后重试。"
            )
    if "could not download" in lowered or "connection" in lowered or "timed out" in lowered:
        return (
            "下载模型权重超时。可设置 HF_ENDPOINT=https://hf-mirror.com 后重试，"
            f"或手动把权重放到 {config.MODEL_DIR}"
        )
    if "provider" in lowered and "not in available providers" in lowered:
        return f"ONNX Runtime 执行器不可用：{message}"
    return message
