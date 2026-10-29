"""直接喂素材给 worker 子进程，观察协议事件流与产物。

用法：
    uv run python scripts/smoke_worker.py temp-dev/samples/物凄.m4s --tier fast
    uv run python scripts/smoke_worker.py temp-dev/samples/物凄.m4s --tier fast --pitch

首次运行会下载权重（htdemucs 约 80 MB，htdemucs_ft 四个专家约 320 MB，
FCPE 约 20 MB），全部走公网，卡住时先确认代理。
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, media  # noqa: E402
from app.protocol import DONE, ERROR, PROGRESS, parse_line  # noqa: E402


def run_worker(stage: str, request: dict) -> dict[str, str]:
    """起一个 worker 子进程，把事件流打到终端，返回产物清单。"""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "app.worker", stage],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )
    assert proc.stdin and proc.stdout

    artifacts: dict[str, str] = {}
    last_pct = -1
    try:
        proc.stdin.write(json.dumps(request))
        proc.stdin.close()

        for line in proc.stdout:
            event = parse_line(line)
            if event is None:
                continue
            if event["t"] == PROGRESS:
                pct = int(event["frac"] * 100)
                if pct != last_pct:
                    detail = event.get("detail") or ""
                    # \x1b[K 清到行尾，否则短文案会叠在上一行长文案的残字上
                    print(f"\r  {stage:<8} {pct:3d}%  {detail}\x1b[K", end="", flush=True)
                    last_pct = pct
            elif event["t"] == DONE:
                artifacts = event.get("artifacts", {})
            elif event["t"] == ERROR:
                print(f"\n[{stage} error] {event.get('message')}")
        print()
        code = proc.wait()
    finally:
        # 父进程提前退出时子进程会一直占着显存空转，必须收掉
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    if code != 0:
        raise SystemExit(f"worker {stage} 退出码 {code}")
    if not artifacts:
        raise SystemExit(f"worker {stage} 没有返回任何产物")
    return artifacts


def report_pitch(path: Path) -> None:
    import json as _json

    payload = _json.loads(path.read_text(encoding="utf-8"))
    curve = payload["f0"]
    voiced = [value for value in curve if value is not None]
    ratio = len(voiced) / len(curve) if curve else 0.0
    print(f"  pitch    {payload['frames']} 帧 / hop {payload['hop_sec']} s")
    print(f"  有声帧   {len(voiced)}（{ratio:.1%}）")
    if voiced:
        print(f"  中位音高 {statistics.median(voiced):.1f} Hz")


def main() -> int:
    parser = argparse.ArgumentParser(description="worker 冒烟测试")
    parser.add_argument("input", type=Path)
    parser.add_argument("--tier", default=config.DEFAULT_TIER, choices=sorted(config.TIERS))
    parser.add_argument("--device", default=None, help="cuda / cpu，默认自动")
    parser.add_argument("--pitch", action="store_true", help="分轨后继续跑音高阶段")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"找不到输入文件：{args.input}")
        return 2

    job = config.job_dir("_smoke")
    job.mkdir(parents=True, exist_ok=True)
    input_wav = job / "input.wav"

    started = time.perf_counter()
    print(f"解码 {args.input.name} …")
    duration = media.decode_to_wav(args.input, input_wav)
    print(f"  → {duration:.2f} s，解码 {time.perf_counter() - started:.1f} s")

    stems_dir = job / "stems"
    request = {
        "input": str(input_wav),
        "stems_dir": str(stems_dir),
        "model": config.tier_model(args.tier),
        "sources": list(config.tier_sources(args.tier)),
    }
    if args.device:
        request["device"] = args.device

    stems = run_worker("separate", request)
    elapsed = time.perf_counter() - started
    print(f"产物（分轨累计 {elapsed:.1f} s，RTF {elapsed / duration:.2f}）：")
    for name, relative in sorted(stems.items()):
        path = job / relative
        if not path.exists():
            print(f"  {name:<7} 缺失：{relative}")
            return 1
        print(f"  {name:<7} {path.stat().st_size / 2**20:5.1f} MiB  {relative}")

    if not args.pitch:
        return 0

    votes = stems_dir / "vocals.wav"
    pitch_request = {"input": str(votes), "out": str(job / "features" / "pitch.json")}
    if args.device:
        pitch_request["device"] = args.device
    pitch_artifacts = run_worker("pitch", pitch_request)
    report_pitch(job / pitch_artifacts["pitch"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
