"""任务元数据的完成时刻：进入终态时定格一次，供前端算处理耗时。"""
from __future__ import annotations

from pathlib import Path

from app.tasks import JobManager


def _new_job(tmp_path: Path) -> tuple[JobManager, str]:
    manager = JobManager(root=tmp_path)
    upload = tmp_path / "sample.wav"
    upload.write_bytes(b"RIFF")
    return manager, manager.create(upload, "sample.wav", "vocals")


def test_finished_is_empty_while_running(tmp_path: Path) -> None:
    manager, job_id = _new_job(tmp_path)
    meta = manager.meta(job_id)
    assert meta["finished"] is None
    assert meta["created"] > 0


def test_finished_stamped_once_on_terminal_state(tmp_path: Path) -> None:
    manager, job_id = _new_job(tmp_path)
    manager._update(job_id, state="ready")
    finished = manager.meta(job_id)["finished"]
    assert finished is not None and finished >= manager.meta(job_id)["created"]

    # 分轨完成后自动排的音高会把 state 拉回 pitching / 再写 ready，耗时不能被改写
    manager._update(job_id, state="pitching")
    manager._update(job_id, state="ready")
    assert manager.meta(job_id)["finished"] == finished


def test_failed_job_also_records_elapsed(tmp_path: Path) -> None:
    manager, job_id = _new_job(tmp_path)
    manager._finish(job_id, "failed", failure="解码失败")
    meta = manager.meta(job_id)
    assert meta["state"] == "failed"
    assert meta["finished"] is not None
