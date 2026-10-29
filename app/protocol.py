"""worker 子进程与主进程之间的事件协议。

约定：worker 的 **stdout 一行一个 JSON 事件**，所有日志走 stderr。
主进程只解析这里定义的三种形状，不猜模型内部状态——这是「换模型不动业务层」的边界。

放在 `app/` 顶层而不是 `app/worker/` 里面：父子两边都要用，
放 worker 包里会让主进程为了解析一行 JSON 而去 import worker 包。
"""

from __future__ import annotations

import json
import sys

PROGRESS = "progress"
DONE = "done"
ERROR = "error"

_TYPES = (PROGRESS, DONE, ERROR)


def _emit(event: dict) -> None:
    # flush 必须有：管道默认块缓冲，不刷就看不到进度
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def progress(frac: float, detail: str | None = None) -> None:
    """整体进度 0..1。detail 是给人看的一句话，不要放机器可解析的数据。"""
    frac = min(1.0, max(0.0, float(frac)))
    _emit({"t": PROGRESS, "frac": round(frac, 4), "detail": detail})


def done(**artifacts: str) -> None:
    """产物清单：名字 → 相对任务目录的路径。"""
    _emit({"t": DONE, "artifacts": dict(artifacts)})


def error(message: str) -> None:
    """可读的错误。父进程负责翻译成用户文案，这里只报事实。"""
    _emit({"t": ERROR, "message": message})


def parse_line(line: str) -> dict | None:
    """父进程侧解析。坏行、空行、混入的杂音一律返回 None，绝不抛异常。"""
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or event.get("t") not in _TYPES:
        return None
    return event
