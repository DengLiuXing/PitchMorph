"""单任务队列：一次只跑一个任务，进度写进 meta.json，取消走协作式检查点。"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import audio, pitch, separate
from .config import AUTO_PITCH, DATA_DIR, MAX_DURATION_S, SAMPLE_RATE
from .errors import JobCancelled

logger = logging.getLogger("pitchmorph.tasks")

# 任务不再自己往前走的三种状态：进入即定格 finished，前端拿它算处理耗时
TERMINAL_STATES = frozenset({"ready", "failed", "cancelled"})


def _now() -> float:
    return time.time()


class JobManager:
    def __init__(self, root: Path = DATA_DIR) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, dict[str, Any]] = {}
        self._cancelled: dict[str, threading.Event] = {}
        self._queue: queue.Queue[tuple[str, str, dict[str, Any]]] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._current: str | None = None

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._loop, name="job-worker", daemon=True)
        self._worker.start()

    def _loop(self) -> None:
        while True:
            job_id, kind, payload = self._queue.get()
            # 排队期间任务可能已被删除（含分轨完自动排的音高）：直接跳过，别让 KeyError 打死 worker
            if job_id not in self._meta:
                logger.info("任务已删除，跳过 %s", job_id)
                self._queue.task_done()
                continue
            self._current = job_id
            try:
                if self._cancelled.get(job_id, threading.Event()).is_set():
                    self._finish(job_id, "cancelled", failure="已取消")
                    continue
                if kind == "separate":
                    self._run_separate(job_id)
                elif kind == "pitch":
                    self._run_pitch(job_id, payload["stem"])
            except JobCancelled:
                self._finish(job_id, "cancelled", failure="已取消")
            except Exception as exc:  # 业务失败统一转成可读原因
                logger.exception("任务失败 %s", job_id)
                self._finish(job_id, "failed", failure=str(exc))
            finally:
                self._current = None
                self._queue.task_done()

    # ---------- 元数据 ----------
    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def meta(self, job_id: str) -> dict[str, Any] | None:
        if job_id in self._meta:
            return self._meta[job_id]
        path = self.job_dir(job_id) / "meta.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._meta[job_id] = data
            return data
        return None

    def all_meta(self) -> list[dict[str, Any]]:
        """列出所有任务。内存里的优先：正在跑的任务落盘可能只写了一半。"""
        metas: list[dict[str, Any]] = []
        for directory in self.root.iterdir():
            if not directory.is_dir():
                continue
            if directory.name in self._meta:
                metas.append(self._meta[directory.name])
                continue
            path = directory / "meta.json"
            if not path.is_file():
                continue
            try:
                metas.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue  # 坏文件不进列表，别让一条坏数据拖垮整个队列页
        return sorted(metas, key=lambda meta: meta.get("created") or 0, reverse=True)

    def _save(self, job_id: str) -> None:
        meta = self._meta.get(job_id)
        if meta is None:
            return
        meta["updated"] = _now()
        path = self.job_dir(job_id) / "meta.json"
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        with self._lock:
            meta = self._meta.get(job_id)
            if meta is None:
                return None
            meta.update(fields)
            # 只写一次：分轨完成后自动排的音高会再把 state 拉回 pitching/ready，
            # 处理时长必须是分轨那一段，不能被音高续算覆盖
            if fields.get("state") in TERMINAL_STATES and meta.get("finished") is None:
                meta["finished"] = _now()
            self._save(job_id)
            return meta

    def _progress(
        self, job_id: str, stage: str, message: str, fraction: float, extra: dict | None = None
    ) -> None:
        with self._lock:
            meta = self._meta.get(job_id)
            if meta is None:
                return
            meta["stage"] = stage
            meta["message"] = message
            meta["fraction"] = round(max(0.0, min(1.0, fraction)), 4)
            meta["extra"] = extra or {}
            self._save(job_id)

    def _finish(self, job_id: str, state: str, failure: str | None = None) -> None:
        self._update(job_id, state=state, failure=failure, fraction=1.0 if state != "failed" else 0.0)
        logger.info("任务结束 id=%s state=%s failure=%s", job_id, state, failure)

    # ---------- 创建与提交 ----------
    def create(self, upload_path: Path, filename: str, mode: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / ("input" + (upload_path.suffix or ".bin"))
        upload_path.replace(target)
        self._meta[job_id] = {
            "id": job_id,
            "mode": mode,
            "filename": filename,
            "state": "queued",
            "stage": "queued",
            "message": "排队中",
            "fraction": 0.0,
            "extra": {},
            "failure": None,
            "created": _now(),
            "finished": None,
            "stems": [],
            "pitch": {},
            "ep": None,
            "duration": None,
        }
        self._cancelled[job_id] = threading.Event()
        self._save(job_id)
        return job_id

    def submit_separate(self, job_id: str) -> None:
        self._queue.put((job_id, "separate", {}))
        self.start()

    def submit_pitch(self, job_id: str, stem: str) -> None:
        meta = self._meta.get(job_id)
        if meta is None:
            return  # 任务已被删除：不要往队列塞幽灵任务
        meta.setdefault("pitch", {})[stem] = {"state": "queued", "fraction": 0.0}
        self._save(job_id)
        self._queue.put((job_id, "pitch", {"stem": stem}))
        self.start()

    def cancel(self, job_id: str) -> None:
        event = self._cancelled.get(job_id)
        if event:
            event.set()

    def delete(self, job_id: str) -> None:
        import shutil

        self._cancelled.pop(job_id, None)
        self._meta.pop(job_id, None)
        # 先删 meta.json：Windows 上导出 zip 可能仍被句柄占用导致整目录删不掉，
        # 但任务必须立刻表现为"不存在"。
        (self.job_dir(job_id) / "meta.json").unlink(missing_ok=True)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    # ---------- 执行体 ----------
    def _should_cancel(self, job_id: str):
        event = self._cancelled.get(job_id)
        # 任务被删掉就当取消：让跑在里面的流水线尽快退出来
        if event is None:
            return lambda: True
        return event.is_set

    def _run_separate(self, job_id: str) -> None:
        from . import runtime

        meta = self._meta[job_id]
        directory = self.job_dir(job_id)
        source_file = next(directory.glob("input.*"))

        self._progress(job_id, "decoding", "解码音频", 0.02)
        source = audio.decode_to_stereo(source_file)
        duration = source.shape[1] / SAMPLE_RATE
        if duration > MAX_DURATION_S:
            raise ValueError(f"音频时长 {duration / 60:.1f} 分钟超过 {MAX_DURATION_S // 60} 分钟上限")
        audio.write_wav(directory / "source.wav", source)
        self._update(job_id, duration=round(duration, 3), ep=runtime.resolve_ep())

        plan = separate.PLANS.get(meta["mode"])
        if plan is None:
            raise ValueError(f"未知的分离档位：{meta['mode']}")
        self._update(job_id, state="separating")

        def on_progress(message: str, fraction: float, extra: dict | None) -> None:
            self._progress(job_id, "separating", message, 0.05 + fraction * 0.88, extra)

        results = separate.separate_track(
            source, plan, on_progress=on_progress, should_cancel=self._should_cancel(job_id)
        )

        if meta["mode"] == "vocals" and audio.is_silent(results["vocals"]):
            raise ValueError("未检出人声，可改用四轨模式看看其他声部")

        self._progress(job_id, "writing", "写入分轨文件", 0.95)
        entries = []
        for name, data in results.items():
            wav = audio.write_wav(directory / f"{name}.wav", data)
            peaks = audio.wave_peaks(data, 2000)
            (directory / f"{name}.peaks.json").write_text(
                json.dumps(peaks, separators=(",", ":")), encoding="utf-8"
            )
            entries.append(
                {
                    "name": name,
                    "file": wav.name,
                    "duration": round(data.shape[1] / SAMPLE_RATE, 3),
                }
            )

        self._update(
            job_id,
            stems=entries,
            state="ready",
            stage="ready",
            message="分轨完成",
            fraction=1.0,
            extra={},
        )
        logger.info("分轨任务完成 id=%s mode=%s stems=%s", job_id, meta["mode"], [e["name"] for e in entries])

        # 分轨完把每条轨的音高都排进队列：点开监视器时数据已经就绪，
        # 不会出现「选了另一条轨还得现算」的情况。某条轨没有有效音高只会
        # 在那条轨上标失败，不影响任务本身。
        if AUTO_PITCH:
            for entry in entries:
                self.submit_pitch(job_id, entry["name"])

    def _run_pitch(self, job_id: str, stem: str) -> None:
        meta = self._meta.get(job_id)
        if meta is None:
            return
        directory = self.job_dir(job_id)
        self._update(job_id, state="pitching")

        def on_progress(value: float) -> None:
            with self._lock:
                m = self._meta.get(job_id)
                if m is None:
                    return
                m.setdefault("pitch", {})[stem] = {"state": "running", "fraction": round(value, 3)}
                m["fraction"] = round(value, 3)  # 顶栏那条细进度也跟着走
                self._save(job_id)

        try:
            curve = pitch.pitch_file(
                directory / f"{stem}.wav",
                on_progress=on_progress,
                progress=lambda message: self._progress(job_id, "pitching", f"{stem}：{message}", 0.0),
                should_cancel=self._should_cancel(job_id),
            )
        except JobCancelled:
            raise
        except Exception as exc:  # 音高失败不能把已经分好的轨判成整任务失败
            with self._lock:
                m = self._meta.get(job_id)
                if m is not None:
                    m.setdefault("pitch", {})[stem] = {"state": "failed", "fraction": 0.0, "failure": str(exc)}
                    m["state"] = "ready"
                    m["message"] = f"{stem} 音高提取失败"
                    self._save(job_id)
            logger.warning("音高失败 id=%s stem=%s %s", job_id, stem, exc)
            return
        (directory / f"pitch_{stem}.json").write_text(
            json.dumps(curve, separators=(",", ":")), encoding="utf-8"
        )
        with self._lock:
            m = self._meta.get(job_id)
            if m is None:
                return  # 算完时任务已被删掉，产物跟着目录一起没了
            m.setdefault("pitch", {})[stem] = {"state": "ready", "fraction": 1.0}
            m["state"] = "ready"
            m["message"] = "音高计算完成"
            self._save(job_id)
        logger.info("音高任务完成 id=%s stem=%s frames=%d", job_id, stem, len(curve["f0"]))


manager = JobManager()
