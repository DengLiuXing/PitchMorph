"""阶段 0 探针：一次回答三个问题，不改环境。

1. torch 能不能在这块卡上真的跑起来（sm_120 / Blackwell）
2. FCPE 能不能装、能不能 import、440 Hz 正弦给出多少
3. 一次推理的显存峰值

用法：uv run python scripts/probe_env.py
退出码 0 = 关键项全过；1 = 有硬失败（torch/CUDA 不可用）
"""

from __future__ import annotations

import inspect
import platform
import sys
import traceback

PROBE_SR = 16000
PROBE_SEC_VOICED = 3.0
PROBE_SEC_SILENCE = 1.0
PROBE_FREQ = 440.0


def _line(key: str, value: object) -> None:
    print(f"{key:<28} {value}")


def _ok(name: str, detail: object) -> None:
    print(f"[PASS] {name}: {detail}")


def _fail(name: str, detail: object) -> None:
    print(f"[FAIL] {name}: {detail}")


def _skip(name: str, detail: object) -> None:
    print(f"[SKIP] {name}: {detail}")


def probe_torch() -> bool:
    """torch + CUDA 真跑一次卷积。返回是否可用 GPU。"""
    try:
        import torch
    except Exception:
        _fail("import torch", "没装，先 uv sync")
        traceback.print_exc()
        return False

    _line("python", sys.version.split()[0])
    _line("platform", platform.platform())
    _line("torch", torch.__version__)
    _line("torch cuda build", torch.version.cuda)
    _line("cuda available", torch.cuda.is_available())

    if not torch.cuda.is_available():
        _fail(
            "cuda",
            "不可用。常见原因：装了 cu126 及更早的 wheel（架构上限 sm_90，"
            "本机是 sm_120），或驱动过旧。",
        )
        return False

    major, minor = torch.cuda.get_device_capability(0)
    total_mb = round(torch.cuda.get_device_properties(0).total_memory / 2**20)
    _line("device", torch.cuda.get_device_name(0))
    _line("capability", f"sm_{major}{minor}")
    _line("total vram MiB", total_mb)

    if (major, minor) != (12, 0):
        _fail("capability", f"期望 (12, 0)，实际 ({major}, {minor})")

    # 真实算子：get_available_providers 式的「看起来有」不算证据
    try:
        torch.cuda.reset_peak_memory_stats()
        x = torch.randn(1, 8, PROBE_SR, device="cuda")
        w = torch.randn(16, 8, 5, device="cuda")
        y = torch.nn.functional.conv1d(x, w)
        torch.cuda.synchronize()
        peak = round(torch.cuda.max_memory_allocated() / 2**20, 1)
        _ok("cuda conv1d", f"out={tuple(y.shape)} peak={peak} MiB")
        return True
    except Exception as exc:
        _fail("cuda conv1d", exc)
        traceback.print_exc()
        return False


def _sine_probe_signal():
    import numpy as np

    n_voiced = int(PROBE_SR * PROBE_SEC_VOICED)
    n_silence = int(PROBE_SR * PROBE_SEC_SILENCE)
    t = np.arange(n_voiced, dtype=np.float32) / PROBE_SR
    voiced = (0.3 * np.sin(2 * np.pi * PROBE_FREQ * t)).astype(np.float32)
    silence = np.zeros(n_silence, dtype=np.float32)
    return np.concatenate([voiced, silence]), n_voiced


def _call_infer(model, audio, sr: int):
    """不同版本的 FCPE 签名不一样，按实际签名筛参数。"""
    try:
        params = inspect.signature(model.infer).parameters
    except (TypeError, ValueError):
        return model.infer(audio, sr)

    kwargs = {"sr": sr, "hop_size": 160, "f0_min": 80, "f0_max": 1100}
    usable = {k: v for k, v in kwargs.items() if k in params}
    return model.infer(audio, **usable)


def probe_fcpe(use_cuda: bool) -> None:
    try:
        import torchfcpe
    except Exception as exc:
        _skip("torchfcpe", f"未安装或 import 失败（{exc}），音高阶段会直接报错")
        return

    _line("torchfcpe", getattr(torchfcpe, "__version__", "unknown"))
    try:
        import numpy as np
        import torch
        from torchfcpe import spawn_bundled_infer_model
    except Exception as exc:
        _fail("import fcpe", exc)
        traceback.print_exc()
        return

    device = "cuda" if use_cuda else "cpu"
    try:
        if use_cuda:
            torch.cuda.reset_peak_memory_stats()
        model = spawn_bundled_infer_model(device=device)
        audio, n_voiced = _sine_probe_signal()
        # torchfcpe 内部直接 .to(device)，必须是 tensor 而不是 ndarray
        wav = torch.from_numpy(audio).unsqueeze(0)
        with torch.no_grad():
            f0 = _call_infer(model, wav, PROBE_SR)
        f0 = np.asarray(f0.detach().cpu().numpy() if hasattr(f0, "detach") else f0)
        f0 = f0.reshape(-1)
        if use_cuda:
            peak = round(torch.cuda.max_memory_allocated() / 2**20, 1)
            _line("fcpe peak vram MiB", peak)
    except Exception as exc:
        _fail("fcpe infer", exc)
        traceback.print_exc()
        return

    total_sec = PROBE_SEC_VOICED + PROBE_SEC_SILENCE
    _line("fcpe frames", f0.shape[0])
    _line("fcpe hop_sec", f"{total_sec / f0.shape[0]:.4f}（期望 0.01）")

    hop = PROBE_SR * 0.01
    voiced_frames = f0[: int(n_voiced / hop)]
    voiced_frames = voiced_frames[np.isfinite(voiced_frames) & (voiced_frames > 0)]
    if voiced_frames.size == 0:
        _fail("fcpe voiced", "全段未检出音高")
        return
    median = float(np.median(voiced_frames))
    drift = median - PROBE_FREQ
    if abs(drift) <= 2.0:
        _ok("fcpe 440 Hz", f"median={median:.2f} Hz, drift={drift:+.2f} Hz")
    else:
        _fail("fcpe 440 Hz", f"median={median:.2f} Hz, drift={drift:+.2f} Hz（容差 ±2）")

    tail = f0[int(n_voiced / hop) + 5 :]
    unvoiced = tail[~np.isfinite(tail) | (tail <= 0)]
    _line("fcpe silence frames", f"{unvoiced.size}/{tail.size} 落在静音区（应接近全部）")
    if tail.size and unvoiced.size < tail.size * 0.9:
        _fail("fcpe 静音段", "静音区检出了过多有效音高")
    else:
        _ok("fcpe 静音段", "静音帧可被区分")


def probe_demucs() -> None:
    """只查签名，不下载权重（htdemucs 约 660 MB）。

    v2 不手写分块：进度靠 `progress=True` 让 demucs 自己打 tqdm，再把 stderr 桥接成
    协议事件。所以这里验的是 `progress` 在不在 —— `callback` 从来就没存在过。
    """
    try:
        import demucs
        import demucs.apply
    except Exception as exc:
        _fail("import demucs", exc)
        return

    _line("demucs", getattr(demucs, "__version__", "unknown"))
    try:
        params = inspect.signature(demucs.apply.apply_model).parameters
    except (TypeError, ValueError) as exc:
        _fail("apply_model signature", exc)
        return

    if "progress" in params:
        _ok("apply_model progress", "有，tqdm 进度可桥接成协议事件")
    else:
        _fail("apply_model progress", f"没有，实际参数：{list(params)}")


def main() -> int:
    print("=== PitchMorph v2 环境探针 ===")
    gpu_ok = probe_torch()
    print()
    probe_demucs()
    print()
    probe_fcpe(gpu_ok)
    print()
    if not gpu_ok:
        _fail("总判定", "GPU 不可用，先修 torch/驱动再继续")
        return 1
    _ok("总判定", "torch 在 sm_120 上可跑")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
