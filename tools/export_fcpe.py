"""把 torchfcpe 的 FCPE 导出成 ONNX（`models/fcpe_net.onnx` 的唯一复现入口）。

为什么要有这个脚本：FCPE 上游只发 PyTorch checkpoint，官方没有 ONNX 实现，
而 `models/fcpe_net.onnx` 是本项目唯一不可再生的产物。没有它，一旦文件损坏就再也做不出来。

需要 torch 环境（运行时不装 torch，只有导出时用）：

    pip install torch torchfcpe onnx

用法：

    python tools/export_fcpe.py                      # 导出到 models/fcpe_net.onnx
    python tools/export_fcpe.py --out /tmp/t.onnx --check
    python tools/export_fcpe.py --check --onnx models/fcpe_net.onnx

切图范围是「mel → f0」，mel 前处理留在图外：上游 `mel_extractor` 里有数据相关分支
（`pad_right < y.size(-1)`），TorchScript 导出会变成 `onnx::If` 直接失败（见 docs/07-spike.md §2）。
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

FIXED_FRAMES = 501  # 5 s @ 16 kHz, hop 160 → 500 + 1
N_MELS = 128
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "models" / "fcpe_net.onnx"
DEFAULT_REF = Path(__file__).resolve().parents[1] / "models" / "fcpe_net.npz"


def export(out_path: Path, opset: int, threshold: float = 0.006) -> None:
    import torch
    from torchfcpe import spawn_bundled_infer_model

    infer = spawn_bundled_infer_model("cpu").eval()
    net = infer.model.eval()

    class Mel2F0(torch.nn.Module):
        """`model(x)` 只给 360 维 cents 后验；f0 是 `model.infer` 解码出来的，必须一起进图。"""

        def __init__(self, model: "torch.nn.Module", thr: float) -> None:
            super().__init__()
            self.model = model
            self.threshold = thr

        def forward(self, mel: "torch.Tensor") -> "torch.Tensor":
            return self.model.infer(mel, decoder="local_argmax", threshold=self.threshold)

    wrapper = Mel2F0(net, threshold).eval()
    dummy = torch.zeros(1, FIXED_FRAMES, N_MELS, dtype=torch.float32)

    with torch.no_grad():
        traced = torch.jit.trace(wrapper, (dummy,), strict=False)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        traced,
        (dummy,),
        str(out_path),
        input_names=["mel"],
        output_names=["f0"],
        opset_version=opset,
        dynamic_axes=None,  # 输入必须固定，见 docs/weights.md §2
        dynamo=False,  # 走 TS 导出器：不需要 onnxscript
    )
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes, opset {opset})")
    print(f"sha256 {digest}")


def check(onnx_path: Path, ref_path: Path) -> None:
    """用仓库里的回归参考值验一遍，判据与 docs/07-spike.md §2 相同。"""
    import numpy as np
    import onnxruntime as ort

    data = np.load(ref_path)
    mel = data["mel"]
    ref = np.nan_to_num(data["ref_f0"], nan=0.0).reshape(-1)

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    got = np.nan_to_num(sess.run(None, {"mel": mel})[0], nan=0.0).reshape(-1)

    valid = (got > 0) & (ref > 0)
    cents = np.abs(1200 * np.log2(got[valid] / ref[valid]))
    rpa = float(np.mean(cents <= 50) * 100)
    print(f"RPA(±50c) {rpa:.1f}%  median {np.median(cents):.4f}c  p99 {np.percentile(cents, 99):.4f}c")
    if rpa < 99.9:
        raise SystemExit("数值不等价：RPA 低于 99.9%")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--opset", type=int, default=17)
    parser.add_argument("--check", action="store_true", help="导出后用 models/fcpe_net.npz 验一遍")
    parser.add_argument("--onnx", type=Path, default=None, help="只验不导出时指定文件")
    args = parser.parse_args()

    if args.onnx is None:
        export(args.out, args.opset)
        target = args.out
    else:
        target = args.onnx
    if args.check:
        check(target, DEFAULT_REF)


if __name__ == "__main__":
    main()
