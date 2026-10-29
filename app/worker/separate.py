"""分轨阶段：demucs 官方 `apply_model` + 逐专家进度。

进度为什么按「专家 × 块」报：demucs 4.0.1 的 `apply_model` **没有 callback 参数**
（探针实测签名里只有 model/mix/shifts/split/overlap/transition_power/progress/
device/num_workers/segment/pool）。逐块进度只存在于它内部 tqdm 的输出里，
所以这里把 stderr 桥接成协议事件，而不是手抄它的分块数值代码。

代价：`_ProgressBridge` 依赖 tqdm 打出的 `NN%`。tqdm 若改格式，进度会退化成
不更新（分轨结果不受影响）。
"""

from __future__ import annotations

import os
import re
import sys
import typing as tp
from contextlib import contextmanager
from pathlib import Path

from app import protocol

_PCT = re.compile(r"(\d{1,3})%")


class _ProgressBridge:
    """把 tqdm 写进 stderr 的进度行翻译成协议进度，其余 stderr 内容原样转出。"""

    def __init__(self, real: tp.Any, on_pct: tp.Callable[[float], None]) -> None:
        self._real = real
        self._on_pct = on_pct

    def write(self, text: str) -> int:
        match = _PCT.search(text)
        if match:
            self._on_pct(min(100, int(match.group(1))) / 100)
        else:
            self._real.write(text)
        return len(text)

    def flush(self) -> None:
        self._real.flush()

    def isatty(self) -> bool:
        # 伪装成终端，tqdm 才会用单行刷新而不是一堆新行
        return True


@contextmanager
def _bridge(on_pct: tp.Callable[[float], None]):
    real = sys.stderr
    sys.stderr = _ProgressBridge(real, on_pct)
    try:
        yield
    finally:
        sys.stderr = real


def _default_device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_mix(path: Path, target_sr: int):
    """读成 `(1, 2, N)` float32，采样率与声道数对齐到模型要求。"""
    import numpy as np
    import soundfile as sf
    import torch

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if data.size == 0:
        raise ValueError("输入音频为空")

    channel_first = torch.from_numpy(np.ascontiguousarray(data.T))
    if sr != target_sr:
        import torchaudio

        channel_first = torchaudio.functional.resample(channel_first, sr, target_sr)

    if channel_first.shape[0] == 1:
        channel_first = channel_first.repeat(2, 1)
    elif channel_first.shape[0] > 2:
        channel_first = channel_first[:2]
    return channel_first.unsqueeze(0)


def _write_wav(path: Path, channel_first, sr: int) -> None:
    """先写 .part 再 replace：取消时不会留下能骗过播放器的半截文件。"""
    import numpy as np
    import soundfile as sf

    samples = np.clip(channel_first.detach().cpu().numpy().T, -1.0, 1.0)
    tmp = path.with_name(path.name + ".part")
    sf.write(str(tmp), samples, sr, subtype="PCM_16", format="WAV")
    os.replace(tmp, path)


def run(req: tp.Mapping[str, tp.Any]) -> dict[str, str]:
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    name = str(req.get("model") or "htdemucs")
    src = Path(req["input"])
    out_dir = Path(req["stems_dir"])
    device = str(req.get("device") or _default_device())
    out_dir.mkdir(parents=True, exist_ok=True)

    protocol.progress(0.01, f"加载 {name}")
    bag = get_model(name)

    # 只落盘需要的轨：另外三条是副产品，写它们纯属浪费磁盘和时间
    wanted = tuple(req.get("sources") or bag.sources)
    sources = list(bag.sources)

    # htdemucs_ft 的 weights 是对角阵 —— 每个专家只负责一个音源：
    #   models[0] → drums, models[1] → bass, models[2] → other, models[3] → vocals
    # 所以「只要人声」时跑 models[3] 一个就够，白跑另外三个纯属浪费。
    # 判据用权重而不是下标：换了 bag 定义也不会选错专家。
    plan = [
        (expert, bag.weights[index])
        for index, expert in enumerate(bag.models)
        if any(weight > 0 for weight, source in zip(bag.weights[index], sources) if source in wanted)
    ]
    if not plan:
        raise ValueError(f"没有专家负责 {wanted}")

    sample_rate = int(bag.samplerate)
    total = len(plan)

    protocol.progress(0.04, "解码输入")
    mix = _load_mix(src, sample_rate)

    totals = [0.0] * len(sources)
    stacked = None

    for index, (expert, weights) in enumerate(plan):
        base = 0.05 + 0.85 * index / total
        span = 0.85 / total

        def on_pct(done: float, *, base: float = base, span: float = span, i: int = index) -> None:
            protocol.progress(base + span * done, f"专家 {i + 1}/{total}")

        with _bridge(on_pct):
            with torch.no_grad():
                estimate = apply_model(expert, mix, split=True, progress=True, device=device)

        estimate = estimate.cpu()
        for k, weight in enumerate(weights):
            estimate[:, k] *= weight
            totals[k] += weight
        stacked = estimate if stacked is None else stacked + estimate
        del estimate
        if device.startswith("cuda"):
            torch.cuda.empty_cache()

    for k in range(stacked.shape[1]):
        # 权重全 0 的通道本来就没人负责（也不会被写盘），跳过以免除零
        if totals[k] > 0:
            stacked[:, k] /= totals[k]

    protocol.progress(0.92, "写盘")
    artifacts: dict[str, str] = {}
    for k, source in enumerate(bag.sources):
        if source not in wanted:
            continue
        _write_wav(out_dir / f"{source}.wav", stacked[0, k], sample_rate)
        artifacts[source] = f"stems/{source}.wav"
    return artifacts
