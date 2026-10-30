"""GAME（`openvpi/GAME`）推理路径的 vendoring 入口。

只做一件事：把 `_vendor/` 放进 `sys.path`，让上游原封不动的绝对 import（`from lib import logging`、
`from modules.functional import ...`）在本进程内可解析。搬进来的文件因此与上游逐字一致，
升级时整目录替换即可，不需要改 import。

⚠️ 只有 `python -m app.worker <stage>` 子进程可以 import 本包：它带进 torch 与 lightning，
   主进程一旦 import 就失去了「OOM 只死子进程」这条边界。
"""

from __future__ import annotations

import sys
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent / "_vendor"

if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

VENDOR_DIR = _VENDOR
