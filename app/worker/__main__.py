"""`python -u -m app.worker <stage>`：stdin 读一份 JSON 请求，stdout 吐事件。

进程边界就是显存边界：stage 跑完进程退出，显存一定归还；
崩了（比如 CUDA OOM）也只死这一个进程，主进程重启一个即可。
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback

from app import protocol

_STAGES = {
    "separate": "app.worker.separate",
    "pitch": "app.worker.pitch",
}


def _configure_streams() -> None:
    """钉死管道编码。

    Windows 上 sys.stdout 默认跟 locale 走（中文系统是 cp936），父进程按 UTF-8
    读就会炸在半个汉字上（`0xbc` 就是 GBK 的残留）。协议是内部契约，
    不该随机器语言变化，所以两端都固定在 UTF-8。
    """
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    # stderr 继承终端，编码保持 locale，只兜住无法编码的字符（tqdm 的方块）
    sys.stderr.reconfigure(errors="replace")


def main(argv: list[str]) -> int:
    _configure_streams()
    if len(argv) != 2 or argv[1] not in _STAGES:
        protocol.error(f"未知阶段 {argv[1:] or '（未提供）'}，可选 {sorted(_STAGES)}")
        return 2

    stage = argv[1]
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError as exc:
        protocol.error(f"请求不是合法 JSON：{exc}")
        return 2
    if not isinstance(request, dict):
        protocol.error("请求必须是 JSON 对象")
        return 2

    try:
        module = importlib.import_module(_STAGES[stage])
        artifacts = module.run(request)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        protocol.error(f"{type(exc).__name__}: {exc}")
        return 1

    protocol.done(**artifacts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
