"""音高阶段：FCPE 对 `stems/vocals.wav` 逐帧取 F0，写 `features/pitch.json`。

FCPE 用 NaN 表示无声（探针实测静音段 96/96 帧可区分），这里统一转成 JSON 的 null。
**不能写 0**：0 Hz 在前端是合法低音，会画出一条贴着底边的假线。

同一份 16 kHz 音频顺带算一次谱通量起音，写 `features/onset.json`：
音高游程答不了「同音高换字」，起音能。两者帧栅格一致（10 ms）。
"""

from __future__ import annotations

import inspect
import json
import math
import os
import sys
import typing as tp
from pathlib import Path

from app import protocol
from app.worker import onsets as onset_detector

SR = 16000
HOP = 160
HOP_SEC = HOP / SR
MODEL_NAME = "fcpe"
F0_MIN = 80.0
F0_MAX = 1100.0

# 只补「短洞」：换气通常在 100 ms 以上，补它会造假；
# 80 ms 以内的空洞多半是 FCPE 在气声/弱音上漏检。
GAP_MAX_SEC = 0.08
GAP_MAX_SEMITONES = 2.5

DEFAULT_THRESHOLD = 0.006


def fill_gaps(
    curve: list[float | None],
    hop_sec: float,
    *,
    max_gap_sec: float = GAP_MAX_SEC,
    max_jump_semitones: float = GAP_MAX_SEMITONES,
) -> list[float | None]:
    """把两侧都有音高、跨度很短的 None 段用对数插值补上。

    音高线断得太碎时人眼无法判断走势，但盲目 `interp_uv` 会把真实换气也填平。
    这里两头都设了闸：洞太长不补，两侧差超过 2.5 个半音也不补（补出来是假线）。
    """
    if not curve:
        return curve

    max_frames = max(1, int(round(max_gap_sec / hop_sec)))
    result = list(curve)
    index = 0

    while index < len(result):
        if result[index] is not None:
            index += 1
            continue

        start = index
        while index < len(result) and result[index] is None:
            index += 1
        end = index

        # 首尾的静音不补：那多半是歌曲开头/结尾的空白
        if start == 0 or end >= len(result):
            continue
        if end - start > max_frames:
            continue

        left = result[start - 1]
        right = result[end]
        if left is None or right is None or left <= 0 or right <= 0:
            continue
        if abs(12 * math.log2(right / left)) > max_jump_semitones:
            continue

        span = end - start
        for offset in range(span):
            ratio = (offset + 1) / (span + 1)
            # 对数（频率比）插值，比线性更接近听觉
            result[start + offset] = round(left * (right / left) ** ratio, 3)

    return result


def to_curve(values: tp.Iterable[tp.Any]) -> list[float | None]:
    """F0 序列 → JSON 友好的曲线。NaN / 0 / 负数一律 None。

    这是本模块唯一的语义转换，抽成纯函数以便脱离 torch 测试。
    """
    curve: list[float | None] = []
    for value in values:
        number = float(value)
        curve.append(round(number, 3) if math.isfinite(number) and number > 0 else None)
    return curve


def _default_device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _load_mono_16k(path: Path):
    import numpy as np
    import soundfile as sf
    import torch

    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    if data.size == 0:
        raise ValueError("输入音频为空")

    mono = torch.from_numpy(np.ascontiguousarray(data.mean(axis=1), dtype=np.float32))
    if rate != SR:
        import torchaudio

        mono = torchaudio.functional.resample(mono, rate, SR)
    return mono.unsqueeze(0), mono.shape[-1] / SR


def _infer(model, wav, *, f0_min: float, f0_max: float, threshold: float):
    """不同版本的 FCPE 签名不同，按实际参数筛。"""
    try:
        params = inspect.signature(model.infer).parameters
    except (TypeError, ValueError):
        return model.infer(wav, SR)

    wanted = {"sr": SR, "f0_min": f0_min, "f0_max": f0_max, "threshold": threshold}
    return model.infer(wav, **{k: v for k, v in wanted.items() if k in params})


def run(req: tp.Mapping[str, tp.Any]) -> dict[str, str]:
    import torch
    from torchfcpe import spawn_bundled_infer_model

    src = Path(req["input"])
    dst = Path(req["out"])
    device = str(req.get("device") or _default_device())

    # 参数全部可选，缺省即默认值。f0 范围收窄能减少倍频误判，
    # 但超出范围的真音也会被丢掉 —— 这是要用户自己权衡的地方。
    f0_min = float(req.get("f0_min") or F0_MIN)
    f0_max = float(req.get("f0_max") or F0_MAX)
    if f0_min >= f0_max:
        raise ValueError(f"f0_min({f0_min}) 必须小于 f0_max({f0_max})")
    threshold = float(req.get("threshold") or DEFAULT_THRESHOLD)
    gap_raw = req.get("gap_ms")
    gap_sec = GAP_MAX_SEC if gap_raw is None else max(0.0, float(gap_raw)) / 1000.0

    protocol.progress(0.05, "加载 FCPE")
    model = spawn_bundled_infer_model(device=device)

    protocol.progress(0.2, "读取人声")
    wav, seconds = _load_mono_16k(src)

    protocol.progress(0.3, "提取音高")
    with torch.no_grad():
        raw = _infer(model, wav, f0_min=f0_min, f0_max=f0_max, threshold=threshold)
    frames = raw.detach().cpu().reshape(-1).tolist()

    # FCPE 会在尾部多补一两帧，按音频时长裁掉，保证时间轴对齐
    curve = to_curve(frames[: int(round(seconds / HOP_SEC)) + 1])
    # 补掉短洞（gap_ms=0 时完全关闭），剩下的长静音保持 None
    if gap_sec > 0:
        curve = fill_gaps(curve, HOP_SEC, max_gap_sec=gap_sec)

    # 起音检测：复用同一份 16 kHz 音频，零额外解码。
    # 关闭时也写空数组 —— 跳过写盘会让上一次的 onset.json 继续生效，
    # 用户点了「关」却发现音符数没变，是最难查的那类 bug。
    onset_flag = req.get("onsets")
    onset_enabled = True if onset_flag is None else bool(onset_flag)
    detected: list[int] = []
    if onset_enabled:
        protocol.progress(0.82, "检测起音")
        try:
            detected = onset_detector.detect_onsets(wav.detach().cpu().numpy().reshape(-1))
        except Exception as exc:  # 起音是附属产物，不能拖垮音高
            print(f"起音检测失败（已降级为空）：{exc!r}", file=sys.stderr, flush=True)
    onset_payload = onset_detector.to_payload(detected, len(curve))

    protocol.progress(0.9, "写盘")
    payload = {
        "version": 1,
        "model": MODEL_NAME,
        "sample_rate": SR,
        "hop_sec": round(HOP_SEC, 6),
        "frames": len(curve),
        # 参数快照：前端据此回显当前档位，重跑时也能沿用
        "params": {
            "f0_min": f0_min,
            "f0_max": f0_max,
            "threshold": threshold,
            "gap_ms": round(gap_sec * 1000),
        },
        "f0": curve,
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, dst)

    onset_dst = dst.with_name("onset.json")
    onset_tmp = onset_dst.with_name(onset_dst.name + ".part")
    onset_tmp.write_text(json.dumps(onset_payload, ensure_ascii=False), encoding="utf-8")
    os.replace(onset_tmp, onset_dst)

    return {
        "pitch": f"{dst.parent.name}/{dst.name}",
        "onset": f"{onset_dst.parent.name}/{onset_dst.name}",
    }
