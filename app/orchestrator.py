"""把一个 Job 变成一次子进程流水线：解码 → 分轨 → 音高。

主进程**不 import torch**，只做三件事：起进程、读 stdout 的 JSON 行、写任务状态。
这条边界就是「换模型不动业务层」的全部含义。

取消 = `kill()`。子进程被杀掉时显存一定归还，不需要任何清理逻辑。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from app import config, media
from app.exports import write_exports
from app.jobs import Job, JobCanceled, Stage, registry
from app.protocol import DONE, ERROR, PROGRESS, parse_line

logger = logging.getLogger(__name__)


def _check_cancel(job: Job) -> None:
    if job.cancel.is_set():
        raise JobCanceled


def _source_file(root: Path) -> Path:
    for entry in root.glob("source.*"):
        return entry
    raise RuntimeError("上传的源文件不见了")


def _run_worker(
    stage: str,
    request: dict,
    job: Job,
    *,
    base: float,
    span: float,
) -> dict[str, str]:
    """起一个 worker 子进程，把事件映射进 Job 的进度；返回产物清单。"""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "app.worker", stage],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(config.ROOT),
    )
    artifacts: dict[str, str] = {}
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps(request, ensure_ascii=False))
        proc.stdin.close()

        for line in proc.stdout:
            _check_cancel(job)
            event = parse_line(line)
            if event is None:
                continue
            if event["t"] == PROGRESS:
                registry.update(
                    job.id,
                    frac=base + span * float(event["frac"]),
                    detail=event.get("detail") or "",
                )
            elif event["t"] == DONE:
                artifacts = event.get("artifacts", {})
            elif event["t"] == ERROR:
                raise RuntimeError(event.get("message") or f"{stage} 报错")

        code = proc.wait()
    finally:
        # 父进程一切出路径都要收尸，否则子进程会占着显存空转（踩过一次）
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    if code != 0:
        raise RuntimeError(f"{stage} 退出码 {code}")
    if not artifacts:
        raise RuntimeError(f"{stage} 没有返回产物")
    return artifacts


def run_pipeline(job: Job) -> None:
    root = config.job_dir(job.id)

    registry.update(job.id, stage=Stage.DECODING, frac=0.02, detail="解码")
    duration = media.decode_to_wav(_source_file(root), root / "input.wav")
    registry.update(job.id, duration_sec=duration, frac=0.05)

    _check_cancel(job)
    registry.update(job.id, stage=Stage.SEPARATING, detail="分轨")
    _run_worker(
        "separate",
        {
            "input": str(root / "input.wav"),
            "stems_dir": str(root / "stems"),
            "model": config.tier_model(job.tier),
            "sources": list(config.tier_sources(job.tier)),
        },
        job,
        base=0.05,
        span=0.78,
    )

    _check_cancel(job)
    registry.update(job.id, stage=Stage.PITCH, frac=0.84, detail="音高")
    try:
        _run_worker(
            "pitch",
            {
                "input": str(root / "stems" / "vocals.wav"),
                "out": str(root / "features" / "pitch.json"),
            },
            job,
            base=0.84,
            span=0.15,
        )
    except JobCanceled:
        raise
    except Exception as exc:
        # 音高失败不毁掉分轨结果：四轨已经能听了，把原因留在日志里
        logger.warning("音高阶段失败（分轨产物保留）：%s", exc)
        registry.update(job.id, detail="音高失败，四轨可用")

    _check_cancel(job)
    registry.update(job.id, detail="生成 MIDI 与工程文件")
    try:
        # 纯函数 + 毫秒级 IO，不必再开子进程
        exported = write_exports(root)
        if exported:
            logger.info("导出 %s", sorted(exported))
    except Exception as exc:
        logger.warning("导出失败（音高与四轨保留）：%s", exc)

    registry.update(
        job.id,
        stage=Stage.DONE,
        frac=1.0,
        detail="完成",
        finished_at=time.time(),
    )
    logger.info("任务 %s 完成，用时 %.1f s", job.id, job.elapsed_sec)
