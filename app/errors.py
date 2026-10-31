"""跨模块共用的异常类型，用于把失败原因直接暴露给前端。"""
from __future__ import annotations


class JobCancelled(RuntimeError):
    """用户取消任务。"""


class JobFailed(RuntimeError):
    """业务失败，message 会原样展示给用户。"""
