"""Time one separation end to end, split by stage.

    uv run python scripts/bench.py samples/物凄.m4s
    uv run python scripts/bench.py song.mp3 --tier quality --parallel 1

Writes nothing into the repo: the decoded WAV and the stems land in a temp
directory. Run it with uvicorn stopped, on AC power, or the numbers belong to
whatever else was competing for the CPU/GPU.

Two things are measured. **Time** per stage, to compare CPU against CUDA on the
same input. **VRAM**, sampled from ``nvidia-smi`` on a background thread, so one
run answers "will this peak fit on an 8 GB card" without a second window open.

Short fixtures are the default for iterating; only the long-run question ("does
VRAM or speed drift after 60+ chunks") needs a full track.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, media, runtime, separator  # noqa: E402

_LOADS: list[tuple[str, float]] = []
_RUNS: list[float] = []
_WRITES: list[tuple[str, float]] = []


class _TimedSession:
    """Proxy that records the wall time of each ``session.run``."""

    __slots__ = ("_inner",)

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def run(self, names, feeds):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        out = self._inner.run(names, feeds)  # type: ignore[attr-defined]
        _RUNS.append(time.perf_counter() - start)
        return out

    def __getattr__(self, key: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, key)


def _instrument() -> None:
    """Time session creation, every run, and stem writes — no app changes.

    The build boundary is ``runtime.ensure_session``, not ``SessionPool.get``:
    sessions no longer go through that pool, so patching it would silently stop
    recording loads altogether. ``separator`` reaches the function through the
    module attribute, which is why replacing it here takes effect.
    """
    original_ensure = runtime.ensure_session

    def timed_ensure(path):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        session = original_ensure(path)
        _LOADS.append((Path(str(path)).name, time.perf_counter() - start))
        return session if isinstance(session, _TimedSession) else _TimedSession(session)

    runtime.ensure_session = timed_ensure  # type: ignore[assignment]

    original_write = separator._write_stem  # noqa: SLF001

    def timed_write(path, audio):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        original_write(path, audio)
        _WRITES.append((Path(path).name, time.perf_counter() - start))

    separator._write_stem = timed_write  # type: ignore[assignment]


def _query_vram_mb() -> int | None:
    """Device memory currently in use, straight from the driver."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    first = (proc.stdout or "").strip().splitlines()
    if not first:
        return None
    try:
        return int(first[0].strip())
    except ValueError:
        return None


class VramSampler:
    """Poll ``nvidia-smi`` off the main thread and keep the high-water mark.

    Sampling has to happen in the background: the call costs 50-100 ms, and
    running it between chunks would be indistinguishable from inference time.
    """

    def __init__(self, interval: float = 0.2) -> None:
        self.interval = interval
        self.baseline: int | None = None
        self.peak: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.baseline = _query_vram_mb()
        if self.baseline is None:
            return
        self.peak = self.baseline
        self._thread = threading.Thread(target=self._loop, name="vram", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            used = _query_vram_mb()
            if used is not None and (self.peak is None or used > self.peak):
                self.peak = used

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.py", description="分轨耗时拆解")
    parser.add_argument("audio", type=Path, help="待测音频")
    parser.add_argument("--tier", default=config.DEFAULT_TIER, choices=sorted(config.TIERS))
    parser.add_argument("--parallel", type=int, default=config.default_parallel())
    parser.add_argument(
        "--no-vram", action="store_true", help="不采样显存（默认在 CUDA 下采样）",
    )
    args = parser.parse_args(argv)

    if not args.audio.is_file():
        print(f"找不到文件：{args.audio}")
        return 2

    _instrument()

    active = runtime.providers()
    usable, reason = runtime.gpu_status()
    primary = active[0].replace("ExecutionProvider", "") if active else "?"
    print(f"执行器      : {primary}")
    if not usable:
        print(f"  CUDA 不可用：{reason}")
    elif runtime.CUDA not in active:
        print("  探针可用，但被 PITCHMORPH_PROVIDERS 强制为 CPU")
    print(f"档位 / 并发 : {args.tier} / {args.parallel}")
    options = runtime.session_options_summary()
    if options:
        print(f"CUDA 选项   : {', '.join(f'{key}={value}' for key, value in options.items())}")

    sampler = VramSampler()
    sampling = not args.no_vram and runtime.using_cuda()

    with tempfile.TemporaryDirectory(prefix="pitchmorph-bench-") as tmp:
        work = Path(tmp)
        started = time.perf_counter()
        duration = media.decode_to_wav(args.audio, work / "input.wav")
        decode_sec = time.perf_counter() - started

        if sampling:
            sampler.start()
        started = time.perf_counter()
        produced = separator.separate_stems(
            work / "input.wav",
            work / "stems",
            tier=config.TIERS[args.tier],
            parallel=args.parallel,
        )
        separate_sec = time.perf_counter() - started
        sampler.stop()

        print(f"音频        : {duration:.1f} s → {len(produced)} 轨")
        print(f"解码        : {decode_sec:.2f} s")
        print(f"模型加载    : {sum(sec for _, sec in _LOADS):.2f} s "
              f"({', '.join(f'{name} {sec:.2f}s' for name, sec in _LOADS)})")
        if _RUNS:
            print(f"推理        : {sum(_RUNS):.1f} s，{len(_RUNS)} 次 run，"
                  f"中位 {statistics.median(_RUNS):.3f} s/chunk")
        if sampler.peak is not None:
            print(f"显存        : 基线 {sampler.baseline} MiB → 峰值 {sampler.peak} MiB")
        print(f"写盘        : {sum(sec for _, sec in _WRITES):.2f} s")
        print(f"分轨合计    : {separate_sec:.1f} s   RTF {separate_sec / duration:.2f}")
        print(f"全流程      : {decode_sec + separate_sec:.1f} s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
