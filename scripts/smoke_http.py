"""端到端打 HTTP：上传 → 轮询 → 检查 /media 播放与 Range 支持。

先起服务（另开一个终端）：
    uv run uvicorn app.main:app --port 8000
再跑：
    uv run python scripts/smoke_http.py temp-dev/samples/物凄.m4s

加了 `--reload` 会清空任务表，长任务跑到一半就丢，所以启动命令不要带它。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ACTIVE = {"queued", "decoding", "separating", "pitch"}


def main() -> int:
    parser = argparse.ArgumentParser(description="HTTP 链路冒烟")
    parser.add_argument("input", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--tier", default="fast", choices=["fast", "quality"])
    args = parser.parse_args()

    if not args.input.exists():
        print(f"找不到输入文件：{args.input}")
        return 2

    with httpx.Client(base_url=args.base, timeout=180.0) as client:
        health = client.get("/api/health")
        print(f"health  {health.status_code}  {health.json()}")
        if health.status_code != 200:
            print("服务没起来？先跑 uv run uvicorn app.main:app --port 8000")
            return 1

        print(f"上传 {args.input.name} …")
        with args.input.open("rb") as handle:
            response = client.post(
                "/api/jobs",
                files={"file": (args.input.name, handle, "application/octet-stream")},
                data={"tier": args.tier},
            )
        if response.status_code != 202:
            print(f"上传被拒 {response.status_code}：{response.json().get('detail')}")
            return 1
        job = response.json()
        job_id = job["id"]
        print(f"  → job {job_id}  stage={job['stage']}")

        last = ""
        deadline = time.time() + 900
        while True:
            time.sleep(0.9)
            jobs = client.get("/api/jobs").json()
            current = next((item for item in jobs if item["id"] == job_id), None)
            if current is None:
                print("任务从列表里消失了")
                return 1
            line = f"{current['stage']:<10} {current['progress']:3d}%  {current['detail']}"
            if line != last:
                print(f"\r  {line}\x1b[K", end="", flush=True)
                last = line
            if current["stage"] not in ACTIVE:
                print()
                if current["stage"] != "done":
                    print(f"结束状态 {current['stage']}：{current['error']}")
                    return 1
                break
            if time.time() > deadline:
                print("\n超时")
                return 1

        print(f"完成，用时 {current['elapsed_sec']} s，时长 {current['duration_sec']} s")

        for stem in ("drums", "bass", "other", "vocals"):
            url = f"/media/{job_id}/stems/{stem}.wav"
            head = client.head(url)
            print(f"  {stem:<7} /media → {head.status_code}")

        ranged = client.get(
            f"/media/{job_id}/stems/vocals.wav", headers={"Range": "bytes=0-99"}
        )
        if ranged.status_code == 206 and len(ranged.content) == 100:
            print("  Range  206  100 字节 ✓（播放器拖动依赖它）")
        else:
            print(f"  Range  期望 206/100，实际 {ranged.status_code}/{len(ranged.content)}")

        pitch = client.get(f"/api/jobs/{job_id}/pitch")
        if pitch.status_code == 200:
            payload = pitch.json()
            voiced = sum(1 for value in payload["f0"] if value is not None)
            print(f"  pitch  {payload['frames']} 帧，有声 {voiced} 帧")
        else:
            print(f"  pitch  {pitch.status_code}：{pitch.json().get('detail')}")

        print(f"\n浏览器打开：{args.base}/media/{job_id}/stems/vocals.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
