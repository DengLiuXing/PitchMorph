"""Stem separation: the ONNX inference loop.

Scope is deliberately narrow — this module only turns an already-normalized
44.1 kHz WAV into stem WAVs. Decoding lives in :mod:`app.media`, weight
download and cache-path resolution in :mod:`app.weights`, shared exception types
in :mod:`app.errors`.

We drive the sessions ourselves instead of calling ``demucs_onnx.separate()``:
that helper hides the chunk loop, and with it the only place cancellation and
real progress can be threaded through. Its chunking math is reproduced here
verbatim (quarter-segment overlap-add, triangular window), so the output is the
same audio — ``scripts/smoke_offline.py`` guards the plumbing and the docstring
of :func:`_chunked_infer` explains how to re-verify parity against upstream.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import soundfile as sf

from . import config, runtime, weights
from .errors import Canceled, SeparationError, friendly_error

log = logging.getLogger("pitchmorph.separator")

#: ``(stage, stem, done, total)`` — stage is one of downloading/loading/separating.
StageCallback = Callable[[str, str | None, int, int], None]
#: Overall job progress in 0..1, valid during downloading and separating.
ProgressCallback = Callable[[float], None]
CancelCheck = Callable[[], bool]


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

    Parity with upstream is a hard requirement: after touching this function,
    run the same audio through ``demucs_onnx.separate(model="htdemucs_ft_vocals")``
    and through here, then compare the two ``vocals.wav`` sample by sample
    (max abs diff must stay < 1e-3).
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


def _model_sources(model: str) -> tuple[str, ...]:
    """Stem order of a model, as reported by the catalog (never hardcoded)."""
    from demucs_onnx import list_models

    return tuple(list_models()[model]["sources"].split(","))


def _write_stem(path: Path, audio: np.ndarray) -> None:
    sf.write(str(path), audio.T, config.SAMPLE_RATE, subtype="PCM_16")


# ---------------------------------------------------------------------------
# Job-level orchestration
# ---------------------------------------------------------------------------


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

    ``src_wav`` must already be 44.1 kHz stereo (see :func:`app.media.decode_to_wav`)
    so that the emitted stems share the input's sample rate.

    The ``htdemucs_ft`` bag runs its specialists in batches of ``parallel`` (one
    session per stem, so it is clamped to ``len(tier.stems)``). Two constraints
    shape the batch shape: a single session already saturates the physical cores
    (``docs/perf-audit.md``), and releasing sessions is process-wide, so it may
    only run once every worker has joined — which is why the release happens
    after the batch, never inside a worker.
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
        if weights.missing_files(tier.model, tier.stems):
            notify("downloading", None, 0)
            weights.ensure_weights(
                tier.model, tier.stems, on_progress=report, should_cancel=should_cancel,
            )
            check()

        paths = weights.resolve_paths(tier.model, tier.stems)
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
            sources = _model_sources("htdemucs_ft")
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
                    {stem: sources.index(stem)},
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
                    with lock:
                        ratios[stem] = 1.0
                with lock:
                    progress = sum(ratios.values()) / total
                    if progress > reported:
                        reported = progress
                report(reported)
                # Every worker has joined, so the cache only holds this batch.
                # Order matters: releasing only drops the cache's references, so
                # the local dict has to go first or the session (and its ~3 GB
                # arena) would stay alive into the next batch's session build.
                sessions.clear()
                _release_sessions()
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
        # Runs on success, cancel and failure alike so an aborted job cannot
        # leave a session (and its VRAM) behind.
        _release_sessions()

    log.info("job separation finished in %.1fs", time.monotonic() - started_all)

    produced = {stem: out_dir / f"{stem}.wav" for stem in tier.stems}
    produced = {stem: path for stem, path in produced.items() if path.is_file()}
    if len(produced) != total:
        missing = sorted(set(tier.stems) - set(produced))
        raise SeparationError(f"模型未产出预期轨道：{', '.join(missing)}")
    return produced


def _release_sessions() -> None:
    """Drop every cached session so its VRAM (and arena) is returned.

    Retention was the original design — it saved a rebuild per specialist. On
    CUDA that trade is wrong: a session whose arena has grown holds ~3 GB, so a
    four-stem batch would need ~12 GB. A rebuild costs ~4 s, which is the price
    of not running out of memory on an 8 GB card.

    The cache lives in :mod:`app.runtime` because that is the only layer that
    can pass CUDA provider options — ``demucs_onnx.SessionPool`` keys on
    provider names, so its sessions are stuck with ORT's defaults.
    """
    try:
        runtime.release_sessions()
    except Exception:  # cleanup must never fail an already-finished job
        log.debug("session release skipped", exc_info=True)


def _cleanup(out_dir: Path) -> None:
    """Remove half-written stems after a failure or cancellation."""
    for path in out_dir.glob("*.wav"):
        path.unlink(missing_ok=True)
