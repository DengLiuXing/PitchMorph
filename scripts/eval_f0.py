"""帧级音高评测：用 vocadito 的人工 F0 标注分开「音高错」与「边界错」。

vocadito 的 `Annotations/F0/*.csv` 是人工校验过的逐帧 F0（第二列 0 表示无音高），
所以这里量的是**曲线本身的准确度**，与音符切分无关：

- 有声召回/精确：该有声的地方我们有没有声、我们报的有声里有多少是真的
- RPA（原始音高准确率）：参考有声的帧里，|误差| ≤ 50 音分的比例
- RCA（原始色度准确率）：同上，但忽略八度（mod 1200），用来区分「唱错八度」与「唱错音」
- 八度错：误差落在 1200±100 音分的比例 —— 单声部模型遇到复音时最典型的失败

用法：

    uv run python scripts/eval_f0.py                 # 全量 40 首，约 2 分钟
    uv run python scripts/eval_f0.py --limit 5       # 先试 5 首

结果缓存到 `temp-dev/work/vocadito_f0.json`，加 `--skip-infer` 可直接复算指标。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.worker.pitch import _infer, _load_mono_16k, to_curve  # noqa: E402

DATASET = ROOT / "temp-dev" / "samples" / "vocadito"
CACHE = ROOT / "temp-dev" / "work" / "vocadito_f0.json"
HOP = 0.01


def load_ref_f0(path: Path) -> tuple[np.ndarray, np.ndarray]:
    data = np.loadtxt(path, delimiter=",", dtype=np.float64, ndmin=2)
    return data[:, 0], data[:, 1]


def align(times: np.ndarray, hz: np.ndarray, frames: int) -> np.ndarray:
    """把参考 F0（不等间隔时间戳）对齐到我们的 10 ms 栅格；取 ±10 ms 内最近点。"""
    grid = np.arange(frames) * HOP
    index = np.searchsorted(times, grid)
    index = np.clip(index, 0, len(times) - 1)
    left = np.clip(index - 1, 0, len(times) - 1)
    choose_left = np.abs(times[left] - grid) < np.abs(times[index] - grid)
    picked = np.where(choose_left, left, index)
    values = hz[picked]
    too_far = np.abs(times[picked] - grid) > 0.011
    values[too_far] = 0.0
    return values


def metrics(ours: np.ndarray, ref: np.ndarray) -> dict[str, float]:
    """ours/ref 都是逐帧 Hz，0 表示无音高。"""
    our_voiced = ours > 0
    ref_voiced = ref > 0
    both = our_voiced & ref_voiced

    cents = np.full(ours.shape, np.inf)
    cents[both] = 1200 * np.log2(ours[both] / ref[both])

    rpa = float(np.mean(np.abs(cents[both]) <= 50)) if both.any() else 0.0
    chroma = np.abs(((cents + 600) % 1200) - 600)
    rca = float(np.mean(chroma[both] <= 50)) if both.any() else 0.0
    octave = float(np.mean(np.abs(np.abs(cents[both]) - 1200) <= 100)) if both.any() else 0.0

    return {
        "voiced_recall": float((both.sum() / ref_voiced.sum()) if ref_voiced.any() else 0.0),
        "voiced_precision": float((both.sum() / our_voiced.sum()) if our_voiced.any() else 0.0),
        "rpa": rpa,
        "rca": rca,
        "octave": octave,
        "frames": int(ref.size),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="vocadito 帧级 F0 评测")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--skip-infer", action="store_true", help="复用缓存")
    parser.add_argument("--refresh", action="store_true", help="强制重算缓存")
    parser.add_argument("--f0-min", type=float, default=80.0)
    parser.add_argument("--f0-max", type=float, default=1100.0)
    args = parser.parse_args()

    wavs = sorted((args.dataset / "Audio").glob("*.wav"), key=lambda p: int(p.stem.split("_")[-1]))
    if args.limit:
        wavs = wavs[: args.limit]

    cache: dict[str, list[float]] = {}
    if CACHE.exists() and not args.refresh:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))

    todo = [wav for wav in wavs if wav.stem not in cache]
    if todo and not args.skip_infer:
        import torch
        from torchfcpe import spawn_bundled_infer_model

        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = spawn_bundled_infer_model(device=device)
        started = time.time()
        with torch.inference_mode():
            for index, wav in enumerate(todo, start=1):
                tensor, _ = _load_mono_16k(wav)
                f0 = _infer(
                    model,
                    tensor.to(device),
                    f0_min=args.f0_min,
                    f0_max=args.f0_max,
                    threshold=0.006,
                )
                cache[wav.stem] = to_curve(f0.reshape(-1).tolist())
                print(f"  {index}/{len(todo)} {wav.stem}", flush=True)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
        print(f"推理 {len(todo)} 首，{time.time() - started:.1f}s → {CACHE}")

    rows = []
    for wav in wavs:
        if wav.stem not in cache:
            continue
        ref_path = args.dataset / "Annotations" / "F0" / f"{wav.stem}_f0.csv"
        if not ref_path.exists():
            continue
        times, hz = load_ref_f0(ref_path)
        ours = np.asarray([value or 0.0 for value in cache[wav.stem]], dtype=np.float64)
        ref = align(times, hz, ours.size)
        row = metrics(ours, ref)
        row["name"] = wav.stem
        rows.append(row)

    if not rows:
        print("没有可评的数据")
        return 1

    print(f"\n{'clip':14s} {'RPA':>7s} {'RCA':>7s} {'八度错':>7s} {'有声召回':>8s} {'有声精确':>8s}")
    for row in rows:
        print(
            f"{row['name']:14s} {row['rpa'] * 100:6.1f}% {row['rca'] * 100:6.1f}% "
            f"{row['octave'] * 100:6.1f}% {row['voiced_recall'] * 100:7.1f}% "
            f"{row['voiced_precision'] * 100:7.1f}%"
        )
    print("-" * 62)
    for key, label in (("rpa", "mean RPA"), ("rca", "mean RCA"), ("octave", "mean 八度错"),
                       ("voiced_recall", "mean 有声召回"), ("voiced_precision", "mean 有声精确")):
        print(f"{label:14s} {statistics.fmean(row[key] for row in rows) * 100:6.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
