"""在 vocadito 上评测音符切分：一次加载模型，跑完整份数据集，再逐个对照人工标注。

vocadito 的价值在于它是**人工标注**（每首还有两个标注者 A1/A2），不是 GAME 的产物，
所以它第一次能回答「绝对准不准」，而不是「和 GAME 像不像」。
A1 与 A2 互相对照的那一行就是人类天花板——比它还好说明指标过拟合了参考。

用法：

    uv run python scripts/eval_vocadito.py --limit 5          # 先试 5 首
    uv run python scripts/eval_vocadito.py                    # 全量 40 首
    uv run python scripts/eval_vocadito.py --model medium --est 0.5

产物（MIDI）写在 `temp-dev/work/vocadito_midi/`，可重复对照。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from compare_notes import (  # noqa: E402
    load_midi,
    load_vocadito_notes,
    onset_stats,
    pitch_stats,
)

DEFAULT_DATASET = ROOT / "temp-dev" / "samples" / "vocadito"
DEFAULT_OUT = ROOT / "temp-dev" / "work" / "vocadito_midi"


def infer_all(wavs: list[Path], out_dir: Path, args: argparse.Namespace) -> None:
    """一份模型 → 全部片段。多个文件共用一个 dataset，模型只加载一次。"""
    import app.worker.game  # noqa: F401  sys.path 里加 vendored 上游
    from inference.api import infer_model, load_inference_model
    from inference.callbacks import SaveCombinedMidiFileCallback
    from inference.data import SlicedAudioFileIterableDataset
    from inference.slicer2 import Slicer
    from lib.config.schema import ValidationConfig

    from app import config

    model_path = config.game_model_path(args.model)
    if model_path is None:
        raise SystemExit(f"没有 {args.model} 权重，先跑 scripts/fetch_game.py --model {args.model}")

    started = time.time()
    model, lang_map = load_inference_model(model_path)
    sr = model.inference_config.features.audio_sample_rate
    dataset = SlicedAudioFileIterableDataset(
        filemap={path.name: path for path in wavs},
        samplerate=sr,
        slicer=Slicer(sr=sr, threshold=-40.0, min_length=1000, min_interval=200, max_sil_kept=100),
        language=lang_map.get(args.language, 0) if args.language else 0,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    infer_model(
        model=model,
        dataset=dataset,
        config=ValidationConfig(
            d3pm_sample_t0=args.t0,
            d3pm_sample_steps=args.nsteps,
            boundary_decoding_threshold=args.seg,
            boundary_decoding_radius=round(args.radius / model.timestep),
            note_presence_threshold=args.est,
        ),
        callbacks=[SaveCombinedMidiFileCallback(output_dir=out_dir, tempo=120)],
        batch_size=1,
        num_workers=0,
        precision="32-true",
    )
    print(f"推理 {len(wavs)} 首，用时 {time.time() - started:.1f}s → {out_dir}")


def report(rows: list[dict]) -> None:
    def mean(key: str) -> float:
        values = [row[key] for row in rows if row[key] is not None]
        return statistics.fmean(values) if values else 0.0

    print("\n" + "=" * 74)
    print(f"{'clip':14s} {'ref':>4s} {'ours':>5s} {'recall':>7s} {'prec':>7s} {'pitch':>7s}")
    for row in rows:
        print(
            f"{row['name']:14s} {row['ref_count']:4d} {row['our_count']:5d} "
            f"{row['recall'] * 100:6.1f}% {row['precision'] * 100:6.1f}% "
            f"{row['pitch'] * 100:6.1f}%"
        )
    print("-" * 74)
    print(
        f"{'mean':14s} {mean('ref_count'):4.0f} {mean('our_count'):5.0f} "
        f"{mean('recall') * 100:6.1f}% {mean('precision') * 100:6.1f}% {mean('pitch') * 100:6.1f}%"
    )
    print(
        f"{'human A2 vs A1':14s} {'':4s} {mean('ceil_count'):5.0f} "
        f"{mean('ceil_recall') * 100:6.1f}% {mean('ceil_precision') * 100:6.1f}% "
        f"{mean('ceil_pitch') * 100:6.1f}%"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="vocadito 音符切分评测")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", default="medium", help="GAME 体积：small / medium / large")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 首（0 = 全部）")
    parser.add_argument("--t0", type=float, default=0.0)
    parser.add_argument("--nsteps", type=int, default=8)
    parser.add_argument("--seg", type=float, default=0.2, help="边界阈值")
    parser.add_argument("--radius", type=float, default=0.02, help="边界半径（秒）")
    parser.add_argument("--est", type=float, default=0.2, help="存在阈值")
    parser.add_argument("--language", default="", help="语言条件，如 zh / en")
    parser.add_argument("--tolerance", type=float, default=0.05, help="起音匹配容差（秒）")
    parser.add_argument("--skip-inference", action="store_true", help="复用上次的 MIDI")
    args = parser.parse_args()

    audio_dir = args.dataset / "Audio"
    notes_dir = args.dataset / "Annotations" / "Notes"
    wavs = sorted(audio_dir.glob("*.wav"), key=lambda p: int(p.stem.split("_")[-1]))
    if args.limit:
        wavs = wavs[: args.limit]
    if not wavs:
        raise SystemExit(f"{audio_dir} 下没有 wav")

    if not args.skip_inference:
        infer_all(wavs, args.out, args)

    rows: list[dict] = []
    for wav in wavs:
        name = wav.stem
        midi_path = args.out / f"{name}.mid"
        if not midi_path.exists():
            print(f"跳过 {name}：没有 {midi_path.name}")
            continue
        ours = load_midi(midi_path)

        ref_path = notes_dir / f"{name}_notesA1.csv"
        ceiling_path = notes_dir / f"{name}_notesA2.csv"
        if not ref_path.exists():
            print(f"跳过 {name}：没有 A1 标注")
            continue
        ref = load_vocadito_notes(ref_path)

        recall, precision = onset_stats(ours, ref, args.tolerance)
        same, near, missing = pitch_stats(ours, ref)
        total = same + near + missing
        row = {
            "name": name,
            "ref_count": len(ref),
            "our_count": len(ours),
            "recall": recall,
            "precision": precision,
            "pitch": same / total if total else 0.0,
        }
        if ceiling_path.exists():
            other = load_vocadito_notes(ceiling_path)
            c_recall, c_precision = onset_stats(other, ref, args.tolerance)
            c_same, c_near, c_missing = pitch_stats(other, ref)
            c_total = c_same + c_near + c_missing
            row["ceil_count"] = len(other)
            row["ceil_recall"] = c_recall
            row["ceil_precision"] = c_precision
            row["ceil_pitch"] = c_same / c_total if c_total else 0.0
        rows.append(row)

    if rows:
        report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
