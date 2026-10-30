"""音符阶段：用 GAME 的 encoder + segmenter 取音符边界，写 `features/notes_game.json`。

**只借边界，不借音高**（`docs/05-game.md` M1）：每段音高仍由 FCPE 的中位数决定，
所以这个文件里只有 `start_sec` / `dur_sec`。

边界为什么不由 GAME 自己写成 MIDI：MIDI 会把边界量化到 tick（120 bpm 下 1 tick ≈ 2 ms），
再反解回帧就是二次误差。这里在 callback 里直接收秒值，不落 MIDI。

上游 `infer_model` 走 Lightning，一次 `Predicting` 就是全部片段；这个阶段的进度曲线
没有块级信息，只报「开始 / 完成」两个点，剩下的交给上层的时间估计。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from app import config, protocol


def _model_path(request: dict) -> Path:
    """请求里的 `model` 可以是体积名（small / medium / large），也可以是 .pt 路径。"""
    explicit = request.get("model")
    if explicit and str(explicit) in config.GAME_SIZES:
        found = config.game_model_path(str(explicit))
        if found is None:
            raise FileNotFoundError(
                f"没有 {explicit} 体积的权重：先跑 scripts/fetch_game.py --model {explicit}"
            )
        return found
    if explicit:
        return Path(str(explicit))
    found = config.game_model_path()
    if found is None:
        raise FileNotFoundError(
            f"找不到 GAME 权重。按 {config.ROOT / 'app' / 'worker' / 'game' / 'VENDOR.md'} 下载，"
            f"解包到 {config.GAME_WEIGHTS_DIR / config.GAME_DEFAULT_MODEL}/"
        )
    return found


def _write_json(path: Path, payload: dict) -> None:
    """先 `.part` 再 `os.replace`：取消时不会留下半个 JSON 让导出层读崩。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def run(request: dict[str, Any]) -> dict[str, str]:
    import app.worker.game  # noqa: F401  把 vendored 上游代码放进 sys.path

    import lightning.pytorch.callbacks
    from inference.api import infer_model, load_inference_model
    from inference.callbacks import SaveCombinedFileCallback
    from inference.data import SlicedAudioFileIterableDataset
    from inference.slicer2 import Slicer
    from lib.config.schema import ValidationConfig

    source = Path(request["input"])
    out = Path(request["out"])
    model_path = _model_path(request)

    protocol.progress(0.05, "加载 GAME")
    # 上游会往 stdout 打印配置（`format_model`）和 tqdm 进度条，直接跑会污染协议通道。
    # 推理期间把 stdout 整个换成 stderr；协议事件只在块外发，因此不受影响。
    with contextlib.redirect_stdout(sys.stderr):
        model, lang_map = load_inference_model(model_path)

    class _Collect(SaveCombinedFileCallback):
        """不写文件，只把每个片段的结果收起来。父类负责排序与去重叠。"""

        def __init__(self) -> None:
            super().__init__(output_dir=out.parent)
            self.segments: list[tuple[float, float, float]] = []

        def flush(self, key, notes, logger_fn) -> None:  # noqa: ANN001
            # note.pitch 是 estimator 给的浮点 MIDI。M1 只借边界时它被丢弃，
            # 但实测 FCPE 段内中位数的音高一致率比它低 14 个点，所以一并带上。
            self.segments.extend((note.onset, note.offset, note.pitch) for note in notes)

    collector = _Collect()

    language = request.get("language")
    if language:
        if not lang_map or language not in lang_map:
            raise ValueError(f"该权重不支持语言 {language!r}，可选 {sorted(lang_map or {})}")
        language_id = lang_map[language]
    else:
        language_id = 0

    sr = model.inference_config.features.audio_sample_rate
    dataset = SlicedAudioFileIterableDataset(
        filemap={source.name: source},
        samplerate=sr,
        slicer=Slicer(sr=sr, threshold=-40.0, min_length=1000, min_interval=200, max_sil_kept=100),
        language=language_id,
    )

    protocol.progress(0.15, "GAME 推理")
    with contextlib.redirect_stdout(sys.stderr):
        infer_model(
            model=model,
            dataset=dataset,
            config=ValidationConfig(
                d3pm_sample_steps=int(request.get("nsteps", 8)),
                boundary_decoding_threshold=float(request.get("seg_threshold", 0.2)),
                boundary_decoding_radius=round(float(request.get("seg_radius", 0.02)) / model.timestep),
                note_presence_threshold=float(request.get("est_threshold", 0.2)),
            ),
            callbacks=[collector],
            batch_size=1,
            num_workers=0,
            precision="32-true",
        )

    segments = [
        {
            "start_sec": round(onset, 4),
            "dur_sec": round(offset - onset, 4),
            "midi": round(pitch, 3),
        }
        for onset, offset, pitch in collector.segments
        if offset > onset
    ]
    _write_json(
        out,
        {
            "version": 1,
            "model": model_path.parent.name,
            "hop_sec": model.timestep,
            "source": source.name,
            "segments": segments,
        },
    )
    protocol.progress(0.92, f"{len(segments)} 个音符边界")
    return {"notes_game": out.relative_to(out.parent.parent).as_posix()}
