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


def _export_stage(job: Job, root: Path) -> None:
    registry.update(job.id, detail="生成 MIDI 与工程文件")
    try:
        # 纯函数 + 毫秒级 IO，不必再开子进程
        exported = write_exports(root)
        if exported:
            logger.info("导出 %s", sorted(exported))
    except Exception as exc:
        logger.warning("导出失败（音高与四轨保留）：%s", exc)


def _pitch_stage(
    job: Job,
    root: Path,
    source: Path,
    *,
    base: float,
    span: float,
    params: dict | None = None,
) -> None:
    """对一个音频文件提音高，再生成派生文件。

    音高失败不毁掉已有产物：四轨已经能听了，原因留在日志里。
    """
    registry.update(job.id, stage=Stage.PITCH, frac=base, detail="音高")
    request: dict = {"input": str(source), "out": str(root / "features" / "pitch.json")}
    if params:
        request.update(params)
    try:
        _run_worker("pitch", request, job, base=base, span=span)
    except JobCanceled:
        raise
    except Exception as exc:
        logger.warning("音高阶段失败（已有产物保留）：%s", exc)
        registry.update(job.id, detail="音高失败")
        return
    _export_stage(job, root)


def _finish(job: Job) -> None:
    registry.update(
        job.id,
        stage=Stage.DONE,
        frac=1.0,
        detail="完成",
        finished_at=time.time(),
    )
    logger.info("任务 %s 完成，用时 %.1f s", job.id, job.elapsed_sec)


def run_pipeline(job: Job) -> None:
    root = config.job_dir(job.id)

    registry.update(job.id, stage=Stage.DECODING, frac=0.02, detail="解码")
    duration = media.decode_to_wav(_source_file(root), root / "input.wav")
    registry.update(job.id, duration_sec=duration, frac=0.05)

    if job.mode == "pitch":
        # 上传的已经是人声干声：跳过分轨，直接对解码结果提音高
        _check_cancel(job)
        _pitch_stage(job, root, root / "input.wav", base=0.1, span=0.85)
    else:
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
        _pitch_stage(job, root, root / "stems" / "vocals.wav", base=0.84, span=0.13)

    _finish(job)


def run_pitch_only(job: Job, params: dict | None = None) -> None:
    """只重跑音高，用来对比不同参数的效果。

    不重新解码、不重新分离、不碰 `stems/`。有 stems 就用 vocals，
    跳过分轨的任务则用 `input.wav`。
    """
    root = config.job_dir(job.id)
    vocals = root / "stems" / "vocals.wav"
    source = vocals if vocals.exists() else root / "input.wav"
    if not source.exists():
        raise RuntimeError("这个任务没有可分析的音频")

    _pitch_stage(job, root, source, base=0.05, span=0.9, params=params)
    _finish(job)
