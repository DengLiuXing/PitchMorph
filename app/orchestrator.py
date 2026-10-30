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
import threading
import time
from pathlib import Path

from app import config, media
from app.exports import DEFAULT_SMOOTH, write_exports
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
    # 兜底：`for line in proc.stdout` 是阻塞读，子进程安静下来（模型加载、
    # 大块推理）时读不到新行，取消要等下一次输出才生效。这个线程只管一件事：
    # 看到取消标志就 kill，管道随之关闭，下面的循环立刻结束并抛 JobCanceled。
    def _watchdog() -> None:
        while proc.poll() is None:
            if job.cancel.is_set():
                proc.kill()
                return
            time.sleep(0.1)

    threading.Thread(target=_watchdog, daemon=True).start()

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


def _export_smooth(job: Job) -> int:
    """任务创建时定下的平滑窗口；缺省回到 `exports.DEFAULT_SMOOTH`。"""
    section = (job.params or {}).get("exports") or {}
    value = section.get("smooth")
    return int(value) if isinstance(value, int | float) else DEFAULT_SMOOTH


def _export_stage(job: Job, root: Path) -> None:
    registry.update(job.id, detail="生成 MIDI 与工程文件")
    try:
        # 纯函数 + 毫秒级 IO，不必再开子进程
        exported = write_exports(root, smooth=_export_smooth(job))
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


def _notes_stage(
    job: Job,
    root: Path,
    source: Path,
    *,
    base: float,
    span: float,
    params: dict | None = None,
    strict: bool = False,
) -> None:
    """GAME 取音符边界，写 `features/notes_game.json`（`docs/05-game.md` M1）。

    流水线里（`strict=False`）权重要么存在要么跳过：这一步失败不该让任务失败——
    `exports` 看到文件不存在就退回启发式切分，结果是「和今天一样」。
    用户主动点「重算音符」时（`strict=True`）则要报错，否则静默什么都没做。
    """
    model = config.game_model_path()
    if model is None:
        if strict:
            raise RuntimeError(
                f"没有 GAME 权重：把权重解包到 {config.GAME_WEIGHTS_DIR / config.GAME_DEFAULT_MODEL}/"
            )
        logger.info("跳过 GAME 音符阶段：%s 下没有权重", config.GAME_WEIGHTS_DIR)
        return

    registry.update(job.id, stage=Stage.NOTES, frac=base, detail="音符边界")
    request: dict = {
        "input": str(source),
        "out": str(root / "features" / "notes_game.json"),
        "model": str(model),
    }
    if params:
        request.update(params)
    try:
        _run_worker("notes", request, job, base=base, span=span)
    except JobCanceled:
        raise
    except Exception as exc:
        if strict:
            raise
        logger.warning("GAME 音符阶段失败（导出退回启发式）：%s", exc)
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


_PITCH_KEYS = ("f0_min", "f0_max", "threshold", "gap_ms", "onsets")
_NOTES_KEYS = ("model", "language", "nsteps", "seg_threshold", "seg_radius", "est_threshold")


def _stage_params(job: Job, stage: str, keys: tuple[str, ...]) -> dict:
    """从任务参数里取出某一阶段的键，None 值丢掉（worker 里 None 等于用默认）。"""
    section = (job.params or {}).get(stage) or {}
    return {key: value for key, value in section.items() if key in keys and value is not None}


def _analysis_source(job: Job, root: Path) -> Path:
    """分析任务的音频：有父任务就用它的 vocals 轨，否则用自己上传解出来的干声。"""
    if job.parent_id:
        parent = config.job_dir(job.parent_id) / "stems" / "vocals.wav"
        if not parent.exists():
            raise RuntimeError(f"父任务 {job.parent_id} 没有 stems/vocals.wav，无法分析")
        return parent
    vocals = root / "stems" / "vocals.wav"
    return vocals if vocals.exists() else root / "input.wav"


def _separate_stage(job: Job, root: Path, *, base: float, span: float) -> None:
    _check_cancel(job)
    registry.update(job.id, stage=Stage.SEPARATING, frac=base, detail="分轨")
    _run_worker(
        "separate",
        {
            "input": str(root / "input.wav"),
            "stems_dir": str(root / "stems"),
            "model": config.tier_model(job.tier),
            "sources": list(config.tier_sources(job.tier)),
        },
        job,
        base=base,
        span=span,
    )


def _run_analysis(job: Job, root: Path, source: Path, *, base: float, span: float) -> None:
    """音高 + 音符两段，共用一个进度区间。参数在创建时就定了，这里只做透传。"""
    pitch_params = _stage_params(job, "pitch", _PITCH_KEYS)
    notes_params = _stage_params(job, "notes", _NOTES_KEYS)
    # 用户专门建的「分析」任务里，GAME 权重缺失要报错；A+B 流水线里只是降级成启发式
    strict = job.scope == "analyze"
    _check_cancel(job)
    _pitch_stage(job, root, source, base=base, span=span * 0.62, params=pitch_params)
    _check_cancel(job)
    _notes_stage(
        job,
        root,
        source,
        base=base + span * 0.66,
        span=span * 0.32,
        params=notes_params,
        strict=strict,
    )


def run_pipeline(job: Job) -> None:
    root = config.job_dir(job.id)

    # 派生分析任务不碰自己的音频：父任务的 vocals 已经是干净的输入
    if job.scope == "analyze" and job.parent_id:
        _run_analysis(job, root, _analysis_source(job, root), base=0.05, span=0.9)
        _finish(job)
        return

    registry.update(job.id, stage=Stage.DECODING, frac=0.02, detail="解码")
    duration = media.decode_to_wav(_source_file(root), root / "input.wav")
    registry.update(job.id, duration_sec=duration, frac=0.05)

    if job.scope == "analyze":
        # 上传的已经是人声干声：跳过分轨，直接对解码结果分析
        _run_analysis(job, root, root / "input.wav", base=0.10, span=0.84)
    elif job.scope == "separate":
        _separate_stage(job, root, base=0.05, span=0.9)
    else:  # both
        _separate_stage(job, root, base=0.05, span=0.67)
        _run_analysis(job, root, root / "stems" / "vocals.wav", base=0.73, span=0.24)

    _finish(job)
