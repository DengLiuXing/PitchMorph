"""Time one separation end to end, split by stage.

    uv run python scripts/bench.py assets/美丽之物.mp3
    uv run python scripts/bench.py song.mp3 --tier quality --parallel 1

Writes nothing into the repo: the decoded WAV and the stems land in a temp
directory. Run it with uvicorn stopped, on AC power, or the numbers belong to
whatever else was competing for the CPU/GPU.

The point is comparing CPU vs CUDA on the same input: run it once before
``scripts/setup.py --apply`` and once after.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, runtime, separator  # noqa: E402

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

    One patch, not two: both boundaries live on ``SessionPool.get``, so a
    second wrapper would silently unhook the first.
    """
    from demucs_onnx.inference import SessionPool

    original_get = SessionPool.get

    def timed_get(self, path, providers):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        session = original_get(self, path, providers)
        _LOADS.append((Path(str(path)).name, time.perf_counter() - start))
        return session if isinstance(session, _TimedSession) else _TimedSession(session)

    SessionPool.get = timed_get  # type: ignore[method-assign]

    original_write = separator._write_stem  # noqa: SLF001

    def timed_write(path, audio):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        original_write(path, audio)
        _WRITES.append((Path(path).name, time.perf_counter() - start))

    separator._write_stem = timed_write  # type: ignore[assignment]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.py", description="分轨耗时拆解")
    parser.add_argument("audio", type=Path, help="待测音频")
    parser.add_argument("--tier", default=config.DEFAULT_TIER, choices=sorted(config.TIERS))
    parser.add_argument("--parallel", type=int, default=config.default_parallel())
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

    with tempfile.TemporaryDirectory(prefix="pitchmorph-bench-") as tmp:
        work = Path(tmp)
        started = time.perf_counter()
        duration = separator.decode_to_wav(args.audio, work / "input.wav")
        decode_sec = time.perf_counter() - started

        started = time.perf_counter()
        produced = separator.separate_stems(
            work / "input.wav",
            work / "stems",
            tier=config.TIERS[args.tier],
            parallel=args.parallel,
        )
        separate_sec = time.perf_counter() - started

        print(f"音频        : {duration:.1f} s → {len(produced)} 轨")
        print(f"解码        : {decode_sec:.2f} s")
        print(f"模型加载    : {sum(sec for _, sec in _LOADS):.2f} s "
              f"({', '.join(f'{name} {sec:.2f}s' for name, sec in _LOADS)})")
        if _RUNS:
            print(f"推理        : {sum(_RUNS):.1f} s，{len(_RUNS)} 次 run，"
                  f"中位 {statistics.median(_RUNS):.3f} s/chunk")
        print(f"写盘        : {sum(sec for _, sec in _WRITES):.2f} s")
        print(f"分轨合计    : {separate_sec:.1f} s   RTF {separate_sec / duration:.2f}")
        print(f"全流程      : {decode_sec + separate_sec:.1f} s")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
