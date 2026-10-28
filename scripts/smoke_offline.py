"""Offline smoke test for the separation flow — no model weights required.

    uv run python scripts/smoke_offline.py

Stubs the ONNX seam so the specialist loop, the single-file branch, batching,
progress reporting and cancellation cleanup can be checked in a second.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demucs_onnx  # noqa: E402

from app import config, errors, runtime, separator, weights  # noqa: E402

CALLS: list[tuple[str, tuple[str, ...]]] = []
PROGRESS: list[float] = []
RELEASES: list[int] = []
CANCEL_AFTER: dict[str, int] = {"infer": 0, "n": 0}
LOCK = threading.Lock()


def fake_load_audio(path, target_sr=44100):
    return np.zeros((2, 4410), "float32"), 44100


def fake_ensure_session(path):
    CALLS.append(("session", (str(path),)))
    return object()


def fake_release():
    """Count releases. The real one clears runtime's session cache and VRAM."""
    RELEASES.append(1)


def fake_infer(session, wanted, mix, *, on_chunk=None, should_cancel=None):
    # The counter is shared by every worker thread, so it needs the lock.
    with LOCK:
        CANCEL_AFTER["n"] += 1
        nth = CANCEL_AFTER["n"]
        limit = CANCEL_AFTER["infer"]
    if limit and nth > limit:
        raise errors.Canceled("任务已取消")
    if should_cancel and should_cancel():
        raise errors.Canceled("任务已取消")
    CALLS.append(("infer", tuple(wanted)))
    if on_chunk:
        for step in range(4):
            on_chunk(step + 1, 4)
    return {name: np.zeros((2, mix.shape[1]), "float32") for name in wanted}


demucs_onnx.load_audio = fake_load_audio
runtime.ensure_session = fake_ensure_session
runtime.release_sessions = fake_release
weights.missing_files = lambda model, stems: []
weights.resolve_paths = lambda model, stems: {
    label: Path(f"{label}.onnx")
    for label, _repo, _file in weights.required_files(model, stems)
}
separator._chunked_infer = fake_infer  # noqa: SLF001


def run(
    tier_key: str,
    out: Path,
    *,
    parallel: int = 1,
    suffix: str = "",
) -> dict[str, Path]:
    CALLS.clear()
    PROGRESS.clear()
    RELEASES.clear()
    CANCEL_AFTER["n"] = 0
    return separator.separate_stems(
        out / "input.wav",
        out / f"{tier_key}{suffix}",
        tier=config.TIERS[tier_key],
        parallel=parallel,
        on_progress=PROGRESS.append,
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pitchmorph-smoke-"))
    src = tmp / "input.wav"
    sf.write(src, np.zeros((4410, 2), "float32"), 44100, subtype="PCM_16")

    quality = run("quality", tmp, parallel=1)
    assert sorted(quality) == ["bass", "drums", "other", "vocals"], quality
    assert [c[1] for c in CALLS if c[0] == "infer"] == [
        ("drums",), ("bass",), ("other",), ("vocals",),
    ], CALLS
    assert PROGRESS == sorted(PROGRESS), "progress must be monotonic"
    assert PROGRESS[-1] == 1.0, PROGRESS
    # One release per batch, plus the unconditional one in the finally block:
    # four batches with parallel=1, so 4 + 1. Getting this wrong is how VRAM
    # ends up held across batches on CUDA.
    assert len(RELEASES) == 5, f"quality parallel=1 should release 4+1 times: {RELEASES}"
    print(f"quality  ok: 4 files, 4 specialist passes, {len(PROGRESS)} progress ticks")

    par = run("quality", tmp, parallel=2, suffix="-p2")
    assert sorted(par) == ["bass", "drums", "other", "vocals"], par
    # Batching must not change which specialists ran, only their overlap.
    assert sorted(c[1] for c in CALLS if c[0] == "infer") == [
        ("bass",), ("drums",), ("other",), ("vocals",),
    ], CALLS
    assert len([c for c in CALLS if c[0] == "session"]) == 4, CALLS
    assert PROGRESS == sorted(PROGRESS), "parallel progress must stay monotonic"
    assert PROGRESS[-1] == 1.0, PROGRESS
    assert len(RELEASES) == 3, f"quality parallel=2 should release 2+1 times: {RELEASES}"
    print(f"parallel ok: 2-stem batches, 4 sessions, "
          f"{len(RELEASES)} releases, {len(PROGRESS)} progress ticks")

    vox = run("vocals", tmp, parallel=4, suffix="-v4")
    assert sorted(vox) == ["vocals"], vox
    assert [c[1] for c in CALLS if c[0] == "infer"] == [("vocals",)], CALLS
    print("vocals   ok: single-stem tier clamps parallel to 1")

    fast = run("fast", tmp)
    assert sorted(fast) == ["bass", "drums", "other", "vocals"], fast
    assert [c[1] for c in CALLS if c[0] == "infer"] == [
        ("drums", "bass", "other", "vocals"),
    ], CALLS
    print("fast     ok: single-file model, one pass, 4 stems")

    CANCEL_AFTER["infer"] = 2
    try:
        run("quality", tmp, suffix="-cancel")
    except errors.Canceled:
        leftover = list((tmp / "quality-cancel").glob("*.wav"))
        assert not leftover, leftover
        print("cancel   ok: aborted mid-run, partial files removed")
    else:
        raise AssertionError("cancel was not honoured")
    finally:
        CANCEL_AFTER["infer"] = 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
