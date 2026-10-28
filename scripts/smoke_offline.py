"""Offline smoke test for the separation flow — no model weights required.

    uv run python scripts/smoke_offline.py

Stubs the ONNX seam so the per-stem bag loop, the single-file branch, progress
reporting and cancellation cleanup can be checked in a second.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demucs_onnx  # noqa: E402

from app import config, separator  # noqa: E402

CALLS: list[tuple[str, tuple[str, ...]]] = []
PROGRESS: list[float] = []
CANCEL_AFTER: dict[str, int] = {"infer": 0, "n": 0}


def fake_load_audio(path, target_sr=44100):
    return np.zeros((2, 4410), "float32"), 44100


class FakePool:
    def get(self, path, providers):
        CALLS.append(("session", (str(path),)))
        return object()

    def clear(self):
        return None


def fake_infer(session, wanted, mix, *, on_chunk=None, should_cancel=None):
    CANCEL_AFTER["n"] += 1
    if CANCEL_AFTER["n"] > CANCEL_AFTER["infer"] >= 0 and CANCEL_AFTER["infer"]:
        raise separator.Canceled("任务已取消")
    CALLS.append(("infer", tuple(wanted)))
    if on_chunk:
        for step in range(4):
            on_chunk(step + 1, 4)
    return {name: np.zeros((2, mix.shape[1]), "float32") for name in wanted}


demucs_onnx.load_audio = fake_load_audio
demucs_onnx.session_pool = lambda: FakePool()
separator.missing_files = lambda model, stems: []
separator._resolve_paths = lambda model, stems: {  # noqa: SLF001
    label: Path(f"{label}.onnx")
    for label, _repo, _file in separator.required_files(model, stems)
}
separator._chunked_infer = fake_infer  # noqa: SLF001


def run(tier_key: str, out: Path) -> dict[str, Path]:
    CALLS.clear()
    PROGRESS.clear()
    return separator.separate_stems(
        out / "input.wav",
        out / tier_key,
        tier=config.TIERS[tier_key],
        on_progress=PROGRESS.append,
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pitchmorph-smoke-"))
    src = tmp / "input.wav"
    sf.write(src, np.zeros((4410, 2), "float32"), 44100, subtype="PCM_16")

    quality = run("quality", tmp)
    assert sorted(quality) == ["bass", "drums", "other", "vocals"], quality
    assert [c[1] for c in CALLS if c[0] == "infer"] == [
        ("drums",), ("bass",), ("other",), ("vocals",),
    ], CALLS
    assert PROGRESS == sorted(PROGRESS), "progress must be monotonic"
    assert PROGRESS[-1] == 1.0, PROGRESS
    print(f"quality  ok: 4 files, 4 specialist passes, {len(PROGRESS)} progress ticks")

    fast = run("fast", tmp)
    assert sorted(fast) == ["bass", "drums", "other", "vocals"], fast
    assert [c[1] for c in CALLS if c[0] == "infer"] == [
        ("drums", "bass", "other", "vocals"),
    ], CALLS
    print("fast     ok: single-file model, one pass, 4 stems")

    CANCEL_AFTER["n"] = 0
    CANCEL_AFTER["infer"] = 2
    try:
        run("quality", tmp)
    except separator.Canceled:
        leftover = list((tmp / "quality").glob("*.wav"))
        assert not leftover, leftover
        print("cancel   ok: aborted mid-run, partial files removed")
    else:
        raise AssertionError("cancel was not honoured")
    finally:
        CANCEL_AFTER["infer"] = 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
