"""路径与默认参数。所有可变配置集中在这里，别散落到各阶段。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PITCHMORPH_DATA", ROOT / "data" / "jobs"))
MODELS_DIR = Path(os.environ.get("PITCHMORPH_MODELS", ROOT / "models"))
WEIGHTS_CACHE = Path(os.environ.get("PITCHMORPH_CACHE", ROOT / "data" / "weights"))

# FCPE：上游只有 PyTorch，ONNX 由 tools/export_fcpe.py 生成并随仓库入库
FCPE_ONNX = MODELS_DIR / "fcpe_net.onnx"
FCPE_FRAMES = 501  # 5 s @ 16 kHz, hop 160
FCPE_HOP_SEC = 0.01

# GAME：官方 ONNX release，首次运行下载
GAME_ZIP = "https://github.com/openvpi/GAME/releases/download/v1.0.3/GAME-1.0.3-medium-onnx.zip"
GAME_ZIP_SIZE = 179_775_226
GAME_ZIP_SHA256 = "5b7a21e64c6310efac399f5d12838fffa70565be162436b5a4a65f290721e7d8"

TARGET_SR = 44100
FCPE_SR = 16000
JOB_TTL_DAYS = 7
