"""Check container support with generated fixtures — no model weights needed.

    uv run python scripts/smoke_media.py

Builds a video+audio mp4, a video-only mp4, a fragmented ``.m4s`` and a
15-minute m4a in a temp dir, then exercises the same probe + decode path the
upload endpoint uses.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, media  # noqa: E402
from app.errors import UnsupportedFormat  # noqa: E402


def make(ffmpeg: str, out: Path, *args: str) -> Path:
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args, str(out)],
        check=True,
        capture_output=True,
    )
    return out


def build_fixtures(ffmpeg: str, work: Path) -> dict[str, Path]:
    source = make(
        ffmpeg, work / "tone.wav",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=12", "-ac", "2",
    )
    return {
        # mpeg4 keeps this independent of which encoders the build ships.
        "clip.mp4": make(
            ffmpeg, work / "clip.mp4",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=12",
            "-i", str(source),
            "-c:v", "mpeg4", "-qscale:v", "20", "-c:a", "aac", "-shortest",
        ),
        "silent.mp4": make(
            ffmpeg, work / "silent.mp4",
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=5",
            "-c:v", "mpeg4", "-qscale:v", "20", "-an",
        ),
        "audio.m4s": make(
            ffmpeg, work / "audio.m4s",
            "-i", str(source), "-c:a", "aac", "-b:a", "128k",
            "-movflags", "frag_keyframe+empty_moov", "-f", "mp4",
        ),
        "long.m4a": make(
            ffmpeg, work / "long.m4a",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=900",
            "-c:a", "aac", "-b:a", "32k",
        ),
    }


def main() -> int:
    ffmpeg = config.find_ffmpeg()
    if not ffmpeg:
        print("no ffmpeg available", file=sys.stderr)
        return 1
    print(f"ffmpeg: {config.ffmpeg_source()} → {ffmpeg}")

    work = Path(tempfile.mkdtemp(prefix="pitchmorph-media-"))
    fixtures = build_fixtures(ffmpeg, work)
    failures = 0

    for name, path in fixtures.items():
        duration, has_audio = media.probe_with_ffmpeg(path)
        accepted = config.can_accept(path.suffix)
        print(f"\n{name:12} probe: {duration and round(duration, 1)}s audio={has_audio} accept={accepted}")

        try:
            decoded = media.decode_to_wav(path, work / f"{name}.wav")
        except UnsupportedFormat as exc:
            expected = name == "silent.mp4"
            print(f"  decode → UnsupportedFormat: {exc}")
            if not expected:
                failures += 1
            continue

        print(f"  decode → {decoded:.1f}s")
        if name == "silent.mp4":
            failures += 1
            print("  expected this to be rejected (no audio stream)")

    long_duration, _ = media.probe_with_ffmpeg(fixtures["long.m4a"])
    too_long = bool(long_duration and long_duration > config.MAX_DURATION_SEC)
    print(f"\nlong.m4a: {long_duration:.0f}s exceeds limit={too_long}")
    if not too_long:
        failures += 1

    print("\nOK" if not failures else f"\n{failures} failure(s)")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
